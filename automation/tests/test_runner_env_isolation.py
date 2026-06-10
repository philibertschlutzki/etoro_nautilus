import pytest
import os
from pathlib import Path
import automation.backtest_runner as runner
import automation.daily_orchestrator as orch

def test_config_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ETORO_CONFIG_DIR", str(tmp_path / "cfg"))
    assert runner.config_dir() == tmp_path / "cfg"
    assert orch.config_dir() == tmp_path / "cfg"

def test_logs_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ETORO_LOGS_DIR", str(tmp_path / "logs"))
    assert runner.logs_dir() == tmp_path / "logs"
    assert orch.logs_dir() == tmp_path / "logs"

def test_defaults_without_env(monkeypatch):
    monkeypatch.delenv("ETORO_CONFIG_DIR", raising=False)
    assert runner.config_dir().name == "config"
