import json, types
from pathlib import Path
import pytest
from automation.optimizer import runner

def _make_trial(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    m = {"global_settings": {"catalog_path": "data/nautilus"}}
    mp = tmp_path / "experiment_manifest.json"
    mp.write_text(json.dumps(m), "utf-8")
    return tmp_path, mp

def test_run_backtest_invocation_and_env(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["env"] = kw["env"]
        captured["timeout"] = kw["timeout"]
        # Erfolg simulieren (Output schreiben)
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    out = runner.run_backtest(trial_dir, mp)

    assert out == trial_dir / "tournament_result.json"
    assert "backtest_runner.py" in " ".join(captured["argv"])
    assert "--config" in captured["argv"] and str(mp) in captured["argv"]
    assert captured["env"]["ETORO_CONFIG_DIR"] == str(trial_dir / "config")
    assert captured["env"]["ETORO_LOGS_DIR"] == str(trial_dir / "logs")
    assert captured["timeout"] == 10800

def test_run_backtest_missing_output_raises(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)

    def fake_fail(*a, **k):
        return types.SimpleNamespace(returncode=1) # Erzeugt KEINE Datei

    monkeypatch.setattr(runner.subprocess, "run", fake_fail)

    with pytest.raises(FileNotFoundError):
        runner.run_backtest(trial_dir, mp)
