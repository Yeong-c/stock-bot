"""아침 시황 재료 (키 불필요): 간밤 미국 지수, 코스피/코스닥, 환율·유가·금, 네이버 주요뉴스 제목."""
from __future__ import annotations

import html
import json
import logging
import re

import requests

log = logging.getLogger("stockbot.market")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0", "Referer": "https://finance.naver.com/"}
_TAG = re.compile(r"<[^>]+>")

WORLD = [(".IXIC", "나스닥"), (".DJI", "다우"), (".INX", "S&P500")]
DOMESTIC = [("KOSPI", "코스피"), ("KOSDAQ", "코스닥")]
INDEX_KEEP = ["미국 USD", "달러인덱스", "WTI", "국제 금"]


def _num(s) -> float:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def fetch_indices() -> list[dict]:
    """[{name, price, change, change_pct, time}] 미국 3지수 + 코스피/코스닥."""
    out = []
    for kind, pairs in (("worldstock", WORLD), ("domestic", DOMESTIC)):
        for code, name in pairs:
            try:
                r = requests.get(f"https://polling.finance.naver.com/api/realtime/{kind}/index/{code}", headers=HEADERS, timeout=15)
                d = r.json()["datas"][0]
                out.append({"name": name, "price": _num(d.get("closePrice")), "change": _num(d.get("compareToPreviousClosePrice")),
                            "change_pct": _num(d.get("fluctuationsRatio")), "time": str(d.get("localTradedAt", ""))[:16].replace("T", " ")})
            except Exception as e:  # noqa: BLE001
                log.warning("지수 조회 실패 %s: %s", code, e)
    return out


def fetch_market_index() -> list[dict]:
    """환율·달러인덱스·WTI·금: [{name, value, change, direction}]"""
    try:
        r = requests.get("https://finance.naver.com/marketindex/", headers=HEADERS, timeout=20)
        t = r.content.decode("cp949", errors="replace")
    except Exception as e:  # noqa: BLE001
        log.warning("시장지표 조회 실패: %s", e)
        return []
    items = re.findall(r'<h3 class="h_lst"><span class="blind">([^<]+)</span></h3>.*?<span class="value">([^<]+)</span>.*?<span class="change">([^<]+)</span>.*?<span class="blind">([^<]+)</span>', t, re.S)
    out = []
    for name, value, change, direction in items:
        name = html.unescape(name).strip()
        if name in INDEX_KEEP:
            out.append({"name": name, "value": _num(value), "change": _num(change), "direction": direction.strip()})
    return out


def fetch_headlines(limit: int = 12) -> list[dict]:
    """네이버 금융 주요뉴스 제목."""
    try:
        r = requests.get("https://finance.naver.com/news/mainnews.naver", headers=HEADERS, timeout=20)
        t = r.content.decode("cp949", errors="replace")
    except Exception as e:  # noqa: BLE001
        log.warning("주요뉴스 조회 실패: %s", e)
        return []
    out = []
    for m in re.finditer(r'<dd class="articleSubject">\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<span class="press">(.*?)</span>', t, re.S):
        href, title, press = m.groups()
        title = html.unescape(_TAG.sub("", title)).strip()
        if title:
            out.append({"title": title, "source": html.unescape(_TAG.sub("", press)).strip(),
                        "url": ("https://finance.naver.com" + href) if href.startswith("/") else href})
        if len(out) >= limit:
            break
    return out


def morning_brief() -> dict:
    return {"indices": fetch_indices(), "market": fetch_market_index(), "headlines": fetch_headlines()}


def format_brief(b: dict) -> str:
    lines = ["📰 오늘 아침 시황 (자동 수집, 참고용)"]
    for i in b.get("indices", []):
        arrow = "▲" if i["change_pct"] > 0 else "▼" if i["change_pct"] < 0 else "－"
        lines.append(f"{arrow} {i['name']} {i['price']:,.2f} ({i['change_pct']:+.2f}%)")
    if b.get("market"):
        parts = []
        for m in b["market"]:
            sign = "+" if m["direction"] == "상승" else "-" if m["direction"] == "하락" else ""
            unit = "원" if m["name"] == "미국 USD" else ""
            label = "달러/원" if m["name"] == "미국 USD" else m["name"]
            parts.append(f"{label} {m['value']:,.2f}{unit} ({sign}{m['change']:,.2f})")
        lines.append("💱 " + " · ".join(parts))
    if b.get("headlines"):
        lines.append("")
        lines.append("주요 뉴스")
        for h in b["headlines"][:10]:
            lines.append(f"• {h['title']} ({h['source']})")
    return "\n".join(lines)
