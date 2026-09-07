"""AI 추천: 검색식 교집합(정량) 상위 후보 → 기업 동향·공시 정성평가 → 종합 상위 N."""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Callable

from ..config import DATA_DIR
from ..data import naver_news
from ..screeners.runner import load_latest_result
from . import get_client, model_of
from .llm import ask_json

log = logging.getLogger("stockbot.ai.recommend")
AI_DIR = DATA_DIR / "ai"
STOCK_DIR = AI_DIR / "stocks"   # 종목별 조사 이력: stocks/{code}.jsonl (한 줄 = 한 번의 분석)


def load_history(code: str, n: int = 5) -> list[dict]:
    p = STOCK_DIR / f"{code}.jsonl"
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
    return rows[-n:]


def save_history(code: str, rec: dict) -> None:
    STOCK_DIR.mkdir(parents=True, exist_ok=True)
    with open(STOCK_DIR / f"{code}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def history_text(hist: list[dict]) -> str:
    if not hist:
        return "- (이전 분석 없음)"
    return "\n".join(f"- {h.get('date')} 점수 {h.get('score')}/10, {h.get('price', 0):,.0f}원: {h.get('summary', '')}"
                     + (f" / 위험: {'; '.join(h.get('risks', [])[:2])}" if h.get('risks') else "") for h in hist)

WEIGHTS = {"bottom_accum": 1.0, "crash_volume": 1.0, "surge_pullback": 1.0,
           "aligned_breakout": 1.2, "aligned_pullback": 0.8, "daily_burst": 1.0}

STOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "정성 점수 0~10 (10=매우 긍정)"},
        "summary": {"type": "string", "description": "한 줄 총평 (40자 이내)"},
        "positives": {"type": "array", "items": {"type": "string"}, "description": "긍정 요인 최대 3개"},
        "risks": {"type": "array", "items": {"type": "string"}, "description": "위험 요인 최대 3개"},
        "recent": {"type": "array", "items": {"type": "string"}, "description": "최근 2주 주요 뉴스·공시 요약 최대 3개"},
        "change": {"type": "string", "description": "이전 분석과 비교해 달라진 점 한 줄. 이전 분석이 없으면 빈 문자열"},
    },
    "required": ["score", "summary", "positives", "risks", "recent", "change"],
    "additionalProperties": False,
}

SYSTEM = (
    "당신은 한국 주식 리서치 애널리스트입니다. 60대 개인투자자가 읽기 쉽게 짧고 쉬운 한국어로 씁니다. "
    "주어진 정량 신호(검색식 결과)와 최근 뉴스·공시, 필요하면 웹검색 결과를 근거로 기업의 최근 동향을 평가합니다. "
    "확인되지 않은 사실은 쓰지 말고, 근거가 약하면 점수를 낮추세요. 매수/매도 지시는 하지 말고 판단 재료만 제공합니다. "
    "모든 문장은 반드시 한국어로 씁니다."
)


def quant_candidates(out: dict, minute_alerts: list[dict] | None = None, n: int = 20) -> list[dict]:
    """검색식 결과의 교집합 점수로 후보 선정."""
    table: dict[str, dict] = {}
    for key, r in out.get("results", {}).items():
        w = WEIGHTS.get(key, 1.0)
        sigs = r.get("signals", [])
        for rank, s in enumerate(sigs, 1):
            bonus = 1.0 if rank <= 3 else 0.5 if rank <= 10 else 0.0
            c = table.setdefault(s["code"], {"code": s["code"], "name": s["name"], "market": s.get("market", ""),
                                             "price": s["price"], "change_pct": s["change_pct"],
                                             "quant": 0.0, "hits": [], "lines": []})
            c["quant"] += w * (1.0 + bonus)
            c["hits"].append(r["title"])
            c["lines"] += [f"[{r['title']}] {ln}" for ln in s.get("lines", [])[:2]]
    for a in minute_alerts or []:
        c = table.get(a["code"])
        if c is not None:
            c["quant"] += 1.0
            if "분봉 거래폭발" not in c["hits"]:
                c["hits"].append("분봉 거래폭발")
    cands = sorted(table.values(), key=lambda c: (len(c["hits"]), c["quant"]), reverse=True)
    return cands[:n]


