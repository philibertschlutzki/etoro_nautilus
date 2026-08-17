"""Issue #984/#1138 (Katalog #986, Pitfall #409 in AGENTS.md) — fünf definierte, getestete
``check_*``-Funktionen (``check_cost_model_floor``, ``check_cost_model_resolution``,
``check_family_n_statistic_coverage``, ``check_family_scope_coherence``,
``check_gate_collinearity_decision_required``) hatten null Aufrufstellen ausserhalb von
``invariants.py``/``tests/``. Fix: alle fünf verdrahtet (report.py — vier davon global/per-Study,
``check_gate_collinearity_decision_required`` per Study); neue ``check_invariant_registry_wired``
plus ``report._invariant_registry_wiring_check()`` als dauerhafter Regressionswächter gegen eine
künftige Wiederkehr.
"""
from automation.optimizer import invariants as inv


def test_check_invariant_registry_wired_passes_when_every_defined_check_is_wired():
    result = inv.check_invariant_registry_wired(
        ["check_a", "check_b"], ["check_a", "check_b"])
    assert result.passed is True
    assert result.actual is None


def test_check_invariant_registry_wired_fails_on_a_missing_check():
    result = inv.check_invariant_registry_wired(
        ["check_a", "check_b", "check_c"], ["check_a"])
    assert result.passed is False
    assert result.actual == ["check_b", "check_c"]
    assert result.severity == "high"


def test_check_invariant_registry_wired_respects_deliberately_unwired():
    result = inv.check_invariant_registry_wired(
        ["check_a", "check_b"], ["check_a"], deliberately_unwired=("check_b",))
    assert result.passed is True


def test_check_invariant_registry_wired_empty_registry_passes():
    result = inv.check_invariant_registry_wired([], [])
    assert result.passed is True
    assert result.actual is None


def test_report_invariant_registry_wiring_check_passes_against_the_real_codebase():
    """Issue #984/#1138 Akzeptanzkriterium: die Introspektion selbst, gegen den TATSAECHLICHEN
    Codebase-Zustand ausgefuehrt — waere einer der fuenf urspruenglich toten Checks nicht
    nachgezogen worden, waere dieser Test rot. Die Introspektion selbst ist reine Datei-/Regex-
    Arbeit (kein ``nautilus_trader``); ``report.py`` zu importieren braucht weiterhin ``optuna``."""
    from automation.optimizer import report
    result = report._invariant_registry_wiring_check()
    assert result.passed is True, result.detail
    assert result.actual is None
