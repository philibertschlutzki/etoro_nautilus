import json
import optuna
from pathlib import Path
from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config

# Issue #796 — ``optimize``/``optimize_symbol`` friert jetzt VOR ``study.optimize`` eine Study-Config
# ein (``freeze_study_config``); dafuer muss ``config/backtest.json`` existieren UND ``trial_config.
# WORK`` (nicht nur ``ro.WORK``) isoliert sein, sonst schreibt der Freeze in den echten Repo-Pfad.
_MIN_BACKTEST_JSON = {"walk_forward": {"is_window_days": 120, "oos_window_days": 30,
                                       "splits": 4, "holdout_days": 45}}


def test_optimize_symbol_forces_n_jobs_1(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path / "config")
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    with open(tmp_path / "config" / "backtest.json", "w", encoding="utf-8") as f:
        json.dump(_MIN_BACKTEST_JSON, f)

    calls = []
    class DummyStudy:
        study_name = "test"
        def set_user_attr(self, k, v): pass
        def enqueue_trial(self, params): pass
        def optimize(self, obj, n_trials=None, n_jobs=None, **kwargs):
            calls.append(n_jobs)

    monkeypatch.setattr(ro.optuna, "create_study", lambda **k: DummyStudy())
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})

    ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=5)

    assert calls == [1], "optimize_symbol must enforce n_jobs=1 for SQLite determinism"

def test_optimize_reads_sampler_config(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path / "config")
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    with open(tmp_path / "config" / "backtest.json", "w", encoding="utf-8") as f:
        json.dump(_MIN_BACKTEST_JSON, f)

    with open(tmp_path / "config" / "optimizer.json", "w", encoding="utf-8") as f:
        json.dump({"seed": 99, "n_startup_trials": 42}, f)

    sampler_calls = []
    class DummySampler:
        pass

    def mock_sampler(**kwargs):
        sampler_calls.append(kwargs)
        return DummySampler()

    monkeypatch.setattr(ro.optuna.samplers, "TPESampler", mock_sampler)

    class DummyStudy:
        study_name = "test"
        def set_user_attr(self, k, v): pass
        def enqueue_trial(self, params): pass
        def optimize(self, obj, n_trials=None, n_jobs=None, **kwargs): pass

    monkeypatch.setattr(ro.optuna, "create_study", lambda **k: DummyStudy())
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})

    ro.optimize("SmaCrossoverStrategy", n_trials=1)

    assert len(sampler_calls) == 1
    assert sampler_calls[0]["seed"] == 99
    assert sampler_calls[0]["n_startup_trials"] == 42
    assert sampler_calls[0].get("multivariate") is True

