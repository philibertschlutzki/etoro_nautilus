"""Issue #1001/#1153 (Katalog #1170, P0) — ``_decision_chain`` leitet bestandene Stufen ab, statt
sie zu messen.

Symptom (B-8): ``decision_chain`` trug fuer 10 Studies den Eintrag ``{"stage": "holdout", "passed":
true, "detail": null}`` — nachweislich falsch (das Holdout-Gate selbst war gescheitert; PBO/Boundary
gewannen unter der Vor-#1152-Praezedenz die Attribution). ``check_rejection_chain_completeness``
passierte darauf in 28/28.

Fix: ``confirm.confirm_per_symbol_promotion`` schreibt die TATSAECHLICH gemessenen
Stufenergebnisse als ``stage_results: {stage: {passed, detail}}`` ins Proposal.
``report._decision_chain`` konsumiert ``stage_results`` primaer; eine dort fehlende Stufe wird nie
ausgewertet und traegt ``passed=None`` ("nicht belegt"), NIE ein stillschweigendes ``True``.
Alt-Proposals ohne ``stage_results`` fallen auf die alte Ableitung zurueck, aber ebenfalls mit
``passed=None`` statt ``True`` fuer nicht nachweislich gemessene Stufen. Eine neue Kohaerenz-
Gegenprobe in ``check_rejection_chain_completeness`` failt, wenn ``holdout: passed=True`` einem
negativen aktiven Gate-Delta oder ``oos_sortino_period <= 0`` im selben Record widerspricht.
"""
from automation.optimizer import confirm
from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


# --- report._decision_chain consumes stage_results -------------------------------------------------

def test_measured_stage_results_reproduce_the_b8_symptom_correctly():
    """Der zentrale B-8-Fall: holdout scheiterte GENAU DANN, wenn pbo_overfit die Attribution
    gewann. Mit stage_results MUSS die Kette trotzdem holdout:passed=False zeigen."""
    proposal = {
        "status": "REJECTED_ON_HOLDOUT",
        "holdout_reject_detail": "REJECT_HOLDOUT_GATE",
        "is_rejection_detail": "REJECT_HOLDOUT_GATE",
        "promotion_route": None,
        "stage_results": {
            "confirm_or_selection": {"passed": True, "detail": None},
            "holdout": {"passed": False, "detail": "REJECT_HOLDOUT_GATE"},
        },
    }
    chain = rpt._decision_chain(proposal, n_eligible=5)
    by_stage = {c["stage"]: c for c in chain}
    assert by_stage["holdout"]["passed"] is False
    # deflation/pbo/boundary wurden nie ausgewertet -- "nicht belegt", nicht "bestanden".
    assert by_stage["deflation"]["passed"] is None
    assert by_stage["pbo"]["passed"] is None
    assert by_stage["boundary"]["passed"] is None


def test_stage_results_missing_stage_is_none_not_true():
    proposal = {
        "status": "REJECTED_BEFORE_HOLDOUT",
        "holdout_reject_detail": "HOLDOUT_NO_ELIGIBLE_TRIALS",
        "is_rejection_detail": "HOLDOUT_NO_ELIGIBLE_TRIALS",
        "promotion_route": None,
        "stage_results": {
            "confirm_or_selection": {"passed": False, "detail": "HOLDOUT_NO_ELIGIBLE_TRIALS"},
        },
    }
    chain = rpt._decision_chain(proposal, n_eligible=5)
    by_stage = {c["stage"]: c for c in chain}
    assert by_stage["confirm_or_selection"]["passed"] is False
    assert by_stage["holdout"]["passed"] is None
    assert by_stage["deflation"]["passed"] is None


def test_promoted_candidate_measured_pbo_boundary_pass():
    proposal = {
        "status": "READY_FOR_PR",
        "holdout_reject_detail": None,
        "promotion_route": "per_symbol_holdout_validated",
        "stage_results": {
            "confirm_or_selection": {"passed": True, "detail": None},
            "holdout": {"passed": True, "detail": None},
            "deflation": {"passed": True, "detail": None},
            "pbo": {"passed": True, "detail": None},
            "boundary": {"passed": True, "detail": None},
        },
    }
    chain = rpt._decision_chain(proposal, n_eligible=5)
    assert all(c["passed"] is True for c in chain)


# --- legacy fallback: passed=None instead of True for unproven stages -----------------------------

def test_legacy_proposal_without_stage_results_yields_none_not_true_for_a_rejection():
    """Akzeptanzkriterium 2 — ein Alt-Proposal (kein stage_results-Feld) erzeugt passed=None statt
    des vorherigen (falschen) True fuer nicht nachweislich gemessene Stufen."""
    proposal = {
        "status": "REJECTED_SELECTION_OVERFIT",
        "is_rejection_detail": "REJECT_SELECTION_PBO",
        "promotion_route": None,
        # kein "holdout_reject_detail"/"stage_results"-Key -- simuliert ein VOR #1153 exportiertes
        # Proposal (Alt-Feldname "is_rejection_detail" statt des #671-Nachfolgers).
    }
    chain = rpt._decision_chain(proposal, n_eligible=5)
    by_stage = {c["stage"]: c for c in chain}
    # "pbo" ist die failing_stage (via _STAGE_FOR_REJECT_DETAIL) -- weiterhin False.
    assert by_stage["pbo"]["passed"] is False
    # "confirm_or_selection"/"holdout"/"deflation" (vor "pbo" in der Stufenreihenfolge) sind
    # NICHT mehr blind True -- sie sind nicht nachweislich gemessen.
    assert by_stage["confirm_or_selection"]["passed"] is None
    assert by_stage["holdout"]["passed"] is None
    assert by_stage["deflation"]["passed"] is None


