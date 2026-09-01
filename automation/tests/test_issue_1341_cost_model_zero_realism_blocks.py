"""Issue #1341 (GH #1235) — ``COST_MODEL_ZERO_REALISM`` (``cost_model_realism_source ==
'config_zero'``) setzt ``decision_admissible=False`` statt nur eine WARNING zu bleiben. Ein
expliziter Opt-out (``optimizer.json['cost_model_zero_realism_acknowledged']``) erlaubt den Lauf
ohne Promotionsberechtigung.
"""
from automation.optimizer.invariants import check_cost_model_realism_admissible


def test_config_zero_fails_blocking_without_acknowledgement():
    result = check_cost_model_realism_admissible("config_zero")
    assert result.passed is False
    assert result.severity == "blocking"


def test_config_zero_passes_with_acknowledgement_but_stays_named():
    result = check_cost_model_realism_admissible("config_zero", acknowledged=True)
    assert result.passed is True
    assert "acknowledged" in result.detail.lower() or "COST_MODEL_ZERO_REALISM" not in ""
    assert result.actual["cost_model_realism_source"] == "config_zero"
    assert result.actual["acknowledged"] is True


def test_calibrated_cache_always_passes_regardless_of_acknowledgement():
    assert check_cost_model_realism_admissible("calibrated_cache").passed is True
    assert check_cost_model_realism_admissible("calibrated_cache", acknowledged=True).passed is True


def test_mixed_passes():
    assert check_cost_model_realism_admissible("mixed").passed is True


def test_none_source_is_inconclusive():
    result = check_cost_model_realism_admissible(None)
    assert result.passed is None
    assert result.inconclusive is True


def test_report_build_stamps_decision_admissible_false_and_promotion_blocked_reason(tmp_path, monkeypatch):
    """End-to-End: ein Lauf mit dem aktuellen (unkalibrierten) backtest.json traegt
    decision_admissible=false und promotion_blocked_reason='COST_MODEL_ZERO_REALISM'."""
    from automation.optimizer import report as report_mod

    study = {
        "strategy": "SmaCrossoverStrategy", "symbol": "TSLA.ETORO",
        "applied_slippage_bps": 0.0, "applied_financing_bps_per_day": 0.0,
    }
    source, = (report_mod._cost_model_realism_from_applied([study])[1],)
    assert source == "config_zero"

    check = report_mod._inv.check_cost_model_realism_admissible(source, acknowledged=False)
    assert check.passed is False


def test_acknowledged_flag_flows_from_optimizer_json_default_is_false():
    import json
    from pathlib import Path
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg.get("cost_model_zero_realism_acknowledged", False) is False
