"""Cron-Check fuer den Live-Bot (``*/5 * * * * cd ~/etoro_nautilus && venv/bin/python3 -m automation.bot_watchdog``).

Prueft, ob der Bot lebt (``data/state/live_bot.lock`` gehalten) oder gerade der Orchestrator in der Datenphase
laeuft, ob der Bot-Log noch geschrieben wird, und erzwingt das 5-GiB-Budget fuer ``logs/``. Meldet nur bei
Zustandswechsel (kein Alarm-Sturm alle 5 Minuten) in ``logs/errors/bot_watchdog_errors.log`` und startet
den Bot NICHT selbst neu. Exit-Code: 0 = ok/Orchestrator laeuft, 1 = Bot down oder haengt.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from automation.live_bot_lock import LOCK_PATH, lock_is_held, read_lock_info
from automation.log_budget import append_error, enforce_log_budget

STATE_PATH = Path("data/state/bot_watchdog.json")
HUNG_AFTER_S = 30 * 60
ORCHESTRATOR_MARKER = "daily_orchestrator"
STATUS_FILE = "bot_watchdog_status.log"


def orchestrator_running(proc: Path = Path("/proc")) -> bool:
    me = os.getpid()
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if ORCHESTRATOR_MARKER in cmd and "python" in cmd and "papertrading" in cmd:
            return True
    return False


def newest_bot_log_age(log_dir: Path, now: float) -> float | None:
    logs = list(log_dir.glob("bot_*.log"))
    if not logs:
        return None
    return now - max(p.stat().st_mtime for p in logs)


def classify(*, lock_held: bool, orchestrator: bool, log_age_s: float | None) -> str:
    if lock_held:
        if log_age_s is not None and log_age_s > HUNG_AFTER_S:
            return "HUNG"
        return "UP"
    return "STARTING" if orchestrator else "DOWN"


def run(log_dir: Path = Path("logs"), state_path: Path = STATE_PATH, lock_path: Path = LOCK_PATH,
        now: float | None = None, orchestrator: bool | None = None) -> str:
    now = time.time() if now is None else now
    budget = enforce_log_budget(log_dir, now=now)
    status = classify(
        lock_held=lock_is_held(lock_path),
        orchestrator=orchestrator_running() if orchestrator is None else orchestrator,
        log_age_s=newest_bot_log_age(log_dir, now),
    )
    try:
        previous = json.loads(state_path.read_text("utf-8")).get("status")
    except (OSError, ValueError):
        previous = None
    if status != previous:
        info = read_lock_info(lock_path) or {}
        line = f"BOT_{status}: vorher {previous}, Lock-Info {info}"
        if status in ("DOWN", "HUNG"):
            append_error(log_dir, "bot_watchdog_errors.log", line)
        else:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / STATUS_FILE, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now(timezone.utc).isoformat()} {line}\n")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"status": status, "since_utc": datetime.now(timezone.utc).isoformat()}),
                              "utf-8")
    if budget["deleted"]:
        print(f"log-budget: {len(budget['deleted'])} Dateien geloescht")
    return status


def main() -> int:
    status = run()
    print(status)
    return 1 if status in ("DOWN", "HUNG") else 0


if __name__ == "__main__":
    sys.exit(main())
