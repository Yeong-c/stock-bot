"""AI(Claude API) 정성 평가 — 검색식 교집합 후보에 대한 기업 동향 분석과 아침 뉴스 정리."""
from __future__ import annotations

import os


def api_key(cfg: dict) -> str:
    return str(cfg.get("ai", {}).get("api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")).strip()


def ai_enabled(cfg: dict) -> bool:
    k = api_key(cfg)
    return bool(k) and k.startswith("sk-")


def get_client(cfg: dict):
    """anthropic.Anthropic 클라이언트. 키가 없으면 None."""
    if not ai_enabled(cfg):
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key(cfg), timeout=300.0, max_retries=2)


def model_of(cfg: dict) -> str:
    return str(cfg.get("ai", {}).get("model", "claude-sonnet-5") or "claude-sonnet-5")
