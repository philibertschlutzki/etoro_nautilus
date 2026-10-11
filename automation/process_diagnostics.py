"""Absturz-Diagnose für den Bot-Prozess: macht stille Prozessenden im Bot-Log sichtbar.

Hintergrund: Der Paper-Bot ist mehrfach ohne jede Log-Zeile verschwunden. Ein nativer Absturz (SIGSEGV/SIGABRT in
Cython/Rust-Erweiterungen) oder ein Signal wie SIGHUP/SIGQUIT lässt Python sonst keine Spur. Dieses Modul
  * schaltet ``faulthandler`` ein (Python-Stack aller Threads bei SIGSEGV/SIGFPE/SIGABRT/SIGBUS/SIGILL; SIGUSR1 druckt
    den Stack auf Wunsch: ``kill -USR1 <pid>``),
  * protokolliert Start-Umgebung (PID, PPID, SID, PGID, TTY, Elternprozess) und Prozessende per ``atexit``,
  * protokolliert SIGHUP/SIGQUIT (mit Absender-PID wenn verfügbar) und beendet dann wie vorher.
SIGKILL ist nicht abfangbar; fehlt am Ende der Marker ``PROZESS-ENDE``/``Bot erfolgreich beendet`` im Log, war es
SIGKILL oder ein harter Absturz (das wertet ``automation/ops_guard.py`` aus).
"""
from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import signal
import sys

_installed = False


def _tty_name() -> str:
    try:
        return os.ttyname(0)
    except OSError:
        return "?"


def _parent_cmdline(ppid: int) -> str:
    try:
        with open(f"/proc/{ppid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()[:160] or "?"
    except OSError:
        return "?"


def install_crash_diagnostics(logger: logging.Logger) -> None:
    """Idempotent. Muss früh im Hauptthread des Bot-Prozesses aufgerufen werden."""
    global _installed
    if _installed:
        return
    _installed = True
    try:
        faulthandler.enable(file=sys.stderr, all_threads=True)
        if hasattr(signal, "SIGUSR1"):
            faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    except Exception as exc:  # Diagnose darf den Start nie verhindern
        logger.warning("[PROZESS] faulthandler nicht aktivierbar: %s", exc)

    ppid = os.getppid()
    try:
        sid, pgid = os.getsid(0), os.getpgid(0)
    except OSError:
        sid = pgid = -1
    logger.info("[PROZESS] Start pid=%s ppid=%s (%s) sid=%s pgid=%s tty=%s", os.getpid(), ppid,
                _parent_cmdline(ppid), sid, pgid, _tty_name())

    def _on_exit() -> None:
        try:
            logger.info("[PROZESS-ENDE] pid=%s endet regulär (atexit); Signal-/Absturz-Enden erscheinen nur als Signal-Zeile.",
                        os.getpid())
            for handler in logging.getLogger().handlers:
                handler.flush()
        except Exception:
            pass

    atexit.register(_on_exit)

    def _make_handler(name: str):
        def _handler(signum, _frame):
            try:
                logger.critical("[PROZESS] Signal %s (%s) empfangen — Prozess wird beendet.", name, signum)
                for handler in logging.getLogger().handlers:
                    handler.flush()
            finally:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)
        return _handler

    for name in ("SIGHUP", "SIGQUIT"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, _make_handler(name))
        except (ValueError, OSError) as exc:
            logger.warning("[PROZESS] Handler für %s nicht installierbar: %s", name, exc)
