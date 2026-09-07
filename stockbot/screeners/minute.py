"""1. 분봉 거래폭발 — 장중 실시간 감시.

기준값: 감시 종목별 1분봉 거래량(최대 1년치)을 큰 순으로 정렬해 상위 60% 의 평균.
        키움 API 연결 시 최대 1년, 미연결 시 네이버 7거래일치를 매일 누적.
신호  : 방금 지난 1분 거래량이 기준값의 6배 이상.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Callable

import pandas as pd

from ..calendar_kr import minutes_since_open, now_kst
from ..data import naver
from ..data.store import load_minute_history, save_minute_history
from .common import volume_baseline

log = logging.getLogger("stockbot.minute")


class MinuteMonitor:
    def __init__(self, cfg: dict, state, kiwoom=None, name_of: Callable[[str], str] | None = None):
        self.cfg = cfg
        self.state = state
        self.kiwoom = kiwoom
        self.name_of = name_of or (lambda c: c)
        self.p = cfg["screeners"]["minute_burst"]
        self._samples: dict[str, tuple[dt.datetime, float]] = {}
        self._last_alert: dict[str, dt.datetime] = {}

    # ---------------- 기준값 ----------------
    def build_baseline(self, code: str, force: bool = False,
                       progress: Callable[[str], None] | None = None) -> dict:
        today = now_kst().date()
        cur = self.state.get_baseline(code)
        if cur and cur.get("built") == today.isoformat() and not force:
            return cur

        lookback = int(self.p.get("lookback_days", 365))
        since = pd.Timestamp(today) - pd.Timedelta(days=lookback)
        bars: pd.DataFrame | None = None
        source = ""

        if self.kiwoom is not None:
            try:
                hist = load_minute_history(code)
                stop_before = hist["ts"].max().to_pydatetime() if hist is not None and len(hist) else since.to_pydatetime()
                new = self.kiwoom.minute_bars(code, stop_before=stop_before,
                                              progress=lambda pg, n: progress and progress(f"{code} 키움 분봉 {pg}페이지/{n}봉"))
                merged = new if hist is None else (pd.concat([hist, new]).sort_values("ts")
                                                    .drop_duplicates("ts", keep="last").reset_index(drop=True))
                if len(merged):
                    save_minute_history(code, merged)
                    bars = merged
                    source = "키움"
            except Exception as e:  # noqa: BLE001
                log.warning("키움 분봉 수집 실패 %s: %s (네이버로 대체)", code, e)

        if bars is None or len(bars) < 100:
            fresh = naver.fetch_minute_bars(code)
            # 네이버는 최근 7거래일만 주므로, 매일 받은 것을 파일에 누적해 점점 긴 기준(최대 1년)을 만든다
            hist = load_minute_history(code)
            if hist is not None and len(hist):
                fresh = fresh.reindex(columns=["ts", "close", "volume"])
                merged = (pd.concat([hist[["ts", "close", "volume"]], fresh]).sort_values("ts")
                          .drop_duplicates("ts", keep="last").reset_index(drop=True))
            else:
                merged = fresh
            if len(merged):
                save_minute_history(code, merged)
            bars = merged
            source = "네이버 누적"

        bars = bars[(bars["ts"] >= since) & (bars["ts"].dt.date < today)]
        t = bars["ts"].dt.time
        bars = bars[(t >= dt.time(9, 0)) & (t <= dt.time(15, 30))]
        if len(bars) == 0:
            info = {"mean": 0.0, "bars": 0, "days": 0, "from": "", "to": "", "source": source,
                    "built": today.isoformat()}
            self.state.set_baseline(code, info)
            return info
        mean = volume_baseline(bars["volume"].values, self.p)
        info = {
            "mean": round(mean, 1),
            "bars": int(len(bars)),
            "days": int(bars["ts"].dt.date.nunique()),
            "from": bars["ts"].min().strftime("%Y-%m-%d"),
            "to": bars["ts"].max().strftime("%Y-%m-%d"),
            "source": source,
            "built": today.isoformat(),
        }
        self.state.set_baseline(code, info)
        return info

    def prepare_all(self, force: bool = False, progress: Callable[[str], None] | None = None) -> dict[str, dict]:
        out = {}
        for code in self.state.watchlist:
            try:
                out[code] = self.build_baseline(code, force=force, progress=progress)
            except Exception as e:  # noqa: BLE001
                log.warning("기준값 생성 실패 %s: %s", code, e)
        return out

    # ---------------- 장중 폴링 ----------------
    def poll(self, now: dt.datetime | None = None) -> list[dict]:
        now = now or now_kst()
        codes = self.state.watchlist
        if not codes:
            return []
        rt = naver.fetch_realtime(codes)
        alerts: list[dict] = []
        since_open = minutes_since_open(self.cfg, now)
        cooldown = dt.timedelta(minutes=float(self.p.get("cooldown_minutes", 10)))
        for code in codes:
            d = rt.get(code)
            if not d:
                continue
            prev = self._samples.get(code)
            self._samples[code] = (now, d["acc_volume"])
            if prev is None:
                continue
            prev_t, prev_aq = prev
            if prev_t.date() != now.date():
                continue
            elapsed = (now - prev_t).total_seconds() / 60.0
            if elapsed <= 0 or elapsed > 5:
                continue
            vol = d["acc_volume"] - prev_aq
            if vol <= 0:
                continue
            vpm = vol / max(elapsed, 1.0)
            base = self.state.get_baseline(code)
            if not base or float(base.get("mean", 0)) <= 0:
                continue
            mult = vpm / float(base["mean"])
            if mult < float(self.p.get("multiple", 6.0)):
                continue
            if since_open < float(self.p.get("ignore_first_minutes", 5)):
                continue
            la = self._last_alert.get(code)
            if la and (now - la) < cooldown:
                continue
            self._last_alert[code] = now
            alert = {
                "time": now.strftime("%H:%M"),
                "code": code,
                "name": d.get("name") or self.name_of(code),
                "minute_volume": int(vpm),
                "baseline": float(base["mean"]),
                "multiple": round(mult, 1),
                "price": d["price"],
                "change_pct": d["change_pct"],
                "open": d["open"],
                "source": base.get("source", ""),
                "days": base.get("days", 0),
            }
            self.state.add_minute_alert(now.strftime("%Y-%m-%d"), alert)
            alerts.append(alert)
        return alerts

    def reset_day(self) -> None:
        self._samples.clear()
        self._last_alert.clear()
