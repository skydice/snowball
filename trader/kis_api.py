import logging
import time
from datetime import datetime, timedelta

import requests

import config

log = logging.getLogger(__name__)

_TOKEN_MARGIN_SEC = 3600  # 만료 1시간 전 갱신


class KISApi:
    def __init__(self, env_dv: str = "real"):
        self.env_dv = env_dv
        if env_dv == "real":
            self._app_key    = config.APP_KEY
            self._app_secret = config.APP_SECRET
            self._account_no = config.ACCOUNT_NO
        else:
            self._app_key    = config.PAPER_APP_KEY
            self._app_secret = config.PAPER_APP_SECRET
            self._account_no = config.PAPER_ACCOUNT_NO
        self._account_suffix = config.ACCOUNT_SUFFIX
        self._base_url = config.BASE_URL
        self._token: str = ""
        self._token_expires_at: datetime = datetime(2000, 1, 1)

    # ------------------------------------------------------------------ auth

    def _ensure_token(self) -> None:
        if datetime.now() < self._token_expires_at - timedelta(seconds=_TOKEN_MARGIN_SEC):
            return
        self._refresh_token()

    def _refresh_token(self) -> None:
        url = f"{self._base_url}/oauth2/tokenP"
        body = {
            "grant_type":  "client_credentials",
            "appkey":      self._app_key,
            "appsecret":   self._app_secret,
        }
        res = requests.post(url, json=body, timeout=10)
        res.raise_for_status()
        data = res.json()
        self._token = data["access_token"]
        # 토큰 유효 시간(초) — API는 86400초(24h) 반환, 실제 유효 23h로 운영
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
        log.info("KIS 토큰 갱신 완료 (만료: %s)", self._token_expires_at.strftime("%Y-%m-%d %H:%M"))

    def _headers(self, tr_id: str, tr_cont: str = "") -> dict:
        return {
            "Content-Type":  "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token}",
            "appkey":        self._app_key,
            "appsecret":     self._app_secret,
            "tr_id":         tr_id,
            "tr_cont":       tr_cont,
            "custtype":      "P",
        }

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        self._ensure_token()
        url = self._base_url + path
        res = requests.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"KIS API 오류 [{tr_id}] rt_cd={data.get('rt_cd')} msg={data.get('msg1')}")
        return data

    def _post(self, path: str, tr_id: str, body: dict) -> dict:
        self._ensure_token()
        url = self._base_url + path
        res = requests.post(url, headers=self._headers(tr_id), json=body, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"KIS API 오류 [{tr_id}] rt_cd={data.get('rt_cd')} msg={data.get('msg1')}")
        return data

    # --------------------------------------------------------------- 시세

    def get_price(self, ticker: str) -> dict:
        """당일 현재가·고가·저가·시가 조회."""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            },
        )
        out = data["output"]
        return {
            "close": float(out["stck_prpr"]),
            "high":  float(out["stck_hgpr"]),
            "low":   float(out["stck_lwpr"]),
            "open":  float(out["stck_oprc"]),
        }

    def get_daily_ohlcv(self, ticker: str, n: int = 65) -> list[dict]:
        """
        최근 n거래일 일봉 데이터 반환 (최신순).
        inquire-daily-itemchartprice 사용 — 1회 호출로 최대 100건.
        """
        from datetime import date, timedelta
        end_date   = date.today().strftime("%Y%m%d")
        start_date = (date.today() - timedelta(days=n * 2)).strftime("%Y%m%d")  # 여유있게 2배

        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         ticker,
                "FID_INPUT_DATE_1":       start_date,
                "FID_INPUT_DATE_2":       end_date,
                "FID_PERIOD_DIV_CODE":    "D",
                "FID_ORG_ADJ_PRC":        "0",  # 수정주가
            },
        )
        rows = data.get("output2", [])
        # 최신순 정렬 보장
        rows.sort(key=lambda r: r["stck_bsop_date"], reverse=True)
        return rows[:n]

    # --------------------------------------------------------------- 잔고

    def get_balance(self) -> dict:
        """
        잔고 조회.
        반환: { "cash": int, "holdings": { ticker: {"qty": int, "avg": float} } }
        """
        tr_id = "TTTC8434R" if self.env_dv == "real" else "VTTC8434R"
        data = self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id,
            {
                "CANO":                self._account_no,
                "ACNT_PRDT_CD":        self._account_suffix,
                "AFHR_FLPR_YN":        "N",
                "OFL_YN":              "",
                "INQR_DVSN":           "02",
                "UNPR_DVSN":           "01",
                "FUND_STTL_ICLD_YN":   "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN":           "00",
                "CTX_AREA_FK100":      "",
                "CTX_AREA_NK100":      "",
            },
        )
        holdings = {}
        for item in data.get("output1", []):
            qty = int(item.get("hldg_qty", 0))
            if qty > 0:
                holdings[item["pdno"]] = {
                    "qty": qty,
                    "avg": float(item.get("pchs_avg_pric", 0)),
                }

        # output2는 단일 object (예수금 등 계좌 요약)
        output2 = data.get("output2", {})
        if isinstance(output2, list):
            output2 = output2[0] if output2 else {}
        cash = int(float(output2.get("dnca_tot_amt", 0)))

        return {"cash": cash, "holdings": holdings}

    # --------------------------------------------------------------- 주문

    def buy_market(self, ticker: str, qty: int) -> dict:
        tr_id = "TTTC0012U" if self.env_dv == "real" else "VTTC0012U"
        data = self._post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            {
                "CANO":             self._account_no,
                "ACNT_PRDT_CD":     self._account_suffix,
                "PDNO":             ticker,
                "ORD_DVSN":         "01",   # 시장가
                "ORD_QTY":          str(qty),
                "ORD_UNPR":         "0",
                "EXCG_ID_DVSN_CD":  "KRX",
                "SLL_TYPE":         "",
                "CNDT_PRIC":        "",
            },
        )
        log.info("매수 주문 완료: %s %d주 (주문번호: %s)", ticker, qty, data.get("output", {}).get("odno"))
        return data

    def sell_market(self, ticker: str, qty: int) -> dict:
        tr_id = "TTTC0011U" if self.env_dv == "real" else "VTTC0011U"
        data = self._post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            {
                "CANO":             self._account_no,
                "ACNT_PRDT_CD":     self._account_suffix,
                "PDNO":             ticker,
                "ORD_DVSN":         "01",   # 시장가
                "ORD_QTY":          str(qty),
                "ORD_UNPR":         "0",
                "EXCG_ID_DVSN_CD":  "KRX",
                "SLL_TYPE":         "01",   # 일반매도
                "CNDT_PRIC":        "",
            },
        )
        log.info("매도 주문 완료: %s %d주 (주문번호: %s)", ticker, qty, data.get("output", {}).get("odno"))
        return data
