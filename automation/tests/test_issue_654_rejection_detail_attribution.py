"""Issue #654 — `is_rejection_detail` im Proposal ist der modale IS-Grund, nicht die
Promotion-Ursache.

DynamicBreakout zeigte `is_rejection_detail: REJECT_OOS_MIN_TOTAL_RETURN`, obwohl das
Symbol-Holdout-Gate BESTANDEN war (eligible, Sortino +3.45) und die Promotion AUSSCHLIESSLICH vom
DSR-Drop (0.735 < 0.95) getötet wurde. Root-Cause: im Promotion-Pfad war
`is_rejection_detail_override` unconditional `None`, wodurch `export_symbol_proposal` auf den
modalen IS-Study-Trial-Grund zurückfiel — der mit der Holdout-/Promotion-Entscheidung nichts zu tun
hat.

Fix: jeder Promotion-Ausgang (DSR-Drop, Bootstrap-CI, PBO, Boundary, No-Edge, Symbol-Holdout-Gate-
Fail, READY_FOR_PR) setzt sein eigenes, korrektes `is_rejection_detail_override`. Der modale
IS-Grund bleibt separat als `dominant_is_rejection_detail` erhalten (Study-Diagnose).
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config


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
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)


def _result_payload(*, sortino_ratio, dd, sortino_period, n_periods, eligible=True,
                     total_return=0.02, total_trades=50, skew=0.0, kurtosis=3.0):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": eligible, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {
                "sortino_ratio": sortino_ratio, "max_drawdown": dd,
                "total_return": total_return, "total_trades": total_trades,
                "sortino_period": sortino_period, "n_periods": n_periods,
                "ret_skew": skew, "ret_kurtosis": kurtosis,
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

    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        sp = next(it)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=n_periods))
    return _fake


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_periods, tournament_extra=None):
    _isolate(monkeypatch, tmp_path, tournament_extra)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory(cohort_periods))
    return ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=n_trials)


def test_dsr_drop_sets_correct_override(tmp_path, monkeypatch, caplog):
    """DynamicBreakout-Referenzfall (#661): Holdout-Gate bestanden, R_symbol > R_global + margin,
    aber DSR < confidence ⇒ REJECT_HOLDOUT_DSR_DROP (NICHT der modale IS-Grund)."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025])

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    # Issue #1002/#1154 (Katalog #1170) — der DSR-Drop ist eine Deflations-Ablehnung (das Holdout-
    # Gate selbst war bestanden), traegt seither REJECTED_ON_DEFLATION statt des geteilten
    # REJECTED_ON_HOLDOUT.
    assert res["status"] == "REJECTED_ON_DEFLATION"
    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_DSR_DROP"

    proposal_path = confirm.export_symbol_proposal(study, "DynamicBreakoutStrategy", "TSLA.ETORO", res)
    payload = json.loads(Path(proposal_path).read_text("utf-8"))
    assert payload["is_rejection_detail"] == "REJECT_HOLDOUT_DSR_DROP"
    # Der modale IS-Grund bleibt separat erhalten (kann von der Promotion-Ursache abweichen).
    assert "dominant_is_rejection_detail" in payload


def test_holdout_gate_itself_failing_sets_correct_override(tmp_path, monkeypatch):
    """Das Symbol-Holdout-Gate selbst scheitert (negativer Sortino, DD-Cap verletzt) ⇒
    REJECT_HOLDOUT_GATE — unabhängig von DSR/CI (die laufen nur, wenn das Gate schon bestanden war)."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025])

    bad_result = _result_payload(sortino_ratio=-1.0, dd=0.9, sortino_period=-0.02, n_periods=200)
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=bad_result,
                                      global_result=bad_result),
    )
    assert res["status"] == "REJECTED_ON_HOLDOUT"
    assert res["holdout_passed"] is False
    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_GATE"


def test_no_edge_over_global_sets_correct_override(tmp_path, monkeypatch):
    """Holdout-Gate bestanden, DSR/CI/PBO/Boundary alle unauffällig, aber R_symbol schlägt R_global
    nicht um die Marge ⇒ REJECT_NO_EDGE_OVER_GLOBAL."""
    global_params = {"price_breakout_period": 20}
    # Grosse, homogene Kohorte ⇒ DSR bleibt unauffällig hoch bei ähnlichem Punkt-Sortino.
    study = _build_study(tmp_path, monkeypatch, n_trials=15,
                         cohort_periods=[0.5 + 0.001 * i for i in range(15)])

    equal_result = _result_payload(sortino_ratio=1.0, dd=0.05, sortino_period=0.5, n_periods=400)
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=equal_result,
                                      global_result=equal_result),
    )
    assert res["holdout_passed"] is True
    assert res["promote"] is False
    assert res["status"] == "REJECTED_NO_EDGE_OVER_GLOBAL"
    assert res["is_rejection_detail_override"] == "REJECT_NO_EDGE_OVER_GLOBAL"


def test_pbo_overfit_sets_correct_override(tmp_path, monkeypatch):
    """PBO > 0.5 (Selektions-Overfit) blockt die Promotion HART, unabhängig vom Holdout-Gate-Ausgang
    ⇒ REJECT_SELECTION_PBO."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025])

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=400)

    with_pbo = confirm._study_pbo
    try:
        # Issue #663 — _study_pbo liefert seit der feineren CSCV-Partition ein (pbo, telemetry)-Tupel.
        confirm._study_pbo = lambda study, **k: (0.75, {  # erzwungenes Selektions-Overfit-Signal
            "pbo_n_groups": 12, "pbo_n_configs": 12, "pbo_metric": "period_return"})
        res = confirm.confirm_per_symbol_promotion(
            study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
        )
    finally:
        confirm._study_pbo = with_pbo

    assert res["status"] == "REJECTED_SELECTION_OVERFIT"
    assert res["is_rejection_detail_override"] == "REJECT_SELECTION_PBO"


