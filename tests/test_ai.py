"""AI 모듈: 정량 교집합 점수·포맷은 네트워크 없이 검증. (Claude 호출은 키가 있어야 하므로 제외)"""
from stockbot.ai.recommend import format_recommend, quant_candidates
from stockbot.ai.news import format_news


def _sig(code, name, price=1000.0):
    return {"code": code, "name": name, "market": "KOSPI", "price": price, "change_pct": 1.0, "lines": ["근거"]}


def test_quant_candidates_prefers_intersection():
    out = {"results": {
        "bottom_accum": {"title": "바닥 매집", "signals": [_sig("A", "에이"), _sig("B", "비")]},
        "daily_burst": {"title": "일봉 거래폭발", "signals": [_sig("C", "씨"), _sig("A", "에이")]},
        "aligned_breakout": {"title": "정배열 돌파", "signals": [_sig("A", "에이")]},
    }}
    c = quant_candidates(out, [{"code": "B"}], 10)
    assert c[0]["code"] == "A" and len(c[0]["hits"]) == 3
    assert c[1]["code"] == "B" and "분봉 거래폭발" in c[1]["hits"]
    assert all(x["quant"] > 0 for x in c)


def test_format_functions():
    res = {"date": "2026-09-07", "n_candidates": 3, "items": [{
        "code": "A", "name": "에이", "price": 1000.0, "change_pct": 2.0, "final": 88.5, "hits": ["바닥 매집"],
        "ai": {"score": 8, "summary": "좋음", "positives": ["수주"], "risks": ["환율"], "recent": []}}]}
    txt = format_recommend(res)
    assert "에이" in txt and "88.5점" in txt and "＋ 수주" in txt
    news = {"date": "2026-09-07", "generated_at": "2026-09-07 08:20", "mood": "긍정", "one_liner": "상승 출발 기대",
            "items": [{"topic": "미국증시", "headline": "나스닥 +1%", "detail": "반도체 강세", "impact": "호재", "affected": ["반도체"]}],
            "today_watch": ["CPI 발표"]}
    t = format_news(news)
    assert "🟢" in t and "나스닥" in t and "CPI" in t
