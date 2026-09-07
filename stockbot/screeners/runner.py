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
    say(f"유니버스 {umeta.get('n_universe')}종목 (제외 목록: 환기 {umeta.get('n_hwangi', 0)} · 관리 {umeta.get('n_admin', 0)} · 정리매매 {umeta.get('n_delist', 0)} · 투자위험 {umeta.get('n_risk', 0)})")

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

    annotate_repeats(results, ref_date, int(cfg.get("output", {}).get("repeat_days", 20)))
    out = {
        "date": ref_date.strftime("%Y-%m-%d"),
        "run_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "universe": umeta,
        "results": results,
        "overlap": overlap_list(results),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULT_DIR / f"{out['date']}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(RESULT_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def _past_results(ref_date: dt.date, days: int) -> list[dict]:
    """기준일 이전 N일(달력) 안의 저장된 결과들."""
    out = []
    if not RESULT_DIR.exists():
        return out
    lo = ref_date - dt.timedelta(days=days)
    for p in sorted(RESULT_DIR.glob("????-??-??.json")):
        try:
            d = dt.date.fromisoformat(p.stem)
        except ValueError:
            continue
        if lo <= d < ref_date:
            try:
                with open(p, encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:  # noqa: BLE001
                pass
    return out


def annotate_repeats(results: dict, ref_date: dt.date, days: int) -> None:
    """같은 검색식에 최근 N일 안에 이미 떴던 종목이면 signal['seen'] = ['YYYY-MM-DD', ...] 표시."""
    past = _past_results(ref_date, days)
    seen: dict[tuple[str, str], list[str]] = {}
    for o in past:
        for key, r in o.get("results", {}).items():
            for sg in r.get("signals", []):
                seen.setdefault((key, sg["code"]), []).append(o.get("date", ""))
    for key, r in results.items():
        n_new = 0
        for sg in r.get("signals", []):
            sg["seen"] = sorted(seen.get((key, sg["code"]), []))
            if not sg["seen"]:
                n_new += 1
        r["count_new"] = n_new


def overlap_list(results: dict) -> list[dict]:
    """여러 검색식에 같이 걸린 종목 (교집합). 정량 점수: 검색식 수 + 상위권 가산."""
    table: dict[str, dict] = {}
    for key, r in results.items():
        for rank, sg in enumerate(r.get("signals", []), 1):
            bonus = 1.0 if rank <= 3 else 0.5 if rank <= 10 else 0.0
            c = table.setdefault(sg["code"], {"code": sg["code"], "name": sg["name"], "price": sg["price"],
                                              "change_pct": sg["change_pct"], "titles": [], "score": 0.0})
            c["titles"].append(r["title"])
            c["score"] += 1.0 + bonus
    rows = [c for c in table.values() if len(c["titles"]) >= 2]
    rows.sort(key=lambda c: (len(c["titles"]), c["score"]), reverse=True)
    for c in rows:
        c["score"] = round(c["score"], 1)
    return rows


def load_latest_result() -> dict | None:
    p = RESULT_DIR / "latest.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)
