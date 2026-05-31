# TIGER 200 IT레버리지 자동매매 시스템 명세서

## 개요

TIGER 200 IT레버리지(243880) 종목을 대상으로 추세 추종 전략을 자동 실행하는 Python 봇.
KIS(한국투자증권) Open API를 통해 실전 계좌에 주문을 실행하며,
Oracle Cloud 서버에서 24/7 운영된다.

---

## 전략 파라미터

| 파라미터 | 값 | 설명 |
|---|---|---|
| 대상 종목 | 243880 | TIGER 200 IT레버리지 |
| 신호 기준 | 139260 | TIGER 200 IT (60일선 계산용) |
| 진입 조건 | 기준 ETF 종가 > 60일 이동평균 | KOSPI200 IT 섹터가 추세 위일 때만 진입 |
| 손절 | -10% | 진입가 대비 -10% |
| 절반 익절 | +40% | 진입가 대비 +40% 도달 시 보유 수량의 절반 매도 |
| 본전 스탑 | 절반 익절 후 진입가 하회 시 청산 | |
| 트레일링 스탑 | 고점 대비 -12% | 절반 익절 후 최고가 추적 손절 |
| 손절 후 쿨다운 | 1거래일 | 손절 청산 다음날 재진입 가능 |
| 투입 비율 | 가용 현금의 95% | |

---

## 디렉토리 구조

```
trader/
├── main.py              # 진입점 — 스케줄러 실행
├── config.py            # 전략 파라미터 + KIS API 설정
├── state.py             # 포지션 상태 저장/로드 (JSON)
├── strategy.py          # 전략 로직 (진입·청산 조건 판단)
├── kis_api.py           # KIS API 래퍼 (인증·시세·주문·잔고)
├── trader_state.json    # 런타임 상태 파일 (자동 생성)
├── trader.log           # 로그 파일 (자동 생성)
└── requirements.txt     # requests, schedule
```

---

## 모듈 명세

### `config.py`

```python
CONFIG = {
    # KIS API 인증 (환경변수에서 로드)
    "APP_KEY":        str,   # KIS_APP_KEY
    "APP_SECRET":     str,   # KIS_APP_SECRET
    "ACCOUNT_NO":     str,   # KIS_ACCOUNT_NO (8자리)
    "ACCOUNT_SUFFIX": str,   # KIS_ACCOUNT_SUFFIX (2자리)
    "BASE_URL":       str,   # https://openapi.koreainvestment.com:9443

    # 종목
    "TICKER":         "243880",  # TIGER 200 IT레버리지
    "BASE_TICKER":    "139260",  # TIGER 200 IT (신호용)

    # 전략
    "STOP_LOSS":        -0.10,
    "TAKE_PROFIT_HALF": +0.40,
    "TRAIL_STOP":       -0.12,
    "COOLDOWN_DAYS":     1,
    "MA_PERIOD":         60,
    "INVEST_RATIO":      0.95,

    # 파일 경로
    "STATE_FILE": "trader_state.json",
}
```

---

### `state.py`

포지션 상태를 JSON 파일로 영속 저장. 프로세스 재시작 시 복원.

```python
# 상태 스키마
State = {
    "in_trade":     bool,    # 포지션 보유 여부
    "entry_price":  float,   # 진입가
    "peak_price":   float,   # 포지션 중 최고가 (트레일링용)
    "half_sold":    bool,     # 절반 익절 완료 여부
    "entry_qty":    int,      # 최초 진입 수량
    "hold_qty":     int,      # 현재 보유 수량
    "entry_date":   str,      # 진입일 (YYYY-MM-DD)
    "cooldown_end": str,      # 쿨다운 종료일 (YYYY-MM-DD)
    "last_run":     str,      # 마지막 실행일 (당일 중복 실행 방지)
}

def load_state() -> State
def save_state(state: State) -> None
```

---

### `kis_api.py`

KIS Open API 래퍼. 토큰 자동 갱신(23시간 유효).

```python
class KISApi:
    # 인증
    def _refresh_token(self) -> None
        # POST /oauth2/tokenP
        # client_credentials 방식

    # 시세
    def get_price(ticker: str) -> dict
        # GET /uapi/domestic-stock/v1/quotations/inquire-price
        # tr_id: FHKST01010100
        # return: { close, high, low, open }

    def get_daily_ohlcv(ticker: str, n: int = 65) -> list[dict]
        # GET /uapi/domestic-stock/v1/quotations/inquire-daily-price
        # tr_id: FHKST01010400
        # return: [{ date, close, high, low }, ...] 최신순

    # 잔고
    def get_balance() -> dict
        # GET /uapi/domestic-stock/v1/trading/inquire-balance
        # tr_id: TTTC8434R
        # return: { cash: int, holdings: { ticker: { qty, avg } } }

    # 주문
    def buy_market(ticker: str, qty: int) -> dict
        # POST /uapi/domestic-stock/v1/trading/order-cash
        # tr_id: TTTC0802U, ord_dvsn: "01" (시장가)

    def sell_market(ticker: str, qty: int) -> dict
        # POST /uapi/domestic-stock/v1/trading/order-cash
        # tr_id: TTTC0801U, ord_dvsn: "01" (시장가)
```

---

### `strategy.py`

전략 로직. 순수 함수로 구성 (API 호출 없음, 테스트 용이).

