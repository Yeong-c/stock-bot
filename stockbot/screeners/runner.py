"""장 마감 후 검색식 2~6 일괄 실행."""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Callable

from ..config import DATA_DIR
from ..data.store import RESULT_DIR, ensure_daily
from ..data.universe import build_universe
from .daily import SCREENERS

log = logging.getLogger("stockbot.runner")


def run_daily_scan(cfg: dict, progress: Callable[[str], None] | None = None) -> dict:
    def say(msg: str):
        log.info(msg)
        if progress:
            progress(msg)

    say("유니버스 구성 중 (상장목록 + 환기종목)")
    universe, umeta = build_universe(cfg)
    say(f"유니버스 {umeta.get('n_universe')}종목 (환기 제외 {umeta.get('n_hwangi')}종목)")

    codes = universe["code"].tolist()
    daily = ensure_daily(codes, years=5, progress=say)
    if not daily:
        raise RuntimeError("일봉 데이터를 하나도 받지 못했습니다")
    ref_ts = max(df["date"].max() for df in daily.values())
    ref_date = ref_ts.date()
    say(f"기준일 {ref_date} · 일봉 {len(daily)}종목 준비 완료, 검색식 실행")

    results: dict[str, dict] = {}
    for key, title, fn in SCREENERS:
        p = cfg["screeners"].get(key, {})
        if not p.get("enabled", True):
            continue
        sigs = []
        for row in universe.itertuples():
            df = daily.get(row.code)
            if df is None or df["date"].max().date() < ref_date:
                continue  # 기준일 거래 없음
            if float(df["volume"].iloc[-1]) <= 0:
                continue  # 거래정지 의심
            try:
                s = fn(df, row, p, ref_date)
            except Exception as e:  # noqa: BLE001
                log.debug("%s %s 오류: %s", key, row.code, e)
                s = None
            if s is not None:
                sigs.append(s)
        sigs.sort(key=lambda s: s.score, reverse=True)
        results[key] = {"title": title, "count": len(sigs), "signals": [s.to_dict() for s in sigs]}
        say(f"  {title}: {len(sigs)}종목")

    out = {
        "date": ref_date.strftime("%Y-%m-%d"),
        "run_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "universe": umeta,
        "results": results,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULT_DIR / f"{out['date']}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(RESULT_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def load_latest_result() -> dict | None:
    p = RESULT_DIR / "latest.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)
