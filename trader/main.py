import argparse
import json
import logging
import sys
import time as _time
import traceback
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
    return MARKET_OPEN <= datetime.now().time() <= MARKET_CLOSE


def _pct(a: float, b: float) -> str:
    return f"{(a / b - 1) * 100:+.1f}%"


def _sell_verified(api: KISApi, cfg: dict, qty: int, label: str,
                   ref_price: float | None = None, max_retries: int = 3) -> bool:
    """매도 주문 후 체결 확인. 미체결 + 현재가 < ref_price 이면 재시도."""
    remaining = qty
    for attempt in range(1, max_retries + 1):
        api.sell_market(cfg["TICKER"], remaining)
        _time.sleep(2)
        balance = api.get_balance()
        actual_qty = balance["holdings"].get(cfg["TICKER"], {}).get("qty", 0)
        if actual_qty == 0:
            return True

        remaining = actual_qty  # 부분 체결 대응: 실제 남은 수량으로 재시도

        if ref_price is not None and attempt < max_retries:
            cur = api.get_price(cfg["TICKER"])["close"]
            if cur < ref_price:
                warn = (
                    f"[{cfg['NAME']}] 매도 미체결 재시도 ({attempt}/{max_retries})\n"
                    f"사유: {label} / 잔고: {actual_qty}주\n"
                    f"현재가 {cur:,.0f} < 기준가 {ref_price:,.0f}"
                )
                log.warning(warn)
                notifier.send(warn)
                _time.sleep(5)
                continue

        msg = (
            f"[긴급] {cfg['NAME']} 매도 미체결\n"
            f"사유: {label}\n"
            f"주문: {qty}주 / 잔고: {actual_qty}주 남음\n"
            f"→ 수동 확인 필요"
        )
        log.error(msg)
        notifier.send(msg)
        return False
    return False


