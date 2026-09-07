"""종목 유니버스: KRX 상장 목록(FinanceDataReader) + 환기종목(KIND) + 공통 필터."""
from __future__ import annotations

import datetime as dt
import html
import logging
import re

import pandas as pd
import requests

from ..config import DATA_DIR

log = logging.getLogger("stockbot.universe")

UNIVERSE_DIR = DATA_DIR / "universe"


def fetch_listing() -> pd.DataFrame:
    import FinanceDataReader as fdr

    df = fdr.StockListing("KRX")
    df = df.rename(columns={"Code": "code", "Name": "name", "Market": "market", "Marcap": "marcap",
                            "Close": "close", "Volume": "volume"})
    df["code"] = df["code"].astype(str).str.zfill(6)
    keep = [c for c in ["code", "name", "market", "marcap", "close", "volume"] if c in df.columns]
    df = df[keep].copy()
    df["marcap"] = pd.to_numeric(df.get("marcap"), errors="coerce").fillna(0)
    return df.reset_index(drop=True)


_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TITLE_RE = re.compile(r"title='([^']*)'|title=\"([^\"]*)\"")


def fetch_hwangi_names() -> set[str]:
    """KIND 투자주의환기종목 지정현황 → 종목명 집합 (코스닥 전용 지정)."""
    last: Exception | None = None
    r = None
    for attempt in range(3):  # KIND 는 가끔 느리다 → 재시도
        try:
            r = requests.post(
                "https://kind.krx.co.kr/investwarn/hwangiissue.do",
                data={"method": "searchHwangiIssueSub", "forward": "hwangiissue_sub", "currentPageSize": "3000",
                      "pageIndex": "1", "orderMode": "", "orderStat": "", "searchMode": "", "searchCodeType": "",
                      "searchCorpName": "", "repIsuSrtCd": ""},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://kind.krx.co.kr/"},
                timeout=60,
            )
            r.raise_for_status()
            break
        except Exception as e:  # noqa: BLE001
            last = e
            r = None
    if r is None:
        raise RuntimeError(f"KIND 환기종목 조회 실패: {last}")
    txt = r.content.decode("cp949", errors="replace")
    names: set[str] = set()
    for row in _ROW_RE.findall(txt):
        if "companysummary_open" not in row:
            continue
        m = _TITLE_RE.search(row)
        if m:
            nm = html.unescape((m.group(1) or m.group(2) or "").strip())
            if nm:
                names.add(nm)
    return names


def _norm_name(s: str) -> str:
    return re.sub(r"\s+|\(주\)|㈜", "", str(s))


def build_universe(cfg: dict) -> tuple[pd.DataFrame, dict]:
    """필터 적용된 유니버스와 메타정보. 실패 시 가장 최근 캐시 사용."""
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")
    meta: dict = {"date": today, "hwangi_ok": False, "n_hwangi": 0}

    try:
        listing = fetch_listing()
        listing.to_csv(UNIVERSE_DIR / f"listing_{today}.csv", index=False, encoding="utf-8-sig")
    except Exception as e:  # noqa: BLE001
        log.warning("상장목록 수집 실패, 캐시 사용: %s", e)
        files = sorted(UNIVERSE_DIR.glob("listing_*.csv"))
        if not files:
            raise
        listing = pd.read_csv(files[-1], dtype={"code": str}, encoding="utf-8-sig")
        listing["code"] = listing["code"].str.zfill(6)
        meta["listing_cached"] = files[-1].name

    hwangi: set[str] = set()
    if cfg["filters"].get("exclude_hwangi", True):
        try:
            hwangi = fetch_hwangi_names()
            meta["hwangi_ok"] = True
            pd.Series(sorted(hwangi)).to_csv(UNIVERSE_DIR / "hwangi_latest.csv", index=False, header=False,
                                              encoding="utf-8-sig")
        except Exception as e:  # noqa: BLE001
            log.warning("환기종목 수집 실패, 캐시 사용: %s", e)
            p = UNIVERSE_DIR / "hwangi_latest.csv"
            if p.exists():
                hwangi = set(pd.read_csv(p, header=None, encoding="utf-8-sig")[0].astype(str))
    meta["n_hwangi"] = len(hwangi)
    hw_norm = {_norm_name(x) for x in hwangi}

    f = cfg["filters"]
    df = listing.copy()
    df["hwangi"] = df["name"].map(lambda n: _norm_name(n) in hw_norm)
    meta["n_listing"] = len(df)
    df = df[df["market"].isin(f.get("markets", ["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"]))]
    df = df[df["marcap"] >= float(f.get("min_marcap_krw", 1e11))]
    if f.get("exclude_hwangi", True):
        df = df[~df["hwangi"]]
    if f.get("exclude_spac", True):
        df = df[~df["name"].str.contains("스팩", na=False)]
    if f.get("exclude_preferred", False):
        df = df[~df["name"].str.match(r".*(우|우B|우C|1우|2우|3우)$", na=False)]
    df = df.reset_index(drop=True)
    meta["n_universe"] = len(df)
    return df, meta


def resolve_name(query: str, listing: pd.DataFrame) -> list[tuple[str, str]]:
    """이름/코드 → [(code, name)] 후보. 정확 일치 우선."""
    q = _norm_name(query).upper()
    if not q:
        return []
    if re.fullmatch(r"\d{6}", q):
        hit = listing[listing["code"] == q]
        return [(r.code, r.name) for r in hit.itertuples()]
    names = listing["name"].map(_norm_name).str.upper()
    exact = listing[names == q]
    if len(exact):
        return [(r.code, r.name) for r in exact.itertuples()]
    part = listing[names.str.contains(re.escape(q), na=False)]
    part = part.sort_values("marcap", ascending=False).head(8)
    return [(r.code, r.name) for r in part.itertuples()]


def load_latest_listing() -> pd.DataFrame | None:
    files = sorted(UNIVERSE_DIR.glob("listing_*.csv"))
    if not files:
        return None
    df = pd.read_csv(files[-1], dtype={"code": str}, encoding="utf-8-sig")
    df["code"] = df["code"].str.zfill(6)
    return df
