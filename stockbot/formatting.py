"""텔레그램 메시지 문구 (아빠가 읽기 쉽게)."""
from __future__ import annotations

TG_LIMIT = 3800

SCREENER_HELP = {
    "minute_burst": "1️⃣ 분봉 거래폭발 (장중 실시간)\n감시 종목의 1분 거래량이 평소(1년치 1분봉 중간 40% 평균)의 3배 이상 터지면 즉시 알림.",
    "bottom_accum": "2️⃣ 바닥 매집\n고점이 2년 이상 전이고 고점 대비 60% 이상 빠진 종목이 월봉 2달 연속 양봉.",
    "crash_volume": "3️⃣ 급락 수급\n30일 내 고점 대비 50% 이상 빠진 종목에 3일 평균의 2.5배 거래량.",
    "surge_pullback": "4️⃣ 급등 눌림\n20일 내 27% 이상 급등했던 종목이 고점 대비 30% 이상 눌림.",
    "aligned_breakout": "5️⃣ 정배열 돌파\n20·60·120·240일선 정배열 + 3일 평균의 3배 거래량 + 양봉 +4~15%.",
    "aligned_pullback": "5️⃣-b 정배열 깊은눌림\n정배열 유지 중 120일 고점 대비 30% 이상 눌린 종목.",
    "daily_burst": "6️⃣ 일봉 거래폭발\n3년치 일봉 거래량 중간 40% 평균의 3배 이상 거래량.",
}

COMMON_FILTER_NOTE = "공통: 시총 1,000억 미만·환기종목·스팩 제외. 신호일 뿐 매매 판단은 직접."


def fmt_won(v: float) -> str:
    return f"{v:,.0f}원"


def fmt_pct(v: float) -> str:
    return f"{v:+.1f}%"


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n\n"):
        cand = para if not cur else cur + "\n\n" + para
        if len(cand) > limit and cur:
            parts.append(cur)
            cur = para
        else:
            cur = cand
    if cur:
        parts.append(cur)
    return parts


def format_signal(s: dict, idx: int) -> str:
    head = f"{idx}. {s['name']} ({s['code']}) {fmt_won(s['price'])} {fmt_pct(s['change_pct'])}"
    body = "\n".join("   └ " + ln for ln in s.get("lines", []))
    return head + ("\n" + body if body else "")


SCREENER_ORDER = [
    ("bottom_accum", "2️⃣ 바닥 매집"),
    ("crash_volume", "3️⃣ 급락 수급"),
    ("surge_pullback", "4️⃣ 급등 눌림"),
    ("aligned_breakout", "5️⃣ 정배열 돌파"),
    ("aligned_pullback", "5️⃣-b 정배열 깊은눌림"),
    ("daily_burst", "6️⃣ 일봉 거래폭발"),
]


def screener_label(key: str) -> str:
    return dict(SCREENER_ORDER).get(key, key)


def format_one_screener(out: dict, key: str, max_per: int = 30, note: str = "") -> list[str]:
    """검색식 하나의 결과만 메시지로."""
    r = out.get("results", {}).get(key)
    date = out.get("date", "")
    if r is None:
        return [f"{screener_label(key)} 은 꺼져 있거나 결과가 없습니다."]
    head = f"🔎 [{screener_label(key)}] {date} 기준 · {r['count']}종목"
    if note:
        head += "\n" + note
    sigs = r.get("signals", [])
    if not sigs:
        return [head + "\n해당 종목 없음\n\n" + COMMON_FILTER_NOTE]
    lines = [head]
    for i, sg in enumerate(sigs[:max_per], 1):
        lines.append(format_signal(sg, i))
    if r["count"] > max_per:
        lines.append(f"… 외 {r['count'] - max_per}종목 (상위 {max_per}개만 표시)")
    lines.append(COMMON_FILTER_NOTE)
    return split_message("\n\n".join(lines))


def format_daily_results(out: dict, max_per: int = 30, header_note: str = "") -> list[str]:
    date = out.get("date", "")
    u = out.get("universe", {})
    msgs: list[str] = []
    intro = f"📋 장 마감 검색 결과 ({date} 기준)\n대상 {u.get('n_universe', '?')}종목"
    if not u.get("hwangi_ok", False):
        intro += "\n⚠️ 환기종목 목록을 오늘 못 받아 이전 목록으로 제외했습니다."
    if header_note:
        intro += "\n" + header_note
    empty: list[str] = []
    for key, r in out.get("results", {}).items():
        sigs = r.get("signals", [])
        if not sigs:
            empty.append(r["title"])
            continue
        lines = [f"🔎 [{r['title']}] {r['count']}종목"]
        for i, s in enumerate(sigs[:max_per], 1):
            lines.append(format_signal(s, i))
        if r["count"] > max_per:
            lines.append(f"… 외 {r['count'] - max_per}종목 (상위 {max_per}개만 표시)")
        msgs.extend(split_message("\n\n".join(lines)))
    if empty:
        intro += "\n해당 없음: " + ", ".join(empty)
    intro += "\n\n" + COMMON_FILTER_NOTE
    return [intro] + msgs


def format_minute_alert(a: dict) -> str:
    candle = ""
    if a.get("open"):
        d = (a["price"] / a["open"] - 1) * 100
        candle = f" · 시가 대비 {d:+.1f}% ({'양봉' if d > 0 else '음봉' if d < 0 else '보합'})"
    return (
        f"🔥 [분봉 거래폭발] {a['name']} ({a['code']})\n"
        f"⏰ {a['time']}  1분 거래량 {a['minute_volume']:,}주 = 평소 {a['baseline']:,.0f}주의 {a['multiple']}배\n"
        f"💰 현재가 {fmt_won(a['price'])} {fmt_pct(a['change_pct'])}{candle}\n"
        f"(평소 = 1분봉 거래량 중간 40% 평균)"
    )


def format_watchlist(codes: list[str], names: dict[str, str], baselines: dict[str, dict]) -> str:
    if not codes:
        return "🔥 감시 종목이 없습니다.\n'➕ 감시 추가' 버튼을 눌러 종목을 넣어주세요."
    lines = [f"🔥 분봉 거래폭발 감시 종목 ({len(codes)}개)"]
    for i, c in enumerate(codes, 1):
        b = baselines.get(c) or {}
        if b.get("mean"):
            info = f"평소 1분 {b['mean']:,.0f}주"
        else:
            info = "기준값 준비 중"
        lines.append(f"{i}. {names.get(c, c)} ({c}) — {info}")
    return "\n".join(lines)


def help_text() -> str:
    parts = ["❓ 사용법\n아래 버튼을 누르면 됩니다.",
             "📋 오늘 검색결과 — 저장된 결과를 검색식별로 골라 보기 (버튼에 종목 수 표시)",
             "🔥 감시종목 — 분봉 거래폭발을 감시 중인 종목 보기",
             "➕ 감시 추가 / ➖ 감시 삭제 — 종목 이름을 보내면 됩니다 (예: 삼성전자)",
             "🔎 지금 검색 — 검색식을 하나 골라 지금 바로 실행 (10분 안에는 재사용, 아니면 1~3분)",
             "",
             "📌 검색식 설명"]
    parts.extend(SCREENER_HELP.values())
    parts.append(COMMON_FILTER_NOTE)
    return "\n\n".join(parts)
