"""Issue #795 (Speicher-Katalog #794-#800) — hartes, durchgesetztes Speicher-Budget je Lauf.

Root-Cause: der Speicherverbrauch von ``data/optimizer`` ist eine unkontrollierte Konsequenz der
Trial-Zahl. Repo-weit verifiziert existiert kein Aufruf von ``shutil.disk_usage``/``statvfs`` oder
einer vergleichbaren Prüfung — weder eine Preflight-Schätzung noch eine laufende Messung noch eine
Obergrenze. Ein Lauf endet dadurch in ``sqlite3.OperationalError: database or disk is full`` statt
kontrolliert.

Dieses Modul liefert drei Bausteine:
  * ``measure_usage``/``free_bytes`` — die beiden rohen Messungen (TTL-gecached, siehe
    ``measure_usage``-Docstring).
  * ``check_budget`` — ``STATUS_OK``/``STATUS_PRESSURE``/``STATUS_EXCEEDED`` aus beiden Messungen.
  * ``assert_preflight_budget`` — die Vorab-Schätzung VOR dem ersten Trial (siehe sweep.py).

``sweep_abort_requested`` ist ein prozessweites ``threading.Event``: der laufende
``run_optimization.disk_budget_callback`` setzt es bei ``STATUS_EXCEEDED``; der Sweep-Dispatcher
(``sweep.py``, #799) prüft es zwischen zwei Symbolen für ein geordnetes Lauf-Ende statt eines
harten ``ENOSPC``-Absturzes.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

# Issue #795 — einfache String-Konstanten statt enum.Enum: Konvention dieses Repos (event_type,
# rejection_reason, seed_source, ... sind ebenfalls Strings, siehe reward.py/parsing.py).
STATUS_OK = "OK"
STATUS_PRESSURE = "PRESSURE"
STATUS_EXCEEDED = "EXCEEDED"

# Issue #795 — prozessweites Signal fuer ein geordnetes Sweep-Ende (siehe Modul-Docstring).
sweep_abort_requested = threading.Event()

_usage_cache: dict[str, tuple[float, int]] = {}
_USAGE_CACHE_TTL_S = 30.0

GIB = 1024 ** 3


class DiskBudgetExceededError(RuntimeError):
    """Issue #795 — Preflight-Abbruch VOR dem ersten Trial: der geschätzte Speicherbedarf des
    geplanten Laufs übersteigt das Budget oder den freien Platz."""


def measure_usage(work_dir: Path, *, use_cache: bool = True) -> int:
    """Summiert die Bytegröße aller Dateien unter ``work_dir`` (``os.walk``, rekursiv — erfasst
    sowohl den Trial-Baum als auch ``sweep/*.db``, siehe Issue-Text "Optuna-Storage einbeziehen").
    TTL-gecached (30s) je ``work_dir``-Pfad, damit ein häufig aufgerufener Callback nicht bei jedem
    Trial den kompletten Baum erneut durchläuft (bei zehntausenden Verzeichnissen nicht kostenlos).
    ``use_cache=False`` erzwingt eine frische Messung (z. B. der Preflight will den AKTUELLEN
    Stand, nicht einen bis zu 30s alten)."""
    key = str(Path(work_dir))
    now = time.monotonic()
    if use_cache and key in _usage_cache:
        ts, cached_val = _usage_cache[key]
        if now - ts < _USAGE_CACHE_TTL_S:
            return cached_val
    total = 0
    if Path(work_dir).exists():
        for dirpath, _dirnames, filenames in os.walk(work_dir):
            for name in filenames:
                fp = os.path.join(dirpath, name)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    continue  # Datei zwischen listdir und stat verschwunden (Race) -> ignorieren
    _usage_cache[key] = (now, total)
    return total


def free_bytes(work_dir: Path) -> int:
    """Freier Speicherplatz (Bytes) auf dem Dateisystem, das ``work_dir`` trägt."""
    return shutil.disk_usage(work_dir).free


def check_budget(work_dir: Path, *, budget_gb: float, reserve_gb: float,
                 use_cache: bool = True) -> str:
    """Liefert ``STATUS_OK``/``STATUS_PRESSURE``/``STATUS_EXCEEDED`` aus dem aktuellen Verbrauch
    (``measure_usage``) UND dem freien Platz (``free_bytes``):

      * ``EXCEEDED`` ⇔ ``used >= budget_gb·2³⁰`` ODER ``free < reserve_gb·2³⁰``.
      * ``PRESSURE`` ⇔ ``used >= 0.8·budget_gb·2³⁰`` ODER ``free < 2·reserve_gb·2³⁰``.
      * sonst ``OK``.
    """
    budget_bytes = budget_gb * GIB
    reserve_bytes = reserve_gb * GIB
    used = measure_usage(work_dir, use_cache=use_cache)
    free = free_bytes(work_dir)

    if used >= budget_bytes or free < reserve_bytes:
        return STATUS_EXCEEDED
    if used >= 0.8 * budget_bytes or free < 2 * reserve_bytes:
        return STATUS_PRESSURE
    return STATUS_OK


def estimate_expected_bytes(n_trials: int, bytes_per_trial_estimate: float) -> int:
    """Reine Schätzfunktion für den Preflight: ``n_trials · bytes_per_trial_estimate``."""
    return int(n_trials * bytes_per_trial_estimate)


def assert_preflight_budget(work_dir: Path, *, expected_bytes: int, budget_gb: float,
                            reserve_gb: float) -> None:
    """Issue #795 — Preflight VOR dem ersten Trial: wirft ``DiskBudgetExceededError``, wenn der
    GEPLANTE Lauf (``expected_bytes``, aus ``estimate_expected_bytes``) das Budget übersteigt ODER
    den freien Platz unter die Reserve drücken würde. Fragt bewusst "passt der geplante Lauf?",
    nicht "wie voll ist es gerade?" (das ist ``check_budget``, laufend während des Sweeps)."""
    budget_bytes = budget_gb * GIB
    reserve_bytes = reserve_gb * GIB
    free = free_bytes(work_dir)

    if expected_bytes > budget_bytes:
        raise DiskBudgetExceededError(
            f"[#795] Geschätzter Speicherbedarf des geplanten Laufs "
            f"({expected_bytes / GIB:.1f} GB) übersteigt das konfigurierte Budget "
            f"({budget_gb:.1f} GB, optimizer.json['disk_budget_gb']). Handlungsempfehlung: "
            f"disk_budget_gb erhöhen, ODER --tier/die Symbolmenge reduzieren, ODER n_trials senken."
        )
    if expected_bytes > free - reserve_bytes:
        raise DiskBudgetExceededError(
            f"[#795] Geschätzter Speicherbedarf des geplanten Laufs "
            f"({expected_bytes / GIB:.1f} GB) würde den freien Platz ({free / GIB:.1f} GB) unter "
            f"die konfigurierte Reserve ({reserve_gb:.1f} GB, optimizer.json['disk_reserve_gb']) "
            f"drücken. Handlungsempfehlung: Platz schaffen, ODER --tier/die Symbolmenge "
            f"reduzieren, ODER disk_reserve_gb senken (falls die Reserve zu konservativ gewählt ist)."
        )


def reset_for_tests() -> None:
    """Test-Helper: leert den Usage-Cache und setzt ``sweep_abort_requested`` zurück (verhindert
    Zustandslecks zwischen Tests, die denselben Prozess/dieselbe Event-Instanz teilen)."""
    _usage_cache.clear()
    sweep_abort_requested.clear()