def run_strategy(cfg: dict, api: KISApi, invest_cash: int | None = None) -> None:
    name = cfg["NAME"]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    log.info("===== [%s] 전략 실행: %s =====", name, now_str)

    notifier.send(
        f"[{name}] 전략 실행 시작\n"
        f"시각: {now_str}"
    )

    if not _is_market_hours():
        log.info("[%s] 장 외 시간 — 스킵", name)
        notifier.send(f"[{name}] 장 외 시간 — 스킵")
        return

    state = load_state(cfg["STATE_FILE"])

    # ── 시세 수집 ──────────────────────────────────────────────────────
    lever_price = api.get_price(cfg["TICKER"])
    close = lever_price["close"]
    high  = lever_price["high"]
    low   = lever_price["low"]
    log.info("[%s] 현재가: %s원  고가: %s원  저가: %s원",
             name, f"{close:,.0f}", f"{high:,.0f}", f"{low:,.0f}")

    ohlcv      = api.get_daily_ohlcv(cfg["BASE_TICKER"], n=config.MA_PERIOD + 5)
    ma60       = calc_ma60(ohlcv, config.MA_PERIOD)
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
            notifier.send(
                f"[{name}] ⚠️ 잔고 불일치 감지\n"
                f"상태파일: {state['hold_qty']}주 / 실제 잔고: 0주\n"
                f"→ 상태 자동 초기화"
            )
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
            notifier.send(
                f"[{name}] ⚠️ MA60 데이터 부족\n"
                f"데이터: {len(ohlcv)}일치 (필요: {config.MA_PERIOD}일)\n"
                f"→ 진입 보류"
            )
            return

        cooldown_active = is_cooldown_active(state)
        log.info("[%s] 쿨다운: %s", name, state.get("cooldown_end") or "없음")

        if should_enter(base_close, ma60, state, cooldown_active):
            invest_amt = invest_cash if invest_cash is not None else int(cash * config.INVEST_RATIO)
            qty = invest_amt // int(close)
            if qty <= 0:
                log.warning("[%s] 매수 수량 0 — 스킵 (예수금 부족)", name)
                notifier.send(
                    f"[{name}] ⚠️ 예수금 부족 — 매수 스킵\n"
                    f"예수금: {cash:,}원\n"
                    f"투입예정: {invest_amt:,}원 (25%)\n"
                    f"현재가: {close:,.0f}원 → 0주"
                )
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

            sl_price = close * (1 + cfg["STOP_LOSS"])
            tp_price = close * (1 + cfg["TAKE_PROFIT_HALF"])
            notifier.send(
                f"[{name}] 매수 체결\n"
                f"종목: {cfg['TICKER']}\n"
                f"수량: {qty}주\n"
                f"진입가: {close:,.0f}원\n"
                f"투입금: {int(close * qty):,}원\n"
                f"────────────\n"
                f"손절선: {sl_price:,.0f}원 ({cfg['STOP_LOSS']*100:.0f}%)\n"
                f"절반익절: {tp_price:,.0f}원 (+{cfg['TAKE_PROFIT_HALF']*100:.0f}%)"
            )

        else:
            if cooldown_active:
                notifier.send(
                    f"[{name}] 대기 — 쿨다운 중\n"
                    f"쿨다운 종료: {state['cooldown_end']}\n"
                    f"기준ETF: {base_close:,.0f}원 / MA{config.MA_PERIOD}: {ma60:,.0f}원"
                )
            else:
                gap_pct = (base_close / ma60 - 1) * 100
                notifier.send(
                    f"[{name}] 대기 — 진입 조건 미충족\n"
                    f"기준ETF: {base_close:,.0f}원\n"
                    f"MA{config.MA_PERIOD}: {ma60:,.0f}원\n"
                    f"차이: {gap_pct:+.1f}% (0% 이상이면 진입)"
                )

    # ── 포지션 있음: 청산 판단 ─────────────────────────────────────────
    else:
        if high > state["peak_price"]:
            state["peak_price"] = high

        entry     = state["entry_price"]
        peak      = state["peak_price"]
        hold_qty  = state["hold_qty"]
        half_sold = state["half_sold"]

        sl_price    = entry * (1 + cfg["STOP_LOSS"])
        tp_price    = entry * (1 + cfg["TAKE_PROFIT_HALF"])
        trail_price = peak  * (1 + cfg["TRAIL_STOP"])
        cur_pct     = (close / entry - 1) * 100

        log.info("[%s] 진입가: %s원  손절선: %s원  절반익절: %s원  트레일: %s원",
                 name, f"{entry:,.0f}", f"{sl_price:,.0f}",
                 f"{tp_price:,.0f}", f"{trail_price:,.0f}")

        exit_reason, exit_price = check_exit(cfg, close, high, low, state)

        if exit_reason == "STOP_LOSS":
            qty = hold_qty
            if _sell_verified(api, cfg, qty, "손절", ref_price=sl_price):
                loss_amt = int((exit_price - entry) * qty)
                state = DEFAULT_STATE.copy()
                state["cooldown_end"] = get_cooldown_end_date(cfg["COOLDOWN_DAYS"])
                notifier.send(
                    f"[{name}] 손절 청산\n"
                    f"종목: {cfg['TICKER']}\n"
                    f"수량: {qty}주\n"
                    f"청산가: {exit_price:,.0f}원\n"
                    f"손익: {cfg['STOP_LOSS']*100:.0f}% ({loss_amt:,}원)\n"
                    f"────────────\n"
                    f"진입가: {entry:,.0f}원\n"
                    f"쿨다운: {state['cooldown_end']}까지"
                )

        elif exit_reason == "HALF_TP":
            qty = hold_qty // 2
            if qty > 0 and _sell_verified(api, cfg, qty, "절반익절", ref_price=tp_price):
                profit_amt = int((exit_price - entry) * qty)
                state["hold_qty"] -= qty
                state["half_sold"] = True
                notifier.send(
                    f"[{name}] 절반 익절\n"
                    f"종목: {cfg['TICKER']}\n"
                    f"매도: {qty}주\n"
                    f"청산가: {exit_price:,.0f}원\n"
                    f"수익: +{cfg['TAKE_PROFIT_HALF']*100:.0f}% (+{profit_amt:,}원)\n"
                    f"────────────\n"
                    f"잔여: {state['hold_qty']}주 보유 중\n"
                    f"본전스탑: {entry:,.0f}원\n"
                    f"트레일링: {trail_price:,.0f}원 (고점 {peak:,.0f}원 기준)"
                )

        elif exit_reason == "BREAK_EVEN_STOP":
            qty = hold_qty
            if _sell_verified(api, cfg, qty, "본전스탑", ref_price=entry):
                profit_amt = int((exit_price - entry) * qty)
                state = DEFAULT_STATE.copy()
                notifier.send(
                    f"[{name}] 본전 스탑 청산\n"
                    f"종목: {cfg['TICKER']}\n"
                    f"수량: {qty}주\n"
                    f"청산가: {exit_price:,.0f}원\n"
                    f"손익: {profit_amt:+,}원\n"
                    f"────────────\n"
                    f"진입가: {entry:,.0f}원 (절반 익절 후 본전 이탈)"
                )

        elif exit_reason == "TRAIL_STOP":
            qty = hold_qty
            if _sell_verified(api, cfg, qty, "트레일링", ref_price=trail_price):
                profit_amt = int((exit_price - entry) * qty)
                profit_pct = (exit_price / entry - 1) * 100
                state = DEFAULT_STATE.copy()
                notifier.send(
                    f"[{name}] 트레일링 스탑 청산\n"
                    f"종목: {cfg['TICKER']}\n"
                    f"수량: {qty}주\n"
                    f"청산가: {exit_price:,.0f}원\n"
                    f"손익: {profit_pct:+.1f}% (+{profit_amt:,}원)\n"
                    f"────────────\n"
                    f"진입가: {entry:,.0f}원\n"
                    f"고점: {peak:,.0f}원 ({_pct(peak, entry)} 대비 진입가)"
                )

        else:
            # 홀딩 유지 — 현재 상태 상세 리포트
            to_sl    = (close / sl_price - 1) * 100
            if half_sold:
                next_line = (
                    f"본전스탑: {entry:,.0f}원 (현재가 {_pct(close, entry)})\n"
                    f"트레일링: {trail_price:,.0f}원 ({_pct(trail_price, close)} 남음)"
                )
            else:
                to_tp = (tp_price / close - 1) * 100
                next_line = (
                    f"절반익절: {tp_price:,.0f}원 (+{to_tp:.1f}% 남음)\n"
                    f"트레일링: 절반 익절 후 활성화"
                )
            notifier.send(
                f"[{name}] 홀딩 유지\n"
                f"종목: {cfg['TICKER']}\n"
                f"현재가: {close:,.0f}원 ({cur_pct:+.1f}%)\n"
                f"고가: {high:,.0f}원 / 저가: {low:,.0f}원\n"
                f"────────────\n"
                f"진입가: {entry:,.0f}원 ({state['entry_date']})\n"
                f"보유: {hold_qty}주  절반익절: {'완료' if half_sold else '미완료'}\n"
                f"────────────\n"
                f"손절선: {sl_price:,.0f}원 ({to_sl:+.1f}% 남음)\n"
                f"{next_line}"
            )

    save_state(state, cfg["STATE_FILE"])
    log.info("[%s] 상태 저장 완료", name)


