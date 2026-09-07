"""일봉 캐시 (data/daily/{code}.csv) 와 증분 갱신."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Callable

import pandas as pd

from ..config import DATA_DIR
from . import naver

log = logging.getLogger("stockbot.store")

DAILY_DIR = DATA_DIR / "daily"
MINUTE_DIR = DATA_DIR / "minute"
RESULT_DIR = DATA_DIR / "results"


def load_daily(code: str) -> pd.DataFrame | None:
    p = DAILY_DIR / f"{code}.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, parse_dates=["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception:  # noqa: BLE001
        return None


def save_daily(code: str, df: pd.DataFrame) -> None:
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(DAILY_DIR / f"{code}.csv", index=False)


def ensure_daily(
    codes: list[str], years: int = 5, workers: int = 8, force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, pd.DataFrame]:
    """캐시를 읽고, 오늘 데이터가 없는 종목만 최근 구간을 다시 받아 합친다."""
    today = dt.date.today()
    end = today.strftime("%Y%m%d")
    full_start = (today - dt.timedelta(days=365 * years + 10)).strftime("%Y%m%d")

    result: dict[str, pd.DataFrame] = {}
    need_full: list[str] = []
    need_inc: dict[str, str] = {}
    for c in codes:
        cached = None if force else load_daily(c)
        if cached is None or len(cached) < 30:
            need_full.append(c)
            continue
        last = cached["date"].max().date()
        result[c] = cached
        if last < today:
            need_inc[c] = (last - dt.timedelta(days=10)).strftime("%Y%m%d")

    if progress:
        progress(f"일봉 캐시 {len(result)}종목, 전체수집 {len(need_full)}종목, 증분 {len(need_inc)}종목")

    if need_full:
        got = naver.fetch_daily_many(need_full, full_start, end, workers=workers,
                                     progress=lambda d, n: progress and progress(f"전체수집 {d}/{n}"))
        for c, df in got.items():
            if len(df):
                save_daily(c, df)
                result[c] = df

    if need_inc:
        # 시작일이 종목마다 달라 개별 요청. 대부분 같은 날짜라 묶어서 요청.
        groups: dict[str, list[str]] = {}
        for c, s in need_inc.items():
            groups.setdefault(s, []).append(c)
        for s, cs in groups.items():
            got = naver.fetch_daily_many(cs, s, end, workers=workers)
            for c, new in got.items():
                if not len(new):
                    continue
                old = result[c]
                merged = (pd.concat([old, new]).sort_values("date")
                          .drop_duplicates("date", keep="last").reset_index(drop=True))
                save_daily(c, merged)
                result[c] = merged
    return result


# ──────────────── 분봉 히스토리 캐시 (키움) ────────────────

def load_minute_history(code: str) -> pd.DataFrame | None:
    p = MINUTE_DIR / f"{code}.csv"
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, parse_dates=["ts"]).sort_values("ts").reset_index(drop=True)
    except Exception:  # noqa: BLE001
        return None


def save_minute_history(code: str, df: pd.DataFrame, keep_days: int = 400) -> None:
    MINUTE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=keep_days)
    df = df[df["ts"] >= cutoff]
    df.to_csv(MINUTE_DIR / f"{code}.csv", index=False)
