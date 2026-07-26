"""Issue #791 (P2) — Die Inferenz-Abdeckung ist lückenhaft und wird nicht als Fehler behandelt.

Root-Cause: 257/1736 Studies durchliefen überhaupt keine Inferenzmethode (`inference_method ==
{eligibility: null, promotion: null}`), davon eine promotet; zusätzlich trugen 14 von 38
`REJECT_SELECTION_PBO`-Ablehnungen `promotion: null` — nicht nachvollziehbar, WARUM keine
Inferenzmethode dokumentiert wurde.

Fix: `inference_method` je Ebene als `{method, applied, skipped_reason}`.
`invariants.check_promotion_inference_coverage` erzwingt `applied == True` bei `promote=True`
(auch für die `#682`/`#783`-Default-Route) UND bei `REJECT_SELECTION_PBO`.
"""
from automation.optimizer import invariants as inv
from automation.optimizer.report import _inference_method_block


def _trial_attrs(evaluated: bool):
    return [{"oos_evaluated": evaluated}]


# ── _inference_method_block: {method, applied, skipped_reason} ─────────────────────────────────
def test_promoted_candidate_with_real_deflation_method_is_applied():
    block = _inference_method_block(
        _trial_attrs(True), {"deflation_inference_method": "stationary_bootstrap"},
        {"status": "READY_FOR_PR"}, n_eligible=5)
    assert block["promotion"] == {"method": "stationary_bootstrap", "applied": True, "skipped_reason": None}


def test_promoted_global_default_without_numeric_deflation_still_reports_applied():
    """Akzeptanzkriterium #791/1: kein Record mit promote=True und promotion.applied != True —
    auch wenn keine ECHTE numerische Deflation vorlag (z. B. deflated_selection deaktiviert)."""
    block = _inference_method_block(
        _trial_attrs(True), {}, {"status": "PROMOTE_GLOBAL_DEFAULT"}, n_eligible=0)
    assert block["promotion"]["applied"] is True
    assert block["promotion"]["method"] == "not_applicable"


def test_rejected_candidate_without_deflation_method_is_not_applied_but_documented():
    block = _inference_method_block(
        _trial_attrs(True), {}, {"status": "REJECTED_ON_HOLDOUT"}, n_eligible=0)
    assert block["promotion"]["applied"] is False
    assert block["promotion"]["skipped_reason"] == "NO_ELIGIBLE_TRIALS"


def test_rejected_candidate_with_eligible_trials_but_no_deflation_method():
    block = _inference_method_block(
        _trial_attrs(True), {}, {"status": "REJECTED_ON_HOLDOUT"}, n_eligible=3)
    assert block["promotion"]["applied"] is False
    assert block["promotion"]["skipped_reason"] == "DEFLATION_NOT_APPLICABLE"


def test_eligibility_skipped_reason_when_no_trial_evaluated():
    block = _inference_method_block(_trial_attrs(False), {}, {"status": None}, n_eligible=0)
    assert block["eligibility"]["applied"] is False
    assert block["eligibility"]["skipped_reason"] == "NO_EVALUATED_TRIALS"


# ── invariants.check_promotion_inference_coverage ───────────────────────────────────────────────
def test_invariant_fails_when_promoted_but_not_applied():
    record = {"inference_method": {"promotion": {"applied": False}}}
    result = inv.check_promotion_inference_coverage({"status": "READY_FOR_PR"}, record)
    assert result.passed is False


def test_invariant_passes_when_promoted_and_applied():
    record = {"inference_method": {"promotion": {"applied": True}}}
    result = inv.check_promotion_inference_coverage({"status": "READY_FOR_PR"}, record)
    assert result.passed is True


def test_invariant_passes_for_global_default_when_applied():
    record = {"inference_method": {"promotion": {"applied": True}}}
    result = inv.check_promotion_inference_coverage({"status": "PROMOTE_GLOBAL_DEFAULT"}, record)
    assert result.passed is True


def test_invariant_requires_documented_inference_for_pbo_rejection():
    """Akzeptanzkriterium #791/3: REJECT_SELECTION_PBO erfordert eine dokumentierte
    Promotions-Inferenz — eine Ablehnung ohne benannte Methode ist nicht nachvollziehbar."""
    record = {"inference_method": {"promotion": {"applied": False}}}
    proposal = {"status": "REJECTED_SELECTION_OVERFIT", "holdout_reject_detail": "REJECT_SELECTION_PBO"}
    result = inv.check_promotion_inference_coverage(proposal, record)
    assert result.passed is False


def test_invariant_not_applicable_for_ordinary_rejections():
    """Ein gewoehnlicher REJECTED_ON_HOLDOUT ohne Inferenz ist KEIN #791-Fehler (die Studie hatte
    z. B. nie eligible Trials) — nur promote=True oder REJECT_SELECTION_PBO sind betroffen."""
    record = {"inference_method": {"promotion": {"applied": False}}}
    proposal = {"status": "REJECTED_ON_HOLDOUT", "holdout_reject_detail": "HOLDOUT_NO_ELIGIBLE_TRIALS"}
    result = inv.check_promotion_inference_coverage(proposal, record)
    assert result.passed is True
