"""A4.5a — optimize_symbol: single-symbol study, per-study SQLite, Gate-2 warm start.

End-to-end with a fully mocked backtest (HI-7: no real subprocess). WORK and
config_dir are isolated to tmp_path / the real config the way test_optimizer_loop does.
"""
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_factory(captured):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        m = json.loads(Path(manifest_path).read_text("utf-8"))
        captured.append(m)   # hold the manifest for assertions
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
            "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05}}}), "utf-8")
        return out
    return _fake


def test_sanitize():
    assert ro._sanitize("TSLA.ETORO") == "TSLA_ETORO"


def test_load_global_best_prefers_ready_proposal(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    (tmp_path / "proposal_SmaCrossoverStrategy.json").write_text(json.dumps({
        "status": "READY_FOR_PR", "proposed_params_override": {"sma_period": 42}}), "utf-8")
    assert ro.load_global_best("SmaCrossoverStrategy", Path("automation/config")) == {"sma_period": 42}


def test_load_global_best_rejected_proposal_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    # a non-READY proposal must NOT be used as the warm-start seed
    (tmp_path / "proposal_SmaCrossoverStrategy.json").write_text(json.dumps({
        "status": "REJECTED_ON_HOLDOUT", "proposed_params_override": {"sma_period": 999}}), "utf-8")
    out = ro.load_global_best("SmaCrossoverStrategy", Path("automation/config"))
    assert out.get("sma_period") != 999   # fell back to strategies.json (params = {})


def test_optimize_symbol_manifest_and_db(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    cap = []
    monkeypatch.setattr(ro, "run_backtest", _fake_factory(cap))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=2)
    assert study.study_name == "study_SmaCrossoverStrategy_TSLA_ETORO"
    assert (tmp_path / "sweep" / "study_SmaCrossoverStrategy_TSLA_ETORO.db").exists()
    assert cap, "backtest mock must have been invoked"
    assert all(m["global_settings"]["instruments"] == ["TSLA.ETORO"] for m in cap)


def test_optimize_symbol_warm_start_enqueues_global(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_factory([]))
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {"sma_period": 33, "cooldown_bars": 7})
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=1)
    # the first (enqueued) trial carries the global best params (Gate 2 warm start)
    assert study.trials[0].params.get("sma_period") == 33


def test_global_optimize_unchanged(tmp_path, monkeypatch):
    """Regression: the global single-strategy study name/path are untouched by A4.5a."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "STORAGE", f"sqlite:///{tmp_path / 'studies.db'}")
    monkeypatch.setattr(ro, "run_backtest", _fake_factory([]))
    study = ro.optimize("SmaCrossoverStrategy", n_trials=1)
    assert study.study_name == "study_SmaCrossoverStrategy"
    assert (tmp_path / "studies.db").exists()
