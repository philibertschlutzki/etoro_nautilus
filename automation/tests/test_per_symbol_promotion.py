"""A4.5b — Gate 3: confirm_per_symbol_promotion + export_symbol_proposal.

Fully mocked backtest (HI-7). The fake's Sortino depends on whether the manifest's
resolved params carry the tuned values, so the symbol-tuned vs. global vectors are
distinguishable on the same holdout. The warm-start pins the best_trial to the
tuned params deterministically.
"""
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config


def _cfg_dir(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    # Provide necessary mock configs
    with open(cfg_dir / "optimizer.json", "w", encoding="utf-8") as f:
        json.dump({"promotion_margin": 0.10, "penalty_unevaluable_oos": -10, "unevaluable_shaping_span": 0.25, "sortino_clip_abs": 5.0, "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0, "bonus_coverage_weight": 1.0, "evaluable_floor_epsilon": 0.001, "lambda_reg": 0.25}, f)
    with open(cfg_dir / "tournament.json", "w", encoding="utf-8") as f:
        json.dump({"max_drawdown": 0.30}, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120, "oos_window_days": 45, "splits": 1, "embargo_period_days": 0}}, f)
    return cfg_dir

def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path))
    # Issue #622 — diese Tests prüfen den Promotions-FLUSS (Edge über Global), nicht das Randlösungs-
    # Veto. Der synthetische Warm-Start-Winner (sma_period=60) läge sonst an einer Suchraumgrenze und
    # würde #622-korrekt als REJECTED_BOUNDARY_SOLUTION geblockt. Randlösungs-Fraktion neutralisieren.
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)



def _factory_by_params(tuned_keyvals, sortino_tuned, sortino_global, dd=0.05):
    """High Sortino when the manifest params carry the tuned values, else low."""
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        is_tuned = all(params.get(k) == v for k, v in tuned_keyvals.items())
        s = sortino_tuned if is_tuned else sortino_global
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [s],
            # Issue #958 — oos_n_periods > 0 macht diesen Trial admissible (ein real evaluierter
            # Trial haette in der Praxis stets > 0 OOS-Perioden).
            "oos_metrics": {"sortino_ratio": s, "max_drawdown": dd, "n_periods": 50}}}), "utf-8")
        return out
    return _fake


def _study(tmp_path, monkeypatch, tuned):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _factory_by_params(tuned, 2.0, 0.5))
    # warm-start with the tuned seed so the (single) best_trial carries tuned params
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: dict(tuned))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=1)
    assert study.best_trial.user_attrs["sampled_params"]["sma_period"] == tuned["sma_period"]
    return study


def test_promotion_pass(tmp_path, monkeypatch):
    tuned = {"sma_period": 60}
    study = _study(tmp_path, monkeypatch, tuned)
    res = confirm.confirm_per_symbol_promotion(study, "SmaCrossoverStrategy", "AAA.ETORO",
                                               global_params={"sma_period": 20},
                                               run_backtest=_factory_by_params(tuned, 2.0, 0.5))
    assert res["promote"] is True and res["status"] == "READY_FOR_PR"
    assert res["R_symbol"] > res["R_global"] + res["promotion_margin"]
    p = confirm.export_symbol_proposal(study, "SmaCrossoverStrategy", "AAA.ETORO", res)
    written = json.loads(Path(p).read_text("utf-8"))
    assert written["status"] == "READY_FOR_PR"
    assert written["proposed_instrument_override"]["sma_period"] == 60
    assert Path(p).name == "proposal_SmaCrossoverStrategy_AAA.ETORO.json"


def test_promotion_no_edge(tmp_path, monkeypatch):
    tuned = {"sma_period": 60}
    study = _study(tmp_path, monkeypatch, tuned)
    # symbol and global perform equally on the holdout -> no margin
    res = confirm.confirm_per_symbol_promotion(study, "SmaCrossoverStrategy", "AAA.ETORO",
                                               global_params={"sma_period": 20},
                                               run_backtest=_factory_by_params(tuned, 1.0, 1.0))
    assert res["promote"] is False and res["status"] == "REJECTED_NO_EDGE_OVER_GLOBAL"
    assert res["holdout_passed"] is True


def test_promotion_rejected_on_holdout(tmp_path, monkeypatch):
    tuned = {"sma_period": 60}
    study = _study(tmp_path, monkeypatch, tuned)
    # symbol fails the holdout gate itself (negative sortino, drawdown over the cap)
    res = confirm.confirm_per_symbol_promotion(study, "SmaCrossoverStrategy", "AAA.ETORO",
                                               global_params={"sma_period": 20},
                                               run_backtest=_factory_by_params(tuned, -0.5, -0.9, dd=0.9))
    assert res["status"] == "REJECTED_ON_HOLDOUT"
    assert res["promote"] is False
    assert res["holdout_passed"] is False


def test_export_does_not_touch_strategies_json(tmp_path, monkeypatch):
    """Gate 3 only emits a proposal JSON; it must never write strategies.json (HI-3)."""
    tuned = {"sma_period": 60}
    study = _study(tmp_path, monkeypatch, tuned)
    res = confirm.confirm_per_symbol_promotion(study, "SmaCrossoverStrategy", "AAA.ETORO",
                                               global_params={"sma_period": 20},
                                               run_backtest=_factory_by_params(tuned, 2.0, 0.5))
    confirm.export_symbol_proposal(study, "SmaCrossoverStrategy", "AAA.ETORO", res)
    # proposal lands under the isolated WORK, and no strategies.json is created there
    assert (tmp_path / "proposal_SmaCrossoverStrategy_AAA.ETORO.json").exists()
    assert not (tmp_path / "strategies.json").exists()
