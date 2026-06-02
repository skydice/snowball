# CLAUDE.md — Snowball Trading Bot

## 프로젝트 개요

TIGER 200 IT레버리지(243880) 추세 추종 자동매매 봇. KIS(한국투자증권) Open API로 실전 계좌에 주문. Oracle Cloud Ubuntu 22.04에서 cron으로 운영.

신호는 기준 ETF(139260 TIGER 200 IT)의 60일선으로 판단하고, 레버리지 ETF(243880)로 매매한다.

## 디렉토리 구조

```
trader/          ← 실제 구현 대상 (아직 미생성)
├── main.py
├── config.py
├── state.py
├── strategy.py
├── kis_api.py
├── notifier.py
├── state_it.json    ← 런타임 자동 생성 (IT 전략 상태)
├── state_ship.json  ← 런타임 자동 생성 (조선 전략 상태)
└── requirements.txt

example/         ← KIS API 레퍼런스 예제 (읽기 전용)
├── llm/         ← API별 함수 단위 예제 (구현 참조용)
└── user/        ← 여러 함수를 조합한 사용 예제
```

## KIS API → kis_api.py 매핑

`example/llm/` 디렉토리의 파일들이 `kis_api.py` 구현의 직접 레퍼런스다. 아래 매핑을 따른다.

| kis_api.py 메서드 | 참조 예제 | 엔드포인트 | tr_id (실전) |
|---|---|---|---|
| `get_price()` | `example/llm/inquire_price/inquire_price.py` | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` |
| `get_daily_ohlcv()` | `example/llm/inquire_daily_price/inquire_daily_price.py` | `/uapi/domestic-stock/v1/quotations/inquire-daily-price` | `FHKST01010400` |
| `get_balance()` | `example/llm/inquire_balance/inquire_balance.py` | `/uapi/domestic-stock/v1/trading/inquire-balance` | `TTTC8434R` |
| `buy_market()` | `example/llm/order_cash/order_cash.py` | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0012U` |
| `sell_market()` | `example/llm/order_cash/order_cash.py` | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0011U` |

### 주의: tr_id 불일치

SPEC.md의 `buy_market` tr_id(`TTTC0802U`)와 `sell_market` tr_id(`TTTC0801U`)는 오래된 값이다.
`example/llm/order_cash/order_cash.py`에서 확인된 실제 값을 사용한다:
- 매수 실전: `TTTC0012U` / 모의: `VTTC0012U`
- 매도 실전: `TTTC0011U` / 모의: `VTTC0011U`

### 주의: 시장가 주문 파라미터

`ord_dvsn`은 `"01"` (시장가). 예제가 `"00"` (지정가)를 쓰는 경우가 있으나 봇은 항상 시장가.
시장가 주문 시 `ord_unpr`은 `"0"` 으로 설정.

### get_daily_ohlcv 호출 방식

`inquire_daily_price`는 1회 호출에 최대 30건만 반환. MA60 계산에는 60일치가 필요하므로 두 번 호출해 합산하거나 `FID_PERIOD_DIV_CODE="D"`로 최근 30거래일씩 페이지네이션해야 한다. 예제(`example/llm/inquire_daily_price/`)의 파라미터 구조 참고.

## 예제 활용 방법

### kis_api.py 구현 시

예제 파일은 `kis_auth`(`ka`) 모듈을 HTTP 레이어로 쓴다. `kis_api.py`는 `requests`를 직접 써서 구현하되, 예제에서 다음 항목을 그대로 가져온다:
- API 엔드포인트 경로
- `tr_id` 값
- 요청 파라미터 키 이름 (대문자, ex. `FID_INPUT_ISCD`, `CANO`)
- 응답 필드 이름 (ex. `stck_prpr`, `stck_hgpr`, `stck_lwpr`)

### 응답 필드 확인

응답 구조가 불확실할 때는 해당 API의 `chk_*.py` 파일을 읽는다.
예: `example/llm/inquire_price/chk_inquire_price.py` → 실제 응답 컬럼 목록 확인.

## 전략 구성 — 2개 전략 동시 운용 (자금 5:5)

전체 현금의 50%를 항상 현금 보유. 나머지 50%를 두 전략에 절반씩 배분 (각 25%).
`run_all` 시작 시 잔고 한 번 조회 → `per_strategy = total_cash × 0.25` 확정 후 각 전략에 전달.
각 전략은 독립적인 상태 파일을 가지며 `main.py`에서 순차 실행된다.

### 전략 1 — IT 레버리지 (config.py의 `STRATEGY_IT`)

| 파라미터 | 값 |
|---|---|
| TICKER | `243880` (TIGER 200 IT레버리지) |
| BASE_TICKER | `139260` (TIGER 200 IT, MA60 신호용) |
| STOP_LOSS | -10% |
| TAKE_PROFIT_HALF | +40% |
| TRAIL_STOP | -12% |
| COOLDOWN_DAYS | 1 거래일 |
| STATE_FILE | `state_it.json` |

### 전략 2 — 조선 레버리지 (config.py의 `STRATEGY_SHIP`)

| 파라미터 | 값 |
|---|---|
| TICKER | `0080Y0` (SOL 조선TOP3플러스레버리지, 신한자산운용 2x) |
| BASE_TICKER | `466920` (MA60 신호용 비레버리지) |
| STOP_LOSS | -5% |
| TAKE_PROFIT_HALF | +50% |
| TRAIL_STOP | -15% |
| COOLDOWN_DAYS | 10 거래일 |
| STATE_FILE | `state_ship.json` |

### 공통 파라미터

```python
MA_PERIOD    = 60     # 60일 이동평균
INVEST_RATIO = 0.475  # 가용 현금의 47.5% (전체 95% ÷ 2)
```

### config.py 구조

```python
STRATEGIES = {
    "it":   { "TICKER": "243880", "BASE_TICKER": "139260",
               "STOP_LOSS": -0.10, "TAKE_PROFIT_HALF": 0.40,
               "TRAIL_STOP": -0.12, "COOLDOWN_DAYS": 1,
               "STATE_FILE": "state_it.json" },
    "ship": { "TICKER": "0080Y0", "BASE_TICKER": "466920",
               "STOP_LOSS": -0.05, "TAKE_PROFIT_HALF": 0.50,
               "TRAIL_STOP": -0.15, "COOLDOWN_DAYS": 10,
               "STATE_FILE": "state_ship.json" },
}
MA_PERIOD    = 60
INVEST_RATIO = 0.475  # 전체 가용 현금의 절반
```

## 텔레그램 알림 (notifier.py)

### 모듈 구조

`trader/notifier.py`로 분리. `main.py`에서 import해서 사용.

```python
def send(msg: str) -> None
    # TELEGRAM_TOKEN, TELEGRAM_CHAT_ID 환경변수 없으면 조용히 skip
    # 실패해도 RuntimeError 대신 로그만 남기고 계속 진행