```python
def calc_ma60(ohlcv: list[dict]) -> float | None
    """최근 60일 종가 평균. 데이터 부족 시 None"""

def is_cooldown_active(state: State) -> bool
    """오늘 날짜가 cooldown_end 이전이면 True"""

def get_cooldown_end_date(n_days: int) -> str
    """오늘부터 n 거래일 후 날짜 (주말 제외)"""

def should_enter(
    kp_close: float,     # 기준 ETF 현재가
    ma60: float,         # 기준 ETF 60일선
    state: State,
    cooldown_active: bool,
) -> bool
    """진입 조건: 포지션 없음 + 쿨다운 없음 + 종가 > MA60"""

def check_exit(
    close: float,
    high: float,
    low: float,
    state: State,
) -> tuple[str | None, float | None]
    """
    청산 조건 체크. (exit_reason, exit_price) 반환.
    exit_reason: 'STOP_LOSS' | 'HALF_TP' | 'BREAK_EVEN_STOP' | 'TRAIL_STOP' | None

    우선순위:
    1. STOP_LOSS: low <= entry * (1 + STOP_LOSS)
    2. HALF_TP: half_sold=False, high >= entry * (1 + TAKE_PROFIT_HALF)
    3. BREAK_EVEN_STOP: half_sold=True, low <= entry_price
    4. TRAIL_STOP: half_sold=True, low <= peak * (1 + TRAIL_STOP)
    """
```

---

### `main.py`

진입점. 스케줄러 + 단일 실행 모드.

```python
def run_strategy() -> None
    """
    1. 장 시간 체크 (09:00~15:30)
    2. 당일 중복 실행 방지 (last_run == today)
    3. KISApi 초기화
    4. 시세 수집 (TICKER, BASE_TICKER)
    5. MA60 계산
    6. 잔고 조회 — 상태 파일과 실제 보유 수량 동기화
    7. 포지션 없음: should_enter() → buy_market()
    8. 포지션 있음: check_exit() → sell_market() 또는 HALF_TP 처리
    9. 상태 저장
    """

def main() -> None
    """
    CLI 인자:
      --run-once   1회 즉시 실행
      --status     현재 상태 출력
      --reset      상태 초기화
      (인자 없음)  스케줄러 실행: 평일 09:05 / 13:00 / 14:50
    """
```

---

## 실행 흐름

```
main.py 실행
    │
    ├── --run-once → run_strategy() → 종료
    ├── --status   → state 출력 → 종료
    ├── --reset    → state 초기화 → 종료
    └── (없음)     → schedule 등록 → while True loop
                         │
                   매일 09:05 / 13:00 / 14:50
                         │
                   run_strategy()
                         │
              ┌──────────┴──────────┐
          포지션 없음           포지션 있음
              │                    │
        진입 조건 체크         청산 조건 체크
        MA60 > 종가?           SL / TP / TS
              │                    │
           매수 실행            매도 실행
              │                    │
           상태 저장            상태 저장
```

---

## 에러 처리

| 상황 | 처리 |
|---|---|
| KIS API 토큰 만료 | `_refresh_token()` 자동 재발급 |
| 주문 실패 (`rt_cd != "0"`) | `RuntimeError` raise, 로그 기록 |
| 잔고 불일치 (수동 매도 등) | `hold_qty == 0` 감지 시 상태 자동 초기화 |
| 장 시간 외 실행 | 즉시 리턴 |
| 당일 중복 실행 | `last_run == today` 체크로 스킵 |
| MA60 계산 불가 (데이터 부족) | 진입 보류, 로그 기록 |

---

## 환경변수

```bash
KIS_APP_KEY        # KIS 앱키 (실전 계좌용)
KIS_APP_SECRET     # KIS 시크릿
KIS_ACCOUNT_NO     # 계좌번호 앞 8자리
KIS_ACCOUNT_SUFFIX # 계좌번호 뒤 2자리 (보통 "01")
```

---

## 배포 (Oracle Cloud Ubuntu 22.04)

```bash
# 의존성
pip install requests schedule

# 환경변수 등록
echo 'export KIS_APP_KEY="..."' >> ~/.bashrc
source ~/.bashrc

# 상태 확인
python main.py --status

# 1회 테스트
python main.py --run-once

# cron 등록 (평일만)
5  9  * * 1-5  cd ~/trader && python main.py --run-once >> trader.log 2>&1
0  13 * * 1-5  cd ~/trader && python main.py --run-once >> trader.log 2>&1
50 14 * * 1-5  cd ~/trader && python main.py --run-once >> trader.log 2>&1
```

---

## 로그 포맷

```
2026-05-29 09:05:12 [INFO] =====================
2026-05-29 09:05:12 [INFO] 전략 실행: 2026-05-29
2026-05-29 09:05:13 [INFO] 레버리지 현재가: 187,940원  고가: 187,940원  저가: 177,640원
2026-05-29 09:05:13 [INFO] TIGER200IT: 187,940원  MA60: 98,320원
2026-05-29 09:05:13 [INFO] 포지션: 보유 | 쿨다운: 없음
2026-05-29 09:05:13 [INFO] 진입가: 70,270  손절선: 63,243  트레일: 200,000  절반익절: 98,378
2026-05-29 09:05:13 [INFO] 홀딩 유지 — 청산 조건 미충족
2026-05-29 09:05:13 [INFO] 상태 저장: {...}
2026-05-29 09:05:13 [INFO] 실행 완료
```

---

## 테스트 시나리오

| 시나리오 | 검증 항목 |
|---|---|
| MA60 이하 진입 시도 | 진입 거부 확인 |
| 손절 -10% 발동 | `STOP_LOSS` 청산 + 쿨다운 1일 |
| +40% 절반 익절 | 수량 절반 매도 + `half_sold=True` |
| 절반 익절 후 본전 하회 | `BREAK_EVEN_STOP` 청산 |
| 절반 익절 후 트레일링 | `TRAIL_STOP` 청산 |
| 수동 매도 후 상태 불일치 | 잔고 동기화로 자동 복구 |
| 프로세스 재시작 | 상태 파일에서 포지션 복원 |
| 당일 중복 실행 | 두 번째 실행 스킵 |
