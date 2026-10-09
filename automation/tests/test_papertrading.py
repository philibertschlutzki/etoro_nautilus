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
    monkeypatch.setattr(pt, "measure_depths", lambda *_a, **_k: {"A.ETORO": depth_days, "B.ETORO": depth_days})
    monkeypatch.setattr(pt, "min_oos_days", lambda *_a, **_k: 1)
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


# ─── Tiefe/Geometrie aus echten Katalogwerten (summary.csv: Krypto 41,7 d, Aktien ~60 d) ───────────────────

def test_crypto_depth_41_6_days_no_longer_blocked():
    wf = pt.derive_walk_forward(41.6, oos_floor_days=4)
    assert sum([wf["is_window_days"], wf["embargo_period_days"], wf["splits"] * wf["oos_window_days"],
                wf["holdout_days"], wf["holdout_embargo_days"]]) <= 41.6 - pt.MARGIN_DAYS


def test_rth_stocks_at_60_days_fit_with_relaxed_gate():
    floor = pt.min_oos_days(["TSLA.ETORO", "NVDA.ETORO", "GOOGL.ETORO"])
    wf = pt.derive_walk_forward(60.0, oos_floor_days=floor)
    assert wf["oos_window_days"] >= floor
    assert pt.profile_spec(60.0, oos_floor_days=floor)["optimizer.json"]["min_oos_session_bars_per_fold"] \
        == pt.RELAXED_MIN_OOS_SESSION_BARS


def test_too_shallow_error_names_required_days():
    with pytest.raises(pt.PaperTradingError, match="mindestens"):
        pt.derive_walk_forward(30.0, oos_floor_days=13)


def test_pick_depth_uses_quantile_not_minimum():
    depths = {"BTC": 41.7, "ETH": 41.7, **{f"S{i}": 60.0 for i in range(10)}, "HK": 249.0}
    assert pt.pick_depth(depths) == 60.0 and pt.pick_depth({}) == 0.0


# ─── Demo-Order-Check (gemockte HTTP-Schicht) ────────────────────────────────────────────────────

def test_demo_order_check_payloads_and_parsing():
    from automation import demo_order_check as d
    assert d.load_instrument_id("BTC.ETORO") == 100000
    assert d.open_payload(100000, 50)["Amount"] == 50.0 and d.open_payload(100000, 50)["IsBuy"] is True
    assert d.close_payload(1) == {"InstrumentID": 1, "UnitsToDeduct": None}
    pnl = {"clientPortfolio": {"credit": 10000, "positions": [{"positionID": 7, "instrumentID": 100000}]}}
    assert d.open_positions(pnl) == {"7": 100000} and d.credit(pnl) == 10000.0


def test_demo_order_check_full_cycle_with_fake_session():
    import asyncio
    from automation import demo_order_check as d

    state = {"open": {}}

    class Resp:
        def __init__(self, status, body): self.status, self._b = status, body
        async def text(self): return json.dumps(self._b)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def get(self, url, headers=None):
            assert "/info/demo/pnl" in url
            return Resp(200, {"clientPortfolio": {"credit": 10000, "positions": [
                {"positionID": k, "instrumentID": v} for k, v in state["open"].items()]}})
        def post(self, url, json=None, headers=None):
            assert "/execution/demo/" in url and "/execution/real" not in url
            if "market-open-orders" in url:
                state["open"]["99"] = json["InstrumentID"]
            else:
                state["open"].pop(url.rsplit("/", 1)[1], None)
            return Resp(200, {})

    async def no_sleep(_): return None
    import unittest.mock as m
    msgs = []
    with m.patch.object(d.asyncio, "sleep", no_sleep):
        rc = asyncio.run(d.run_check("k", "u", place_test_order=True, symbol="BTC.ETORO", amount=50,
                                     out=msgs.append, session_factory=Session))
    assert rc == 0 and state["open"] == {} and any("Stufe 3" in x for x in msgs)


# ─── Tagesachse: 1000 OneDay-Kerzen ≈ 4 Jahre ────────────────────────────────────────────────────

def test_daily_geometry_fits_four_years_and_crypto_depth():
    for depth in (1500.0, 1000.0):
        wf, bars = pt.derive_walk_forward_daily(depth)
        total = (wf["is_window_days"] + wf["embargo_period_days"] + wf["splits"] * wf["oos_window_days"]
                 + wf["holdout_days"] + wf["holdout_embargo_days"])
        assert total <= depth - pt.MARGIN_DAYS and bars in (91, 49)
    with pytest.raises(pt.PaperTradingError, match="mindestens"):
        pt.derive_walk_forward_daily(200.0)


def test_daily_spec_sets_axis_and_stays_non_production(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(_REPO / "automation" / "config", root / "automation" / "config")
    overlay = pt.materialize_papertrading_profile(project_root=root, spec=pt.daily_profile_spec(1400.0))
    bt = json.loads((overlay / "backtest.json").read_text("utf-8"))
    opt = json.loads((overlay / "optimizer.json").read_text("utf-8"))
    assert bt["bar_axis"] == "OneDay" and opt["config_profile"] == "papertrading" and opt["champion_enabled"] is False


def test_plan_axis_auto_falls_back_to_daily(monkeypatch):
    def shallow(*_a, **_k): raise pt.PaperTradingError("1h zu flach")
    monkeypatch.setattr(pt, "plan_geometry", shallow)
    monkeypatch.setattr(pt, "measure_daily_depths", lambda *_a, **_k: {"A.ETORO": 1400.0, "B.ETORO": 1000.0})
    plan = pt.plan_axis(Path("/x"), None, axis="auto")
    assert plan["axis"] == "daily" and plan["spec"]["backtest.json"]["bar_axis"] == "OneDay"
    with pytest.raises(pt.PaperTradingError):
        pt.plan_axis(Path("/x"), None, axis="hourly")
    monkeypatch.setattr(pt, "measure_daily_depths", lambda *_a, **_k: {})
    with pytest.raises(pt.PaperTradingError, match="1h zu flach"):
        pt.plan_axis(Path("/x"), None, axis="auto")