def test_boundary_overfit_sets_correct_override(tmp_path, monkeypatch):
    """Randlösungs-Veto (> 30 %, aber < 50 % der Params an der Suchraumgrenze) ⇒
    REJECT_BOUNDARY_SOLUTION. Issue #763 — ab 0.5 gilt stattdessen HOLD_BOUNDARY_UNRESOLVED (siehe
    test_boundary_unresolved_hold_sets_correct_override_and_writes_diagnosis_cache unten), 0.4
    bleibt im ursprünglichen #622-Tier."""
    global_params = {"price_breakout_period": 20}
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.4)  # erzwungene Randlösung
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([0.02, 0.025]))
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=400)
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["status"] == "REJECTED_BOUNDARY_SOLUTION"
    assert res["is_rejection_detail_override"] == "REJECT_BOUNDARY_SOLUTION"


def test_boundary_unresolved_hold_sets_correct_override_and_writes_diagnosis_cache(
        tmp_path, monkeypatch):
    """Issue #763 — >= 0.5 (die Hälfte der Gewinner-Parameter an der Grenze) ist ein STÄRKERES
    Signal als der #622-Veto: HOLD_BOUNDARY_UNRESOLVED statt eines terminalen REJECT, UND der
    Bounds-Vorschlag (Richtung aus _boundary_hit_directions) landet im #761-Diagnose-Cache."""
    global_params = {"price_breakout_period": 20}
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.9)  # erzwungene Randlösung
    monkeypatch.setattr(ro, "_boundary_hit_directions",
                        lambda *a, **k: {"cooldown_bars": "low", "max_bars_in_trade": "high"})
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([0.02, 0.025]))
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    recorded = []
    from automation.optimizer import sweep_diagnostics as sd
    monkeypatch.setattr(sd, "record_diagnosed_pair", lambda rec, **k: recorded.append(rec))

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=400)
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["status"] == "HOLD_BOUNDARY_UNRESOLVED"
    assert res["is_rejection_detail_override"] == "HOLD_BOUNDARY_UNRESOLVED"
    assert res["promote"] is False
    assert len(recorded) == 1
    assert recorded[0]["strategy"] == "DynamicBreakoutStrategy"
    assert recorded[0]["symbol"] == "TSLA.ETORO"
    assert recorded[0]["binding_cause"] == "boundary_solution"
    assert "cooldown_bars" in recorded[0]["proposed_bounds"]
    assert "max_bars_in_trade" in recorded[0]["proposed_bounds"]


def test_ready_for_pr_has_no_rejection_override(tmp_path, monkeypatch):
    """Eine tatsächliche Promotion (READY_FOR_PR) trägt keine Ablehnungs-Ursache (None) —
    nicht die IS-Study-Rejection-Reason irgendeines anderen Trials."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.5, 0.55])

    symbol_result = _result_payload(sortino_ratio=3.0, dd=0.05, sortino_period=0.6, n_periods=400)
    global_result = _result_payload(sortino_ratio=0.2, dd=0.05, sortino_period=0.05, n_periods=400)
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["status"] == "READY_FOR_PR"
    assert res["promote"] is True
    assert res["is_rejection_detail_override"] is None
