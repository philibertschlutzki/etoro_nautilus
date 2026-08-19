"""Issue #1032/#1181 (P1) — ``gate_inventory.n_solo_rejections`` trägt die bestandene Kohorte
(#1155 im Code vorhanden, aber inert).

Root-Cause: ``invariants.gate_inventory_table``'s #1003/#1155-Fix liest ``oos_rejection_reasons``
aus den Trial-``user_attrs`` (``has_reasons_field``), um die primäre, robuste
``n_solo_rejections``-Zählung zu aktivieren. ``run_optimization.py`` stempelte dieses Feld aber NIE
als ``trial.set_user_attr(...)`` — es war fälschlich in
``_INTENTIONALLY_UNSTAMPED_METRIC_FIELDS`` als "kein trial_attrs-Konsument" allowlisted, obwohl der
#1003/#1155-Fix genau das inzwischen wurde. ``has_reasons_field`` war am Report-Zeitpunkt dadurch
IMMER ``False`` — die Zählung fiel lautlos auf die alte, delta-basierte Näherung mit dem
dokumentierten #956/#1122-Blinden-Fleck zurück (dieselbe Fehlerklasse, die #1003/#1155 bereits
beheben sollte).
"""
import ast
from pathlib import Path

from automation.optimizer import parsing
from automation.optimizer import run_optimization as ro
from automation.optimizer import invariants as inv

_RUN_OPTIMIZATION_PATH = Path(ro.__file__)


def _stamped_trial_user_attr_keys() -> set[str]:
    tree = ast.parse(_RUN_OPTIMIZATION_PATH.read_text("utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "set_user_attr"
                and isinstance(func.value, ast.Name) and func.value.id == "trial"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
    return keys


def test_run_optimization_now_stamps_oos_rejection_reasons():
    assert "oos_rejection_reasons" in _stamped_trial_user_attr_keys()


def test_field_is_no_longer_shadowed_by_the_stale_no_consumer_allowlist_entry():
    assert "oos_rejection_reasons" not in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS
    assert "oos_rejection_reasons" in {
        f for f in parsing.TournamentMetrics.__dataclass_fields__ if f.startswith("oos_")
    }


def test_stamped_reasons_activate_the_robust_solo_rejection_count():
    """End-to-end (auf der bereits vorhandenen gate_inventory_table-Logik): sobald
    oos_rejection_reasons tatsaechlich in trial_attrs steht (wie es run_optimization.py jetzt
    stempelt), greift Fix Punkt 1 aus #1003/#1155 und n_solo_rejections weicht vom blinden-Fleck-
    anfaelligen delta-basierten Naeherungswert ab, wo es zaehlt."""
    gates = ["oos_min_psr", "oos_min_trades"]
    # Ein Trial mit undefinierter PSR-Statistik: KEIN oos_gate_deltas-Eintrag fuer oos_min_psr
    # (derselbe #956/#1122-Blinde-Fleck), aber ein gestempelter Rejection-Grund.
    trials = [
        {"oos_evaluated": True, "oos_gate_deltas": {"oos_min_trades": 5.0},
         "oos_rejection_reasons": ["oos_min_psr: 0.5 < 0.75"]},
    ]
    is_rej_counts = {"REJECT_OOS_MIN_PSR": 1}
    table = inv.gate_inventory_table(trials, gates, is_rejection_detail_counts=is_rej_counts)
    psr_row = next(r for r in table if r["gate"] == "oos_min_psr")
    # Ohne den Fix (has_reasons_field=False) waere violated={} fuer diesen Trial (blinder Fleck)
    # -> n_eligible_with_all faelschlich erhoeht, n_solo_rejections faellt auf 0 (delta-basiert)
    # zurueck. Mit gestempelten reasons zaehlt der Trial korrekt als solo fuer oos_min_psr.
    assert psr_row["n_solo_rejections"] == 1
