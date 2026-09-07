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
_NAME_AFTER_IMG_RE = re.compile(r"alt='[^']*'>\s*([^<]+?)\s*</a>")
_KIND_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://kind.krx.co.kr/"}


def _kind_post(path: str, data: dict) -> str:
    """KIND 목록 POST (가끔 느려 재시도). 인코딩은 페이지마다 달라 utf-8 → cp949 순으로 시도."""
    last: Exception | None = None
    for _ in range(3):
        try:
            r = requests.post("https://kind.krx.co.kr" + path, data=data, headers=_KIND_HEADERS, timeout=60)
            r.raise_for_status()
            try:
                return r.content.decode("utf-8")
            except UnicodeDecodeError:
                return r.content.decode("cp949", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"KIND 조회 실패 {path}: {last}")


def _kind_names(txt: str) -> set[str]:
    names: set[str] = set()
    for row in _ROW_RE.findall(txt):
        if "legend" not in row:  # 시장구분 아이콘이 있는 행만 (헤더 제외)
            continue
        m = _TITLE_RE.search(row)
        nm = html.unescape((m.group(1) or m.group(2) or "").strip()) if m else ""
        if not nm:
            m2 = _NAME_AFTER_IMG_RE.search(row)
            nm = html.unescape(m2.group(1).strip()) if m2 else ""
        if nm:
            names.add(nm)
    return names


def fetch_hwangi_names() -> set[str]:
    """KIND 투자주의환기종목 → 종목명 집합."""
    return _kind_names(_kind_post("/investwarn/hwangiissue.do", {
        "method": "searchHwangiIssueSub", "forward": "hwangiissue_sub", "currentPageSize": "3000", "pageIndex": "1"}))


def fetch_admin_names() -> set[str]:
    """KIND 관리종목 → 종목명 집합."""
    return _kind_names(_kind_post("/investwarn/adminissue.do", {
        "method": "searchAdminIssueSub", "forward": "adminissue_sub", "currentPageSize": "3000", "pageIndex": "1"}))


def fetch_delisting_names(months: int = 3) -> set[str]:
    """KIND 상장폐지 결정 종목(정리매매 대상) 최근 N개월 → 종목명 집합."""
    end = dt.date.today()
    start = end - dt.timedelta(days=31 * months)
    txt = _kind_post("/investwarn/delcompany.do", {
        "method": "searchDelCompanySub", "forward": "delcompany_sub", "currentPageSize": "3000", "pageIndex": "1",
        "startDate": start.strftime("%Y-%m-%d"), "endDate": end.strftime("%Y-%m-%d")})
    names: set[str] = set()
    for row in _ROW_RE.findall(txt):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 2 and cells[1]:
            names.add(cells[1])
    return names


def fetch_naver_alert_codes(kind: str = "risk") -> set[str]:
    """네이버 투자경보 목록(caution/warning/risk) → 종목코드 집합. 투자위험 제외에 사용."""
    r = requests.get("https://finance.naver.com/sise/investment_alert.naver", params={"type": kind},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    txt = r.content.decode("cp949", errors="replace")
    return set(re.findall(r'/item/main\.naver\?code=(\d{6})', txt))


def _cached_set(name: str, fetch, meta: dict) -> set:
    """수집 실패 시 마지막 성공본 사용."""
    p = UNIVERSE_DIR / f"{name}_latest.csv"
    try:
        vals = set(fetch())
        pd.Series(sorted(vals)).to_csv(p, index=False, header=False, encoding="utf-8-sig")
        meta[f"{name}_ok"] = True
    except Exception as e:  # noqa: BLE001
        log.warning("%s 수집 실패, 캐시 사용: %s", name, e)
        meta[f"{name}_ok"] = False
        vals = set(pd.read_csv(p, header=None, encoding="utf-8-sig", dtype=str)[0]) if p.exists() else set()
    meta[f"n_{name}"] = len(vals)
    return vals


def _norm_name(s: str) -> str:
    return re.sub(r"\s+|\(주\)|㈜", "", str(s))


def build_universe(cfg: dict) -> tuple[pd.DataFrame, dict]:
    """필터 적용된 유니버스와 메타정보. 실패 시 가장 최근 캐시 사용."""
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")
    meta: dict = {"date": today}

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

    f = cfg["filters"]
    hwangi = _cached_set("hwangi", fetch_hwangi_names, meta) if f.get("exclude_hwangi", True) else set()
    admin = _cached_set("admin", fetch_admin_names, meta) if f.get("exclude_admin", True) else set()
    delist = _cached_set("delist", fetch_delisting_names, meta) if f.get("exclude_halt", True) else set()
    risk = _cached_set("risk", lambda: fetch_naver_alert_codes("risk"), meta) if f.get("exclude_risk", True) else set()
    meta["hwangi_ok"] = meta.get("hwangi_ok", True)  # 하위 호환 (메시지 표시용)

    hw_norm = {_norm_name(x) for x in hwangi}
    ad_norm = {_norm_name(x) for x in admin}
    de_norm = {_norm_name(x) for x in delist}
    df = listing.copy()
    nn = df["name"].map(_norm_name)
    df["hwangi"] = nn.isin(hw_norm)
    df["admin"] = nn.isin(ad_norm)
    df["delist"] = nn.isin(de_norm)
    df["risk"] = df["code"].isin(risk)
    meta["n_listing"] = len(df)
    df = df[df["market"].isin(f.get("markets", ["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"]))]
    df = df[df["marcap"] >= float(f.get("min_marcap_krw", 1e11))]
    if f.get("exclude_hwangi", True):
        df = df[~df["hwangi"]]
    if f.get("exclude_admin", True):
        df = df[~df["admin"]]
    if f.get("exclude_halt", True):
        df = df[~df["delist"]]
    if f.get("exclude_risk", True):
        df = df[~df["risk"]]
    if f.get("exclude_spac", True):
        df = df[~df["name"].str.contains("스팩", na=False)]
    if f.get("exclude_preferred", True):
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
