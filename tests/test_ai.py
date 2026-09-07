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


def test_history_roundtrip(tmp_path, monkeypatch):
    from stockbot.ai import recommend as R
    monkeypatch.setattr(R, "STOCK_DIR", tmp_path / "stocks")
    assert R.load_history("000001") == []
    R.save_history("000001", {"date": "2026-09-01", "score": 5, "summary": "보통", "price": 1000, "risks": ["환율"]})
    R.save_history("000001", {"date": "2026-09-07", "score": 8, "summary": "개선", "price": 1200, "risks": []})
    h = R.load_history("000001", 5)
    assert [x["score"] for x in h] == [5, 8]
    txt = R.history_text(h)
    assert "2026-09-01" in txt and "환율" in txt
    r = {"code": "000001", "name": "테스트", "price": 1200.0, "change_pct": 1.5, "hits": ["바닥 매집"],
         "ai": {"score": 8, "summary": "개선", "positives": ["a"], "risks": [], "recent": [], "change": "위험 해소", "history": h}}
    out = R.format_stock_analysis(r)
    assert "이전 대비: 위험 해소" in out and "이전 분석 기록" in out


def test_model_caps():
    from stockbot.ai.llm import _caps
    assert _caps("claude-haiku-4-5")["web_search_type"] == "web_search_20250305"
    assert _caps("claude-haiku-4-5")["effort"] is False
    assert _caps("claude-sonnet-5")["web_search_type"] == "web_search_20260209" and _caps("claude-sonnet-5")["fallbacks"] is False
    assert _caps("claude-opus-5")["fallbacks"] is True
