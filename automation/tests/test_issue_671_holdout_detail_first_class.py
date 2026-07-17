"""Issue #671 — Proposal-Attribution: modaler IS-Grund verdeckt den echten Holdout-Rejection-Grund
(#654-Nachprüfung).

Symptom: Proposals zeigten weiter ``dominant_is_rejection_detail: REJECT_OOS_MIN_EXPECTANCY`` neben
dem echten Selektions-/Holdout-Grund (``is_rejection_detail: REJECT_SELECTION_PBO``) — der
ausgewiesene ``dominant_*``-Grund ist der MODALE IS-Grund über die Trials, irreführend, wenn die
Promotion an einer ANDEREN Confirm-Hürde scheitert.

Fix: (1) ein erstklassiges ``holdout_reject_detail``-Feld (identisch zu ``is_rejection_detail``,
aber unter einem eindeutigen Namen); (2) ``dominant_rejection`` (Top-Level) richtet sich auf die
Confirm-Ursache aus, wenn die Strategie eligible Trials hatte, aber am Holdout/Selektion scheiterte.
"""
import json
import logging
from pathlib import Path

from automation.optimizer import run_optimization as ro, confirm


def _cfg_dir(tmp_path, tournament_extra=None):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    with open(cfg_dir / "optimizer.json", "w", encoding="utf-8") as f:
        json.dump({
            "promotion_margin": 0.10, "penalty_unevaluable_oos": -10,
            "unevaluable_shaping_span": 0.25, "sortino_clip_abs": 5.0,
            "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
            "bonus_coverage_weight": 1.0, "evaluable_floor_epsilon": 0.001,
            "lambda_reg": 0.25,
        }, f)
    tcfg = {
        "max_drawdown": 0.30, "deflated_selection": True, "deflation_confidence": 0.95,
        "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
        "oos_min_win_rate": 0.0,
    }
    if tournament_extra:
        tcfg.update(tournament_extra)
    with open(cfg_dir / "tournament.json", "w", encoding="utf-8") as f:
        json.dump(tcfg, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}}, f)
    return cfg_dir


def _isolate(monkeypatch, tmp_path, tournament_extra=None):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    from automation.optimizer import trial_config
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)


def _result_payload(*, sortino_ratio, dd, sortino_period, n_periods, eligible=True,
                     total_return=0.02, total_trades=50):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": eligible, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {
                "sortino_ratio": sortino_ratio, "max_drawdown": dd,
                "total_return": total_return, "total_trades": total_trades,
                "sortino_period": sortino_period, "n_periods": n_periods,
                "ret_skew": 0.0, "ret_kurtosis": 3.0,
            },
        },
    }


def _write_result(trial_dir, payload):
    out = Path(trial_dir) / "tournament_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), "utf-8")
    return out


def _cohort_factory(sortino_periods, n_periods=200):
    import itertools
    it = itertools.cycle(sortino_periods)

    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        sp = next(it)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=n_periods))
    return _fake


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_periods, tournament_extra=None):
    _isolate(monkeypatch, tmp_path, tournament_extra)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory(cohort_periods))
    return ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=n_trials)


