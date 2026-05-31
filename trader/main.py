import argparse
import json
import logging
import sys
from datetime import date, datetime, time

import config
import notifier
from kis_api import KISApi
from state import DEFAULT_STATE, load_state, save_state
from strategy import (
    calc_ma60,
    check_exit,
    get_cooldown_end_date,
    is_cooldown_active,
    should_enter,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("trader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

MARKET_OPEN  = time(9, 0)
MARKET_CLOSE = time(15, 30)


def _is_market_hours() -> bool:
    now = datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def run_strategy(cfg: dict, api: KISApi) -> None:
    name = cfg["NAME"]
    log.info("===== [%s] 전략 실행: %s =====", name, date.today())

    if not _is_market_hours():
        log.info("[%s] 장 외 시간 — 스킵", name)
        return

    state = load_state(cfg["STATE_FILE"])

    # ── 시세 수집 ──────────────────────────────────────────────────────
    lever_price = api.get_price(cfg["TICKER"])
    close = lever_price["close"]
    high  = lever_price["high"]
    low   = lever_price["low"]
    log.info("[%s] 현재가: %s원  고가: %s원  저가: %s원",
             name, f"{close:,.0f}", f"{high:,.0f}", f"{low:,.0f}")

    ohlcv = api.get_daily_ohlcv(cfg["BASE_TICKER"], n=config.MA_PERIOD + 5)
    ma60  = calc_ma60(ohlcv, config.MA_PERIOD)
    base_close = float(ohlcv[0]["stck_clpr"]) if ohlcv else 0.0
    log.info("[%s] 기준ETF: %s원  MA%d: %s",
             name, f"{base_close:,.0f}", config.MA_PERIOD,
             f"{ma60:,.0f}" if ma60 else "계산불가")

    # ── 잔고 조회 ──────────────────────────────────────────────────────
    balance  = api.get_balance()
    cash     = balance["cash"]
    holdings = balance["holdings"]

    # 보유 수량 동기화 (수동 매도 등 감지)
    if state["in_trade"]:
        actual_qty = holdings.get(cfg["TICKER"], {}).get("qty", 0)
        if actual_qty == 0 and state["hold_qty"] > 0:
            log.warning("[%s] 잔고 불일치 감지 (보유: 0주) — 상태 초기화", name)
            notifier.send(f"[{name}] ⚠️ 잔고 불일치 감지 — 상태 자동 초기화")
            state = DEFAULT_STATE.copy()
            save_state(state, cfg["STATE_FILE"])
            return
        state["hold_qty"] = actual_qty

    log.info("[%s] 포지션: %s  예수금: %s원",
             name, "보유" if state["in_trade"] else "없음", f"{cash:,}")

    # ── 포지션 없음: 진입 판단 ─────────────────────────────────────────
    if not state["in_trade"]:
        if ma60 is None:
            log.warning("[%s] MA60 데이터 부족 — 진입 보류", name)
            return

        cooldown_active = is_cooldown_active(state)
        log.info("[%s] 쿨다운: %s", name, state.get("cooldown_end") or "없음")

        if should_enter(base_close, ma60, state, cooldown_active):
            invest_amt = int(cash * config.INVEST_RATIO)
            qty = invest_amt // int(close)
            if qty <= 0:
                log.warning("[%s] 매수 수량 0 — 스킵 (예수금 부족)", name)
                return

            api.buy_market(cfg["TICKER"], qty)

            state["in_trade"]    = True
            state["entry_price"] = close
            state["peak_price"]  = close
            state["half_sold"]   = False
            state["entry_qty"]   = qty
            state["hold_qty"]    = qty
            state["entry_date"]  = date.today().isoformat()
            state["cooldown_end"] = ""

            msg = (f"[매수] {cfg['TICKER']} / {qty}주 / "
                   f"진입가 {close:,.0f}원")
            log.info("[%s] %s", name, msg)
            notifier.send(f"[{name}] {msg}")
        else:
            reason = (
                "쿨다운 중" if cooldown_active
                else f"MA{config.MA_PERIOD}({ma60:,.0f}) > 현재가({base_close:,.0f})"
            )
            msg = f"[대기] {reason}"
            log.info("[%s] %s", name, msg)
            notifier.send(f"[{name}] {msg}")

    # ── 포지션 있음: 청산 판단 ─────────────────────────────────────────
    else:
        # 고점 갱신
        if high > state["peak_price"]:
            state["peak_price"] = high

        entry = state["entry_price"]
        pct = (close / entry - 1) * 100
        log.info("[%s] 진입가: %s원  손절선: %s원  절반익절: %s원  트레일: %s원",
                 name,
                 f"{entry:,.0f}",
                 f"{entry * (1 + cfg['STOP_LOSS']):,.0f}",
                 f"{entry * (1 + cfg['TAKE_PROFIT_HALF']):,.0f}",
                 f"{state['peak_price'] * (1 + cfg['TRAIL_STOP']):,.0f}")

        exit_reason, exit_price = check_exit(cfg, close, high, low, state)

        if exit_reason == "STOP_LOSS":
            qty = state["hold_qty"]
            api.sell_market(cfg["TICKER"], qty)
            pct = (exit_price / entry - 1) * 100
            state = DEFAULT_STATE.copy()
            state["cooldown_end"] = get_cooldown_end_date(cfg["COOLDOWN_DAYS"])
            msg = (f"[매도-손절] {cfg['TICKER']} / {qty}주 / "
                   f"{exit_price:,.0f}원 / {pct:+.1f}%")
            log.info("[%s] %s  쿨다운: %s", name, msg, state["cooldown_end"])
            notifier.send(f"[{name}] {msg}\n쿨다운: {state['cooldown_end']}까지")

        elif exit_reason == "HALF_TP":
            qty = state["hold_qty"] // 2
            if qty > 0:
                api.sell_market(cfg["TICKER"], qty)
                state["hold_qty"] -= qty
                state["half_sold"] = True
                pct = (exit_price / entry - 1) * 100
                msg = (f"[매도-절반익절] {cfg['TICKER']} / {qty}주 / "
                       f"{exit_price:,.0f}원 / {pct:+.1f}%")
                log.info("[%s] %s  잔여: %d주", name, msg, state["hold_qty"])
                notifier.send(f"[{name}] {msg}\n잔여 {state['hold_qty']}주 보유 중")

        elif exit_reason == "BREAK_EVEN_STOP":
            qty = state["hold_qty"]
            api.sell_market(cfg["TICKER"], qty)
            pct = (exit_price / entry - 1) * 100
            state = DEFAULT_STATE.copy()
            msg = (f"[매도-본전스탑] {cfg['TICKER']} / {qty}주 / "
                   f"{exit_price:,.0f}원 / {pct:+.1f}%")
            log.info("[%s] %s", name, msg)
            notifier.send(f"[{name}] {msg}")

        elif exit_reason == "TRAIL_STOP":
            qty = state["hold_qty"]
            api.sell_market(cfg["TICKER"], qty)
            pct = (exit_price / entry - 1) * 100
            state = DEFAULT_STATE.copy()
            msg = (f"[매도-트레일링] {cfg['TICKER']} / {qty}주 / "
                   f"{exit_price:,.0f}원 / {pct:+.1f}%")
            log.info("[%s] %s", name, msg)
            notifier.send(f"[{name}] {msg}")

        else:
            msg = (f"[홀딩] {cfg['TICKER']} / "
                   f"현재가 {close:,.0f}원 / 진입가 {entry:,.0f}원 / {pct:+.1f}%")
            log.info("[%s] %s", name, msg)
            notifier.send(f"[{name}] {msg}")

    save_state(state, cfg["STATE_FILE"])
    log.info("[%s] 상태 저장 완료", name)


def run_all(api: KISApi) -> None:
    for cfg in config.STRATEGIES.values():
        try:
            run_strategy(cfg, api)
        except Exception as e:
            log.error("[%s] 오류 발생: %s", cfg["NAME"], e, exc_info=True)
            notifier.send(f"[에러] {cfg['NAME']}: {e}")


def print_status() -> None:
    for key, cfg in config.STRATEGIES.items():
        state = load_state(cfg["STATE_FILE"])
        print(f"\n── {cfg['NAME']} ({key}) ──")
        print(json.dumps(state, indent=2, ensure_ascii=False))


def reset_state(key: str) -> None:
    targets = list(config.STRATEGIES.keys()) if key == "all" else [key]
    for k in targets:
        cfg = config.STRATEGIES[k]
        save_state(DEFAULT_STATE.copy(), cfg["STATE_FILE"])
        print(f"[{cfg['NAME']}] 상태 초기화 완료")


def main() -> None:
    parser = argparse.ArgumentParser(description="Snowball 자동매매 봇")
    parser.add_argument("--env", default="real", choices=["real", "demo"],
                        help="실전(real) / 모의(demo)")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run-once", help="1회 즉시 실행")
    p_run.add_argument("strategy", nargs="?", default="all",
                       choices=["all", "it", "ship"])

    sub.add_parser("status", help="현재 포지션 상태 출력")

    p_reset = sub.add_parser("reset", help="상태 초기화")
    p_reset.add_argument("strategy", choices=["all", "it", "ship"])

    args = parser.parse_args()

    if args.cmd == "status":
        print_status()
        return

    if args.cmd == "reset":
        reset_state(args.strategy)
        return

    api = KISApi(env_dv=args.env)

    if args.cmd == "run-once":
        if args.strategy == "all":
            run_all(api)
        else:
            cfg = config.STRATEGIES[args.strategy]
            try:
                run_strategy(cfg, api)
            except Exception as e:
                log.error("[%s] 오류 발생: %s", cfg["NAME"], e, exc_info=True)
                notifier.send(f"[에러] {cfg['NAME']}: {e}")
        return

    # 인자 없음 → 스케줄러 모드
    import schedule

    def job():
        if datetime.now().weekday() < 5:  # 평일만
            run_all(api)

    schedule.every().day.at("09:05").do(job)
    schedule.every().day.at("13:00").do(job)
    schedule.every().day.at("14:50").do(job)

    log.info("스케줄러 시작 (09:05 / 13:00 / 14:50, 평일)")
    while True:
        schedule.run_pending()
        import time as _time
        _time.sleep(30)


if __name__ == "__main__":
    main()
