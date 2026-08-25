"""Issue #1249 (GH #1119) — Schema-vs-Config-Drift: ``check_gate_collinearity_decision_required``
in ``fail_fast_invariants`` aufnehmen.

Symptom. ``tournament.json['gate_collinearity_policy']``s Schema-Dokumentation behauptete, der
Check stehe „in fail_fast_invariants" und breche „VOR Phase 1 ab". Tatsächlich fehlte er in
``optimizer.json['fail_fast_invariants']`` — 13 blockierende FAILs entstanden nach voller
Rechenzeit statt vor Phase 1, ohne dass ein Wächter die Doku-Behauptung je gegen die tatsächliche
Liste geprüft hätte.

Fix. (1) Der Check steht jetzt in ``optimizer.json['fail_fast_invariants']``. (2) Neuer
Wächter ``invariants.check_fail_fast_schema_consistency``: jede ``check_*``-Funktion, deren
Schema-Doku in ``optimizer.json``/``tournament.json`` die Zeichenkette ``'fail_fast_invariants'``
enthält, muss tatsächlich in der Liste stehen.
"""
import json
from pathlib import Path

from automation.optimizer import invariants as inv


def _load_production_configs() -> dict:
    tcfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    ocfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    return {"tournament.json": tcfg, "optimizer.json": ocfg}


# ── Akzeptanzkriterium: fail_fast_invariants enthält den Check ──────────────────────────────────
def test_production_optimizer_json_lists_gate_collinearity_in_fail_fast_invariants():
    ocfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert "check_gate_collinearity_decision_required" in ocfg["fail_fast_invariants"]


# ── check_fail_fast_schema_consistency ───────────────────────────────────────────────────────────
def test_fail_fast_schema_consistency_passes_on_repaired_production_config():
    docs = _load_production_configs()
    result = inv.check_fail_fast_schema_consistency(
        docs, fail_fast_invariants=docs["optimizer.json"]["fail_fast_invariants"])
    assert result.passed is True


def test_fail_fast_schema_consistency_fails_when_documented_check_is_removed():
    """Wird der dokumentierte Check aus der Liste entfernt (Regression des #1249-Symptoms), MUSS
    der Wächter FAILen — die Schema-Doku behauptet weiterhin die Mitgliedschaft."""
    docs = _load_production_configs()
    fail_fast = [c for c in docs["optimizer.json"]["fail_fast_invariants"]
                if c != "check_gate_collinearity_decision_required"]
    result = inv.check_fail_fast_schema_consistency(docs, fail_fast_invariants=fail_fast)
    assert result.passed is False
    assert "check_gate_collinearity_decision_required" in result.actual


def test_fail_fast_schema_consistency_ignores_unrelated_check_mentions():
    docs = {"a.json": {"field": "siehe check_something_unrelated fuer Details, keine "
                                  "Erwaehnung der Fail-Fast-Liste hier."}}
    result = inv.check_fail_fast_schema_consistency(docs, fail_fast_invariants=[])
    assert result.passed is True


def test_fail_fast_schema_consistency_flags_multiple_missing_checks():
    docs = {"a.json": {"field": "check_alpha und check_beta stehen beide in "
                                  "fail_fast_invariants laut dieser Doku."}}
    result = inv.check_fail_fast_schema_consistency(docs, fail_fast_invariants=["check_alpha"])
    assert result.passed is False
    assert list(result.actual.keys()) == ["check_beta"]


# ── check_fail_fast_actual_convention: globaler Scope ist kein Konventionsverstoss ──────────────
def test_fail_fast_actual_convention_exempts_global_scope_collinearity_check():
    checks = [{"name": "check_gate_collinearity_decision_required", "passed": False,
              "actual": [{"pair": ["oos_min_psr", "oos_min_alpha_tstat"], "rho": 0.95}]}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_gate_collinearity_decision_required"])
    assert result.passed is True


def test_fail_fast_actual_convention_still_flags_non_exempt_checks():
    checks = [{"name": "check_holding_time_cap", "passed": False, "actual": None}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_holding_time_cap"])
    assert result.passed is False