def test_pbo_failed_candidate_has_first_class_holdout_reject_detail(tmp_path, monkeypatch):
    """Akzeptanzkriterium (#671): für einen an PBO gescheiterten Kandidaten ist
    holdout_reject_detail == 'REJECT_SELECTION_PBO' das Top-Level-Attribut; der modale IS-Grund
    ist als sekundäre Diagnose markiert (separates Feld, überschreibt NICHT die Attribution)."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025])

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=400)

    with_pbo = confirm._study_pbo
    try:
        confirm._study_pbo = lambda study, **k: (0.75, {
            "pbo_n_groups": 12, "pbo_n_configs": 12, "pbo_metric": "period_return"})
        res = confirm.confirm_per_symbol_promotion(
            study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
        )
    finally:
        confirm._study_pbo = with_pbo

    assert res["is_rejection_detail_override"] == "REJECT_SELECTION_PBO"

    proposal_path = confirm.export_symbol_proposal(study, "DynamicBreakoutStrategy", "TSLA.ETORO", res)
    payload = json.loads(Path(proposal_path).read_text("utf-8"))

    # Das erstklassige Feld trägt die exakte, gewinnende Confirm-Ursache.
    assert payload["holdout_reject_detail"] == "REJECT_SELECTION_PBO"
    # Top-Level dominant_rejection zeigt JETZT dieselbe Confirm-Ursache (nicht den modalen IS-Grund).
    assert payload["dominant_rejection"] == "REJECT_SELECTION_PBO"
    # Rückwärtskompat: is_rejection_detail bleibt identisch gesetzt.
    assert payload["is_rejection_detail"] == "REJECT_SELECTION_PBO"
    # Der modale IS-Grund bleibt als SEPARATE, sekundäre Diagnose erhalten.
    assert "dominant_is_rejection_detail" in payload


def test_dsr_drop_failed_candidate_has_first_class_holdout_reject_detail(tmp_path, monkeypatch):
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025])

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_DSR_DROP"

    proposal_path = confirm.export_symbol_proposal(study, "DynamicBreakoutStrategy", "TSLA.ETORO", res)
    payload = json.loads(Path(proposal_path).read_text("utf-8"))
    assert payload["holdout_reject_detail"] == "REJECT_HOLDOUT_DSR_DROP"
    assert payload["dominant_rejection"] == "REJECT_HOLDOUT_DSR_DROP"


def test_ready_for_pr_has_null_holdout_reject_detail(tmp_path, monkeypatch):
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=15,
                         cohort_periods=[0.5 + 0.001 * i for i in range(15)])

    strong_symbol = _result_payload(sortino_ratio=4.0, dd=0.02, sortino_period=0.6, n_periods=400,
                                    total_return=0.30)
    weak_global = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=400,
                                  total_return=0.0)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=strong_symbol,
                                      global_result=weak_global),
    )
    assert res["status"] == "READY_FOR_PR"
    assert res["is_rejection_detail_override"] is None

    proposal_path = confirm.export_symbol_proposal(study, "DynamicBreakoutStrategy", "TSLA.ETORO", res)
    payload = json.loads(Path(proposal_path).read_text("utf-8"))
    assert payload["holdout_reject_detail"] is None
    assert payload["status"] == "READY_FOR_PR"


def test_no_eligible_trials_keeps_modal_is_reason_as_dominant_rejection(tmp_path, monkeypatch):
    """HOLDOUT_NO_ELIGIBLE_TRIALS ist KEINE Confirm-Stage-Ursache (die Selektion erreichte NIE
    einen Holdout-Lauf) — dominant_rejection bleibt hier der modale PER-TRIAL IS-Grund, NICHT
    überschrieben."""
    global_params = {"price_breakout_period": 20}
    # Alle Trials scheitern bereits IS-seitig (kein oos_eligible) ⇒ eligible_trials bleibt leer.
    _isolate(monkeypatch, tmp_path)

    def _all_ineligible(trial_dir: Path, manifest_path: Path) -> Path:
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=-1.0, dd=0.9, sortino_period=-0.02, n_periods=200, eligible=False,
            total_return=-0.5))

    monkeypatch.setattr(ro, "run_backtest", _all_ineligible)
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=3)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_all_ineligible,
    )
    assert res["is_rejection_detail_override"] == "HOLDOUT_NO_ELIGIBLE_TRIALS"

    proposal_path = confirm.export_symbol_proposal(study, "DynamicBreakoutStrategy", "TSLA.ETORO", res)
    payload = json.loads(Path(proposal_path).read_text("utf-8"))
    assert payload["holdout_reject_detail"] == "HOLDOUT_NO_ELIGIBLE_TRIALS"
    # dominant_rejection bleibt die modale PER-TRIAL-IS-Ursache (nicht mit der Confirm-Ursache
    # überschrieben, da nie ein Holdout-Lauf erreicht wurde).
    assert payload["dominant_rejection"] != "HOLDOUT_NO_ELIGIBLE_TRIALS"
