"""Claude 호출 공통: 웹검색 도구 + JSON 구조화 출력 + pause_turn 처리."""
from __future__ import annotations

import json
import logging
from typing import Any

from . import model_of

log = logging.getLogger("stockbot.ai")

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}


def ask_json(client, cfg: dict, system: str, user: str, schema: dict, *, web_search: bool = True,
             max_uses: int = 4, effort: str = "medium", max_tokens: int = 8000) -> dict[str, Any]:
    """웹검색(선택)을 곁들여 질문하고 JSON(schema) 으로 답을 받는다."""
    tools = []
    if web_search and cfg.get("ai", {}).get("web_search", True):
        tools.append({**WEB_SEARCH_TOOL, "max_uses": max_uses, "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"}})
    messages: list[dict] = [{"role": "user", "content": user}]
    kwargs: dict[str, Any] = dict(
        model=model_of(cfg), max_tokens=max_tokens, system=system, messages=messages,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )
    if tools:
        kwargs["tools"] = tools
    text = ""
    for _ in range(6):  # pause_turn 재개 최대 5회
        resp = client.beta.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            raise RuntimeError("모델이 답변을 거부했습니다")
        if resp.stop_reason == "pause_turn":
            kwargs["messages"] = messages + [{"role": "assistant", "content": resp.content}]
            continue
        texts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        text = texts[-1] if texts else ""
        break
    if not text:
        raise RuntimeError("모델 응답이 비어 있습니다")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise
