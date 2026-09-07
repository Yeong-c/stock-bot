"""아침 뉴스 정리: 금리·환율·미국증시·지정학·업종 이슈를 웹검색으로 정리해 시장 영향까지."""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Callable

from ..config import DATA_DIR
from ..data import naver_news
from . import get_client, model_of
from .llm import ask_json

log = logging.getLogger("stockbot.ai.news")
NEWS_DIR = DATA_DIR / "news"

SCHEMA = {
    "type": "object",
    "properties": {
        "mood": {"type": "string", "description": "오늘 시장 분위기 한 단어: 긍정/부정/중립/혼조"},
        "one_liner": {"type": "string", "description": "오늘 시장을 한 문장으로"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "분류: 미국증시/금리/환율/유가·원자재/지정학/반도체·AI/국내정책/기타"},
                    "headline": {"type": "string", "description": "핵심 사실 한 줄"},
                    "detail": {"type": "string", "description": "쉬운 설명 1~2문장"},
                    "impact": {"type": "string", "description": "한국 증시 영향: 호재/악재/중립"},
                    "affected": {"type": "array", "items": {"type": "string"}, "description": "영향 받는 업종·종목 최대 3개"},
                },
                "required": ["topic", "headline", "detail", "impact", "affected"],
                "additionalProperties": False,
            },
        },
        "today_watch": {"type": "array", "items": {"type": "string"}, "description": "오늘 확인할 일정·지표 최대 4개"},
    },
    "required": ["mood", "one_liner", "items", "today_watch"],
    "additionalProperties": False,
}

SYSTEM = (
    "당신은 한국 개인투자자를 위한 아침 시황 브리핑 작성자입니다. 60대가 읽기 쉽게 짧고 쉬운 한국어로 씁니다. "
    "웹검색으로 지난 밤~오늘 아침의 최신 사실을 확인하고, 각 항목마다 한국 증시에 호재인지 악재인지 명확히 표시합니다. "
    "추측·루머는 제외하고, 숫자(지수 등락률, 환율, 금리)는 확인된 값만 씁니다. 6~9개 항목으로 정리합니다."
)


def run_news_brief(cfg: dict, progress: Callable[[str], None] | None = None) -> dict:
    def say(m):
        log.info(m)
        if progress:
            progress(m)

    client = get_client(cfg)
    if client is None:
        raise RuntimeError("AI 기능을 쓰려면 config.yaml 의 ai.api_key 에 Anthropic API 키를 넣어주세요")
    today = dt.datetime.now()
    say("주요 뉴스 제목 수집 중…")
    heads = naver_news.fetch_main_news(25)
    head_txt = "\n".join(f"- {h['title']} ({h['source']})" for h in heads) or "- (없음)"
    user = (
        f"오늘은 {today:%Y년 %m월 %d일 (%a)} 입니다. 한국 주식시장 개장 전 아침 브리핑을 만들어 주세요.\n\n"
        "반드시 다룰 것: ① 간밤 미국 증시(다우·나스닥·S&P·반도체지수) 등락과 이유 ② 미국 금리·연준 발언 ③ 달러/원 환율 ④ 유가·원자재 "
        "⑤ 지정학·무역(관세) ⑥ 반도체·AI 등 주도 업종 뉴스 ⑦ 국내 정책·수급 이슈 ⑧ 오늘 예정 일정(지표 발표, 실적 등).\n"
        "웹검색으로 최신 수치를 확인하세요.\n\n[참고: 네이버 금융 주요뉴스 제목]\n" + head_txt
    )
    say("AI 가 뉴스 정리 중… (1~3분)")
    res = ask_json(client, cfg, SYSTEM, user, SCHEMA, web_search=True, max_uses=8,
                   effort=str(cfg.get("ai", {}).get("effort", "medium")), max_tokens=10000)
    res["date"] = today.strftime("%Y-%m-%d")
    res["generated_at"] = today.strftime("%Y-%m-%d %H:%M")
    res["model"] = model_of(cfg)
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    with open(NEWS_DIR / f"{res['date']}.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open(NEWS_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    say("뉴스 정리 완료")
    return res


def load_latest_news() -> dict | None:
    p = NEWS_DIR / "latest.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


_ICON = {"호재": "🟢", "악재": "🔴", "중립": "⚪"}


def format_news(res: dict) -> str:
    lines = [f"📰 오늘의 시장 뉴스 정리 ({res.get('date')} {res.get('generated_at', '')[-5:]})",
             f"분위기: {res.get('mood', '')} — {res.get('one_liner', '')}"]
    for it in res.get("items", []):
        icon = _ICON.get(it.get("impact", ""), "⚪")
        lines.append("")
        lines.append(f"{icon} [{it.get('topic', '')}] {it.get('headline', '')}")
        lines.append(f"   {it.get('detail', '')}")
        if it.get("affected"):
            lines.append(f"   → 영향: {', '.join(it['affected'][:3])} ({it.get('impact', '')})")
    if res.get("today_watch"):
        lines.append("")
        lines.append("📅 오늘 볼 것: " + " / ".join(res["today_watch"][:4]))
    return "\n".join(lines)
