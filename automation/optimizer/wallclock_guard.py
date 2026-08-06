"""Issue #828 Fix Punkt 5 (Katalog #828-#835, GitHub-Issue #751) — hartes Laufzeit-Budget je Sweep,
strukturgleich zu ``disk_guard`` (#795).

Root-Cause #828: ein 62-h-Hochrechnungslauf (122 Symbole, unveraendertes Budget) ohne harte
Obergrenze ist operativ nicht steuerbar — ``sweep_max_wallclock_h`` laesst kein neues Symbol mehr
beginnen, sobald die konfigurierte Laufzeit-Grenze erreicht ist, und beendet den Lauf GEORDNET
(bereits abgeschlossene Symbole bleiben als Proposals erhalten, ein Report entsteht weiterhin —
siehe #833) statt unkontrolliert bis zum Prozess-Kill weiterzulaufen.

``sweep_wallclock_exceeded`` ist ein prozessweites ``threading.Event``, analog
``disk_guard.sweep_abort_requested``: der Sweep-Dispatcher (``sweep.py``, #799-Transaktionsgrenze)
prueft es ZWISCHEN zwei Symbolen — laufende Studies werden nie abgebrochen, nur keine NEUEN mehr
gestartet."""
from __future__ import annotations

import json
import threading
from pathlib import Path

# Issue #828 — prozessweites Signal fuer ein geordnetes Sweep-Ende wegen Laufzeit-Ueberschreitung
# (getrennt von disk_guard.sweep_abort_requested, damit ein #833-Report den Abbruchgrund
# unterscheiden kann: aborted_wallclock vs. aborted_disk).
sweep_wallclock_exceeded = threading.Event()


def check_wallclock_budget(elapsed_s: float, *, max_hours: float | None) -> bool:
    """``True``, wenn die verstrichene Laufzeit (``elapsed_s``, Sekunden seit Sweep-Start) das
    konfigurierte Budget (``max_hours``, ``optimizer.json['sweep_max_wallclock_h']``) erreicht oder
    überschritten hat. ``max_hours=None`` (Key fehlt/ist ``null``) ⇒ IMMER ``False`` — kein Budget,
    bit-identisch zum Pre-#828-Verhalten (ein 62-h-Lauf lief bereits vor diesem Fix unbegrenzt)."""
    if max_hours is None:
        return False
    return elapsed_s >= float(max_hours) * 3600.0


def reset_for_tests() -> None:
    """Test-Helper: setzt ``sweep_wallclock_exceeded`` zurück (verhindert Zustandslecks zwischen
    Tests, die denselben Prozess/dieselbe Event-Instanz teilen, analog ``disk_guard.reset_for_
    tests``)."""
    sweep_wallclock_exceeded.clear()


# Issue #931 (Pitfall #304) — der Disk-Preflight kannte ``expected_trials`` eine Sekunde nach
# Laufbeginn und haette daraus mit einem Erfahrungswert fuer ``backtest_ms`` die Zeitprognose
# stellen koennen; geprueft wurde nur der Plattenplatz, die eigentlich knappe Ressource (Zeit)
# nicht. Fallback-Median, falls kein Vorlauf-Erfahrungswert vorliegt (beobachteter Median eines
# Referenzlaufs: 7575 ms).
DEFAULT_BACKTEST_MS_MEDIAN = 7575.0


def estimate_expected_wallclock_h(*, expected_trials: int, backtest_ms_median: float,
                                  parallelism_degree: float) -> float:
    """Issue #931 — reine Hochrechnung: ``expected_trials · backtest_ms_median`` CPU-Millisekunden,
    durch den (gemessenen oder konfigurierten) Parallelitätsgrad geteilt, in Stunden. Rein,
    deterministisch. ``parallelism_degree <= 0`` ⇒ konservativ 1.0 (keine Parallelität angenommen)."""
    p = parallelism_degree if parallelism_degree and parallelism_degree > 0 else 1.0
    return (float(expected_trials) * float(backtest_ms_median) / 1000.0 / 3600.0) / p


def _degrade_state_path(work_dir: Path) -> Path:
    return Path(work_dir) / "wallclock_degrade_state.json"


def write_degrade_factor(work_dir: Path, factor: float) -> Path:
    """Issue #931 Fix 2 — persistiert den globalen Trial-Budget-Degradations-Faktor
    (``wallclock_budget_policy='degrade'``) in einer kleinen Zustandsdatei, GETRENNT von
    ``optimizer.json`` (die Config bleibt statisch/menschlich gepflegt). Jeder spätere,
    unabhängige ``optimizer.json``-Read (jede Study lädt ihre Config frisch von der Platte, siehe
    ``run_optimization._optimize_symbol_impl``) kann diesen Faktor über ``read_degrade_factor``
    zusätzlich konsultieren, ohne dass ein In-Memory-Objekt über Prozess-/Study-Grenzen gereicht
    werden müsste."""
    path = _degrade_state_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"n_trials_degrade_factor": round(float(factor), 6)}), encoding="utf-8")
    return path


def read_degrade_factor(work_dir: Path) -> float:
    """Gegenstück zu ``write_degrade_factor``. Fehlt die Datei (kein Degrade-Preflight in diesem
    Lauf, ODER ``wallclock_budget_policy != 'degrade'``) ⇒ 1.0 (kein Effekt, rückwärtskompatibel)."""
    path = _degrade_state_path(work_dir)
    if not path.exists():
        return 1.0
    try:
        data = json.loads(path.read_text("utf-8")) or {}
        return float(data.get("n_trials_degrade_factor", 1.0))
    except (OSError, ValueError, TypeError):
        return 1.0


def clear_degrade_state(work_dir: Path) -> None:
    """Lauf-Start-Aufräumen: eine STALE Degrade-Datei eines abgebrochenen Vorlaufs darf einen
    neuen Lauf nicht stillschweigend beeinflussen (analog #794s Trial-Verzeichnis-Purge)."""
    path = _degrade_state_path(work_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
