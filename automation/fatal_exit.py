"""Harter Prozessausstieg mit Spur im Log.

Die WebSocket-Adapter beenden den Bot bei Verbindungsabbruch mit ``os._exit(1)`` (Absicht: ein Supervisor startet
neu). ``os._exit`` überspringt ``atexit`` und lässt den asynchronen Nautilus-Logger ungeschrieben: der Bot
verschwand deshalb ohne jede Zeile. ``hard_exit`` schreibt vorher Grund, Aufrufstelle und Exception über den
Python-Logger (Handler werden geleert) und nach ``logs/errors/bot_fatal.log``, gibt dem Nautilus-Logger einen
Moment zum Schreiben und beendet dann mit Exit-Code 1.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

FLUSH_GRACE_S = 0.5
FATAL_LOG = Path("logs") / "errors" / "bot_fatal.log"


def hard_exit(reason: str, *, code: int = 1) -> None:
    caller = sys._getframe(1)
    where = f"{Path(caller.f_code.co_filename).name}:{caller.f_code.co_name}:{caller.f_lineno}"
    exc = sys.exc_info()
    detail = "".join(traceback.format_exception(*exc)).strip() if exc[0] is not None else ""
    message = f"HARD_EXIT pid={os.getpid()} at {where}: {reason}" + (f"\n{detail}" if detail else "")
    try:
        logging.getLogger("fatal_exit").critical(message)
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass
    except Exception:
        pass
    try:
        FATAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(FATAL_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")
    except OSError:
        pass
    time.sleep(FLUSH_GRACE_S)
    os._exit(code)
