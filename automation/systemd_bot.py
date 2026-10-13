"""Bot als systemd-User-Dienst: Start/Stopp über ``systemctl --user`` statt ``subprocess.Popen`` im Orchestrator.

Hintergrund: Ein per ``Popen`` gestarteter Bot hat keinen Aufpasser; fällt er aus (mehrfach passiert, zuletzt per
SIGKILL), bleibt er weg, bis jemand ihn neu startet. Mit ``PAPER_BOT_SYSTEMD_UNIT=etoro-paper-bot.service`` in der
Umgebung des Orchestrators übernimmt systemd (``Restart=always``) das Wiederanlaufen; der Orchestrator entscheidet nur
noch, WELCHE Whitelist gilt, und stoppt/startet den Dienst. Ohne die Variable ändert sich nichts.

Die Funktionen sind rein über injizierbares ``run`` testbar und rufen nie etwas anderes als ``systemctl --user``.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from automation.live_bot_lock import lock_is_held

UNIT_ENV = "PAPER_BOT_SYSTEMD_UNIT"
Run = Callable[..., subprocess.CompletedProcess]


def configured_unit(environ=None) -> str | None:
    """Name der Dienst-Unit, wenn der systemd-Modus aktiv ist (``PAPER_BOT_SYSTEMD_UNIT`` gesetzt)."""
    value = (environ if environ is not None else os.environ).get(UNIT_ENV, "").strip()
    return value or None


def systemctl_user(*args: str, run: Run = subprocess.run, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)


def is_active(unit: str, *, run: Run = subprocess.run) -> bool:
    return systemctl_user("is-active", "--quiet", unit, run=run, timeout=30.0).returncode == 0


def start_unit(unit: str, *, run: Run = subprocess.run) -> tuple[bool, str]:
    """``systemctl --user start``; (Erfolg, Fehlertext)."""
    proc = systemctl_user("start", unit, run=run, timeout=120.0)
    return proc.returncode == 0, (proc.stderr or proc.stdout or "").strip()[:300]


def make_stop_fn(unit: str, *, run: Run = subprocess.run, poll_s: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 held: Callable[[Path], bool] = lock_is_held) -> Callable[..., bool]:
    """Ersatz für ``live_bot_lock.send_sigterm_and_wait``: ``systemctl --user stop`` (SIGTERM, ``TimeoutStopSec`` der
    Unit, danach SIGKILL), dann warten, bis die Sperre frei ist. ``stop`` unterdrückt ``Restart=always`` für diesen
    Stopp. True ⇔ Sperre frei."""
    def stop_fn(lock_path: Path, pid: int | None = None, *, timeout_s: float) -> bool:
        systemctl_user("stop", unit, run=run, timeout=timeout_s + 30.0)
        deadline = monotonic() + max(0.0, float(timeout_s))
        while True:
            if not held(Path(lock_path)):
                return True
            if monotonic() >= deadline:
                return False
            sleep(poll_s)
    return stop_fn
