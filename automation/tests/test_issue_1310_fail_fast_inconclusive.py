"""Issue #1310 (GH #1187, P1) — Blockierende Fail-Fast-Wächter bestehen auf leerer Kohorte.

Symptom. ``check_holding_time_cap``, ``check_selection_statistic_availability``, ``check_guard_
reference_coherence`` (alle in ``fail_fast_invariants``, alle ``blocking``) melden ``passed=true``
mit "nicht anwendbar" (B-7).

Root-Cause. Fail-open ist für einen Diagnose-Check vertretbar, für einen Wächter mit
Abbruchvollmacht nicht: "ich konnte nicht prüfen" wird zu "geprüft und in Ordnung".

Fix.
1. Für jeden Check in ``optimizer.json['fail_fast_invariants']`` ist der Leer-Zweig ``passed=None``
   statt ``True`` (deckungsgleich mit #1309/#1186, hier zusätzlich als Klasseninvariante über alle
   sieben konfigurierten Checks geprüft — ``check_effective_stop_distance``/``check_bar_quality``/
   ``check_tick_population`` waren bereits korrekt; ``check_gate_collinearity_decision_required``
   hat KEINEN "leere Kohorte"-Zweig mit blockierender Severity, nur einen expliziten Policy-Opt-out
   [``policy='warn'``], der unveraendert bleibt).
2. Neue Invariante ``check_fail_fast_inconclusive_budget`` (severity ``blocking``): FAILt, wenn
   mehr als die Hälfte der Fail-Fast-Wächter eines Laufs ``passed is None`` tragen.
3. ``decision_admissible`` berücksichtigt ``passed is None`` bei Fail-Fast-Wächtern gleichrangig
   mit ``passed is False`` — bereits durch die bestehende Definition abgedeckt (``not None ==
   True`` in Python), hier verifiziert statt neu implementiert.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report


# ── Fix Punkt 1 — die vier korrigierten Checks liefern passed=None auf leerer Kohorte ───────────

def test_check_holding_time_cap_is_none_on_empty_cohort():
    result = inv.check_holding_time_cap([])
    assert result.passed is None
    assert result.evaluable is False
    assert result.severity == "blocking"


def test_check_guard_reference_coherence_is_none_when_unconfigured_or_no_telemetry():
    result = inv.check_guard_reference_coherence(None, [])
    assert result.passed is None
    assert result.evaluable is False
    assert result.severity == "blocking"


def test_check_guard_reference_coherence_is_none_when_observed_median_non_positive():
    result = inv.check_guard_reference_coherence(1600.0, [0.0, -1.0])
    assert result.passed is None
    assert result.evaluable is False
    assert result.severity == "blocking"


def test_check_selection_statistic_availability_is_none_on_empty_cohort():
    result = inv.check_selection_statistic_availability([])
    assert result.passed is None
    assert result.evaluable is False
    assert result.severity == "blocking"


# ── Regressionsschutz: bereits vor diesem Fix korrekte Checks bleiben unveraendert ───────────────

def test_check_effective_stop_distance_was_already_none_before_this_fix():
    result = inv.check_effective_stop_distance([])
    assert result.passed is None


def test_check_tick_population_was_already_none_before_this_fix():
    result = inv.check_tick_population([])
    assert result.passed is None


def test_check_gate_collinearity_decision_required_warn_policy_is_a_deliberate_opt_out_not_a_bug():
    """policy='warn' ist explizit KEIN 'leere Kohorte, kann nicht pruefen'-Fall, sondern eine
    bewusste Konfigurationsentscheidung (reine Telemetrie) — bleibt unveraendert passed=True,
    OHNE evaluable=False/severity='blocking' zu behaupten."""
    result = inv.check_gate_collinearity_decision_required({}, policy="warn")
    assert result.passed is True
    assert result.evaluable is True


# ── Fix Punkt 1 als Klasseninvariante: keine der sieben konfigurierten Checks liefert je
# passed=true bei evaluable=false gleichzeitig (Regressionsschutz, siehe auch #1309s
# check_inconclusive_not_reported_as_pass, hier auf die konkrete fail_fast_invariants-Liste
# angewandt).

_FAIL_FAST_INVARIANTS = [
    "check_holding_time_cap", "check_guard_reference_coherence", "check_effective_stop_distance",
    "check_selection_statistic_availability", "check_gate_collinearity_decision_required",
    "check_bar_quality", "check_tick_population",
]


def test_production_optimizer_json_lists_exactly_these_seven_fail_fast_checks():
    import json
    from automation.optimizer.trial_config import config_dir
    cfg = json.loads((config_dir() / "optimizer.json").read_text("utf-8"))
    assert sorted(cfg.get("fail_fast_invariants") or []) == sorted(_FAIL_FAST_INVARIANTS)


def test_no_fail_fast_check_with_zero_trades_reports_passed_true():
    """Akzeptanzkriterium: in einem Lauf mit 0 Trades traegt kein Fail-Fast-Wächter passed=true."""
    empty_results = [
        inv.check_holding_time_cap([]),
        inv.check_guard_reference_coherence(None, []),
        inv.check_effective_stop_distance([]),
        inv.check_selection_statistic_availability([]),
        inv.check_tick_population([]),
    ]
    for r in empty_results:
        assert r.passed is not True, f"{r.name} traegt passed=True bei 0 Trades"


# ── invariants.check_fail_fast_inconclusive_budget ───────────────────────────────────────────────

def _check(name, passed):
    return {"name": name, "check": name, "passed": passed, "severity": "blocking"}


def test_budget_passes_when_at_most_half_are_inconclusive():
    checks = [_check("A", None), _check("B", True), _check("C", True), _check("D", True)]
    result = inv.check_fail_fast_inconclusive_budget(checks, fail_fast_invariants=["A", "B", "C", "D"])
    assert result.passed is True


def test_budget_fails_when_more_than_half_are_inconclusive():
    checks = [_check("A", None), _check("B", None), _check("C", None), _check("D", True)]
    result = inv.check_fail_fast_inconclusive_budget(checks, fail_fast_invariants=["A", "B", "C", "D"])
    assert result.passed is False
    assert set(result.actual["inconclusive"]) == {"A", "B", "C"}


def test_budget_treats_a_missing_check_the_same_as_passed_none():
    """Ein konfigurierter Check, der GAR NICHT im Strom auftaucht, zaehlt ebenfalls als "kein
    Verdikt" — dieselbe extremste Form von Nicht-Auswertbarkeit."""
    checks = [_check("A", True)]
    result = inv.check_fail_fast_inconclusive_budget(checks, fail_fast_invariants=["A", "B"])
    assert result.passed is True  # 1/2 == 50%, "mehr als die Haelfte" (>50%) noch nicht ueberschritten.
    checks2 = [_check("A", True)]
    result2 = inv.check_fail_fast_inconclusive_budget(checks2, fail_fast_invariants=["A", "B", "C"])
    assert result2.passed is False  # 2/3 > 50%.
    assert set(result2.actual["inconclusive"]) == {"B", "C"}


