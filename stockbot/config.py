from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"

DEFAULTS: dict[str, Any] = {
    "telegram": {"bot_token": "", "pair_code": "1234", "allowed_chat_ids": []},
    "kiwoom": {"app_key": "", "app_secret": "", "is_mock": False},
    "schedule": {
        "timezone": "Asia/Seoul",
        "market_open": "09:00",
        "market_close": "15:30",
        "realtime_poll_seconds": 60,
        "premarket_prepare_time": "08:30",
        "daily_scan_time": "15:45",
        "holidays": [],
    },
    "filters": {
        "min_marcap_krw": 100_000_000_000,
        "exclude_hwangi": True,
        "exclude_admin": True,
        "exclude_risk": True,
        "exclude_halt": True,
        "exclude_spac": True,
        "exclude_preferred": True,
        "markets": ["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"],
    },
    "output": {"max_per_screener": 20, "repeat_days": 20},
    "screeners": {
        "minute_burst": {
            "enabled": True, "multiple": 6.0, "top_pct": 60, "lookback_days": 365,
            "cooldown_minutes": 10, "ignore_first_minutes": 5,
        },
        "bottom_accum": {
            "enabled": True, "min_decline_years": 2, "drop_pct": 60, "months": 2,
            "include_current_month": True,
        },
        "crash_volume": {
            "enabled": True, "lookback_days": 30, "drop_pct": 50, "vol_multiple": 2.5, "vol_avg_days": 3,
        },
        "surge_pullback": {"enabled": True, "lookback_days": 20, "surge_pct": 27, "pullback_pct": 30},
        "aligned_breakout": {
            "enabled": True, "vol_multiple": 3.0, "vol_avg_days": 3, "min_change_pct": 4, "max_change_pct": 15,
        },
        "aligned_pullback": {"enabled": True, "drop_from_high_pct": 30, "high_lookback_days": 120},
        "daily_burst": {"enabled": True, "multiple": 6.0, "top_pct": 60, "lookback_years": 3},
    },
    "update": {"repo": "", "branch": "main"},
    "watchlist": [],
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


RULES_PATH = ROOT / "screeners.yaml"
RULE_SECTIONS = ("filters", "output", "screeners")


def load_config(path: Path | None = None) -> dict[str, Any]:
    """DEFAULTS ← screeners.yaml(규칙, 업데이트로 갱신) ← config.yaml(개인 설정).
    config.yaml 의 규칙 섹션은 screeners.yaml 이 있으면 무시 (override_rules: true 면 적용)."""
    path = path or CONFIG_PATH
    raw: dict = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    rules: dict = {}
    if RULES_PATH.exists():
        with open(RULES_PATH, encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        if not raw.get("override_rules"):
            raw = {k: v for k, v in raw.items() if k not in RULE_SECTIONS}
    cfg = _merge(_merge(DEFAULTS, rules), raw)
    cfg["watchlist"] = [str(c).zfill(6) for c in (cfg.get("watchlist") or [])]
    cfg["telegram"]["allowed_chat_ids"] = [int(x) for x in (cfg["telegram"].get("allowed_chat_ids") or [])]
    return cfg


def kiwoom_configured(cfg: dict) -> bool:
    k = cfg.get("kiwoom", {})
    return bool(k.get("app_key")) and bool(k.get("app_secret"))


def telegram_configured(cfg: dict) -> bool:
    tok = str(cfg.get("telegram", {}).get("bot_token", ""))
    return ":" in tok and "여기에" not in tok