def analyze_one(client, cfg: dict, c: dict, date: str | None = None) -> dict:
    """한 종목 정성 분석. 이전 분석 이력을 참고시키고, 결과를 종목별 이력 파일에 누적한다."""
    news = naver_news.fetch_stock_news(c["code"], 12)
    disc = naver_news.fetch_disclosures(c["code"], 8)
    hist = load_history(c["code"], 4)
    news_txt = "\n".join(f"- {x['date']} {x['title']} ({x['source']})" for x in news) or "- (없음)"
    disc_txt = "\n".join(f"- {x['date']} {x['title']}" for x in disc) or "- (없음)"
    sig_txt = "\n".join(f"- {ln}" for ln in c.get("lines", [])) or "- (없음)"
    hits = c.get("hits", [])
    user = (
        f"종목: {c['name']} ({c['code']}, {c.get('market', '')})\n현재가 {c['price']:,.0f}원, 전일대비 {c.get('change_pct', 0):+.1f}%\n\n"
        f"[정량 신호 — 오늘 걸린 검색식 {len(hits)}개: {', '.join(hits) or '없음'}]\n{sig_txt}\n\n"
        f"[이 종목에 대한 이전 AI 분석 기록]\n{history_text(hist)}\n\n"
        f"[최근 뉴스 제목]\n{news_txt}\n\n[최근 공시 제목]\n{disc_txt}\n\n"
        "위 자료와 (가능하면) 웹검색으로 확인한 최근 2주 동향을 바탕으로 이 기업의 정성 점수(0~10), 한 줄 총평, "
        "긍정 요인, 위험 요인, 최근 주요 사건, 그리고 이전 분석 대비 달라진 점을 JSON 으로 주세요. "
        "실적·수주·규제·소송·유상증자·거래정지 같은 사실을 우선 확인하고, 이전 분석에서 지적한 위험이 해소됐는지도 보세요."
    )
    try:
        res = ask_json(client, cfg, SYSTEM, user, STOCK_SCHEMA, web_search=True, max_uses=3,
                       effort=str(cfg.get("ai", {}).get("effort", "medium")))
        res["score"] = max(0, min(10, int(res.get("score", 0))))
        res["ok"] = True
        save_history(c["code"], {
            "date": date or dt.date.today().isoformat(), "name": c["name"], "price": c["price"],
            "change_pct": c.get("change_pct", 0), "hits": hits, "score": res["score"], "summary": res.get("summary", ""),
            "positives": res.get("positives", []), "risks": res.get("risks", []), "recent": res.get("recent", []),
            "change": res.get("change", ""), "model": model_of(cfg),
        })
    except Exception as e:  # noqa: BLE001
        log.warning("AI 분석 실패 %s: %s", c["code"], e)
        res = {"score": 0, "summary": f"분석 실패: {str(e)[:60]}", "positives": [], "risks": [], "recent": [],
               "change": "", "ok": False}
    res["history"] = hist
    return res


def analyze_stock(cfg: dict, code: str, name: str, price: float, change_pct: float = 0.0) -> dict:
    """요청 시 단일 종목 분석 (텔레그램 '삼성전자 분석', 데스크톱 '종목 분석'). 오늘 검색식 신호도 같이 넣는다."""
    client = get_client(cfg)
    if client is None:
        raise RuntimeError("AI 기능을 쓰려면 config.yaml 의 ai.api_key 에 Anthropic API 키를 넣어주세요")
    out = load_latest_result() or {}
    hits, lines = [], []
    for key, r in out.get("results", {}).items():
        for s in r.get("signals", []):
            if s["code"] == code:
                hits.append(r["title"])
                lines += [f"[{r['title']}] {ln}" for ln in s.get("lines", [])[:2]]
    c = {"code": code, "name": name, "market": "", "price": price, "change_pct": change_pct, "hits": hits, "lines": lines}
    res = analyze_one(client, cfg, c)
    return {"code": code, "name": name, "price": price, "change_pct": change_pct, "hits": hits, "ai": res}