```

### 알림 발송 시점 및 메시지 형식

| 시점 | 예시 메시지 |
|---|---|
| 매수 체결 | `[매수] 243880 / 10주 / 진입가 187,940원` |
| 매도 체결 (손절) | `[매도-손절] 243880 / 10주 / 청산가 169,146원 / 수익률 -10.0%` |
| 매도 체결 (절반익절) | `[매도-절반익절] 243880 / 5주 / 청산가 263,116원 / 수익률 +40.0%` |
| 매도 체결 (트레일링) | `[매도-트레일링] 243880 / 5주 / 청산가 237,000원 / 수익률 +26.2%` |
| 매도 체결 (본전스탑) | `[매도-본전스탑] 243880 / 5주 / 청산가 187,940원 / 수익률 ±0%` |
| 홀딩 유지 | `[홀딩] 243880 / 현재가 195,000원 / 진입가 187,940원 / 수익률 +3.7%` |
| 포지션 없음·미진입 | `[대기] MA60(98,320) > 현재가(90,000) — 진입 조건 미충족` |
| 에러 | `[에러] RuntimeError: 주문 실패 — rt_cd=1, msg=...` |

### 환경변수 추가

```bash
TELEGRAM_TOKEN    # BotFather에서 발급한 봇 토큰
TELEGRAM_CHAT_ID  # 메시지 수신할 chat_id (개인 DM 또는 그룹)
```

### 텔레그램 봇 설정 방법

1. [@BotFather](https://t.me/BotFather) 에서 `/newbot` → 토큰 발급
2. 봇에게 메시지 1개 보낸 후 `https://api.telegram.org/bot<TOKEN>/getUpdates` 로 `chat_id` 확인
3. `.env`에 추가

### 구현 시 주의사항

