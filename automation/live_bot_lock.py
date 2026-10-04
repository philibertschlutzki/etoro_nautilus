"""Issue #1358 (GH #1254, P0) — Live-Bot-Lebenszyklus: genau EIN Bot je Konto.

Vor diesem Modul startete ``daily_orchestrator.phase5_live_deployment`` bei jedem (täglichen) Lauf
einen weiteren ``momentum_ls_run.py``-Prozess und schrieb eine PID-Datei, die nirgends gelesen wurde:
nach n Tagen liefen n Bots gegen dasselbe Konto (kumulierte Ziel-Exposure n · 0,6), alle schrieben
denselben ``etoro_state_manager``-Zustand. Dieses Modul liefert die gemeinsame, von Bot UND
Orchestrator genutzte Sperr-Mechanik:

* ``LiveBotLock`` — exklusive ``fcntl.flock(LOCK_EX | LOCK_NB)`` auf ``data/state/live_bot.lock``,
  gehalten über die Lebensdauer des Bot-Prozesses (das Betriebssystem gibt sie bei JEDEM Prozessende
  frei, auch bei ``kill -9`` — anders als eine PID-Datei gibt es keine verwaiste Sperre). Inhalt:
  ``{pid, started_utc, environment, whitelist_sha256}``.
* ``compute_whitelist_sha256`` — Fingerabdruck dessen, was der Bot handelt (Paar → Strategie →
  ``live_params_sha256``, Issue #1360); NICHT der täglich wechselnden Kennzahlen.
* ``reconcile_live_bot`` — die Phase-5-Entscheidung (Start / unverändert / Neustart / Stopp bei
  Demotion) als reine, mit injizierbaren Seiteneffekten testbare Funktion.

Bewusst OHNE ``nautilus_trader``-Import (importierbar aus dem Orchestrator und aus Tests).
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = PROJECT_ROOT / "data" / "state" / "live_bot.lock"

#: Exit-Code ``momentum_ls_run`` bei bereits gehaltener Sperre (Event ``LIVE_BOT_ALREADY_RUNNING``).
EXIT_ALREADY_RUNNING = 4

DEFAULT_STOP_TIMEOUT_S = 120.0
SHUTDOWN_POLICIES = ("keep", "flatten")


class LiveBotAlreadyRunning(RuntimeError):
    """Die exklusive Sperre wird bereits gehalten. ``info`` = Sperr-Inhalt des Halters (oder ``{}``)."""

    def __init__(self, info: dict | None = None) -> None:
        self.info = dict(info or {})
        super().__init__(f"live_bot.lock bereits gehalten: {self.info!r}")


def compute_whitelist_sha256(per_symbol_winners: dict) -> str:
    """SHA-256 über die handelsrelevante Projektion der Whitelist: je Symbol ``(Strategie,
    live_params_sha256)``, sortiert. Bewusst NICHT über die gesamten Einträge — OOS-Kennzahlen,
    Zeitstempel und Klausel-Details wechseln täglich und würden jeden Lauf zu einem Neustart machen;
    eine geänderte Strategie oder ein geänderter Parametersatz (``live_params_sha256``, #1360) MUSS
    dagegen einen Neustart auslösen."""
    projection = sorted(
        (
            str(symbol),
            str((entry or {}).get("strategy")),
            (entry or {}).get("live_params_sha256"),
        )
        for symbol, entry in (per_symbol_winners or {}).items()
    )
    blob = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def read_lock_info(lock_path: Path = LOCK_PATH) -> dict | None:
    """Liest den Sperr-Inhalt (``None`` bei fehlender/leerer/defekter Datei). Aussage über den
    LETZTEN Halter — ob er noch lebt, beantwortet nur ``lock_is_held``."""
    try:
        raw = Path(lock_path).read_text("utf-8")
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def lock_is_held(lock_path: Path = LOCK_PATH) -> bool:
    """True ⇔ ein lebender Prozess hält die exklusive Sperre (nicht-blockierender Probe-Versuch)."""
    path = Path(lock_path)
    if not path.exists():
        return False
    try:
        fd = os.open(str(path), os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                return True
            raise
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


class LiveBotLock:
    """Exklusive Prozess-Sperre. ``acquire`` wirft ``LiveBotAlreadyRunning``, wenn sie gehalten wird;
    sonst bleibt der Dateideskriptor (und damit die Sperre) bis ``release``/Prozessende offen."""

    def __init__(self, lock_path: Path = LOCK_PATH) -> None:
        self.path = Path(lock_path)
        self._fd: int | None = None
        self.info: dict = {}

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, *, environment: str, whitelist_sha256: str | None,
                pid: int | None = None, now: datetime | None = None) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise LiveBotAlreadyRunning(read_lock_info(self.path)) from exc
            raise
        info = {
            "pid": int(pid if pid is not None else os.getpid()),
            "started_utc": (now or datetime.now(timezone.utc)).isoformat(),
            "environment": environment,
            "whitelist_sha256": whitelist_sha256,
        }
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.dumps(info).encode("utf-8"), 0)
        os.fsync(fd)
        self._fd = fd
        self.info = info
        return info

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            os.ftruncate(self._fd, 0)
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


# ─── Phase-5-Abgleich ──────────────────────────────────────────────────────────

@dataclass
class ReconcileResult:
    """Ergebnis von ``reconcile_live_bot``. ``start_new`` ⇔ der Aufrufer darf JETZT genau einen
    neuen Bot starten. ``events`` = ``(event_type, payload)``-Paare zum Emittieren."""
    action: str
    start_new: bool
    holder_pid: int | None = None
    events: list[tuple[str, dict]] = field(default_factory=list)


def send_sigterm_and_wait(
    lock_path: Path, pid: int, *, timeout_s: float, poll_s: float = 0.5,
    kill: Callable[[int, int], None] = os.kill, sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """SIGTERM an den Sperrinhaber, dann warten, bis die Sperre frei ist (max. ``timeout_s``).
    True ⇔ die Sperre wurde freigegeben. Ein bereits verschwundener Prozess zählt als Erfolg."""
    try:
        kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        return not lock_is_held(lock_path)
    deadline = monotonic() + float(timeout_s)
    while monotonic() < deadline:
        if not lock_is_held(lock_path):
            return True
        sleep(poll_s)
    return not lock_is_held(lock_path)


def reconcile_live_bot(
    lock_path: Path,
    desired_whitelist_sha256: str | None,
    *,
    stop_timeout_s: float = DEFAULT_STOP_TIMEOUT_S,
    stop_fn: Callable[..., bool] = send_sigterm_and_wait,
    reason: str | None = None,
) -> ReconcileResult:
    """Die Phase-5-Entscheidung über den laufenden Bot (Issue #1358 Fix Punkte 3 und 4).

    ``desired_whitelist_sha256 is None`` ⇒ HEUTE soll KEIN Bot handeln (Demotion, leere Whitelist,
    OOS-Gate verfehlt, 0 zulässige Paare): ein laufender Bot erhält SIGTERM
    (``LIVE_BOT_STOPPED_ON_DEMOTION``) — was heute nicht zugelassen ist, handelt heute nicht.

    Sonst: gleicher ``whitelist_sha256`` ⇒ ``LIVE_BOT_UNCHANGED``, KEIN Neustart. Abweichend ⇒
    SIGTERM, Warten bis zu ``stop_timeout_s`` auf die Freigabe der Sperre, dann ``start_new``;
    Timeout ⇒ ``stop_timeout`` ohne zweiten Start."""
    if not lock_is_held(lock_path):
        if desired_whitelist_sha256 is None:
            return ReconcileResult("nothing_running", start_new=False)
        return ReconcileResult("start", start_new=True)

    info = read_lock_info(lock_path) or {}
    pid = info.get("pid")
    holder_sha = info.get("whitelist_sha256")

    if desired_whitelist_sha256 is not None and holder_sha == desired_whitelist_sha256:
        return ReconcileResult(
            "unchanged", start_new=False, holder_pid=pid,
            events=[("LIVE_BOT_UNCHANGED", {"pid": pid, "whitelist_sha256": holder_sha})],
        )

    freed = False
    if isinstance(pid, int):
        freed = stop_fn(lock_path, pid, timeout_s=stop_timeout_s)
    payload = {
        "pid": pid, "holder_whitelist_sha256": holder_sha,
        "desired_whitelist_sha256": desired_whitelist_sha256,
        "stop_timeout_s": stop_timeout_s, "lock_released": bool(freed), "reason": reason,
    }
    if not freed:
        return ReconcileResult(
            "stop_timeout", start_new=False, holder_pid=pid,
            events=[("LIVE_BOT_STOP_TIMEOUT", payload)],
        )
    if desired_whitelist_sha256 is None:
        return ReconcileResult(
            "stopped_on_demotion", start_new=False, holder_pid=pid,
            events=[("LIVE_BOT_STOPPED_ON_DEMOTION", payload)],
        )
    return ReconcileResult(
        "restart", start_new=True, holder_pid=pid,
        events=[("LIVE_BOT_RESTART_REQUESTED", payload)],
    )