def test_budget_trivially_passes_without_any_configured_fail_fast_invariants():
    result = inv.check_fail_fast_inconclusive_budget([], fail_fast_invariants=[])
    assert result.passed is True


def test_budget_uses_the_first_occurrence_when_a_name_repeats():
    checks = [_check("A", None), _check("A", True)]
    result = inv.check_fail_fast_inconclusive_budget(checks, fail_fast_invariants=["A"])
    assert result.passed is False  # das ERSTE Vorkommen (None) zaehlt.


def test_budget_severity_is_blocking():
    result = inv.check_fail_fast_inconclusive_budget(
        [_check("A", None)], fail_fast_invariants=["A"])
    assert result.severity == "blocking"


# ── Akzeptanzkriterium: FAILt für den (rekonstruierten) Referenzlauf da354bc2 ────────────────────
# Der committete Report ``logs/run_da354bc2_20260827T131447775582.json`` zeigt vier der sieben
# fail_fast_invariants (check_holding_time_cap/check_guard_reference_coherence/check_effective_
# stop_distance/check_selection_statistic_availability) mit einer leeren-Kohorte-Situation — mit
# DIESEM Fix (Punkt 1) tragen alle vier passed=None statt des vormaligen passed=true (den drei
# ERSTGENANNTEN) bzw. blieb bereits None (check_effective_stop_distance).

def test_reference_run_da354bc2_reconstruction_fails_the_budget(monkeypatch, tmp_path):
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    reconstructed_checks = [
        _check("check_holding_time_cap", None),
        _check("check_guard_reference_coherence", None),
        _check("check_effective_stop_distance", None),
        _check("check_selection_statistic_availability", None),
        {**_check("check_gate_collinearity_decision_required", True), "scope": "S/SYM"},
        _check("check_bar_quality", False),
    ]
    result = inv.check_fail_fast_inconclusive_budget(
        reconstructed_checks, fail_fast_invariants=_FAIL_FAST_INVARIANTS)
    assert result.passed is False
    assert len(result.actual["inconclusive"]) >= 4  # > 50% von 7.
    # Akzeptanzkriterium 3 — die Begruendung nennt die INCONCLUSIVE-Waechter namentlich.
    for name in ("check_holding_time_cap", "check_guard_reference_coherence",
                 "check_effective_stop_distance", "check_selection_statistic_availability"):
        assert name in result.detail


def test_reference_run_da354bc2_reconstruction_yields_decision_admissible_false():
    """Akzeptanzkriterium: decision_admissible ist in diesem Fall false (unveraendert) —
    bereits durch die bestehende _compute_decision_admissible-Definition abgedeckt, hier gegen die
    rekonstruierte da354bc2-Situation verifiziert."""
    reconstructed_checks = [
        _check("check_holding_time_cap", None),
        _check("check_guard_reference_coherence", None),
        _check("check_effective_stop_distance", None),
        _check("check_selection_statistic_availability", None),
    ]
    assert report._compute_decision_admissible(reconstructed_checks) is False
