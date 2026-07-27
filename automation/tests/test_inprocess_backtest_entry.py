"""A4.9 — in-process backtest entry + fault isolation + config-sharing.

Fully mocked (HI-7): no real backtest. Verifies the runner's mode dispatch
(subprocess default unchanged; inprocess fault isolation) and the build_trial
config-sharing flag.
"""
import json
import types
from pathlib import Path

import optuna
import pytest

from automation.optimizer import runner, trial_config


def _make_trial(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    mp = tmp_path / "experiment_manifest.json"
    mp.write_text(json.dumps({"global_settings": {"catalog_path": "data/nautilus"}}), "utf-8")
    return tmp_path, mp


# --- runner fault isolation (inprocess mode) -------------------------------
def test_inprocess_trial_exception_becomes_pruned(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)

    def boom(manifest_path, output_path):
        raise RuntimeError("trial-level")

    monkeypatch.setattr(runner, "run_backtest_inprocess", boom, raising=False)
    with pytest.raises(optuna.TrialPruned):
        runner.run_backtest(trial_dir, mp, mode="inprocess")


def test_inprocess_import_error_fails_fast(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)

    def boom(manifest_path, output_path):
        raise ImportError("fundamental")

    monkeypatch.setattr(runner, "run_backtest_inprocess", boom, raising=False)
    with pytest.raises(ImportError):
        runner.run_backtest(trial_dir, mp, mode="inprocess")


def test_inprocess_missing_output_pruned(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)
    # entry "succeeds" but writes no tournament_result.json -> pruned, not a hard crash
    monkeypatch.setattr(runner, "run_backtest_inprocess", lambda m, o: None, raising=False)
    with pytest.raises(optuna.TrialPruned):
        runner.run_backtest(trial_dir, mp, mode="inprocess")


def test_inprocess_success_returns_output(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)

    def ok(manifest_path, output_path):
        Path(output_path).write_text("{}", "utf-8")

    monkeypatch.setattr(runner, "run_backtest_inprocess", ok, raising=False)
    out = runner.run_backtest(trial_dir, mp, mode="inprocess")
    assert out == trial_dir / "tournament_result.json" and out.exists()


# --- subprocess default unchanged (regression) -----------------------------
def test_subprocess_mode_unchanged(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out = runner.run_backtest(trial_dir, mp)   # default mode="subprocess"
    assert out == trial_dir / "tournament_result.json"
    assert "backtest_runner.py" in " ".join(captured["argv"])


# --- importable in-process entry -------------------------------------------
def test_run_backtest_inprocess_is_importable_and_validates():
    from automation.backtest_runner import run_backtest_inprocess
    assert callable(run_backtest_inprocess)
    # a manifest without catalog_path must raise (no heavy backtest needed)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "m.json"
        mp.write_text(json.dumps({"global_settings": {}}), "utf-8")
        with pytest.raises(ValueError):
            run_backtest_inprocess(mp, Path(td) / "out.json")


# --- config-sharing flag (build_trial) -------------------------------------
def test_build_trial_copy_config_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))
    import datetime as dt
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.timezone.utc)

    # default: per-trial config copy happens (backward-compatible)
    td_copy, _ = trial_config.build_trial("SmaCrossoverStrategy", {}, study_name="s_copy",
                                          trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4)
    assert (td_copy / "config" / "backtest.json").exists()

    # Issue #796 — copy_config=False OHNE study_config_dir ist ein Konfigurationsfehler (kein
    # stiller Fallback auf die Repo-Config, die ein falsches walk_forward tragen wuerde).
    with pytest.raises(ValueError):
        trial_config.build_trial("SmaCrossoverStrategy", {}, study_name="s_share_missing",
                                 trial_number=0, seed=42, now=now, holdout_days=45,
                                 n_folds=4, copy_config=False)

    # config-sharing: EINE eingefrorene Study-Config (freeze_study_config) statt einer Pro-Trial-Kopie
    wf_settings = trial_config.resolve_wf_settings(
        Path("automation/config"), holdout_days=45, n_folds=4)
    study_cfg_dir = trial_config.freeze_study_config("s_share", wf_settings,
                                                      base_cfg=Path("automation/config"))
    assert (study_cfg_dir / "backtest.json").exists()
    td_share, mp_share = trial_config.build_trial("SmaCrossoverStrategy", {}, study_name="s_share",
                                                  trial_number=0, seed=42, now=now, holdout_days=45,
                                                  n_folds=4, copy_config=False,
                                                  study_config_dir=study_cfg_dir)
    assert not (td_share / "config" / "backtest.json").exists()
    # the manifest is still written and self-describing
    gs = json.loads(Path(mp_share).read_text("utf-8"))["global_settings"]
    assert gs["walk_forward"]["splits"] == 4 and "start_capital" in gs
