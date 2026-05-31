import json
import os

DEFAULT_STATE: dict = {
    "in_trade":    False,
    "entry_price": 0.0,
    "peak_price":  0.0,
    "half_sold":   False,
    "entry_qty":   0,
    "hold_qty":    0,
    "entry_date":  "",
    "cooldown_end": "",
}


def load_state(state_file: str) -> dict:
    if not os.path.exists(state_file):
        return DEFAULT_STATE.copy()
    with open(state_file, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, state_file: str) -> None:
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
