"""Issue #1381 (GH #1283, Pitfall #502) — Config-Profile: Overlay-Erzeugung, Stempel, Champion-Sperre und die
14. Deployment-Klausel ``config_profile_production`` (fail-closed)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from automation.optimizer import champions, config_profile, deployment_gate, summary_de

_REPO = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(_REPO / "automation" / "config", root / "automation" / "config")
    return root


def test_materialize_smoke_overlays_and_stamps(tmp_path):
    root = _project(tmp_path)
    overlay = config_profile.materialize("smoke", project_root=root)
    assert overlay == (root / "automation" / "config_smoke").resolve()
    opt = json.loads((overlay / "optimizer.json").read_text("utf-8"))
    assert opt["config_profile"] == "smoke" and opt["champion_enabled"] is False
    assert opt["gate1_buffer_days"] == 0
    bt = json.loads((overlay / "backtest.json").read_text("utf-8"))
    assert bt["walk_forward"]["holdout_days"] == 14
    assert not (overlay / "config_profiles.json").exists()
    # Basis bleibt unverändert.
    base_opt = json.loads((root / "automation/config/optimizer.json").read_text("utf-8"))
    assert base_opt["config_profile"] == "production"


def test_overlay_must_live_under_project_automation(tmp_path):
    root = _project(tmp_path)
    with pytest.raises(config_profile.ConfigProfileError):
        config_profile.materialize("smoke", project_root=root, dest=tmp_path / "elsewhere")
    with pytest.raises(config_profile.ConfigProfileError):
        config_profile.materialize("production", project_root=root)
    with pytest.raises(config_profile.ConfigProfileError):
        config_profile.materialize("nope", project_root=root)


def test_deep_merge_keeps_sibling_keys():
    out = config_profile.deep_merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}})
    assert out == {"a": {"x": 1, "y": 3}, "b": 1}


def test_champion_store_locked_outside_production():
    assert champions.champion_store_enabled({"champion_enabled": True, "config_profile": "production"})
    assert champions.champion_store_enabled({"champion_enabled": True})
    assert not champions.champion_store_enabled({"champion_enabled": True, "config_profile": "smoke"})


def test_clause_14_is_fail_closed_on_missing_field():
    clause = deployment_gate._clause_config_profile_production
    assert clause({"config_profile": "production"}) is True
    assert clause({"config_profile": "smoke"}) is False
    assert clause({"status": "ready_for_pr"}) is None
    assert clause(None) is None


def test_summary_banner_first_line_only_for_non_production():
    smoke = summary_de.generate_german_summary({"run_id": "r", "config_profile": "smoke"})
    assert smoke.splitlines()[0].startswith("**SMOKE — keine Evidenz**")
    prod = summary_de.generate_german_summary({"run_id": "r", "config_profile": "production"})
    assert "keine Evidenz**" not in prod.splitlines()[0]
    assert config_profile.banner("production") is None


def test_gitignore_excludes_overlays():
    assert "automation/config_*/" in (_REPO / ".gitignore").read_text("utf-8").splitlines()
