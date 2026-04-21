from __future__ import annotations

import os
import socket

from praxis_core.logging import get_logger

log = get_logger("observability.sd_notify")


def _send(message: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(message.encode("utf-8"))
        return True
    except OSError as e:
        log.warning("sd_notify.send_fail", message=message[:40], error=str(e))
        return False


def notify_ready() -> bool:
    return _send("READY=1\n")


def notify_watchdog() -> bool:
    return _send("WATCHDOG=1\n")


def notify_stopping() -> bool:
    return _send("STOPPING=1\n")
