import logging
import os

import requests

log = logging.getLogger(__name__)

_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send(msg: str) -> None:
    """텔레그램 메시지 발송. 토큰 미설정 시 skip. 실패해도 예외 미전파."""
    if not _TOKEN or not _CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": msg},
            timeout=5,
        )
    except Exception as e:
        log.warning("텔레그램 발송 실패: %s", e)
