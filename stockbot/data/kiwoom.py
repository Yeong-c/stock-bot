"""키움 REST API 최소 클라이언트 — 분봉 과거 데이터(ka10080) 수집 전용.

접근토큰: POST /oauth2/token {grant_type, appkey, secretkey}
분봉:     POST /api/dostk/chart, header api-id=ka10080, body {stk_cd, tic_scope, upd_stkpc_tp}
연속조회: 응답 헤더 cont-yn / next-key 를 다음 요청 헤더에 그대로 전달
제한:     TR당 약 1회/초 → 요청 사이에 1초 대기
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Callable

import pandas as pd
import requests

log = logging.getLogger("stockbot.kiwoom")

PROD_URL = "https://api.kiwoom.com"
MOCK_URL = "https://mockapi.kiwoom.com"


class KiwoomError(RuntimeError):
    pass


def _num(v) -> float:
    """키움은 '+70000', '-1200' 처럼 부호가 붙어 옴 → 절대값 숫자."""
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", "")
    if s in ("", "+", "-"):
        return 0.0
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


class KiwoomClient:
    def __init__(self, app_key: str, app_secret: str, is_mock: bool = False, min_interval: float = 1.05):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base = MOCK_URL if is_mock else PROD_URL
        self.min_interval = min_interval
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._last_call: dict[str, float] = {}
        self.s = requests.Session()

    # ---- 인증 ----
    def token(self) -> str:
        if self._token and time.time() < self._token_exp - 120:
            return self._token
        r = self.s.post(
            self.base + "/oauth2/token",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret},
            headers={"Content-Type": "application/json;charset=UTF-8"},
            timeout=20,
        )
        r.raise_for_status()
        js = r.json()
        tok = js.get("token") or js.get("access_token")
        if not tok:
            raise KiwoomError(f"토큰 발급 실패: {js}")
        self._token = tok
        exp = js.get("expires_dt")
        try:
            self._token_exp = dt.datetime.strptime(str(exp), "%Y%m%d%H%M%S").timestamp() if exp else time.time() + 6 * 3600
        except ValueError:
            self._token_exp = time.time() + 6 * 3600
        return tok

    def check(self) -> str:
        self.token()
        return "OK (토큰 발급 성공)"

    # ---- 요청 ----
    def request(self, path: str, api_id: str, body: dict, cont_yn: str = "N", next_key: str = ""):
        # TR별 1회/초 제한
        wait = self.min_interval - (time.time() - self._last_call.get(api_id, 0))
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {self.token()}",
                "api-id": api_id,
                "cont-yn": cont_yn,
                "next-key": next_key,
            }
            r = self.s.post(self.base + path, json=body, headers=headers, timeout=30)
            self._last_call[api_id] = time.time()
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            js = r.json()
            rc = js.get("return_code")
            if rc == 5:  # 허용 요청 수 초과
                time.sleep(1.5 * (attempt + 1))
                continue
            if rc == 3:  # 토큰 만료
                self._token = None
                continue
            if rc not in (None, 0):
                raise KiwoomError(f"{api_id} 오류 {rc}: {js.get('return_msg')}")
            nc = r.headers.get("cont-yn") or js.get("cont-yn") or js.get("cont_yn") or "N"
            nk = r.headers.get("next-key") or js.get("next-key") or js.get("next_key") or ""
            return js, nc, nk
        raise KiwoomError(f"{api_id} 재시도 초과")

    # ---- 분봉 ----
    def minute_bars(
        self, code: str, stop_before: dt.datetime | None = None, max_pages: int = 400,
        progress: Callable[[int, int], None] | None = None,
    ) -> pd.DataFrame:
        """1분봉을 최신→과거 순으로 연속조회. stop_before 이전 데이터가 나오면 중단.

        반환: ts, open, high, low, close, volume (오름차순)
        """
        recs: list[tuple] = []
        cont, key = "N", ""
        pages = 0
        while pages < max_pages:
            js, cont, key = self.request(
                "/api/dostk/chart", "ka10080",
                {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1"}, cont, key,
            )
            pages += 1
            items = js.get("stk_min_pole_chart_qry") or []
            oldest = None
            for it in items:
                t = str(it.get("cntr_tm", "")).strip()
                if len(t) < 12:
                    continue
                ts = dt.datetime.strptime(t[:14].ljust(14, "0"), "%Y%m%d%H%M%S")
                oldest = ts if oldest is None or ts < oldest else oldest
                recs.append((
                    ts, _num(it.get("open_pric")), _num(it.get("high_pric")), _num(it.get("low_pric")),
                    _num(it.get("cur_prc")), _num(it.get("trde_qty")),
                ))
            if progress:
                progress(pages, len(recs))
            if not items or cont != "Y" or not key:
                break
            if stop_before is not None and oldest is not None and oldest <= stop_before:
                break
        if not recs:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(recs, columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)
        return df
