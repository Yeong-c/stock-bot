"""Claude 호출 공통: 웹검색 도구 + JSON 구조화 출력 + pause_turn 처리. 모델별 지원 차이를 흡수한다."""
from __future__ import annotations

import json
import logging
from typing import Any

from . import model_of

log = logging.getLogger("stockbot.ai")


def _caps(model: str) -> dict:
    """모델별 지원 기능. haiku 4.5 는 구형 웹검색 도구만 되고 effort 를 못 받는다."""
    m = model.lower()
    old = m.startswith("claude-haiku") or "4-5" in m
    return {
        "web_search_type": "web_search_20250305" if old else "web_search_20260209",
        "effort": not old,
        "fallbacks": m.startswith(("claude-opus-5", "claude-fable")),
    }


def ask_json(client, cfg: dict, system: str, user: str, schema: dict, *, web_search: bool = True,
             max_uses: int = 4, effort: str = "medium", max_tokens: int = 8000) -> dict[str, Any]:
    """웹검색(선택)을 곁들여 질문하고 JSON(schema) 으로 답을 받는다."""
    model = model_of(cfg)
    caps = _caps(model)
    tools = []
    if web_search and cfg.get("ai", {}).get("web_search", True):
        tools.append({"type": caps["web_search_type"], "name": "web_search", "max_uses": max_uses,
                      "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"}})
    messages: list[dict] = [{"role": "user", "content": user}]
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
    if caps["effort"]:
        output_config["effort"] = effort
    kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=system, messages=messages,
                                  output_config=output_config)
    if caps["fallbacks"]:
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    if tools:
        kwargs["tools"] = tools
    import anthropic

    def _run(kw: dict) -> str:
        out = ""
        for _ in range(6):  # pause_turn 재개 최대 5회
            resp = client.beta.messages.create(**kw)
            if resp.stop_reason == "refusal":
                raise RuntimeError("모델이 답변을 거부했습니다")
            if resp.stop_reason == "pause_turn":
                # 서버 도구 반복 한도 → 지금까지의 assistant 턴을 그대로 붙여 이어가기
                content = [b.model_dump(mode="json", exclude_none=True) for b in resp.content]
                kw["messages"] = messages + [{"role": "assistant", "content": content}]
                continue
            texts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
            out = texts[-1] if texts else ""
            break
        return out

    text = ""
    try:
        text = _run(dict(kwargs))
    except anthropic.BadRequestError as e:
        # 간헐적 'Invalid request data' → 검색 횟수를 줄여 한 번 더, 그래도 안 되면 검색 없이
        log.warning("요청 오류(%s) → 검색 축소 후 재시도", str(e)[:120])
        if tools:
            tools[0]["max_uses"] = min(3, max_uses)
            try:
                text = _run(dict(kwargs))
            except anthropic.BadRequestError:
                kwargs.pop("tools", None)
                text = _run(dict(kwargs))
        else:
            raise
    if not text:
        raise RuntimeError("모델 응답이 비어 있습니다")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise
