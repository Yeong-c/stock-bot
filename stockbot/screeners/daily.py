"""일봉 기반 검색식 2~6.

각 함수: run_xxx(df, row, p, ref_date) -> Signal | None
  df   : date, open, high, low, close, volume (오름차순, 마지막 행 = 기준일)
  row  : 유니버스 행 (code, name, market, marcap)
  p    : config 의 해당 검색식 파라미터 dict
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .common import Signal, last_change_pct, month_label, volume_baseline


def _base(df: pd.DataFrame, row, key: str, title: str, score: float, lines: list[str], extra=None) -> Signal:
    return Signal(
        key=key, title=title, code=row.code, name=row.name, market=str(row.market),
        price=float(df["close"].iloc[-1]), change_pct=last_change_pct(df), score=float(score),
        lines=lines, extra=extra or {},
    )


# 2. 바닥 매집 ───────────────────────────────────────────────
def run_bottom_accum(df: pd.DataFrame, row, p: dict, ref_date: dt.date) -> Signal | None:
    if len(df) < 400:
        return None
    hi_i = int(df["high"].values.argmax())
    peak = float(df["high"].iloc[hi_i])
    peak_date = df["date"].iloc[hi_i]
    years = (ref_date - peak_date.date()).days / 365.25
    if years < float(p.get("min_decline_years", 2)):
        return None
    close = float(df["close"].iloc[-1])
    drop = (1 - close / peak) * 100 if peak > 0 else 0
    if drop < float(p.get("drop_pct", 60)):
        return None
    m = (df.set_index("date").resample("MS")
         .agg(open=("open", "first"), close=("close", "last")).dropna())
    if len(m) == 0:
        return None
    cur_month = pd.Timestamp(ref_date.year, ref_date.month, 1)
    if not p.get("include_current_month", True) and m.index[-1] == cur_month:
        m = m.iloc[:-1]
    n = int(p.get("months", 2))
    last = m.tail(n)
    if len(last) < n or not bool((last["close"] > last["open"]).all()):
        return None
    labels = [month_label(ts) + ("(진행중)" if ts == cur_month else "") for ts in last.index]
    lines = [
        f"고점 {peak:,.0f}원({month_label(peak_date)}, {years:.1f}년 전) 대비 -{drop:.0f}%",
        f"월봉 {' · '.join(labels)} 연속 양봉",
    ]
    return _base(df, row, "bottom_accum", "바닥 매집", drop, lines)


# 3. 급락 수급 ───────────────────────────────────────────────
def run_crash_volume(df: pd.DataFrame, row, p: dict, ref_date: dt.date) -> Signal | None:
    n = int(p.get("lookback_days", 30))
    k = int(p.get("vol_avg_days", 3))
    if len(df) < n + k + 1:
        return None
    w = df.tail(n)
    hi_i = int(w["high"].values.argmax())
    hi = float(w["high"].iloc[hi_i])
    hi_date = w["date"].iloc[hi_i]
    close = float(df["close"].iloc[-1])
    drop = (1 - close / hi) * 100 if hi > 0 else 0
    if drop < float(p.get("drop_pct", 50)):
        return None
    vol = float(df["volume"].iloc[-1])
    prev = df["volume"].iloc[-1 - k:-1].mean()
    if prev <= 0:
        return None
    mult = vol / prev
    if mult < float(p.get("vol_multiple", 2.5)):
        return None
    lines = [
        f"{n}일 내 고점 {hi:,.0f}원({hi_date:%m.%d}) 대비 -{drop:.0f}%",
        f"거래량 {vol:,.0f}주 = 최근 {k}일 평균의 {mult:.1f}배",
    ]
    return _base(df, row, "crash_volume", "급락 수급", mult, lines)


# 4. 급등 눌림 ───────────────────────────────────────────────
def run_surge_pullback(df: pd.DataFrame, row, p: dict, ref_date: dt.date) -> Signal | None:
    n = int(p.get("lookback_days", 20))
    if len(df) < n + 2:
        return None
    w = df.tail(n + 1)
    highs = w["high"].values.astype(float)
    lows = w["low"].values.astype(float)
    hi_i = int(highs[1:].argmax()) + 1
    hi = highs[hi_i]
    base = float(lows[:hi_i + 1].min())
    if base <= 0 or hi <= 0:
        return None
    surge = (hi / base - 1) * 100
    if surge < float(p.get("surge_pct", 27)):
        return None
    close = float(df["close"].iloc[-1])
    drop = (1 - close / hi) * 100
    if drop < float(p.get("pullback_pct", 30)):
        return None
    hi_date = w["date"].iloc[hi_i]
    lines = [
        f"{n}일 내 저점 {base:,.0f}원 → 고점 {hi:,.0f}원({hi_date:%m.%d}) +{surge:.0f}% 급등",
        f"고점 대비 -{drop:.0f}% 눌림",
    ]
    return _base(df, row, "surge_pullback", "급등 눌림", surge, lines)


# 5. 정배열 ───────────────────────────────────────────────
def _ma_aligned(df: pd.DataFrame) -> tuple[bool, dict]:
    c = df["close"].astype(float)
    if len(c) < 240:
        return False, {}
    mas = {n: float(c.tail(n).mean()) for n in (20, 60, 120, 240)}
    ok = mas[20] > mas[60] > mas[120] > mas[240]
    return ok, mas


def run_aligned_breakout(df: pd.DataFrame, row, p: dict, ref_date: dt.date) -> Signal | None:
    ok, mas = _ma_aligned(df)
    if not ok:
        return None
    k = int(p.get("vol_avg_days", 3))
    vol = float(df["volume"].iloc[-1])
    prev = df["volume"].iloc[-1 - k:-1].mean()
    if prev <= 0:
        return None
    mult = vol / prev
    if mult < float(p.get("vol_multiple", 3.0)):
        return None
    chg = last_change_pct(df)
    if not (float(p.get("min_change_pct", 4)) <= chg <= float(p.get("max_change_pct", 15))):
        return None
    if not float(df["close"].iloc[-1]) > float(df["open"].iloc[-1]):
        return None
    lines = [
        f"20>60>120>240일선 정배열 (20일선 {mas[20]:,.0f}원)",
        f"양봉 +{chg:.1f}% · 거래량 {vol:,.0f}주 = 최근 {k}일 평균의 {mult:.1f}배",
    ]
    return _base(df, row, "aligned_breakout", "정배열 돌파", mult, lines)


def run_aligned_pullback(df: pd.DataFrame, row, p: dict, ref_date: dt.date) -> Signal | None:
    ok, mas = _ma_aligned(df)
    if not ok:
        return None
    n = int(p.get("high_lookback_days", 120))
    w = df.tail(n)
    hi = float(w["high"].max())
    close = float(df["close"].iloc[-1])
    drop = (1 - close / hi) * 100 if hi > 0 else 0
    if drop < float(p.get("drop_from_high_pct", 30)):
        return None
    lines = [
        f"20>60>120>240일선 정배열 유지",
        f"{n}일 고점 {hi:,.0f}원 대비 -{drop:.0f}% 눌림",
    ]
    return _base(df, row, "aligned_pullback", "정배열 깊은눌림", drop, lines)


# 6. 일봉 거래폭발 ───────────────────────────────────────────
def run_daily_burst(df: pd.DataFrame, row, p: dict, ref_date: dt.date) -> Signal | None:
    years = float(p.get("lookback_years", 3))
    cutoff = pd.Timestamp(ref_date) - pd.Timedelta(days=int(365 * years))
    hist = df[df["date"] >= cutoff]
    if len(hist) < 120:
        return None
    base = volume_baseline(hist["volume"].values[:-1], p)
    if base <= 0:
        return None
    vol = float(df["volume"].iloc[-1])
    mult = vol / base
    if mult < float(p.get("multiple", 6.0)):
        return None
    chg = last_change_pct(df)
    candle = "양봉" if float(df["close"].iloc[-1]) > float(df["open"].iloc[-1]) else "음봉"
    band = f"상위 {p.get('top_pct'):.0f}%" if p.get("top_pct") else "중간구간"
    lines = [
        f"거래량 {vol:,.0f}주 = {years:.0f}년 일봉 {band} 평균 {base:,.0f}주의 {mult:.1f}배",
        f"{candle} ({chg:+.1f}%)",
    ]
    return _base(df, row, "daily_burst", "일봉 거래폭발", mult, lines)


SCREENERS: list[tuple[str, str, object]] = [
    ("bottom_accum", "바닥 매집", run_bottom_accum),
    ("crash_volume", "급락 수급", run_crash_volume),
    ("surge_pullback", "급등 눌림", run_surge_pullback),
    ("aligned_breakout", "정배열 돌파", run_aligned_breakout),
    ("aligned_pullback", "정배열 깊은눌림", run_aligned_pullback),
    ("daily_burst", "일봉 거래폭발", run_daily_burst),
]
