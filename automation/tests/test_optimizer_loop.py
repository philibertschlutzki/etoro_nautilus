import json
from pathlib import Path
import optuna
from automation.optimizer import run_optimization as ro
from automation.optimizer import spaces, confirm
from automation.optimizer import trial_config

def _fake_backtest_factory(sortino, dd, evaluated=True, eligible=True, win=3):
    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "fully_eligible_pairs": 1,
            "per_symbol_winners": {"BTCUSD": {}, "ETHUSD": {}}, # Universe size 2 for testing
            "aggregate_winner": {
                "oos_evaluated": evaluated,
                "oos_eligible": eligible,
                "win_count": win,
                "median_is_sortino": 1.0,
                "oos_fold_sortinos": [sortino],
                "oos_metrics": {
                    "sortino_ratio": sortino,
                    "max_drawdown": dd
                }
            }
        }), "utf-8")
        return out
    return _fake

def test_spaces_sma_keys():
    t = optuna.trial.FixedTrial({"sma_period": 20, "cooldown_bars": 10})
    p = spaces.sample_params("SmaCrossoverStrategy", t)
    assert set(p.keys()) == {"sma_period", "cooldown_bars"}

def test_optimize_creates_db_and_proposal(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest_factory(1.5, 0.1))

    # Isolate storage to tmp_path across all relevant modules
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(ro, "STORAGE", f"sqlite:///{tmp_path / 'studies.db'}")
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(confirm, "config_dir", lambda: Path("automation/config"))

    study = ro.optimize("SmaCrossoverStrategy", n_trials=2)
    assert len(study.trials) == 2
    assert study.trials[0].params
    assert (tmp_path / "studies.db").exists()

def test_holdout_pass_and_reject(tmp_path, monkeypatch):
    # Setup isolated paths
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(ro, "STORAGE", f"sqlite:///{tmp_path / 'studies.db'}")
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(confirm, "config_dir", lambda: Path("automation/config"))

    # passing case
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest_factory(1.2, 0.1))

    study = ro.optimize("SmaCrossoverStrategy", n_trials=2)

    res = confirm.confirm_on_holdout(
        study, "SmaCrossoverStrategy",
        run_backtest=_fake_backtest_factory(1.2, 0.1)
    )
    assert res["passed"] is True

    p = confirm.export_proposal(study, "SmaCrossoverStrategy", res)
    assert json.loads(Path(p).read_text("utf-8"))["status"] == "READY_FOR_PR"

    # failing case
    res2 = confirm.confirm_on_holdout(
        study, "SmaCrossoverStrategy",
        run_backtest=_fake_backtest_factory(-0.5, 0.5)
    )
    p2 = confirm.export_proposal(study, "SmaCrossoverStrategy", res2)
    assert json.loads(Path(p2).read_text("utf-8"))["status"] == "REJECTED_ON_HOLDOUT"
