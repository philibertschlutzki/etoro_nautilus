"""Paper-Trading-Modus: abgeleitete Geometrie, Demo-Sperre, Overlay-Stempel, CLI-Flag."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from automation import daily_orchestrator as orch
from automation import papertrading as pt
from automation.optimizer import config_profile

_REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("days", [45, 96, 99, 444])
def test_geometry_fits_depth(days):
    wf = pt.derive_walk_forward(days)
    total = (wf["is_window_days"] + wf["embargo_period_days"] + wf["splits"] * wf["oos_window_days"]
             + wf["holdout_days"] + wf["holdout_embargo_days"])
    assert total <= days - pt.MARGIN_DAYS
    assert min(wf["is_window_days"], wf["oos_window_days"], wf["holdout_days"]) > 0


def test_too_shallow_depth_fails_loud():
    with pytest.raises(pt.PaperTradingError):
        pt.derive_walk_forward(20)


@pytest.mark.parametrize("env", ["real", "REAL", "prod"])
def test_real_environment_is_refused(env):
    with pytest.raises(pt.PaperTradingError):
        pt.assert_demo_environment(env)


@pytest.mark.parametrize("env", [None, "", "demo", "Demo"])
def test_demo_environment_allowed(env):
    pt.assert_demo_environment(env)


def test_overlay_is_stamped_and_not_production(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(_REPO / "automation" / "config", root / "automation" / "config")
    overlay = pt.materialize_papertrading_profile(96, project_root=root)
    opt = json.loads((overlay / "optimizer.json").read_text("utf-8"))
    assert opt["config_profile"] == "papertrading" and opt["champion_enabled"] is False
    assert not config_profile.is_production(opt)
    bt = json.loads((overlay / "backtest.json").read_text("utf-8"))
    assert {k: bt["walk_forward"][k] for k in pt.derive_walk_forward(96)} == pt.derive_walk_forward(96)


def test_orchestrator_flag_and_real_abort(monkeypatch):
    assert orch.build_arg_parser().parse_args(["--papertrading"]).papertrading is True
    assert orch.build_arg_parser().parse_args([]).papertrading is False
    monkeypatch.setenv("ETORO_ENV", "real")
    monkeypatch.setattr(orch, "load_dotenv", lambda *_a, **_k: None)
    with pytest.raises(pt.PaperTradingError):
        orch._enter_papertrading(orch.logging.getLogger("t"))


def test_enter_papertrading_forces_demo_and_live_orders(monkeypatch):
    monkeypatch.delenv("ETORO_ENV", raising=False)
    monkeypatch.setattr(orch, "load_dotenv", lambda *_a, **_k: None)
    orch._enter_papertrading(orch.logging.getLogger("t"))
    import os
    assert os.environ["ETORO_ENV"] == "demo" and os.environ["ETORO_DRY_RUN"] == "0"
