"""합성 데이터로 검색식 로직 검증. 실행: python -m pytest tests -q"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import numpy as np
import pandas as pd

from stockbot.config import DEFAULTS
from stockbot.screeners import daily as D
from stockbot.screeners.common import trimmed_mean

ROW = SimpleNamespace(code="000001", name="테스트", market="KOSPI", marcap=5e11)


def make_df(closes, volumes=None, start="2021-01-04"):
    n = len(closes)
    dates = pd.bdate_range(start, periods=n)
    closes = np.asarray(closes, dtype=float)
    opens = np.roll(closes, 1); opens[0] = closes[0]
    highs = np.maximum(opens, closes) * 1.01
    lows = np.minimum(opens, closes) * 0.99
    vols = np.asarray(volumes if volumes is not None else np.full(n, 100_000), dtype=float)
    return pd.DataFrame({"date": dates, "open": opens, "high": highs, "low": lows, "close": closes, "volume": vols})


def test_top_mean():
    from stockbot.screeners.common import top_mean, volume_baseline
    v = np.arange(1, 11)  # 1..10 → 상위 60% = 10,9,8,7,6,5 평균 7.5
    assert abs(top_mean(v, 60) - 7.5) < 1e-9
    assert abs(volume_baseline(v, {"top_pct": 60}) - 7.5) < 1e-9
    assert abs(volume_baseline(v, {"trim_pct": 30}) - trimmed_mean(v, 30)) < 1e-9


def test_trimmed_mean_middle_band():
    v = np.arange(1, 101)  # 1..100
    m = trimmed_mean(v, 30)  # 31..70 평균 = 50.5
    assert abs(m - 50.5) < 1e-9
    assert trimmed_mean([], 30) == 0.0


def test_bottom_accum_hits_after_two_green_months():
    # 3년 전 고점 10,000 → 서서히 하락해 3,000 (−70%), 마지막 두 달 양봉
    n = 780
    closes = np.linspace(10_000, 3_000, n)
    closes[-44:] = np.linspace(3_000, 3_600, 44)  # 최근 2달 상승
    df = make_df(closes)
    ref = df["date"].iloc[-1].date()
    p = DEFAULTS["screeners"]["bottom_accum"]
    s = D.run_bottom_accum(df, ROW, p, ref)
    assert s is not None and s.key == "bottom_accum"
    assert "양봉" in s.lines[1]


def test_bottom_accum_rejects_recent_peak():
    n = 780
    closes = np.linspace(3_000, 10_000, n)  # 고점이 최근 → 탈락
    df = make_df(closes)
    assert D.run_bottom_accum(df, ROW, DEFAULTS["screeners"]["bottom_accum"], df["date"].iloc[-1].date()) is None


def test_crash_volume():
    closes = np.full(60, 10_000.0)
    closes[-25:] = 4_500  # 30일 내 고점 대비 −55%
    vols = np.full(60, 100_000.0); vols[-1] = 300_000  # 3배
    df = make_df(closes, vols)
    s = D.run_crash_volume(df, ROW, DEFAULTS["screeners"]["crash_volume"], df["date"].iloc[-1].date())
    assert s is not None and s.score >= 2.5
    vols[-1] = 150_000
    df = make_df(closes, vols)
    assert D.run_crash_volume(df, ROW, DEFAULTS["screeners"]["crash_volume"], df["date"].iloc[-1].date()) is None


def test_surge_pullback():
    closes = np.full(40, 1_000.0)
    closes[-15:-8] = np.linspace(1_000, 1_400, 7)  # +40% 급등
    closes[-8:] = 900  # 고점 대비 −36%
    df = make_df(closes)
    s = D.run_surge_pullback(df, ROW, DEFAULTS["screeners"]["surge_pullback"], df["date"].iloc[-1].date())
    assert s is not None and "급등" in s.lines[0]


def test_aligned_breakout_and_pullback():
    n = 300
    closes = np.linspace(1_000, 3_000, n)  # 꾸준한 상승 → 정배열
    closes[-1] = closes[-2] * 1.06  # +6% 양봉
    vols = np.full(n, 100_000.0); vols[-1] = 400_000
    df = make_df(closes, vols)
    ref = df["date"].iloc[-1].date()
    s = D.run_aligned_breakout(df, ROW, DEFAULTS["screeners"]["aligned_breakout"], ref)
    assert s is not None and s.key == "aligned_breakout"
    # 눌림 버전: 정배열 유지하며 120일 고점 대비 −30% 는 아니므로 None
    assert D.run_aligned_pullback(df, ROW, DEFAULTS["screeners"]["aligned_pullback"], ref) is None


def test_daily_burst():
    n = 800
    vols = np.random.default_rng(0).integers(80_000, 120_000, n).astype(float)
    vols[-1] = 900_000  # 상위 60% 평균(≈11만)의 6배 이상
    df = make_df(np.full(n, 5_000.0), vols)
    s = D.run_daily_burst(df, ROW, DEFAULTS["screeners"]["daily_burst"], df["date"].iloc[-1].date())
    assert s is not None and s.score > 6
    vols[-1] = 400_000  # 6배 미만 → 탈락
    assert D.run_daily_burst(make_df(np.full(n, 5_000.0), vols), ROW, DEFAULTS["screeners"]["daily_burst"], df["date"].iloc[-1].date()) is None


def test_minute_monitor_poll(monkeypatch, tmp_path):
    from stockbot.screeners import minute as M
    from stockbot.state import State

    st = State(tmp_path / "state.json")
    st.set_watchlist(["005930"])
    st.set_baseline("005930", {"mean": 1000.0, "bars": 100, "days": 5, "source": "test", "built": "x"})
    cfg = {"schedule": DEFAULTS["schedule"], "screeners": DEFAULTS["screeners"]}
    mon = M.MinuteMonitor(cfg, st, None)
    seq = iter([100_000, 107_000])  # 1분 7,000주 = 기준의 7배
    monkeypatch.setattr(M.naver, "fetch_realtime", lambda codes: {
        "005930": {"name": "삼성전자", "price": 70000, "change_pct": 1.0, "acc_volume": next(seq),
                   "open": 69000, "high": 0, "low": 0, "prev_close": 0, "status": "OPEN"}})
    t0 = dt.datetime(2026, 9, 3, 10, 0, tzinfo=M.now_kst().tzinfo)
    assert mon.poll(t0) == []
    alerts = mon.poll(t0 + dt.timedelta(minutes=1))
    assert len(alerts) == 1 and alerts[0]["multiple"] == 7.0
    assert st.minute_alerts("2026-09-03")


def test_repeats_and_overlap(tmp_path, monkeypatch):
    import json
    from stockbot.screeners import runner as R
    monkeypatch.setattr(R, "RESULT_DIR", tmp_path)
    sig = lambda code, name: {"code": code, "name": name, "price": 100.0, "change_pct": 1.0, "lines": []}
    past = {"date": "2026-09-01", "results": {"bottom_accum": {"title": "바닥 매집", "signals": [sig("A", "에이")]}}}
    (tmp_path / "2026-09-01.json").write_text(json.dumps(past), encoding="utf-8")
    results = {"bottom_accum": {"title": "바닥 매집", "count": 2, "signals": [sig("A", "에이"), sig("B", "비")]},
               "daily_burst": {"title": "일봉 거래폭발", "count": 1, "signals": [sig("A", "에이")]}}
    R.annotate_repeats(results, dt.date(2026, 9, 7), 20)
    a, b = results["bottom_accum"]["signals"]
    assert a["seen"] == ["2026-09-01"] and b["seen"] == [] and results["bottom_accum"]["count_new"] == 1
    ov = R.overlap_list(results)
    assert len(ov) == 1 and ov[0]["code"] == "A" and ov[0]["titles"] == ["바닥 매집", "일봉 거래폭발"]
