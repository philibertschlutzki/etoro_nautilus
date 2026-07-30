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

import threading

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
