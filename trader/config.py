import os
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://openapi.koreainvestment.com:9443"

APP_KEY        = os.environ["KIS_APP_KEY"]
APP_SECRET     = os.environ["KIS_APP_SECRET"]
ACCOUNT_NO     = os.environ["KIS_ACCT_STOCK"]

PAPER_APP_KEY    = os.environ["KIS_PAPER_APP_KEY"]
PAPER_APP_SECRET = os.environ["KIS_PAPER_APP_SECRET"]
PAPER_ACCOUNT_NO = os.environ["KIS_PAPER_STOCK"]

ACCOUNT_SUFFIX = os.environ["KIS_PROD_TYPE"]  # "01"

MA_PERIOD    = 60
INVEST_RATIO = 0.475  # 가용 현금의 47.5% (두 전략 합산 95%)

STRATEGIES = {
    "it": {
        "NAME":             "IT레버리지",
        "TICKER":           "243880",   # TIGER 200 IT레버리지
        "BASE_TICKER":      "139260",   # TIGER 200 IT (MA60 신호용)
        "STOP_LOSS":        -0.10,
        "TAKE_PROFIT_HALF": +0.40,
        "TRAIL_STOP":       -0.12,
        "COOLDOWN_DAYS":    1,
        "STATE_FILE":       "state_it.json",
    },
    "ship": {
        "NAME":             "조선레버리지",
        "TICKER":           "0080Y0",   # SOL 조선TOP3플러스레버리지
        "BASE_TICKER":      "466920",   # MA60 신호용 비레버리지
        "STOP_LOSS":        -0.05,
        "TAKE_PROFIT_HALF": +0.50,
        "TRAIL_STOP":       -0.15,
        "COOLDOWN_DAYS":    10,
        "STATE_FILE":       "state_ship.json",
    },
}
