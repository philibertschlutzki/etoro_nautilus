"""Issue #847 (P1) — 17 PBO-Ablehnungen ohne dokumentierte Promotions-Inferenz.

`check_promotion_inference_coverage` (#791) verlangt bereits `inference_method.promotion.applied
== True` für jede `REJECT_SELECTION_PBO`-Ablehnung — aber `report._inference_method_block` prüfte
bislang AUSSCHLIESSLICH `holdout_metrics['deflation_inference_method']` (die DSR-spezifische
Methode). Eine Study, die ausschliesslich am PBO-Gate scheiterte (keine DSR-Berechnung nötig/
gelaufen), hatte `deflation_inference_method is None` ⇒ `promotion.applied = False` — obwohl eine
CSCV/PBO-Inferenz TATSÄCHLICH gelaufen war (`holdout_metrics['pbo']` ist gesetzt). Fix:
`_inference_method_block` erkennt jetzt auch die PBO-Inferenz (`holdout_metrics['pbo'] is not
None`) als dokumentierte Promotions-Methode (`method='cscv'`) an.

Akzeptanzkriterien:
- AK-1: check_promotion_inference_coverage PASST für eine Study, die ausschliesslich per PBO
  verworfen wurde und `pbo` gesetzt trägt.
- AK-2: Jede REJECTED_SELECTION_OVERFIT-Study trägt pbo, pbo_n_configs_raw,
  pbo_n_configs_effective und die Schwelle im Report (confirm.py-Export).
"""
from automation.optimizer import invariants as inv
from automation.optimizer.report import _inference_method_block


def _trial_attrs(evaluated: bool = True):
    return [{"oos_evaluated": evaluated}]


# ── AK-1: _inference_method_block erkennt eine gelaufene PBO-Inferenz ───────────────────────────
def test_ak1_pbo_rejection_with_computed_pbo_is_applied():
    holdout_metrics = {
        "pbo": 0.89, "pbo_n_groups": 10, "pbo_n_configs_raw": 24, "pbo_n_configs": 18,
        "pbo_threshold": 0.5,
    }
    block = _inference_method_block(
        _trial_attrs(), holdout_metrics,
        {"status": "REJECTED_SELECTION_OVERFIT", "holdout_reject_detail": "REJECT_SELECTION_PBO"},
        n_eligible=5,
    )
    assert block["promotion"]["applied"] is True
    assert block["promotion"]["method"] == "cscv"
    assert block["promotion"]["pbo"] == 0.89
    assert block["promotion"]["pbo_n_configs_effective"] == 18
    assert block["promotion"]["pbo_n_configs_raw"] == 24
    assert block["promotion"]["pbo_threshold"] == 0.5


def test_ak1_invariant_now_passes_for_pbo_rejection(tmp_path=None):
    holdout_metrics = {"pbo": 0.89, "pbo_n_groups": 10, "pbo_n_configs_raw": 24, "pbo_n_configs": 18}
    proposal = {"status": "REJECTED_SELECTION_OVERFIT", "holdout_reject_detail": "REJECT_SELECTION_PBO"}
    block = _inference_method_block(_trial_attrs(), holdout_metrics, proposal, n_eligible=5)
    record = {"inference_method": block}
    result = inv.check_promotion_inference_coverage(proposal, record)
    assert result.passed is True


def test_dsr_method_takes_priority_over_pbo_when_both_present():
    """Wurde die DSR tatsaechlich berechnet (deflation_inference_method gesetzt), bleibt SIE die
    dokumentierte Methode -- unveraendertes Verhalten fuer den Regelfall."""
    holdout_metrics = {
        "deflation_inference_method": "stationary_bootstrap",
        "pbo": 0.89, "pbo_n_configs": 18,
    }
    block = _inference_method_block(
        _trial_attrs(), holdout_metrics, {"status": "REJECTED_ON_HOLDOUT"}, n_eligible=5)
    assert block["promotion"]["method"] == "stationary_bootstrap"


def test_pbo_none_falls_through_to_existing_behavior():
    """Regressionswaechter: ist pbo gar nicht berechnet worden (None), bleibt das Pre-#847-
    Verhalten unveraendert (kein falsch-positives 'applied')."""
    block = _inference_method_block(
        _trial_attrs(), {}, {"status": "REJECTED_ON_HOLDOUT"}, n_eligible=3)
    assert block["promotion"]["applied"] is False
    assert block["promotion"]["skipped_reason"] == "DEFLATION_NOT_APPLICABLE"


def test_pbo_zero_is_a_valid_computed_value_not_falsy_none():
    """pbo=0.0 (kein Overfit-Verdacht) ist ein GUELTIGES Ergebnis, kein 'nicht berechnet' -- muss
    weiterhin als angewandte Inferenz zaehlen (0.0 is not None)."""
    holdout_metrics = {"pbo": 0.0, "pbo_n_configs": 12}
    block = _inference_method_block(
        _trial_attrs(), holdout_metrics, {"status": "REJECTED_ON_HOLDOUT"}, n_eligible=5)
    assert block["promotion"]["applied"] is True
    assert block["promotion"]["method"] == "cscv"
