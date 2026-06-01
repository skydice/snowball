# Snowball Trading Bot

KIS(한국투자증권) Open API를 이용한 레버리지 ETF 추세추종 자동매매 봇.

- **IT 레버리지**: TIGER 200 IT레버리지(243880) — MA60 신호: TIGER 200 IT(139260)
- **조선 레버리지**: SOL 조선TOP3플러스레버리지(0080Y0) — MA60 신호: 비레버리지(466920)

두 전략에 가용 현금 95%를 5:5로 배분. Oracle Cloud Ubuntu에서 cron으로 운영.

## 전략 로직

| 단계 | 조건 |
|------|------|
| 진입 | BASE_TICKER 종가 > MA60, 쿨다운 없음 |
| 손절 | IT: -10% / 조선: -5% |
| 절반 익절 | IT: +40% / 조선: +50% |
| 본전 스탑 | 절반 익절 후 진입가 하회 |
| 트레일링 스탑 | IT: 고점 대비 -12% / 조선: -15% |

쿨다운: IT 1거래일, 조선 10거래일 (손절 후 재진입 방지)

## 설치 및 실행

### 의존성 설치

```bash
# uv 설치 (최초 1회)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 의존성 설치
cd trader
uv sync
```

### 환경변수 설정

`trader/.env` 파일 생성:

```bash
# KIS API — 실전 계좌
KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_시크릿

# KIS API — 모의 계좌 (--env demo 실행 시 사용)
KIS_PAPER_APP_KEY=모의_앱키
KIS_PAPER_APP_SECRET=모의_시크릿

# KIS 계좌 정보
KIS_HTS_ID=HTS아이디
KIS_ACCT_STOCK=실전계좌번호앞8자리
KIS_PAPER_STOCK=모의계좌번호앞8자리
KIS_PROD_TYPE=01

# 텔레그램 알림 (선택 — 없으면 알림 없이 동작)
TELEGRAM_TOKEN=봇토큰
TELEGRAM_CHAT_ID=수신chat_id
```

KIS API 키는 [한국투자증권 Open API](https://apiportal.koreainvestment.com) 에서 발급.
텔레그램 봇 토큰은 [@BotFather](https://t.me/BotFather)에서 `/newbot`으로 발급.

### 실행 명령어

```bash
cd trader

uv run main.py run-once          # 두 전략 모두 1회 실행
uv run main.py run-once it       # IT 전략만 실행
uv run main.py run-once ship     # 조선 전략만 실행
uv run main.py --env demo run-once  # 모의 계좌로 테스트

uv run main.py status            # 두 전략 현재 상태 출력
uv run main.py reset it          # IT 전략 상태 초기화
uv run main.py reset ship        # 조선 전략 상태 초기화

uv run main.py                   # 스케줄러 모드 (09:05 / 13:00 / 14:50 자동 실행)
```

## 서버 배포 (Oracle Cloud Ubuntu)

### 타임존 설정

```bash
sudo timedatectl set-timezone Asia/Seoul
sudo systemctl restart cron
```

### cron 등록

```bash
crontab -e
```

```
5  9  * * 1-5  cd /home/ubuntu/snowball/trader && /home/ubuntu/.local/bin/uv run main.py run-once >> trader.log 2>&1
0  13 * * 1-5  cd /home/ubuntu/snowball/trader && /home/ubuntu/.local/bin/uv run main.py run-once >> trader.log 2>&1
50 14 * * 1-5  cd /home/ubuntu/snowball/trader && /home/ubuntu/.local/bin/uv run main.py run-once >> trader.log 2>&1
```

## 디렉토리 구조

```
trader/
├── main.py         # 진입점, CLI, 스케줄러
├── config.py       # 전략 파라미터, 환경변수 로드
├── state.py        # JSON 상태 영속화
├── strategy.py     # 순수 전략 함수 (진입/청산 판단)
├── kis_api.py      # KIS Open API 클라이언트
├── notifier.py     # 텔레그램 알림
└── pyproject.toml  # uv 의존성 관리

example/            # KIS API 레퍼런스 예제 (읽기 전용)
```

런타임 자동 생성: `trader/state_it.json`, `trader/state_ship.json`, `trader/trader.log`
