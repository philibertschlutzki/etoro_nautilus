"""Issue #1042/#1191 (P3) — Zwei Größen namens "mittleres Verhältnis".

Symptom. Study-Feld ``realized_stop_loss_ratio_mean`` (z. B. DynamicBreakout 14,6036) und
Invarianten-Nutzlast ``ratio_pooled_mean`` (13,9747) trugen fast denselben Namen und unterschieden
sich um bis zu 0,63. Ursache: das Study-Feld nutzt ``oos_gross_loss_mean_bps_trailing_stop`` (Median
der PER-TRIAL-Mittel), die Invariante ``oos_gross_loss_mean_bps_trailing_stop_pooled`` (ein
EINZIGER trade-gewichteter Mittelwert über alle Trades).

Fix. Beide Felder nach ihrer Quelle benannt: ``realized_stop_loss_ratio_mean_per_trial``
(report.py, ``report._study_record``) und ``realized_stop_loss_ratio_mean_pooled``
(invariants.py, ``check_effective_stop_distance``). Kein Feldname bezeichnet mehr zwei
verschiedene Aggregationen.
"""
import ast
from pathlib import Path

from automation.optimizer import invariants as inv


_REPORT_PATH = Path(inv.__file__).parent / "report.py"
_INVARIANTS_PATH = Path(inv.__file__).parent / "invariants.py"


# ── Akzeptanzkriterium #1042: Suchtest über die Feldnamen-Registry ───────────────────────────────
def test_ambiguous_old_field_names_no_longer_appear_as_string_literals():
    """Die alten, kollidierenden Namen (``realized_stop_loss_ratio_mean`` OHNE Suffix,
    ``ratio_pooled_mean``) duerfen als STRING-LITERAL (Dict-Key/Feldname) nirgends mehr auftreten
    -- nur noch in Kommentaren/Docstrings, die die Historie erklaeren (AST-Scan sieht nur echte
    String-Konstanten, keine Kommentare)."""
    for path in (_REPORT_PATH, _INVARIANTS_PATH):
        tree = ast.parse(path.read_text("utf-8"))
        string_literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "realized_stop_loss_ratio_mean" not in string_literals, path
        assert "ratio_pooled_mean" not in string_literals, path


def test_disambiguated_names_appear_as_string_literals_in_their_respective_modules():
    report_tree = ast.parse(_REPORT_PATH.read_text("utf-8"))
    report_literals = {
        node.value for node in ast.walk(report_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "realized_stop_loss_ratio_mean_per_trial" in report_literals

    invariants_tree = ast.parse(_INVARIANTS_PATH.read_text("utf-8"))
    invariants_literals = {
        node.value for node in ast.walk(invariants_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "realized_stop_loss_ratio_mean_pooled" in invariants_literals


# ── report._study_record: das umbenannte Feld selbst ──────────────────────────────────────────
def test_check_effective_stop_distance_carries_the_pooled_variant_under_its_new_name():
    records = [{
        "strategy": "S", "symbol": "X", "atr_median_bps": 20.0, "atr_trailing_multiplier_median": 2.0,
        "oos_gross_loss_mean_bps_trailing_stop_pooled": 16.0,
        "gross_loss_median_bps_trailing_stop": 16.0,
        "oos_n_trailing_stop_losses": 50,
        # Issue #1081/#1229 — der Legacy-Fallback konsumiert seither die GEMESSENE Distanz.
        "stop_distance_bps_measured": 40.0,
    }]
    result = inv.check_effective_stop_distance(records)
    assert result.passed is True
    assert "realized_stop_loss_ratio_mean_pooled" in result.actual["S/X"]
    assert "ratio_pooled_mean" not in result.actual["S/X"]
