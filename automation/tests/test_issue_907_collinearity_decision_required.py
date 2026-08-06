"""Issue #907 (P2) — Der Kollinearitäts-Alarm ([#667]) feuert sechsmal ohne jede
Entscheidungspflicht (Pitfall #280), symmetrisch dazu: eine in ``fail_fast_invariants`` gelistete
Invariante ohne ausgewertetes Ergebnis ist genauso unverdrahtet, nur eine Ebene tiefer.
"""
from automation.optimizer import invariants as inv


# ── check_gate_collinearity_decision_required ────────────────────────────────────────────────────
def test_warn_policy_always_passes():
    result = inv.check_gate_collinearity_decision_required(
        {("oos_min_psr", "oos_min_profit_factor"): 0.988}, policy="warn")
    assert result.passed is True


def test_require_decision_fails_without_accepted_pair():
    result = inv.check_gate_collinearity_decision_required(
        {("oos_min_psr", "oos_min_profit_factor"): 0.988}, threshold=0.90)
    assert result.passed is False
    assert result.severity == "blocking"
    assert result.actual[0]["pair"] == ["oos_min_psr", "oos_min_profit_factor"]


def test_require_decision_passes_with_documented_acceptance():
    accepted = [{
        "pair": ["oos_min_psr", "oos_min_profit_factor"],
        "rationale": "Deferred pending #897 calibration run (Issue #906).",
        "decided_in_issue": 906,
    }]
    result = inv.check_gate_collinearity_decision_required(
        {("oos_min_psr", "oos_min_profit_factor"): 0.988}, threshold=0.90,
        accepted_pairs=accepted)
    assert result.passed is True


def test_accepted_pair_without_rationale_does_not_count():
    """Ein Eintrag ohne rationale/decided_in_issue ist keine dokumentierte Entscheidung."""
    accepted = [{"pair": ["oos_min_psr", "oos_min_profit_factor"]}]
    result = inv.check_gate_collinearity_decision_required(
        {("oos_min_psr", "oos_min_profit_factor"): 0.988}, threshold=0.90,
        accepted_pairs=accepted)
    assert result.passed is False


def test_pairs_below_threshold_are_never_flagged():
    result = inv.check_gate_collinearity_decision_required(
        {("a", "b"): 0.5}, threshold=0.90)
    assert result.passed is True


def test_none_correlation_is_skipped():
    result = inv.check_gate_collinearity_decision_required({("a", "b"): None}, threshold=0.90)
    assert result.passed is True


def test_multiple_offending_pairs_all_reported():
    result = inv.check_gate_collinearity_decision_required(
        {("a", "b"): 0.95, ("c", "d"): -0.92, ("e", "f"): 0.5}, threshold=0.90)
    assert result.passed is False
    assert len(result.actual) == 2


# ── check_fail_fast_invariants_wired ─────────────────────────────────────────────────────────────
def test_wired_check_passes_when_all_configured_names_were_evaluated():
    result = inv.check_fail_fast_invariants_wired(
        ["check_holding_time_cap", "check_guard_reference_coherence", "check_effective_stop_distance"],
        fail_fast_invariants=["check_holding_time_cap", "check_guard_reference_coherence"])
    assert result.passed is True


def test_wired_check_fails_when_a_configured_name_never_ran():
    """Reproduziert den ea4c409d-Referenzfall: check_guard_reference_coherence ist konfiguriert,
    lief aber (Root-Cause #901) nie sichtbar aus."""
    result = inv.check_fail_fast_invariants_wired(
        ["check_holding_time_cap"],
        fail_fast_invariants=["check_holding_time_cap", "check_guard_reference_coherence"])
    assert result.passed is False
    assert "check_guard_reference_coherence" in result.actual


def test_wired_check_not_applicable_without_configured_list():
    result = inv.check_fail_fast_invariants_wired(["check_holding_time_cap"], fail_fast_invariants=[])
    assert result.passed is True
