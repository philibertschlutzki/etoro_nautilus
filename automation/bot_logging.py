"""Datei-Logging und Exit-Diagnose fuer den Live-Bot.

* ``logs/bot_YYYYMMDD.log`` — alle INFO+ Zeilen des Bots (rotierend), damit der Bot auch ohne Umleitung
  der Orchestrator-Ausgabe nachvollziehbar bleibt.
* ``logs/errors/bot_errors.log`` — nur WARNING+ (ERROR/CRITICAL), wird vom Platzbudget nie geloescht.
* Stille Tode: ``faulthandler`` schreibt Python-Tracebacks bei SIGSEGV/SIGABRT/SIGBUS/SIGFPE/SIGILL nach
  ``logs/errors/bot_fatal.log``; SIGHUP/SIGPIPE werden geloggt und ignoriert (SIGTERM/SIGINT behandelt der Shutdown-Koordinator), ``atexit``
  und ``sys.excepthook`` schreiben das Ende. Ein SIGKILL laesst sich nicht abfangen — dann fehlt genau
  diese Endzeile, und der Watchdog meldet den Ausfall.
"""
from __future__ import annotations

import atexit
import faulthandler
import logging
import logging.handlers
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from automation.log_budget import error_log_dir

FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_installed = False
_fatal_file = None


def install_bot_logging(log_dir: Path | str = "logs", *, logger: logging.Logger | None = None) -> Path:
    """Haengt Datei-Handler an den Root-Logger. Idempotent. Rueckgabe: Pfad des Bot-Logs."""
    global _installed, _fatal_file
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"bot_{datetime.now(timezone.utc):%Y%m%d}.log"
    if _installed:
        return log_path
    root = logging.getLogger()
    fmt = logging.Formatter(FMT)
    main_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8")
    main_handler.setLevel(logging.INFO)
    main_handler.setFormatter(fmt)
    err_handler = logging.handlers.RotatingFileHandler(
        error_log_dir(log_dir) / "bot_errors.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    err_handler.setLevel(logging.WARNING)
    err_handler.setFormatter(fmt)
    root.addHandler(main_handler)
    root.addHandler(err_handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    log = logger or logging.getLogger("bot_logging")

    try:
        _fatal_file = open(error_log_dir(log_dir) / "bot_fatal.log", "a", buffering=1, encoding="utf-8")
        _fatal_file.write(f"--- Bot-Start {datetime.now(timezone.utc).isoformat()} PID {os.getpid()}\n")
        faulthandler.enable(file=_fatal_file, all_threads=True)
    except OSError as exc:
        log.warning("faulthandler nicht aktiviert: %s", exc)

    def _excepthook(exc_type, exc, tb):
        log.critical("UNBEHANDELTE EXCEPTION im Hauptthread — Bot endet", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args):
        log.error("Unbehandelte Exception im Thread %s", getattr(args.thread, "name", "?"),
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook
    atexit.register(lambda: log.warning("PROZESS-ENDE (atexit) PID %s", os.getpid()))
    log.info("Bot-Logging aktiv: %s, Errors: %s, PID %s, PPID %s", log_path,
             error_log_dir(log_dir) / "bot_errors.log", os.getpid(), os.getppid())
    _installed = True
    return log_path


def log_fatal_signals(logger: logging.Logger) -> None:
    """Loggt SIGHUP/SIGPIPE (SIGTERM/SIGINT behandelt der Shutdown-Koordinator). Ein HUP vom
    Terminal oder ein PIPE auf der Ausgabe war bisher ein lautloser Tod; jetzt steht er im Log und der
    Bot laeuft weiter (SIGHUP/SIGPIPE werden ignoriert, statt ihn zu beenden)."""
    def make(name: str):
        def handler(signum, _frame):
            logger.error("SIGNAL %s empfangen (PID %s, PPID %s) — ignoriert", name, os.getpid(), os.getppid())
        return handler

    for name in ("SIGHUP", "SIGPIPE"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, make(name))
            except (ValueError, OSError):
                pass