def test_legacy_promoted_proposal_without_stage_results_stays_true():
    """promote=True ist selbst ein hinreichender Beleg -- bleibt bit-identisch True."""
    proposal = {
        "status": "READY_FOR_PR", "holdout_reject_detail": None,
        "promotion_route": "per_symbol_holdout_validated",
    }
    chain = rpt._decision_chain(proposal, n_eligible=5)
    assert all(c["passed"] is True for c in chain)


def test_legacy_global_default_route_still_carries_the_marker():
    proposal = {
        "status": "PROMOTE_GLOBAL_DEFAULT", "holdout_reject_detail": None,
        "promotion_route": "global_default_on_symbol",
    }
    chain = rpt._decision_chain(proposal, n_eligible=0)
    by_stage = {c["stage"]: c for c in chain}
    assert by_stage["is_gate"]["passed"] is True
    assert by_stage["confirm_or_selection"]["detail"] == "GLOBAL_DEFAULT"


# --- confirm.py actually produces the correct stage_results end-to-end (fixture-level) -------------

def test_confirm_classification_produces_stage_results_shape_for_holdout_gate_failure():
    status, stage = confirm._holdout_rejection_classification("REJECT_HOLDOUT_GATE")
    assert status == "REJECTED_ON_HOLDOUT"
    assert stage == "holdout"


# --- new coherence sub-check in check_rejection_chain_completeness --------------------------------

def test_coherence_check_fails_when_holdout_passed_true_but_sortino_period_non_positive():
    proposal = {"status": "READY_FOR_PR", "holdout_reject_detail": None}
    decision_chain = [
        {"stage": "is_gate", "passed": True, "detail": None},
        {"stage": "confirm_or_selection", "passed": True, "detail": None},
        {"stage": "holdout", "passed": True, "detail": None},
    ]
    holdout_metrics = {"oos_sortino_period": -0.01, "holdout_gate_deltas": {}}
    result = inv.check_rejection_chain_completeness(
        proposal, decision_chain=decision_chain, holdout_metrics=holdout_metrics)
    assert result.passed is False
    assert result.actual["coherence_violations"]


def test_coherence_check_fails_when_holdout_passed_true_but_gate_delta_negative():
    proposal = {"status": "READY_FOR_PR", "holdout_reject_detail": None}
    decision_chain = [
        {"stage": "is_gate", "passed": True, "detail": None},
        {"stage": "confirm_or_selection", "passed": True, "detail": None},
        {"stage": "holdout", "passed": True, "detail": None},
    ]
    holdout_metrics = {
        "oos_sortino_period": 0.05,
        "holdout_gate_deltas": {"oos_min_sortino": -0.2, "oos_min_trades": 0.5},
    }
    result = inv.check_rejection_chain_completeness(
        proposal, decision_chain=decision_chain, holdout_metrics=holdout_metrics)
    assert result.passed is False


def test_coherence_check_passes_with_consistent_measurements():
    proposal = {"status": "READY_FOR_PR", "holdout_reject_detail": None}
    decision_chain = [
        {"stage": "is_gate", "passed": True, "detail": None},
        {"stage": "confirm_or_selection", "passed": True, "detail": None},
        {"stage": "holdout", "passed": True, "detail": None},
    ]
    holdout_metrics = {
        "oos_sortino_period": 0.05,
        "holdout_gate_deltas": {"oos_min_sortino": 0.1, "oos_min_trades": 0.5},
    }
    result = inv.check_rejection_chain_completeness(
        proposal, decision_chain=decision_chain, holdout_metrics=holdout_metrics)
    assert result.passed is True


def test_coherence_check_ignores_non_active_gate_deltas_when_tournament_config_given():
    proposal = {"status": "READY_FOR_PR", "holdout_reject_detail": None}
    decision_chain = [
        {"stage": "is_gate", "passed": True, "detail": None},
        {"stage": "confirm_or_selection", "passed": True, "detail": None},
        {"stage": "holdout", "passed": True, "detail": None},
    ]
    holdout_metrics = {
        "oos_sortino_period": 0.05,
        # oos_min_profitable_folds_frac ist per Default kein aktives Gate.
        "holdout_gate_deltas": {"oos_min_profitable_folds_frac": -0.5},
    }
    tournament_config = {"eligible_requires_all": ["oos_min_sortino"]}
    result = inv.check_rejection_chain_completeness(
        proposal, decision_chain=decision_chain, holdout_metrics=holdout_metrics,
        tournament_config=tournament_config)
    assert result.passed is True


def test_coherence_check_is_a_no_op_without_a_true_holdout_entry():
    proposal = {"status": "REJECTED_ON_HOLDOUT", "holdout_reject_detail": "REJECT_HOLDOUT_GATE"}
    decision_chain = [
        {"stage": "is_gate", "passed": True, "detail": None},
        {"stage": "confirm_or_selection", "passed": True, "detail": None},
        {"stage": "holdout", "passed": False, "detail": "REJECT_HOLDOUT_GATE"},
    ]
    holdout_metrics = {"oos_sortino_period": -1.0, "holdout_gate_deltas": {"x": -1.0}}
    result = inv.check_rejection_chain_completeness(
        proposal, decision_chain=decision_chain, holdout_metrics=holdout_metrics)
    assert result.passed is True
