"""네이버 7일치 분봉이 날마다 파일에 누적되는지."""
import pandas as pd

from stockbot.screeners import minute as M
from stockbot.state import State


def test_naver_minute_bars_accumulate(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "load_minute_history", lambda code: pd.DataFrame({
        "ts": pd.to_datetime(["2026-08-01 09:00", "2026-08-01 09:01"]), "close": [1.0, 1.0], "volume": [100.0, 200.0]}))
    saved = {}
    monkeypatch.setattr(M, "save_minute_history", lambda code, df, keep_days=400: saved.setdefault("df", df))
    monkeypatch.setattr(M.naver, "fetch_minute_bars", lambda code: pd.DataFrame({
        "ts": pd.to_datetime(["2026-08-01 09:01", "2026-09-01 09:00"]), "close": [1.0, 1.0], "volume": [250.0, 300.0]}))
    cfg = {"schedule": {}, "screeners": {"minute_burst": {"lookback_days": 365, "top_pct": 100}}}
    st = State(tmp_path / "s.json")
    info = M.MinuteMonitor(cfg, st, None).build_baseline("000001", force=True)
    assert len(saved["df"]) == 3                     # 병합: 중복 1개 제거
    assert info["source"] == "네이버 누적" and info["days"] == 2
    assert abs(info["mean"] - (100 + 250 + 300) / 3) < 0.1   # 중복은 최신 값(250) 사용
