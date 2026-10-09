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


def test_enter_papertrading_needs_no_env_switch(monkeypatch):
    import os
    monkeypatch.delenv("ETORO_ENV", raising=False)
    monkeypatch.delenv("ETORO_DRY_RUN", raising=False)
    monkeypatch.setattr(orch, "load_dotenv", lambda *_a, **_k: None)
    orch._enter_papertrading(orch.logging.getLogger("t"))
    assert "ETORO_ENV" not in os.environ and "ETORO_DRY_RUN" not in os.environ


# ─── End-to-End mit Mocks (keine eToro-Keys im CI): zu wenig Daten ⇒ sauberer No-Trade-Lauf ───────────

def _stub_main(monkeypatch, tmp_path, *, depth_days):
    import sys
    monkeypatch.setattr(sys, "argv", ["daily_orchestrator.py", "--papertrading", "--offline"])
    monkeypatch.setattr(orch, "load_dotenv", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "IMPORT_PATH", tmp_path / "imp")
    monkeypatch.setattr(orch, "REPORTS_DIR", tmp_path / "rep")
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_setup_orchestrator_logging", lambda: orch.logging.getLogger("e2e"))
    monkeypatch.setattr(orch, "cleanup_old_logs", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(orch, "phase1_universe_and_mapping", lambda *a, **k: {})
    monkeypatch.setattr(orch, "phase2_data_acquisition", lambda *a, **k: {})
    monkeypatch.setattr(pt, "measure_depth_days", lambda *_a, **_k: depth_days)
    monkeypatch.delenv("ETORO_ENV", raising=False)
    calls = []
    monkeypatch.setattr(orch, "phase5b_incubation", lambda *a, **k: calls.append(k) or {"status": "no_deploy"})
    monkeypatch.setattr(orch, "phase5_live_deployment", lambda *a, **k: pytest.fail("Phase 5 darf entfallen"))
    return calls


def test_e2e_too_little_data_is_a_clean_no_trade_run(monkeypatch, tmp_path):
    calls = _stub_main(monkeypatch, tmp_path, depth_days=10.0)
    assert orch.main() == 0
    assert calls == []                                   # kein Bot, keine Orders


def test_e2e_enough_data_hands_derived_geometry_to_incubation(monkeypatch, tmp_path):
    calls = _stub_main(monkeypatch, tmp_path, depth_days=99.0)
    assert orch.main() == 0
    (kw,) = calls
    assert kw["inc_cfg_override"]["enabled"] is True
    assert kw["inc_cfg_override"]["walk_forward"]["splits"] == pt.SPLITS


def test_e2e_real_env_aborts_before_any_phase(monkeypatch, tmp_path):
    _stub_main(monkeypatch, tmp_path, depth_days=99.0)
    monkeypatch.setenv("ETORO_ENV", "real")
    monkeypatch.setattr(orch, "phase1_universe_and_mapping", lambda *a, **k: pytest.fail("kein Netzzugriff"))
    assert orch.main() == 2


def test_phase5b_without_winners_starts_no_bot(tmp_path, monkeypatch):
    """Selektion ohne Gewinner (wenig Daten) ⇒ Zyklus läuft durch, es wird kein Demo-Bot gestartet."""
    from automation.tests.test_issue_1368_incubation import _orch_env, _Popen
    o = _orch_env(tmp_path, monkeypatch)
    popen = _Popen()
    res = o.phase5b_incubation(o.logging.getLogger("t"), popen=popen, selection_fn=lambda *a: None)
    assert res["status"] != "error" and popen.calls == []
