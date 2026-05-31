from datetime import date, timedelta
from typing import Optional, Tuple


def calc_ma60(ohlcv: list[dict], period: int = 60) -> Optional[float]:
    """최근 period일 종가 평균. 데이터 부족 시 None."""
    if len(ohlcv) < period:
        return None
    closes = [float(row["stck_clpr"]) for row in ohlcv[:period]]
    return sum(closes) / period


def is_cooldown_active(state: dict) -> bool:
    end = state.get("cooldown_end", "")
    if not end:
        return False
    return date.today().isoformat() < end


def get_cooldown_end_date(n_days: int) -> str:
    """오늘부터 n 거래일(주말 제외) 후 날짜."""
    d = date.today()
    count = 0
    while count < n_days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.isoformat()


def should_enter(base_close: float, ma60: float, state: dict, cooldown_active: bool) -> bool:
    if state["in_trade"]:
        return False
    if cooldown_active:
        return False
    return base_close > ma60


def check_exit(
    cfg: dict,
    close: float,
    high: float,
    low: float,
    state: dict,
) -> Tuple[Optional[str], Optional[float]]:
    """
    청산 조건 체크. (exit_reason, exit_price) 반환.
    우선순위: STOP_LOSS → HALF_TP → BREAK_EVEN_STOP → TRAIL_STOP
    """
    entry = state["entry_price"]
    peak  = state["peak_price"]
    half_sold = state["half_sold"]

    if low <= entry * (1 + cfg["STOP_LOSS"]):
        return "STOP_LOSS", entry * (1 + cfg["STOP_LOSS"])

    if not half_sold and high >= entry * (1 + cfg["TAKE_PROFIT_HALF"]):
        return "HALF_TP", entry * (1 + cfg["TAKE_PROFIT_HALF"])

    if half_sold and low <= entry:
        return "BREAK_EVEN_STOP", entry

    if half_sold and low <= peak * (1 + cfg["TRAIL_STOP"]):
        return "TRAIL_STOP", peak * (1 + cfg["TRAIL_STOP"])

    return None, None
