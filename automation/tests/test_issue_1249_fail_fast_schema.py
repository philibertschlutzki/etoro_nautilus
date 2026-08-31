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


# ── Issue #1328 (Katalog #1323-1329, P3) — der Check wurde WIEDER aus fail_fast_invariants
# entfernt (Option B): er braucht bereits vorliegende Trial-Daten EINER Study
# (``report._study_record``) und ist damit strukturell nie ein VOR-Phase-1-Preflight — sein
# einziger Aufrufer feuerte nie bei ``n_studies=0``, wodurch ``check_fail_fast_invariants_wired``
# in JEDEM leeren Lauf FAILte. Die Entscheidungspflicht selbst (severity='blocking' je Study,
# wenn auswertbar) bleibt unveraendert aktiv — nur die Teilnahme an der VOR-Phase-1-Abbruch-Gate
# entfaellt. Die zugehoerige Schema-Doku (tournament.json['_schema']['fields']
# ['gate_collinearity_policy']) wurde im selben Fix mitentfernt, siehe Test unten.
def test_production_optimizer_json_no_longer_lists_gate_collinearity_in_fail_fast_invariants():
    ocfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert "check_gate_collinearity_decision_required" not in ocfg["fail_fast_invariants"]


def test_production_tournament_json_schema_no_longer_claims_fail_fast_membership():
    """Regressionswaechter gegen das urspruengliche #1249-Symptom in die andere Richtung: die
    Schema-Doku darf jetzt nicht mehr behaupten, der Check stehe in fail_fast_invariants (sonst
    wuerde check_fail_fast_schema_consistency erneut Drift melden)."""
    docs = _load_production_configs()
    result = inv.check_fail_fast_schema_consistency(
        docs, fail_fast_invariants=docs["optimizer.json"]["fail_fast_invariants"])
    assert result.passed is True


# ── check_fail_fast_schema_consistency ───────────────────────────────────────────────────────────
def test_fail_fast_schema_consistency_passes_on_repaired_production_config():
    docs = _load_production_configs()
    result = inv.check_fail_fast_schema_consistency(
        docs, fail_fast_invariants=docs["optimizer.json"]["fail_fast_invariants"])
    assert result.passed is True


def test_fail_fast_schema_consistency_fails_when_documented_check_is_removed():
    """Wird ein Check, den eine Schema-Doku als 'in fail_fast_invariants' benennt, aus der
    tatsaechlichen Liste entfernt (Regression der #1249-Symptomklasse), MUSS der Wächter FAILen —
    die Doku behauptet weiterhin die Mitgliedschaft. Synthetisches Doc statt Produktionsconfig
    (seit #1328 dokumentiert tournament.json keine fail_fast_invariants-Mitgliedschaft mehr fuer
    check_gate_collinearity_decision_required, siehe Test oben)."""
    docs = {"tournament.json": {"gate_collinearity_policy": (
        "... oder der Sweep bricht VOR Phase 1 ab "
        "(invariants.check_gate_collinearity_decision_required, in fail_fast_invariants) ...")}}
    result = inv.check_fail_fast_schema_consistency(docs, fail_fast_invariants=[])
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
def test_fail_fast_actual_convention_exempts_an_explicitly_passed_global_scope_check():
    """Issue #1328 — ``check_gate_collinearity_decision_required`` steht seit diesem Fix NICHT
    mehr im DEFAULT von ``global_scope_checks`` (kein fail_fast_invariants-Mitglied mehr); der
    generische Ausnahme-Mechanismus selbst bleibt aber unveraendert nutzbar fuer jeden explizit
    als global-skopiert benannten Check."""
    checks = [{"name": "check_gate_collinearity_decision_required", "passed": False,
              "actual": [{"pair": ["oos_min_psr", "oos_min_alpha_tstat"], "rho": 0.95}]}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_gate_collinearity_decision_required"],
        global_scope_checks=frozenset({"check_gate_collinearity_decision_required"}))
    assert result.passed is True


def test_fail_fast_actual_convention_default_no_longer_exempts_gate_collinearity():
    """Ohne den expliziten Override greift der DEFAULT (seit #1328 nur noch
    ``check_bar_quality``) nicht mehr fuer ``check_gate_collinearity_decision_required`` — waere
    der Check noch in fail_fast_invariants gelistet, wuerde ein nicht-Pair-``actual`` jetzt
    korrekt als Konventionsverstoss gemeldet."""
    checks = [{"name": "check_gate_collinearity_decision_required", "passed": False,
              "actual": [{"pair": ["oos_min_psr", "oos_min_alpha_tstat"], "rho": 0.95}]}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_gate_collinearity_decision_required"])
    assert result.passed is False


def test_fail_fast_actual_convention_default_exempts_check_bar_quality():
    """Issue #1327 — ``check_bar_quality`` ist seit diesem Fix im DEFAULT von
    ``global_scope_checks``: ein FAILender check_bar_quality ohne Pair-``actual`` loest KEINEN
    zusaetzlichen Konventionsverstoss mehr aus."""
    checks = [{"name": "check_bar_quality", "passed": False,
              "actual": {"reason": "bar_coverage_ratio", "value": 0.069}}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_bar_quality"])
    assert result.passed is True


def test_fail_fast_actual_convention_still_flags_non_exempt_checks():
    checks = [{"name": "check_holding_time_cap", "passed": False, "actual": None}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_holding_time_cap"])
    assert result.passed is False
