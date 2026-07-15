"""A4.6 — sweep meta-orchestrator: enumeration, tiering, dispatch, never Phase 5.

Fully mocked (HI-7): no real backtest, no real optimize_symbol/confirm. The
timing guard applies — this file must finish in well under a second.
"""
import subprocess

import pytest

from automation.optimizer import sweep


_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def test_enumeration_deployable_and_gate1(monkeypatch):
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda *a, **k: ["A.ETORO", "B.ETORO", "C.ETORO"])
    monkeypatch.setattr(sweep, "load_tier_a_winners", lambda *a, **k: {"SmaCrossoverStrategy": ["A.ETORO", "C.ETORO"]})
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    bars = {"A.ETORO": 10_000, "B.ETORO": 10_000, "C.ETORO": 50}  # C fails Gate 1
    pairs = sweep.enumerate_tunable_pairs(["SmaCrossoverStrategy"], None,
                                          tier="deployable", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("SmaCrossoverStrategy", "A.ETORO", "OK")]  # B not deployable, C Gate-1 fail


def test_enumeration_all_tier_is_cross_product(monkeypatch):
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    bars = {"A.ETORO": 10_000, "B.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(["SmaCrossoverStrategy"], ["A.ETORO", "B.ETORO"],
                                          tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("SmaCrossoverStrategy", "A.ETORO", "OK"),
                     ("SmaCrossoverStrategy", "B.ETORO", "OK")]


def test_enumeration_refine_fails_loud(monkeypatch):
    # Issue #623 — der frühere `candidate_syms = []`-Platzhalter machte '--tier refine' zu einem
    # STILLEN No-Op (0 Paare, keine Warnung). Bis zur echten P3-Refinement-Heuristik bricht der Modus
    # jetzt fail-loud ab, statt lautlos nichts zu tun.
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    bars = {"A.ETORO": 10_000}
    with pytest.raises(NotImplementedError, match="refine"):
        sweep.enumerate_tunable_pairs(["SmaCrossoverStrategy"], ["A.ETORO"],
                                      tier="refine", available_bars=bars, config=_GATE_CFG)


def test_sweep_dispatch_writes_proposals_no_backtest(monkeypatch, tmp_path):
    calls = []

    def fake_opt(strategy, symbol, **k):
        calls.append((strategy, symbol))
        return object()

    def fake_confirm(study, strategy, symbol, global_params, **k):
        return {"promote": True, "status": "READY_FOR_PR", "symbol_params": {}}

    monkeypatch.setattr(sweep, "enumerate_tunable_pairs",
                        lambda *a, **k: [("SmaCrossoverStrategy", "A.ETORO", "OK")])
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)

    out = sweep.run_per_symbol_sweep(["SmaCrossoverStrategy"], ["A.ETORO"],
                                     tier="deployable", optimize_symbol=fake_opt, confirm=fake_confirm)
    assert calls == [("SmaCrossoverStrategy", "A.ETORO")]
    assert out == [tmp_path / "proposal_SmaCrossoverStrategy_A.ETORO.json"]


def test_sweep_never_calls_popen(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Phase 5 verboten")))
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: [])
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    assert sweep.run_per_symbol_sweep(["SmaCrossoverStrategy"], [], tier="deployable") == []


def test_resolve_strategies_all_reads_active(monkeypatch):
    strategies = sweep._resolve_strategies("all")
    assert "SmaCrossoverStrategy" in strategies   # active in strategies.json
    # explicit comma list is honoured verbatim
    assert sweep._resolve_strategies("Foo,Bar") == ["Foo", "Bar"]
