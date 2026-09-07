"""네이버 금융 데이터 (로그인·키 불필요).

- 일봉/월봉: api.finance.naver.com/siseJson.naver
- 분봉(최근 약 7거래일): fchart.stock.naver.com/sise.nhn
- 실시간 현재가·누적거래량: polling.finance.naver.com
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import re
import time
from typing import Callable

import pandas as pd
import requests

log = logging.getLogger("stockbot.naver")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}

_session = requests.Session()
_session.headers.update(HEADERS)


def _get(url: str, params: dict | None = None, timeout: int = 20, retries: int = 3) -> requests.Response:
    last: Exception | None = None
    for i in range(retries):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"요청 실패 {url}: {last}")


# ──────────────────────────── 일봉 / 월봉 ────────────────────────────

def fetch_chart(code: str, start: str, end: str, timeframe: str = "day") -> pd.DataFrame:
    """siseJson 차트. start/end = 'YYYYMMDD'. timeframe = day|week|month.

    반환: date(datetime64), open, high, low, close, volume (정렬됨)
    """
    r = _get(
        "https://api.finance.naver.com/siseJson.naver",
        params={"symbol": code, "requestType": 1, "startTime": start, "endTime": end, "timeframe": timeframe},
    )
    txt = r.text.strip()
    if not txt:
        return _empty()
    try:
        rows = json.loads(txt.replace("'", '"'))
    except json.JSONDecodeError:
        return _empty()
    rows = [x for x in rows[1:] if isinstance(x, list) and len(x) >= 6 and x[0]]
    if not rows:
        return _empty()
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["date", "open", "high", "low", "close", "volume"]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date", keep="last")
    df = df[df["close"] > 0]
    return df.reset_index(drop=True)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def fetch_daily_many(
    codes: list[str], start: str, end: str, workers: int = 8,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    done = 0

    def one(c: str):
        try:
            return c, fetch_chart(c, start, end, "day")
        except Exception as e:  # noqa: BLE001
            log.warning("일봉 수집 실패 %s: %s", c, e)
            return c, None

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for c, df in ex.map(one, codes):
            done += 1
            if df is not None:
                out[c] = df
            if progress and done % 100 == 0:
                progress(done, len(codes))
    return out


# ──────────────────────────── 분봉 (최근 며칠) ────────────────────────────

_ITEM_RE = re.compile(r'<item data="([^"]+)"')


def fetch_minute_bars(code: str) -> pd.DataFrame:
    """네이버가 제공하는 최근 약 7거래일치 1분봉.

    반환: ts(datetime64), close, volume(그 1분 동안의 거래량)
    """
    r = _get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": code, "timeframe": "minute", "count": 5000, "requestType": 0},
    )
    txt = r.content.decode("euc-kr", errors="replace")
    recs = []
    for m in _ITEM_RE.finditer(txt):
        parts = m.group(1).split("|")
        if len(parts) < 6:
            continue
        ts, close, cum = parts[0], parts[4], parts[5]
        if close in ("null", "") or cum in ("null", ""):
            continue
        recs.append((ts, float(close), float(cum)))
    if not recs:
        return pd.DataFrame(columns=["ts", "close", "volume"])
    df = pd.DataFrame(recs, columns=["ts", "close", "cum"])
    df["ts"] = pd.to_datetime(df["ts"], format="%Y%m%d%H%M", errors="coerce")
    df = df.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts", keep="last")
    df["day"] = df["ts"].dt.date
    df["volume"] = df.groupby("day")["cum"].diff()
    first = df["volume"].isna()
    df.loc[first, "volume"] = df.loc[first, "cum"]
    df["volume"] = df["volume"].clip(lower=0)
    return df[["ts", "close", "volume"]].reset_index(drop=True)


# ──────────────────────────── 실시간 ────────────────────────────

def _to_float(v) -> float:
    try:
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0


def fetch_realtime(codes: list[str]) -> dict[str, dict]:
    """여러 종목의 현재가/등락률/누적거래량. 반환 dict[code] = {...}"""
    out: dict[str, dict] = {}
    codes = [c for c in codes if c]
    for i in range(0, len(codes), 40):
        chunk = codes[i:i + 40]
        try:
            r = _get(
                "https://polling.finance.naver.com/api/realtime",
                params={"query": "SERVICE_ITEM:" + ",".join(chunk)},
                timeout=15,
            )
            raw = r.content
            try:
                txt = raw.decode("utf-8")
            except UnicodeDecodeError:
                txt = raw.decode("euc-kr", errors="replace")
            js = json.loads(txt)
            for area in js.get("result", {}).get("areas", []):
                for d in area.get("datas", []):
                    cd = str(d.get("cd", "")).zfill(6)
                    out[cd] = {
                        "name": d.get("nm", ""),
                        "price": _to_float(d.get("nv")),
                        "change": _to_float(d.get("cv")),
                        "change_pct": _to_float(d.get("cr")),
                        "acc_volume": _to_float(d.get("aq")),
                        "open": _to_float(d.get("ov")),
                        "high": _to_float(d.get("hv")),
                        "low": _to_float(d.get("lv")),
                        "prev_close": _to_float(d.get("pcv")),
                        "status": d.get("ms", ""),
                    }
        except Exception as e:  # noqa: BLE001
            log.warning("실시간 조회 실패 (%s...): %s", chunk[:3], e)
    return out
