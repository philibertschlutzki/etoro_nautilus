"""Paper-Auswahl: Profit-Faktor und Expectancy müssen wirklich gelten, nicht mehr eligible Paare werden
zurückgezogen (ausser bei offener Position) und können später wiederkommen."""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation import incubation as inc
from automation import papertrading as pt

_REPO = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _cfg():
    cfg = inc.incubation_config({"incubation": {"enabled": True}})
    cfg["max_concurrent"] = 10
    cfg["retire_ineligible"] = True
    return cfg


def _cycle(stages, winners, tmp_path, now, cfg=None, keep=()):
    return inc.run_incubation_cycle(
        stages, winners=winners, tournament_cfg={"incubation": {"enabled": True}},
        resolve_params=lambda s, y: {"p": y}, now=now, ledger_dir=tmp_path / "inc",
        inc_cfg=cfg or _cfg(), keep_symbols=keep)


def test_daily_overlay_enforces_profit_factor_and_expectancy(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(_REPO / "automation" / "config", root / "automation" / "config")
    base = json.loads((_REPO / "automation" / "config" / "tournament.json").read_text("utf-8"))
    overlay = pt.materialize_papertrading_profile(project_root=root, spec=pt.daily_profile_spec(1400.0))
    t = json.loads((overlay / "tournament.json").read_text("utf-8"))
    required = t["eligible_requires_all"]
    assert "min_profit_factor" in required and "min_expectancy" in required
    assert all(g in required for g in base["eligible_requires_all"])
    assert "min_profit_factor" not in base["eligible_requires_all"]        # Produktion unverändert


def test_ineligible_pair_is_retired_and_its_slot_refilled(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    good = {"strategy": "Strat", "oos_eligible": True, "oos_metrics": {"psr": 0.9}}
    _cycle(stages, {"BTC.ETORO": dict(good), "ETH.ETORO": dict(good)}, tmp_path, _T0)
    assert stages.stage("Strat", "BTC.ETORO") == inc.INCUBATING

    winners = {"BTC.ETORO": {**good, "oos_eligible": False}, "ETH.ETORO": dict(good),
               "SOL.ETORO": dict(good)}
    res = _cycle(stages, winners, tmp_path, _T0 + timedelta(days=1))
    assert stages.stage("Strat", "BTC.ETORO") == inc.RETIRED
    assert stages.stage("Strat", "ETH.ETORO") == inc.INCUBATING
    assert {r["pair"] for r in res.started} == {"Strat/SOL.ETORO"}

    # wieder eligible -> wieder inkubiert
    winners["BTC.ETORO"] = dict(good)
    _cycle(stages, winners, tmp_path, _T0 + timedelta(days=2))
    assert stages.stage("Strat", "BTC.ETORO") == inc.INCUBATING


def test_pair_with_open_position_is_kept(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    good = {"strategy": "Strat", "oos_eligible": True, "oos_metrics": {"psr": 0.9}}
    _cycle(stages, {"BTC.ETORO": dict(good)}, tmp_path, _T0)
    _cycle(stages, {"BTC.ETORO": {**good, "oos_eligible": False}}, tmp_path, _T0 + timedelta(days=1),
           keep={"BTC.ETORO"})
    assert stages.stage("Strat", "BTC.ETORO") == inc.INCUBATING


def test_production_cycle_never_retires_for_eligibility(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    good = {"strategy": "Strat", "oos_eligible": True, "oos_metrics": {"psr": 0.9}}
    cfg = _cfg()
    cfg.pop("retire_ineligible")
    _cycle(stages, {"BTC.ETORO": dict(good)}, tmp_path, _T0, cfg=cfg)
    _cycle(stages, {"BTC.ETORO": {**good, "oos_eligible": False}}, tmp_path, _T0 + timedelta(days=1), cfg=cfg)
    assert stages.stage("Strat", "BTC.ETORO") == inc.INCUBATING


def test_open_position_symbols_reads_the_paper_account():
    from automation import daily_orchestrator as orch

    pnl = {"clientPortfolio": {"positions": [{"positionID": 1, "instrumentID": 100000, "isBuy": True,
                                               "units": 0.002406, "openRate": 82942.5}]}}
    env = {"ETORO_API_KEY": "k", "ETORO_USER_KEY": "u"}          # keine CID-Anheftung -> demo
    log = logging.getLogger("t")
    assert orch._open_position_symbols(log, fetch=lambda *_a: pnl, environ=env) == {"BTC.ETORO"}
    assert orch._open_position_symbols(log, fetch=lambda *_a: pnl, environ={}) is None

    def boom(*_a):
        raise RuntimeError("offline")
    assert orch._open_position_symbols(log, fetch=boom, environ=env) is None
