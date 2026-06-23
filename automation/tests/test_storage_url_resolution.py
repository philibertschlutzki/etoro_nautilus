"""A4.7 — configurable Optuna storage URL (SQLite default, Postgres opt-in).

Only URL resolution is tested — never a real Postgres connection (HI-7).
Priority: ENV ETORO_OPTUNA_STORAGE > optimizer.json['storage_url'] > sqlite default.
"""
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro


def test_default_is_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.delenv("ETORO_OPTUNA_STORAGE", raising=False)
    url = ro.resolve_storage(study_name="study_X_A_ETORO")
    assert url.startswith("sqlite:///") and "study_X_A_ETORO.db" in url


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setenv("ETORO_OPTUNA_STORAGE", "postgresql://u:p@db/opt")
    assert ro.resolve_storage(study_name="s") == "postgresql://u:p@db/opt"


def test_json_storage_url_overrides_default(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.delenv("ETORO_OPTUNA_STORAGE", raising=False)
    (tmp_path / "optimizer.json").write_text(json.dumps({"storage_url": "postgresql://h/db"}), "utf-8")
    assert ro.resolve_storage(study_name="s", base_cfg=tmp_path) == "postgresql://h/db"


def test_env_beats_json(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setenv("ETORO_OPTUNA_STORAGE", "postgresql://env/win")
    (tmp_path / "optimizer.json").write_text(json.dumps({"storage_url": "postgresql://json/lose"}), "utf-8")
    assert ro.resolve_storage(study_name="s", base_cfg=tmp_path) == "postgresql://env/win"


def test_json_null_falls_back_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.delenv("ETORO_OPTUNA_STORAGE", raising=False)
    (tmp_path / "optimizer.json").write_text(json.dumps({"storage_url": None}), "utf-8")
    url = ro.resolve_storage(study_name="s", base_cfg=tmp_path)
    assert url.startswith("sqlite:///") and url.endswith("s.db")


def test_repo_optimizer_json_default_is_null():
    """The committed config keeps SQLite as the strict default (storage_url = null)."""
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["storage_url"] is None
