"""Issue #785 (P1) — `check_rejection_chain_completeness` bestand 1736/1736, obwohl 37 Records
(alle `#682`-Promotionen, heute `PROMOTE_GLOBAL_DEFAULT`) eine ganze Stufe fehlte.

Root-Cause: `passed = True if status in (None, 'READY_FOR_PR') else ...` war für den Erfolgsfall
PER KONSTRUKTION `True` — der Check prüfte den einzigen Fall nicht, der zählt (AGENTS.md-Pitfall
#236).

Fix: `report._decision_chain` baut eine VOLLSTAENDIGE Kette mit positiven UND negativen Stufen
(`{stage, passed, detail}`); `check_rejection_chain_completeness` fordert bei `promote=True` die
Anwesenheit aller obligatorischen Stufen mit `passed=True`.
"""
from automation.optimizer import invariants as inv
from automation.optimizer.report import _decision_chain, _rejection_chain_view


def test_ready_for_pr_with_no_rejection_detail_has_all_mandatory_stages_passed():
    proposal = {"status": "READY_FOR_PR", "holdout_reject_detail": None}
    chain = _decision_chain(proposal, n_eligible=5)
    stages = {c["stage"]: c["passed"] for c in chain}
    assert stages["is_gate"] is True
    assert stages["confirm_or_selection"] is True
    assert stages["holdout"] is True


def test_global_default_route_carries_confirm_or_selection_with_global_default_detail():
    """Akzeptanzkriterium #785/2: die #682-Route trägt nach #783 die Stufen is_gate,
    confirm_or_selection(passed=True, detail='GLOBAL_DEFAULT'), holdout(passed=True)."""
    proposal = {
        "status": "PROMOTE_GLOBAL_DEFAULT", "holdout_reject_detail": None,
        "promotion_route": "global_default_on_symbol",
    }
    chain = _decision_chain(proposal, n_eligible=0)
    by_stage = {c["stage"]: c for c in chain}
    assert by_stage["is_gate"]["passed"] is True
    assert by_stage["confirm_or_selection"]["passed"] is True
    assert by_stage["confirm_or_selection"]["detail"] == "GLOBAL_DEFAULT"
    assert by_stage["holdout"]["passed"] is True


def test_rejection_terminates_the_chain_at_the_failing_stage():
    proposal = {"status": "REJECTED_ON_HOLDOUT", "holdout_reject_detail": "REJECT_SELECTION_PBO"}
    chain = _decision_chain(proposal, n_eligible=10)
    stages = [c["stage"] for c in chain]
    assert stages == ["is_gate", "confirm_or_selection", "holdout", "deflation", "pbo"]
    assert chain[-1]["passed"] is False
    assert chain[-1]["detail"] == "REJECT_SELECTION_PBO"
    # Stufen VOR pbo gelten als bestanden (der Confirm-Pfad erreicht pbo erst danach).
    assert all(c["passed"] for c in chain[:-1])


def test_is_gate_failure_when_no_eligible_trials_and_not_promoted():
    proposal = {
        "status": "REJECTED_ON_HOLDOUT", "holdout_reject_detail": "HOLDOUT_NO_ELIGIBLE_TRIALS",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
    }
    chain = _decision_chain(proposal, n_eligible=0)
    assert chain == [{"stage": "is_gate", "passed": False, "detail": "REJECT_OOS_MIN_TRADES"}]


def test_rejection_chain_view_is_derived_and_shows_only_failed_stages():
    proposal = {"status": "REJECTED_ON_HOLDOUT", "holdout_reject_detail": "REJECT_BOUNDARY_SOLUTION"}
    chain = _decision_chain(proposal, n_eligible=10)
    view = _rejection_chain_view(chain)
    assert view == [{"stage": "boundary", "detail": "REJECT_BOUNDARY_SOLUTION"}]


# ── check_rejection_chain_completeness (Akzeptanzkriterium #785/1) ─────────────────────────────
def test_missing_confirm_or_selection_stage_fails_for_promoted_record():
    """Genau das #742-Symptom: promote=True, aber confirm_or_selection fehlt in der Kette."""
    chain = [
        {"stage": "is_gate", "passed": True, "detail": None},
        {"stage": "holdout", "passed": True, "detail": None},
    ]
    result = inv.check_rejection_chain_completeness(
        {"status": "READY_FOR_PR"}, decision_chain=chain)
    assert result.passed is False
    assert "confirm_or_selection" in result.actual["missing_stages"]


def test_complete_chain_for_global_default_passes():
    proposal = {"status": "PROMOTE_GLOBAL_DEFAULT", "promotion_route": "global_default_on_symbol"}
    chain = _decision_chain(proposal, n_eligible=0)
    result = inv.check_rejection_chain_completeness(proposal, decision_chain=chain)
    assert result.passed is True
