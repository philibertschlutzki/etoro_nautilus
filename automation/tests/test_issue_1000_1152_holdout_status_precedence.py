"""Issue #1000/#1152 (Katalog #1170, P0) — Status-Praezedenz maskiert das zuerst gescheiterte Gate
in 10/28 Studies.

Symptom (B-8): zehn Studies mit gescheitertem Holdout-Gate (PSR bis 0,143 unter Schwelle, negativer
Sortino) wurden als ``REJECTED_SELECTION_OVERFIT``/``REJECTED_BOUNDARY_SOLUTION`` ausgewiesen.

Root-Cause: ``elif not holdout_passed:`` stand HINTER ``if pbo_overfit:``/``elif boundary_overfit:``
in ``confirm.confirm_per_symbol_promotion``. ``pbo_overfit``/``boundary_overfit`` werden UNABHAENGIG
vom Holdout-Ergebnis berechnet — die Kette gab dem ZULETZT geprueften, nicht dem ZUERST verletzten
Kriterium den Vorrang.

Fix: ``not holdout_passed`` steht jetzt an ERSTER Stelle. Zusaetzlich ``blocking_stage``/
``all_failed_stages`` machen die gleichzeitige Verletzung mehrerer Kriterien sichtbar, ohne die
Ursachen-Attribution zu verfaelschen.
"""
import json
import itertools
from pathlib import Path

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
        "max_drawdown": 0.30, "deflated_selection": False,
        "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
        "oos_min_win_rate": 0.0, "holdout_bootstrap_ci": False,
        "promotion_correction_mode": "conjunction",
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


# --- Fixture-Ebene: die Klassifikationsfunktion direkt --------------------------------------------

def test_holdout_gate_family_details_map_to_holdout_stage():
    for detail in ("REJECT_HOLDOUT_GATE", "REJECT_HOLDOUT_BOOTSTRAP_CI"):
        status, stage = confirm._holdout_rejection_classification(detail)
        assert status == "REJECTED_ON_HOLDOUT"
        assert stage == "holdout"


def test_deflation_details_map_to_deflation_stage():
    for detail in ("REJECT_HOLDOUT_DSR_DROP", "REJECT_DEFLATION_HETEROGENEOUS"):
        status, stage = confirm._holdout_rejection_classification(detail)
        assert status == "REJECTED_ON_DEFLATION"
        assert stage == "deflation"


def test_before_holdout_details_map_to_confirm_or_selection_stage():
    for detail in ("REJECT_PROMOTED_TRIAL_INADMISSIBLE", "REJECT_STUDY_INVARIANT_BLOCKING",
                   "REJECT_COHERENCE_VIOLATION", "REJECT_INVALID_TIMEBOX",
                   "REJECT_HOLDOUT_UNREACHABLE"):
        status, stage = confirm._holdout_rejection_classification(detail)
        assert status == "REJECTED_BEFORE_HOLDOUT"
        assert stage == "confirm_or_selection"


# --- End-to-End: die Praezedenz-Kette selbst ------------------------------------------------------

def test_failed_holdout_gate_wins_over_pbo_overfit(tmp_path, monkeypatch):
    """Der zentrale B-8-Fall: ein Kandidat, dessen Holdout-Gate scheitert (negativer Sortino),
    UND dessen IS-Selektion gleichzeitig PBO-ueberfittet erscheint, wird als
    REJECT_HOLDOUT_GATE ausgewiesen -- NICHT als REJECTED_SELECTION_OVERFIT."""
    global_params = {"price_breakout_period": 20}
    # Ein PBO-ueberfitteter Cohort: extreme Streuung zwischen IS-Trials.
    study = _build_study(tmp_path, monkeypatch, n_trials=6,
                         cohort_periods=[0.02, 5.0, -3.0, 8.0, -6.0, 9.0])
    # Symbol-Holdout scheitert (negativer Sortino, DD-Cap verletzt) -- das Basisgate ist tot.
    bad_result = _result_payload(sortino_ratio=-1.0, dd=0.9, sortino_period=-0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=bad_result,
                                      global_result=global_result),
    )
    assert res["holdout_passed"] is False
    assert res["status"] == "REJECTED_ON_HOLDOUT"
    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_GATE"
    assert res["blocking_stage"] == "holdout"
    # Der PBO-Befund (falls vorhanden) bleibt in all_failed_stages sichtbar, verliert aber die
    # Attribution.
    assert res["status"] != "REJECTED_SELECTION_OVERFIT"


def test_all_failed_stages_lists_every_simultaneous_violation(tmp_path, monkeypatch):
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025])
    bad_result = _result_payload(sortino_ratio=-1.0, dd=0.9, sortino_period=-0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=bad_result,
                                      global_result=global_result),
    )
    assert res["holdout_passed"] is False
    assert "holdout" in res["all_failed_stages"]
    assert res["blocking_stage"] == res["all_failed_stages"][0]


def test_promoted_candidate_has_no_blocking_stage(tmp_path, monkeypatch):
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025])
    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.05, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["status"] == "READY_FOR_PR"
    assert res["blocking_stage"] is None
    assert res["all_failed_stages"] == []