def run_all(api: KISApi) -> None:
    # 전략 실행 전 전체 예수금 스냅샷 — 각 전략에 25%씩 배분 (50% 현금 보유)
    try:
        total_cash = api.get_balance()["cash"]
        n_strategies = len(config.STRATEGIES)
        per_strategy = int(total_cash * 0.50 / n_strategies)
        log.info("총 예수금: %s원 → 전략당 투입 %s원 (각 25%%)", f"{total_cash:,}", f"{per_strategy:,}")
    except Exception as e:
        log.warning("잔고 선조회 실패, 개별 조회로 fallback: %s", e)
        per_strategy = None

    for cfg in config.STRATEGIES.values():
        try:
            run_strategy(cfg, api, invest_cash=per_strategy)
        except Exception as e:
            tb = traceback.format_exc()
            log.error("[%s] 오류 발생:\n%s", cfg["NAME"], tb)
            notifier.send(
                f"[에러] {cfg['NAME']}\n"
                f"{e}\n"
                f"────────────\n"
                f"{tb[-300:]}"  # 마지막 300자만
            )


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
                tb = traceback.format_exc()
                log.error("[%s] 오류 발생:\n%s", cfg["NAME"], tb)
                notifier.send(
                    f"[에러] {cfg['NAME']}\n"
                    f"{e}\n"
                    f"────────────\n"
                    f"{tb[-300:]}"
                )
        return

    # 인자 없음 → 스케줄러 모드
    import schedule

    def job():
        if datetime.now().weekday() < 5:
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
