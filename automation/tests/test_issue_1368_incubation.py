"""Issue #1368 (GH #1265, P1) — Forward-Evidenz statt Jahreswartezeit: Demo-Inkubation mit eingefrorenen
Parametern und sequenziell korrigiertem Promotionstest.

Akzeptanzkriterien:
- ``sequential.py``: Simulation unter H0 (SR = 0, 10 000 Pfade, K = 26, n_concurrent = 3) ⇒ Fehlpromotionsrate
  ≤ 5 %; unter SR_annual = 2,0 wird die Median-Zeit bis zur Promotion berichtet.
- Ledger-Test: Parameteränderung ⇒ neues Ledger, alte Evidenz zählt nicht.
- Orchestrator-Test: ``INCUBATING`` startet nie im ``real``-Environment; ``LIVE_*`` nur über das Deployment-Gate.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from automation import incubation as inc
from automation.live_params import live_params_sha256, load_live_param_sources, resolve_live_params
from automation.optimizer import sequential as seq

REPO = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
_K, _N = 26, 3


# ─── Sequenzieller Test ──────────────────────────────────────────────────────────────

def test_bonferroni_threshold_over_looks_and_concurrent_candidates():
    assert seq.bonferroni_threshold(0.95, 26, 3) == pytest.approx(1 - 0.05 / 78)
    assert inc.bonferroni_threshold_for(inc.incubation_config({})) == pytest.approx(1 - 0.05 / 78)


def test_h0_simulation_false_promotion_rate_at_most_five_percent():
    res = seq.simulate_sequential(n_paths=10_000, k_max=_K, n_concurrent=_N, sr_annual=0.0)
    assert res["n_paths"] == 10_000 and res["k_max"] == 26 and res["n_concurrent"] == 3
    assert res["false_promotion_rate"] <= 0.05


def test_sr2_simulation_reports_the_median_time_to_promotion(capsys):
    res = seq.simulate_sequential(n_paths=2_000, k_max=_K, n_concurrent=_N, sr_annual=2.0)
    assert res["promotion_rate"] > 0.0
    assert res["median_looks_to_promotion"] is not None and 1 <= res["median_looks_to_promotion"] <= _K
    assert res["median_bars_to_promotion"] == pytest.approx(res["median_looks_to_promotion"] * 35)
    print(f"SR_annual=2.0: promotion_rate={res['promotion_rate']:.3f}, "
          f"median_looks={res['median_looks_to_promotion']}, median_bars={res['median_bars_to_promotion']}")


@pytest.mark.parametrize("psr,look,expected", [
    (0.9999, 3, "PROMOTE"), (0.0001, 3, "RETIRE"), (0.7, 3, "CONTINUE"), (0.7, 26, "EXHAUSTED"),
])
def test_sequential_decision_outcomes(psr, look, expected):
    d = seq.sequential_decision([0.001] * 100, look_index=look, psr_fn=lambda _r: psr)
    assert d.decision == expected and d.n_bars == 100
    assert d.threshold == pytest.approx(seq.bonferroni_threshold())


def test_too_few_ledger_bars_never_promote():
    d = seq.sequential_decision([1.0] * 5, look_index=2, psr_fn=lambda _r: 1.0)
    assert d.decision == "CONTINUE" and d.psr is None
    assert seq.sequential_decision([], look_index=26).decision == "EXHAUSTED"


def test_default_psr_is_the_bootstrap_psr():
    rng = np.random.default_rng(1)
    strong = list(rng.normal(0.003, 0.01, 400))
    assert seq.sequential_decision(strong, look_index=4).decision == "PROMOTE"
    noise = list(rng.normal(0.0, 0.01, 400))
    assert seq.sequential_decision(noise, look_index=4).decision in ("CONTINUE", "RETIRE")


# ─── Evidenz-Ledger ──────────────────────────────────────────────────────────────────

def test_parameter_change_starts_a_new_ledger_and_old_evidence_does_not_count(tmp_path):
    ledger = inc.EvidenceLedger("Strat", "AAA.ETORO", tmp_path)
    old_sha, new_sha = "a" * 64, "b" * 64
    for i in range(30):
        ledger.append(i, 0.01, old_sha)
    assert len(ledger.returns(old_sha)) == 30
    ledger.append(100, -0.02, new_sha)
    assert ledger.params_sha256() == new_sha
    assert ledger.returns(new_sha) == [-0.02]
    assert ledger.returns(old_sha) == []                          # alte Evidenz zählt nicht
    archived = tmp_path / f"Strat_AAA.ETORO.{old_sha[:12]}.jsonl"
    assert archived.exists() and len(archived.read_text().splitlines()) == 30


def test_session_bar_recorder_writes_net_returns_once_per_bar(tmp_path):
    ledger = inc.EvidenceLedger("Strat", "AAA.ETORO", tmp_path)
    equity = iter([0.0, 10.0, 5.0])

    class _Bar:
        def __init__(self, ts):
            self.ts_event = ts

    rec = inc.SessionBarLedgerRecorder(ledger, "c" * 64, equity_fn=lambda _s: next(equity),
                                       capital_base_fn=lambda _s: 1000.0)
    rec(None, _Bar(1))
    rec(None, _Bar(2))
    rec(None, _Bar(2))                                           # Doppelaufruf derselben Bar zählt nicht
    rec(None, _Bar(3))
    assert ledger.returns("c" * 64) == pytest.approx([0.01, -0.005])


def test_session_gate_calls_the_observer_for_in_session_bars_only():
    from automation.strategies import hourly_strategy_base as hsb

    calls = []

    class _Probe:
        _session_bar_observer = staticmethod(lambda strategy, bar: calls.append(bar))

        def __init__(self, in_session):
            self._in = in_session

        def _bar_in_session(self, bar):
            return self._in

        def _note_out_of_session_bar(self, bar):
            pass

        def _note_in_session_bar(self, bar):
            pass

    gated = hsb._session_gated(lambda self, bar: "traded")
    assert gated(_Probe(True), "bar-in") == "traded"
    assert gated(_Probe(False), "bar-out") is None
    assert calls == ["bar-in"]

    class _Broken(_Probe):
        _session_bar_observer = staticmethod(lambda strategy, bar: 1 / 0)

    assert gated(_Broken(True), "bar") == "traded"                # Beobachter-Fehler stört den Handel nie


# ─── Zustandsmaschine & Deployment-Grenze ───────────────────────────────────────────

def test_live_stages_only_via_the_deployment_gate(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    stages.transition("S", "X", inc.CANDIDATE, reason="t", now=_T0)
    stages.transition("S", "X", inc.INCUBATING, reason="t", params_sha256="d" * 64, now=_T0)
    for decision in (None, {}, {"admitted": None}, {"admitted": False}, {"admitted": "yes"}):
        with pytest.raises(inc.StageTransitionError):
            stages.transition("S", "X", inc.LIVE_SMALL, reason="t", deployment_decision=decision, now=_T0)
    with pytest.raises(inc.StageTransitionError):
        stages.transition("S", "X", inc.LIVE_FULL, reason="t", deployment_decision={"admitted": True})
    stages.transition("S", "X", inc.LIVE_SMALL, reason="t", deployment_decision={"admitted": True}, now=_T0)
    assert inc.DeploymentStages(tmp_path / "stages.json").stage("S", "X") == inc.LIVE_SMALL


@pytest.mark.parametrize("environment", ["real", "REAL", "live", ""])
def test_incubation_never_runs_outside_demo(environment, tmp_path):
    with pytest.raises(inc.IncubationEnvironmentError):
        inc.assert_stage_environment(inc.INCUBATING, environment)
    inc.assert_stage_environment(inc.INCUBATING, "demo")
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    stages.transition("S", "X", inc.INCUBATING, reason="t", params_sha256="e" * 64, now=_T0)
    with pytest.raises(inc.IncubationEnvironmentError):
        inc.incubation_bot_spec(stages, environment=environment)
    assert inc.incubation_bot_spec(stages, environment="demo")[0]["symbol"] == "X"


def _winners():
    return {"AAA.ETORO": {"strategy": "Strat", "oos_eligible": True, "oos_metrics": {"psr": 0.9}},
            "BBB.ETORO": {"strategy": "Strat", "oos_eligible": True, "oos_metrics": {"psr": 0.8}},
            "CCC.ETORO": {"strategy": "Strat", "oos_eligible": False, "oos_metrics": {"psr": 0.99}},
            "DDD.ETORO": {"strategy": "Strat", "oos_eligible": True, "oos_metrics": {"psr": 0.85}},
            "EEE.ETORO": {"strategy": "Strat", "oos_eligible": True, "oos_metrics": {"psr": 0.70}}}


def _cycle(stages, tmp_path, *, now, psr=None, admitted=None, trips=None, winners=None):
    gate_calls = []

    def gate(strategy, symbol):
        gate_calls.append((strategy, symbol))
        return {"admitted": admitted, "blocking_clause": None if admitted else "status_ready_for_pr"}

    res = inc.run_incubation_cycle(
        stages, winners=_winners() if winners is None else winners,
        tournament_cfg={"incubation": {"enabled": True}},
        resolve_params=lambda s, y: {"sma_period": 10, "symbol_tag": y}, deployment_decision_fn=gate, now=now,
        ledger_dir=tmp_path / "inc", psr_fn=(lambda _r: psr) if psr is not None else None,
        distribution_trips=trips)
    return res, gate_calls


def test_selection_ranks_by_oos_psr_and_caps_at_max_concurrent(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    res, _ = _cycle(stages, tmp_path, now=_T0)
    assert [s["pair"] for s in res.started] == ["Strat/AAA.ETORO", "Strat/DDD.ETORO", "Strat/BBB.ETORO"]
    rec = json.loads((tmp_path / "inc" / "incubation_Strat_AAA.ETORO.json").read_text())
    assert rec["params_sha256"] == live_params_sha256(rec["params"]) == stages.entry("Strat", "AAA.ETORO")[
        "params_sha256"]
    # Am nächsten Tag: kein Prüfzeitpunkt fällig, keine freien Plätze.
    res2, _ = _cycle(stages, tmp_path, now=_T0 + timedelta(days=1))
    assert res2.evaluated == [] and res2.started == []


def _ledger_bars(tmp_path, stages, symbol, n, value=0.001):
    sha = stages.entry("Strat", symbol)["params_sha256"]
    ledger = inc.EvidenceLedger("Strat", symbol, tmp_path / "inc")
    for i in range(n):
        ledger.append(i, value, sha)


def test_promote_without_admitted_gate_stays_incubating(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    _cycle(stages, tmp_path, now=_T0)
    _ledger_bars(tmp_path, stages, "AAA.ETORO", 40)
    res, gate_calls = _cycle(stages, tmp_path, now=_T0 + timedelta(days=7), psr=0.99999, admitted=None)
    aaa = next(r for r in res.evaluated if r["pair"] == "Strat/AAA.ETORO")
    assert aaa["decision"] == "PROMOTE" and aaa["blocked_by"] == "deployment_gate"
    assert ("Strat", "AAA.ETORO") in gate_calls
    assert stages.stage("Strat", "AAA.ETORO") == inc.INCUBATING


def test_promote_with_admitted_gate_goes_live_small_and_then_live_full(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    _cycle(stages, tmp_path, now=_T0)
    _ledger_bars(tmp_path, stages, "AAA.ETORO", 40)
    _cycle(stages, tmp_path, now=_T0 + timedelta(days=7), psr=0.99999, admitted=True)
    assert stages.stage("Strat", "AAA.ETORO") == inc.LIVE_SMALL
    assert inc.stage_capital_fractions(stages, {}) == {"AAA.ETORO": 0.25}
    # LIVE_SMALL: weniger als t_full_bars Echtgeld-Bars ⇒ keine Entscheidung.
    live = inc.EvidenceLedger("Strat", "AAA.ETORO", tmp_path / "inc" / "live")
    sha = stages.entry("Strat", "AAA.ETORO")["params_sha256"]
    for i in range(100):
        live.append(i, 0.001, sha)
    t1 = _T0 + timedelta(days=14)
    _cycle(stages, tmp_path, now=t1, psr=0.99999, admitted=True)
    assert stages.stage("Strat", "AAA.ETORO") == inc.LIVE_SMALL
    for i in range(100, 420):
        live.append(i, 0.001, sha)
    _cycle(stages, tmp_path, now=t1 + timedelta(days=7), psr=0.99999, admitted=False)
    assert stages.stage("Strat", "AAA.ETORO") == inc.LIVE_SMALL         # Gate verweigert ⇒ kein LIVE_FULL
    _cycle(stages, tmp_path, now=t1 + timedelta(days=14), psr=0.99999, admitted=True)
    assert stages.stage("Strat", "AAA.ETORO") == inc.LIVE_FULL
    assert inc.stage_capital_fractions(stages, {}) == {"AAA.ETORO": 1.0}


def test_retire_exhausted_and_distribution_trigger(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    _cycle(stages, tmp_path, now=_T0)
    for sym in ("AAA.ETORO", "BBB.ETORO", "DDD.ETORO"):
        _ledger_bars(tmp_path, stages, sym, 40)
    _cycle(stages, tmp_path, now=_T0 + timedelta(days=7), psr=0.00001, admitted=True, winners={})
    assert {stages.stage("Strat", s) for s in ("AAA.ETORO", "BBB.ETORO", "DDD.ETORO")} == {inc.RETIRED}
    # Retirierte Paare werden nicht erneut inkubiert (neue Evidenz wäre ein neuer Test).
    res, _ = _cycle(stages, tmp_path, now=_T0 + timedelta(days=8))
    assert [s["pair"] for s in res.started] == ["Strat/EEE.ETORO"]
    # EXHAUSTED nach k_max Prüfzeitpunkten.
    _ledger_bars(tmp_path, stages, "EEE.ETORO", 40)
    _cycle(stages, tmp_path, now=_T0 + timedelta(days=8 + 7 * 26), psr=0.5, winners={})
    assert stages.stage("Strat", "EEE.ETORO") == inc.RETIRED
    # Verteilungs-Auslöser (#1362) seit Stufenbeginn ⇒ RETIRED.
    stages2 = inc.DeploymentStages(tmp_path / "stages2.json")
    _cycle(stages2, tmp_path, now=_T0)
    trips = {"AAA.ETORO": {"utc": "2026-10-06T12:00:00Z"}, "BBB.ETORO": {"utc": "2026-10-01T00:00:00Z"}}
    _cycle(stages2, tmp_path, now=_T0 + timedelta(days=2), trips=trips, winners={})
    assert stages2.stage("Strat", "AAA.ETORO") == inc.RETIRED
    assert stages2.stage("Strat", "BBB.ETORO") == inc.INCUBATING        # Auslösung vor Stufenbeginn


def test_incubation_whitelist_carries_only_frozen_verifiable_pairs(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    _cycle(stages, tmp_path, now=_T0)
    payload = inc.write_incubation_whitelist(stages, directory=tmp_path / "inc", path=tmp_path / "wl.json")
    assert sorted(payload["per_symbol_winners"]) == ["AAA.ETORO", "BBB.ETORO", "DDD.ETORO"]
    entry = payload["per_symbol_winners"]["AAA.ETORO"]
    assert entry["stage"] == inc.INCUBATING and entry["params_sha256"] == live_params_sha256(entry["params"])
    # Manipulierter Record ⇒ Paar ausgelassen.
    rec_path = tmp_path / "inc" / "incubation_Strat_BBB.ETORO.json"
    rec = json.loads(rec_path.read_text())
    rec["params"]["sma_period"] = 99
    rec_path.write_text(json.dumps(rec))
    payload = inc.write_incubation_whitelist(stages, directory=tmp_path / "inc", path=tmp_path / "wl.json")
    assert "BBB.ETORO" not in payload["per_symbol_winners"]


def test_distribution_trip_round_trip(tmp_path):
    inc.record_distribution_trip("AAA.ETORO", path=tmp_path / "t.json", now=_T0, detail={"z_live": -3.1})
    assert inc.read_distribution_trips(tmp_path / "t.json")["AAA.ETORO"]["z_live"] == -3.1
    assert inc.read_distribution_trips(tmp_path / "missing.json") == {}


# ─── Allocator: LIVE_SMALL skaliert die reguläre Allokation ─────────────────────────

def test_allocator_scales_live_small_symbols():
    from automation import momentum_ls_allocator as mla
    from automation.momentum_ls_allocator import MomentumLSAllocator

    class _Cache:
        def positions_open(self, instrument_id=None):
            return []

    InstrumentId = mla.InstrumentId                # derselbe Typ, den der Allocator verwendet
    alloc = MomentumLSAllocator(["AAA.ETORO", "BBB.ETORO"], max_symbol_exposure_fraction=0.10,
                                symbol_capital_fractions={"AAA.ETORO": 0.25})
    a = alloc.get_allocation(InstrumentId.from_str("AAA.ETORO"), _Cache(), 100_000.0)
    b = alloc.get_allocation(InstrumentId.from_str("BBB.ETORO"), _Cache(), 100_000.0)
    assert b == pytest.approx(10_000.0) and a == pytest.approx(2_500.0)
    plain = MomentumLSAllocator(["AAA.ETORO"], max_symbol_exposure_fraction=0.10)
    assert plain.get_allocation(InstrumentId.from_str("AAA.ETORO"), _Cache(), 100_000.0) == pytest.approx(10_000.0)


# ─── Bot: Inkubations-Modus ─────────────────────────────────────────────────────────

def test_incubation_bots_config_uses_frozen_params_and_rejects_tampering():
    from automation.momentum_ls_run import _build_incubation_bots_config

    params = {"sma_period": 17}
    entry = {"strategy": "SmaCrossoverStrategy", "stage": inc.INCUBATING, "params": params,
             "params_sha256": live_params_sha256(params)}
    registry = {"SmaCrossoverStrategy": ("automation.strategies.sma_crossover", "SmaCrossoverStrategy",
                                         "SmaCrossoverConfig")}
    universe = {"universe": [{"symbol": "TSLA.ETORO"}]}
    syms, bots = _build_incubation_bots_config(
        universe, {"per_symbol_winners": {"TSLA.ETORO": entry}}, registry, {"TSLA.ETORO": "1001"})
    assert syms == ["TSLA.ETORO"] and bots[0]["params"] == params and bots[0]["stage"] == inc.INCUBATING
    tampered = {**entry, "params": {"sma_period": 18}}
    assert _build_incubation_bots_config(
        universe, {"per_symbol_winners": {"TSLA.ETORO": tampered}}, registry, {"TSLA.ETORO": "1001"}) == ([], [])
    live_stage = {**entry, "stage": inc.LIVE_SMALL}
    assert _build_incubation_bots_config(
        universe, {"per_symbol_winners": {"TSLA.ETORO": live_stage}}, registry, {"TSLA.ETORO": "1001"}) == ([], [])


def test_bot_refuses_incubation_in_the_real_environment(tmp_path):
    """Der Bot selbst prüft das Environment VOR jedem weiteren Schritt (kein Node, keine Sperre): Exit 6."""
    env = {**os.environ, "ETORO_ENV": "real", "ETORO_DRY_RUN": "0", "PYTHONPATH": str(REPO)}
    proc = subprocess.run(
        [sys.executable, str(REPO / "automation" / "momentum_ls_run.py"), "--incubation",
         "--tournament", str(tmp_path / "missing.json")],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 6, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "INCUBATION_ENVIRONMENT_REFUSED" in proc.stdout + proc.stderr


# ─── Orchestrator: Phase 5b ─────────────────────────────────────────────────────────

def _orch_env(tmp_path, monkeypatch, *, enabled=True):
    from automation import daily_orchestrator as orch

    cfg = json.loads((REPO / "automation" / "config" / "tournament.json").read_text("utf-8"))
    cfg["incubation"]["enabled"] = enabled
    cfg_path = tmp_path / "tournament_cfg.json"
    cfg_path.write_text(json.dumps(cfg), "utf-8")
    monkeypatch.setattr(orch, "TOURNAMENT_CFG", cfg_path)
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(orch, "logs_dir", lambda: tmp_path / "logs")
    (tmp_path / "data" / "state").mkdir(parents=True, exist_ok=True)
    return orch


class _Popen:
    def __init__(self):
        self.calls = []
        self.pid = 4242

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return self


def _selection(tmp_path, winners):
    def fn(log, inc_cfg, output_path):
        assert inc_cfg["walk_forward"] == {"is_window_days": 40, "embargo_period_days": 3, "splits": 3,
                                           "oos_window_days": 12}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"per_symbol_winners": winners}), "utf-8")
        return output_path
    return fn


def test_phase5b_is_off_by_default(tmp_path, monkeypatch):
    orch = _orch_env(tmp_path, monkeypatch, enabled=False)
    popen = _Popen()
    res = orch.phase5b_incubation(orch.logging.getLogger("t1368"), popen=popen,
                                  selection_fn=lambda *a: pytest.fail("keine Selektion, wenn deaktiviert"))
    assert res == {"status": "disabled"} and popen.calls == []
    shipped = json.loads((REPO / "automation" / "config" / "tournament.json").read_text("utf-8"))
    assert shipped["incubation"]["enabled"] is False


def test_phase5b_starts_the_demo_bot_never_real(tmp_path, monkeypatch):
    orch = _orch_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ETORO_ENV", "real")                 # selbst ein real-Orchestrator startet demo
    defaults, raw = load_live_param_sources(REPO / "automation" / "config")
    monkeypatch.setattr(orch, "_load_live_param_sources", lambda: (defaults, raw))
    winners = {"AAA.ETORO": {"strategy": "SmaCrossoverStrategy", "oos_eligible": True,
                             "oos_metrics": {"psr": 0.9}}}
    popen = _Popen()
    res = orch.phase5b_incubation(orch.logging.getLogger("t1368"), popen=popen,
                                  selection_fn=_selection(tmp_path, winners), now=_T0)
    assert res["status"] == "started" and res["incubating"] == ["AAA.ETORO"]
    (cmd, kwargs), = popen.calls
    assert "--incubation" in cmd and kwargs["env"]["ETORO_ENV"] == "demo"
    assert cmd[cmd.index("--tournament") + 1] == str(tmp_path / "data" / "state" / "incubation_whitelist.json")
    stages = inc.DeploymentStages(tmp_path / "data" / "state" / "deployment_stages.json")
    assert stages.stage("SmaCrossoverStrategy", "AAA.ETORO") == inc.INCUBATING
    assert stages.entry("SmaCrossoverStrategy", "AAA.ETORO")["params_sha256"] == live_params_sha256(
        resolve_live_params("SmaCrossoverStrategy", "AAA.ETORO", defaults, raw))


def test_phase5b_promotion_goes_through_the_deployment_gate(tmp_path, monkeypatch):
    """PROMOTE im Ledger, aber kein Proposal ⇒ das Gate verweigert (promotion_record_exists) ⇒ bleibt
    INCUBATING; der Demo-Bot handelt weiter, kein Echtgeld."""
    orch = _orch_env(tmp_path, monkeypatch)
    defaults, raw = load_live_param_sources(REPO / "automation" / "config")
    monkeypatch.setattr(orch, "_load_live_param_sources", lambda: (defaults, raw))
    winners = {"AAA.ETORO": {"strategy": "SmaCrossoverStrategy", "oos_eligible": True,
                             "oos_metrics": {"psr": 0.9}}}
    orch.phase5b_incubation(orch.logging.getLogger("t1368"), no_deploy=True,
                            selection_fn=_selection(tmp_path, winners), now=_T0)
    stages = inc.DeploymentStages(tmp_path / "data" / "state" / "deployment_stages.json")
    sha = stages.entry("SmaCrossoverStrategy", "AAA.ETORO")["params_sha256"]
    ledger = inc.EvidenceLedger("SmaCrossoverStrategy", "AAA.ETORO", tmp_path / "data" / "state" / "incubation")
    rng = np.random.default_rng(3)
    for i, r in enumerate(rng.normal(0.003, 0.01, 400)):
        ledger.append(i, float(r), sha)
    res = orch.phase5b_incubation(orch.logging.getLogger("t1368"), no_deploy=True,
                                  selection_fn=_selection(tmp_path, winners), now=_T0 + timedelta(days=7))
    (ev,) = res["cycle"]["evaluated"]
    assert ev["decision"] == "PROMOTE" and ev["blocked_by"] == "deployment_gate"
    assert ev["blocking_clause"] == "promotion_record_exists"
    assert inc.DeploymentStages(stages.path).stage("SmaCrossoverStrategy", "AAA.ETORO") == inc.INCUBATING


def test_phase5_with_incubation_enabled_requires_a_live_stage(tmp_path, monkeypatch):
    """Mit aktivierter Inkubation ist die Deployment-Grenze notwendig, nicht hinreichend: ein zugelassenes
    Paar ohne LIVE-Stufe wird nicht gehandelt; in LIVE_SMALL trägt der Eintrag capital_fraction 0,25."""
    from automation.optimizer.manifest import catalog_fingerprint

    orch = _orch_env(tmp_path, monkeypatch)
    defaults, raw = load_live_param_sources(REPO / "automation" / "config")
    live = resolve_live_params("SmaCrossoverStrategy", "AAA.ETORO", defaults, raw)
    tournament = {
        "fully_eligible_pairs": 1, "oos_not_evaluable_pairs": 0, "oos_failed_pairs": 0,
        "per_symbol_winners": {"AAA.ETORO": {"strategy": "SmaCrossoverStrategy", "oos_eligible": True,
                                             "oos_evaluated": True}},
        "aggregate_winner": {"strategy": "SmaCrossoverStrategy", "win_count": 1, "oos_evaluated": True,
                             "oos_eligible": True, "oos_metrics": {"sortino_ratio": 1.0, "max_drawdown": 0.1}},
    }
    tfile = tmp_path / "tournament.json"
    tfile.write_text(json.dumps(tournament), encoding="utf-8")
    optimizer_dir = tmp_path / "data" / "optimizer"
    optimizer_dir.mkdir(parents=True)
    (tmp_path / "automation").mkdir()
    (tmp_path / "automation" / "momentum_ls_run.py").touch()
    proposal = {
        "status": "READY_FOR_PR", "R_symbol": 1.0, "R_global": 0.2, "promotion_margin": 0.0,
        "data_snapshot_sha256": catalog_fingerprint(),
        "proposed_instrument_override": {"sma_period": live["sma_period"]},
        "selection_end_utc": "2026-07-31T00:00:00Z", "holdout_start_utc": "2026-08-03T00:00:00Z",
        "holdout_embargo_days": 3, "holdout_overlap_days": 0, "config_profile": "production",
        "holdout": {"symbol": {
            "deflated_dsr": 0.97, "oos_psr": 0.80, "holdout_ci_lower_sortino": 0.05, "pbo": 0.30,
            "pbo_n_configs": 40, "blocking_invariant_names": [], "oos_expectancy_cost_stress_2x": 0.001,
            "oos_expectancy": 12.5, "oos_expectancy_winsorized": 10.0}, "global": {}},
    }
    (optimizer_dir / "proposal_SmaCrossoverStrategy_AAA.ETORO.json").write_text(json.dumps(proposal), "utf-8")
    log = orch.logging.getLogger("t1368")
    wl_path = tmp_path / "data" / "state" / "whitelist_tournament.json"

    assert orch.phase5_live_deployment(log, {"universe": []}, {"tournament_path": str(tfile)}, no_deploy=True) == 0
    assert json.loads(wl_path.read_text())["per_symbol_winners"] == {}          # keine Stufe ⇒ kein Kapital

    stages = inc.DeploymentStages(tmp_path / "data" / "state" / "deployment_stages.json")
    stages.transition("SmaCrossoverStrategy", "AAA.ETORO", inc.INCUBATING, reason="t",
                      params_sha256=live_params_sha256(live), now=_T0)
    stages.transition("SmaCrossoverStrategy", "AAA.ETORO", inc.LIVE_SMALL, reason="t",
                      deployment_decision={"admitted": True}, now=_T0)
    orch.phase5_live_deployment(log, {"universe": []}, {"tournament_path": str(tfile)}, no_deploy=True)
    entry = json.loads(wl_path.read_text())["per_symbol_winners"]["AAA.ETORO"]
    assert entry["stage"] == inc.LIVE_SMALL and entry["capital_fraction"] == 0.25

    # Parameter seit der Inkubation geändert ⇒ die Forward-Evidenz gilt nicht für den gehandelten Kandidaten.
    stages.data["SmaCrossoverStrategy/AAA.ETORO"]["params_sha256"] = "f" * 64
    inc.write_json_atomic(stages.path, stages.data)
    orch.phase5_live_deployment(log, {"universe": []}, {"tournament_path": str(tfile)}, no_deploy=True)
    assert json.loads(wl_path.read_text())["per_symbol_winners"] == {}


def test_phase5b_runs_before_phase5_in_main():
    src = (REPO / "automation" / "daily_orchestrator.py").read_text("utf-8")
    body = src[src.index("def main()"):]
    assert body.index("phase5b_incubation(") < body.index("phase5_live_deployment(")
