"""한국 주식시장 시간 도우미."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def today_str() -> str:
    return now_kst().strftime("%Y-%m-%d")


def parse_hhmm(s: str) -> dt.time:
    h, m = str(s).split(":")
    return dt.time(int(h), int(m), tzinfo=KST)


def is_weekday(d: dt.date | None = None) -> bool:
    d = d or now_kst().date()
    return d.weekday() < 5


def is_holiday(cfg: dict, d: dt.date | None = None) -> bool:
    d = d or now_kst().date()
    hol = {str(x) for x in cfg.get("schedule", {}).get("holidays", []) or []}
    return d.strftime("%Y-%m-%d") in hol


def is_trading_day(cfg: dict, d: dt.date | None = None) -> bool:
    d = d or now_kst().date()
    return is_weekday(d) and not is_holiday(cfg, d)


def in_market_hours(cfg: dict, t: dt.datetime | None = None, grace_minutes: int = 2) -> bool:
    """장중(09:00~15:30 + 여유 몇 분)인지."""
    t = t or now_kst()
    sch = cfg.get("schedule", {})
    o = parse_hhmm(sch.get("market_open", "09:00"))
    c = parse_hhmm(sch.get("market_close", "15:30"))
    start = dt.datetime.combine(t.date(), o.replace(tzinfo=None), KST)
    end = dt.datetime.combine(t.date(), c.replace(tzinfo=None), KST) + dt.timedelta(minutes=grace_minutes)
    return start <= t <= end


def minutes_since_open(cfg: dict, t: dt.datetime | None = None) -> float:
    t = t or now_kst()
    o = parse_hhmm(cfg.get("schedule", {}).get("market_open", "09:00"))
    start = dt.datetime.combine(t.date(), o.replace(tzinfo=None), KST)
    return (t - start).total_seconds() / 60.0