- `notifier.py`는 환경변수 미설정 시 알림 없이 동작. 로컬 테스트에서 토큰 없어도 봇이 멈추면 안 됨.
- 알림 실패(네트워크 오류 등)는 `logging.warning`으로만 처리. 매매 로직에 영향 주지 말 것.
- 에러 알림은 `main.py`의 최상위 `try/except`에서 발송.

## 환경변수

```bash
KIS_APP_KEY        # KIS 앱키 (실전)
KIS_APP_SECRET     # KIS 시크릿 (실전)
KIS_PAPER_APP_KEY  # KIS 앱키 (모의)
KIS_PAPER_APP_SECRET # KIS 시크릿 (모의)
KIS_HTS_ID         # HTS ID
KIS_ACCT_STOCK     # 실전 계좌번호 앞 8자리
KIS_PAPER_STOCK    # 모의 계좌번호 앞 8자리
KIS_PROD_TYPE      # 계좌상품코드 (보통 "01")
TELEGRAM_TOKEN     # 텔레그램 봇 토큰
TELEGRAM_CHAT_ID   # 텔레그램 수신 chat_id
```

## 청산 조건 우선순위 (strategy.py)

`check_exit(cfg, close, high, low, state)`에서 아래 순서로 체크. 전략별 파라미터(`cfg`)를 인자로 받는다.

1. `STOP_LOSS` — `low <= entry * (1 + cfg["STOP_LOSS"])`
2. `HALF_TP` — `half_sold=False` 이고 `high >= entry * (1 + cfg["TAKE_PROFIT_HALF"])`
3. `BREAK_EVEN_STOP` — `half_sold=True` 이고 `low <= entry_price`
4. `TRAIL_STOP` — `half_sold=True` 이고 `low <= peak * (1 + cfg["TRAIL_STOP"])`

`should_enter()`, `calc_ma60()` 등 모든 전략 함수도 `cfg`를 파라미터로 받아 두 전략 공용으로 동작.

## 실행 방법

```bash
# 의존성 설치 (최초 1회)
uv sync

# 실행
uv run main.py run-once            # 두 전략 모두 1회 즉시 실행
uv run main.py run-once it         # IT 전략만 실행
uv run main.py run-once ship       # 조선 전략만 실행
uv run main.py --env demo run-once # 모의 계좌로 테스트
uv run main.py status              # 두 전략 상태 출력
uv run main.py reset it            # IT 전략 상태 초기화
uv run main.py reset ship          # 조선 전략 상태 초기화
uv run main.py                     # 스케줄러 (09:05 / 13:00 / 14:50, 두 전략 순차 실행)
```

cron 등록 시:
```
5  9  * * 1-5  cd ~/trader && uv run main.py run-once >> trader.log 2>&1
0  13 * * 1-5  cd ~/trader && uv run main.py run-once >> trader.log 2>&1
50 14 * * 1-5  cd ~/trader && uv run main.py run-once >> trader.log 2>&1
```

## 테스트 시나리오

`strategy.py`는 순수 함수로 구성 → `kis_api.py` 없이 단위 테스트 가능. 두 전략 모두 동일 함수, `cfg`만 교체해서 검증.

| 시나리오 | 검증 포인트 |
|---|---|
| MA60 이하에서 진입 시도 | `should_enter()` → False |
| IT 손절 -10% | `STOP_LOSS` 반환 + 쿨다운 1거래일 |
| 조선 손절 -5% | `STOP_LOSS` 반환 + 쿨다운 10거래일 |
| IT +40% 절반 익절 | `HALF_TP` 반환 + `half_sold=True` |
| 조선 +50% 절반 익절 | `HALF_TP` 반환 + `half_sold=True` |
| 절반 익절 후 본전 하회 | `BREAK_EVEN_STOP` 반환 |
| 잔고 불일치 (수동 매도) | `hold_qty == 0` → 상태 자동 초기화 |
| 당일 중복 실행 | `last_run == today` → 즉시 리턴 |
| 두 전략 동시 포지션 | 각각 독립 상태 파일, 자금 간섭 없음 |

## 개발 시 주의사항

- `kis_api.py`에서 주문 실패(`rt_cd != "0"`) 시 `RuntimeError` raise — 조용히 넘기지 말 것.
- 토큰은 23시간 유효. 매일 첫 실행 시 갱신, 만료 체크 후 자동 재발급.
- `state.py`는 JSON 파일 영속화. 프로세스 재시작해도 포지션 복원돼야 함.
- 장 외 시간(09:00 전, 15:30 후) 실행 시 즉시 리턴.