def format_stock_analysis(r: dict) -> str:
    ai = r["ai"]
    lines = [f"🔍 {r['name']} ({r['code']}) AI 분석 — {r['price']:,.0f}원 {r.get('change_pct', 0):+.1f}%",
             f"정성 점수 {ai.get('score', 0)}/10 · 오늘 검색식 {len(r.get('hits', []))}개" + (f": {', '.join(r['hits'])}" if r.get('hits') else ""),
             f"💬 {ai.get('summary', '')}"]
    for p in ai.get("positives", [])[:3]:
        lines.append(f"＋ {p}")
    for x in ai.get("risks", [])[:3]:
        lines.append(f"－ {x}")
    for x in ai.get("recent", [])[:3]:
        lines.append(f"· {x}")
    if ai.get("change"):
        lines.append(f"↔ 이전 대비: {ai['change']}")
    hist = ai.get("history", [])
    if hist:
        lines.append("")
        lines.append("📚 이전 분석 기록")
        for h in hist[-4:]:
            lines.append(f"  {h.get('date')} {h.get('score')}/10 {h.get('price', 0):,.0f}원 — {h.get('summary', '')}")
    lines.append("")
    lines.append("AI 의견은 참고용이며 매매 판단은 직접 하세요.")
    return "\n".join(lines)


def run_recommend(cfg: dict, out: dict | None = None, minute_alerts: list[dict] | None = None,
                  progress: Callable[[str], None] | None = None) -> dict:
    def say(m):
        log.info(m)
        if progress:
            progress(m)

    client = get_client(cfg)
    if client is None:
        raise RuntimeError("AI 기능을 쓰려면 config.yaml 의 ai.api_key 에 Anthropic API 키를 넣어주세요")
    out = out or load_latest_result()
    if not out:
        raise RuntimeError("먼저 검색식을 실행해 주세요 (검색 결과 없음)")
    a = cfg.get("ai", {})
    cands = quant_candidates(out, minute_alerts, int(a.get("candidates", 20)))
    if not cands:
        raise RuntimeError("검색식에 걸린 종목이 없어 후보가 없습니다")
    say(f"AI 분석 시작: 후보 {len(cands)}종목 (검색식 교집합 상위)")
    done = 0
    with cf.ThreadPoolExecutor(max_workers=int(a.get("workers", 3))) as ex:
        futs = {ex.submit(analyze_one, client, cfg, c, out.get("date")): c for c in cands}
        for f in cf.as_completed(futs):
            c = futs[f]
            c["ai"] = f.result()
            done += 1
            say(f"AI 분석 {done}/{len(cands)}: {c['name']} {c['ai'].get('score', 0)}점")
    qmax = max(c["quant"] for c in cands) or 1.0
    for c in cands:
        c["final"] = round(50 * c["quant"] / qmax + 5 * c["ai"]["score"], 1)  # 정량 50 + 정성 50
    cands.sort(key=lambda c: c["final"], reverse=True)
    top = cands[:int(a.get("top_n", 10))]
    res = {"date": out.get("date"), "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "model": model_of(cfg), "n_candidates": len(cands), "items": top}
    AI_DIR.mkdir(parents=True, exist_ok=True)
    with open(AI_DIR / f"{res['date']}.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open(AI_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    say("AI 분석 완료")
    return res


def load_latest_recommend() -> dict | None:
    p = AI_DIR / "latest.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def format_recommend(res: dict, limit: int | None = None) -> str:
    items = res.get("items", [])
    if limit:
        items = items[:limit]
    lines = [f"🤖 AI 추천 {len(items)}종목 ({res.get('date')} 기준, 후보 {res.get('n_candidates')}종목 검토)",
             "정량(검색식 교집합) 50점 + 정성(동향·공시) 50점"]
    for i, c in enumerate(items, 1):
        ai = c.get("ai", {})
        lines.append("")
        lines.append(f"{i}. {c['name']} ({c['code']}) {c['price']:,.0f}원 {c['change_pct']:+.1f}%  종합 {c['final']}점")
        lines.append(f"   검색식 {len(c['hits'])}개: {', '.join(c['hits'])} · AI {ai.get('score', 0)}/10")
        lines.append(f"   💬 {ai.get('summary', '')}")
        for p in ai.get("positives", [])[:2]:
            lines.append(f"   ＋ {p}")
        for r in ai.get("risks", [])[:2]:
            lines.append(f"   － {r}")
        if ai.get("change"):
            lines.append(f"   ↔ 이전 대비: {ai['change']}")
    lines.append("")
    lines.append("AI 의견은 참고용이며 매매 판단은 직접 하세요.")
    return "\n".join(lines)
