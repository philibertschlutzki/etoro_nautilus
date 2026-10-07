"""Issue #1360 (GH #1256, P0) — der Live-Bot handelt die validierten Parameter.

Akzeptanzkriterien:
- Proposal mit ``atr_trailing_multiplier = 2.7``, Override fehlt ⇒ Klausel False,
  ``blocking_clause == "live_params_match_promotion"``;
- Override == Proposal ⇒ True; der Bot instanziiert exakt diese Werte;
- Paar mit ``admitted = True`` und Phase-4 ``oos_eligible = False`` wird gehandelt.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.live_params import (
    live_param_values_match, live_params_sha256, load_live_param_sources,
    mismatching_live_params, resolve_live_params,
)
from automation.optimizer.deployment_gate import (
    DEPLOYMENT_CLAUSES, build_promotion_record_from_proposal, evaluate_deployment_eligibility,
)
from automation.optimizer.invariants import check_deployment_gate_completeness

REPO = Path(__file__).resolve().parents[2]
_SNAPSHOT = "cafebabe" * 8
_CFG = {"deflation_confidence": 0.95, "oos_min_psr": 0.75, "pbo_min_configs": 10}
_DEFAULTS = {"Strat": {"atr_trailing_multiplier": 1.5, "sma_period": 5, "trade_amount_usd": 1500.0}}
_STRATEGIES_RAW = [{"strategy_class": "Strat", "params": {"sma_period": 10}}]


def _record(**overrides) -> dict:
    rec = {
        "status": "READY_FOR_PR", "R_symbol": 1.0, "R_global": 0.2, "promotion_margin": 0.0,
        "data_snapshot_sha256": _SNAPSHOT, "deflated_dsr": 0.97, "oos_psr": 0.80,
        "holdout_ci_lower_sortino": 0.05, "pbo": 0.30, "pbo_n_configs": 40,
        "blocking_invariant_names": [], "expectancy_cost_stress_2x": 0.001,
        "holdout_expectancy_notional_weighted": 0.05, "holdout_expectancy_winsorized": 0.04,
        "proposed_instrument_override": {}, "run_id": "run_x",
        # Issue #1357 (GH #1253) — dreizehnte Klausel ``holdout_disjoint``: Selektion endet das
        # Holdout-Embargo vor dem Holdout-Beginn, keine Ueberlappung (fehlende Felder waeren fail-closed).
        "selection_end_utc": "2026-07-31T00:00:00Z", "holdout_start_utc": "2026-08-03T00:00:00Z",
        "holdout_embargo_days": 3, "holdout_overlap_days": 0, "config_profile": "production",
    }
    rec.update(overrides)
    return rec


def _evaluate(record, strategies_raw=None):
    return evaluate_deployment_eligibility(
        ("Strat", "SYM.ETORO"), {("Strat", "SYM.ETORO"): record}, _CFG,
        current_snapshot_sha256=_SNAPSHOT,
        live_param_sources=(_DEFAULTS, _STRATEGIES_RAW if strategies_raw is None else strategies_raw))


# ─── resolve_live_params: einzige Quelle ──────────────────────────────────────────

def test_resolve_live_params_precedence_and_trade_amount_usd_removed():
    raw = [{"strategy_class": "Strat", "params": {"sma_period": 10},
            "instrument_overrides": {"SYM.ETORO": {"sma_period": 33, "extra": True}}}]
    live = resolve_live_params("Strat", "SYM.ETORO", _DEFAULTS, raw)
    assert live == {"atr_trailing_multiplier": 1.5, "sma_period": 33, "extra": True}
    other = resolve_live_params("Strat", "OTHER.ETORO", _DEFAULTS, raw)
    assert other["sma_period"] == 10 and "extra" not in other
    assert resolve_live_params("Unknown", "SYM.ETORO", _DEFAULTS, raw) == {}
    assert "trade_amount_usd" in _DEFAULTS["Strat"]          # die Eingabe bleibt unverändert


def test_value_comparison_rules():
    assert live_param_values_match(2.7, 2.7 + 1e-12)
    assert not live_param_values_match(2.7, 2.71)
    assert live_param_values_match(3, 3) and not live_param_values_match(3, 4)
    assert live_param_values_match("a", "a") and not live_param_values_match("a", "b")
    assert live_param_values_match(True, True) and not live_param_values_match(True, 1)
    assert not live_param_values_match(1.0, True)
    assert mismatching_live_params({"a": 1}, None) is None
    assert mismatching_live_params({"a": 1}, {"a": 1, "b": 2}) == ["b"]      # fehlender Key = Abweichung


def test_live_params_sha256_is_order_independent_and_value_sensitive():
    assert live_params_sha256({"a": 1, "b": 2}) == live_params_sha256({"b": 2, "a": 1})
    assert live_params_sha256({"a": 1}) != live_params_sha256({"a": 2})


# ─── Zwölfte Deployment-Klausel ────────────────────────────────────────────────────

def test_twelfth_clause_exists_last_and_completeness_check_follows():
    # Issue #1357 (GH #1253) haengt ``holdout_disjoint`` als dreizehnte Klausel dahinter.
    assert len(DEPLOYMENT_CLAUSES) == 14
    assert DEPLOYMENT_CLAUSES[11] == "live_params_match_promotion"
    full = {"deployment_gate": {"clause_results": {c: True for c in DEPLOYMENT_CLAUSES}}}
    assert check_deployment_gate_completeness({"X": full}).passed is True
    eleven = {"deployment_gate": {"clause_results": {
        c: True for c in DEPLOYMENT_CLAUSES if c != "live_params_match_promotion"}}}
    r = check_deployment_gate_completeness({"X": eleven})
    assert r.passed is False and r.actual == {"X": ["live_params_match_promotion"]}


def test_proposal_without_override_in_config_blocks_on_the_new_clause():
    decision = _evaluate(_record(proposed_instrument_override={"atr_trailing_multiplier": 2.7}))
    assert decision.admitted is False
    assert decision.clause_results["live_params_match_promotion"] is False
    assert decision.blocking_clause == "live_params_match_promotion"
    assert decision.clause_details["live_params_match_promotion"]["mismatching_keys"] == [
        "atr_trailing_multiplier"]


def test_override_equal_to_proposal_passes():
    raw = [{"strategy_class": "Strat", "params": {"sma_period": 10},
            "instrument_overrides": {"SYM.ETORO": {"atr_trailing_multiplier": 2.7}}}]
    decision = _evaluate(_record(proposed_instrument_override={
        "atr_trailing_multiplier": 2.7, "sma_period": 10}), strategies_raw=raw)
    assert decision.admitted is True
    assert decision.clause_results["live_params_match_promotion"] is True


def test_missing_proposal_field_is_fail_closed_none():
    rec = _record()
    rec.pop("proposed_instrument_override")
    decision = _evaluate(rec)
    assert decision.clause_results["live_params_match_promotion"] is None
    assert decision.admitted is False
    assert decision.blocking_clause == "live_params_match_promotion"


def test_empty_override_is_trivially_matching_and_older_override_after_repromotion_is_caught():
    assert _evaluate(_record(proposed_instrument_override={})).admitted is True
    stale_raw = [{"strategy_class": "Strat", "instrument_overrides": {
        "SYM.ETORO": {"atr_trailing_multiplier": 2.0}}}]
    decision = _evaluate(_record(proposed_instrument_override={"atr_trailing_multiplier": 2.7}),
                         strategies_raw=stale_raw)
    assert decision.clause_results["live_params_match_promotion"] is False


def test_promotion_record_carries_the_override_from_the_proposal():
    proposal = {"status": "READY_FOR_PR", "proposed_instrument_override": {"sma_period": 7},
                "holdout": {"symbol": {}}}
    assert build_promotion_record_from_proposal(proposal)["proposed_instrument_override"] == {
        "sma_period": 7}
    assert "proposed_instrument_override" in build_promotion_record_from_proposal({})


# ─── Bot: Zulassung nur über deployment_gate.admitted, Parameter-Parität je Paar ───

def _bot_inputs(winner_extra: dict | None = None, *, override: dict | None = None,
                promoted: dict | None = None, admitted: bool = True, verified: bool = True):
    """Whitelist-Eintrag wie ihn ``daily_orchestrator.phase5_live_deployment`` schreibt: Fingerabdruck der
    aufgeloesten Live-Parameter + promoviertes Override (``promoted``, Default = ``override``)."""
    strat_entry = {"strategy_class": "SmaCrossoverStrategy", "params": {"sma_period": 10}}
    if override is not None:
        strat_entry["instrument_overrides"] = {"TSLA.ETORO": override}
    defaults = {"SmaCrossoverStrategy": {"sma_period": 5, "trade_amount_pct": 15.0}}
    live = resolve_live_params("SmaCrossoverStrategy", "TSLA.ETORO", defaults, [strat_entry])
    winner = {"strategy": "SmaCrossoverStrategy", "deployment_gate": {"admitted": admitted}}
    if verified:
        winner["proposed_instrument_override"] = dict(promoted if promoted is not None else (override or {}))
        winner["live_params_sha256"] = live_params_sha256(live)
    winner.update(winner_extra or {})
    return dict(
        universe_data={"universe": [{"symbol": "TSLA.ETORO"}]},
        tournament_data={"per_symbol_winners": {"TSLA.ETORO": winner}},
        registry={"SmaCrossoverStrategy": ("automation.strategies.sma_crossover",
                                           "SmaCrossoverStrategy", "SmaCrossoverConfig")},
        defaults=defaults, strategies_raw=[strat_entry], symbol_to_etoro_id={"TSLA.ETORO": "1001"},
    )


def test_admitted_pair_is_traded_even_when_phase4_fields_are_false():
    from automation.momentum_ls_run import _build_bots_config
    inp = _bot_inputs({"oos_eligible": False, "oos_evaluated": False})
    syms, bots = _build_bots_config(**inp)
    assert syms == ["TSLA.ETORO"] and len(bots) == 1


@pytest.mark.parametrize("winner", [
    {"strategy": "SmaCrossoverStrategy", "oos_eligible": True, "oos_evaluated": True},   # kein Gate
    {"strategy": "SmaCrossoverStrategy", "oos_eligible": True, "oos_evaluated": True,
     "deployment_gate": {"admitted": False}},
    {"strategy": "SmaCrossoverStrategy", "deployment_gate": {}},
])
def test_pair_without_admitted_gate_is_never_traded(winner):
    from automation.momentum_ls_run import _build_bots_config
    inp = _bot_inputs()
    inp["tournament_data"]["per_symbol_winners"]["TSLA.ETORO"] = winner
    assert _build_bots_config(**inp) == ([], [])


def test_bot_instantiates_exactly_the_promoted_values():
    from automation.momentum_ls_run import _build_bots_config
    promoted = {"sma_period": 33, "atr_trailing_multiplier": 2.7}
    inp = _bot_inputs(override=promoted)
    syms, bots = _build_bots_config(**inp)
    assert syms == ["TSLA.ETORO"]
    assert bots[0]["params"]["sma_period"] == 33
    assert bots[0]["params"]["atr_trailing_multiplier"] == 2.7
    assert bots[0]["live_params_sha256"] == live_params_sha256(
        resolve_live_params("SmaCrossoverStrategy", "TSLA.ETORO", inp["defaults"], inp["strategies_raw"]))


def test_live_params_mismatch_skips_the_pair_and_logs(caplog):
    from automation.momentum_ls_run import _build_bots_config
    inp = _bot_inputs(promoted={"atr_trailing_multiplier": 2.7})        # Override fehlt live
    with caplog.at_level("ERROR"):
        syms, bots = _build_bots_config(**inp)
    assert (syms, bots) == ([], [])
    assert "LIVE_PARAMS_MISMATCH" in caplog.text and "atr_trailing_multiplier" in caplog.text


def test_whitelist_sha_mismatch_skips_the_pair():
    from automation.momentum_ls_run import _build_bots_config
    inp = _bot_inputs({"live_params_sha256": "0" * 64})
    assert _build_bots_config(**inp) == ([], [])
    assert _build_bots_config(**_bot_inputs())[0] == ["TSLA.ETORO"]


def test_unverifiable_whitelist_entry_is_fail_closed():
    from automation.momentum_ls_run import _build_bots_config
    assert _build_bots_config(**_bot_inputs(verified=False)) == ([], [])


# ─── Orchestrator: Whitelist-Eintrag trägt live_params_sha256 + Proposal-Override ──

def test_phase5_whitelist_entry_carries_live_params_sha256_and_override(tmp_path, monkeypatch):
    from automation import daily_orchestrator as orch
    from automation.optimizer.manifest import catalog_fingerprint

    defaults, strategies_raw = load_live_param_sources(REPO / "automation" / "config")
    live = resolve_live_params("SmaCrossoverStrategy", "AAA.ETORO", defaults, strategies_raw)
    override = {"sma_period": live["sma_period"]}
    tournament = {
        "fully_eligible_pairs": 1, "oos_not_evaluable_pairs": 0, "oos_failed_pairs": 0,
        "per_symbol_winners": {"AAA.ETORO": {
            "strategy": "SmaCrossoverStrategy", "oos_eligible": True, "oos_evaluated": True}},
        "aggregate_winner": {"strategy": "SmaCrossoverStrategy", "win_count": 1,
                             "oos_evaluated": True, "oos_eligible": True,
                             "oos_metrics": {"sortino_ratio": 1.0, "max_drawdown": 0.1}},
    }
    tfile = tmp_path / "tournament.json"
    tfile.write_text(json.dumps(tournament), encoding="utf-8")
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data" / "state").mkdir(parents=True)
    optimizer_dir = tmp_path / "data" / "optimizer"
    optimizer_dir.mkdir(parents=True)
    (tmp_path / "automation").mkdir()
    (tmp_path / "automation" / "momentum_ls_run.py").touch()
    proposal = {
        "status": "READY_FOR_PR", "R_symbol": 1.0, "R_global": 0.2, "promotion_margin": 0.0,
        "data_snapshot_sha256": catalog_fingerprint(),
        "proposed_instrument_override": override,
        # Issue #1357 (GH #1253) — dreizehnte Klausel ``holdout_disjoint``: Selektion endet das
        # Holdout-Embargo vor dem Holdout-Beginn, keine Ueberlappung (fehlende Felder waeren fail-closed).
        "selection_end_utc": "2026-07-31T00:00:00Z", "holdout_start_utc": "2026-08-03T00:00:00Z",
        "holdout_embargo_days": 3, "holdout_overlap_days": 0, "config_profile": "production",
        "holdout": {"symbol": {
            "deflated_dsr": 0.97, "oos_psr": 0.80, "holdout_ci_lower_sortino": 0.05, "pbo": 0.30,
            "pbo_n_configs": 40, "blocking_invariant_names": [],
            "oos_expectancy_cost_stress_2x": 0.001, "oos_expectancy": 12.5,
            "oos_expectancy_winsorized": 10.0,
            # Issue #1362 — Holdout-Round-Trip-Statistik fuer den Live-Verteilungs-Ausloeser B.
            "oos_trade_return_bps_mean": 12.5, "oos_trade_return_bps_std": 40.0,
            "oos_trade_return_bps_n": 77}, "global": {}},
    }
    (optimizer_dir / "proposal_SmaCrossoverStrategy_AAA.ETORO.json").write_text(
        json.dumps(proposal), encoding="utf-8")

    logger = orch.logging.getLogger("t1360")
    rc = orch.phase5_live_deployment(
        logger, {"universe": []}, {"tournament_path": str(tfile)}, no_deploy=True)
    assert rc == 0
    wl = json.loads((tmp_path / "data" / "state" / "whitelist_tournament.json").read_text())
    entry = wl["per_symbol_winners"]["AAA.ETORO"]
    assert entry["deployment_gate"]["admitted"] is True
    assert entry["deployment_gate"]["clause_results"]["live_params_match_promotion"] is True
    assert entry["live_params_sha256"] == live_params_sha256(live)
    assert entry["proposed_instrument_override"] == override
    assert (entry["holdout_trade_return_bps_mean"], entry["holdout_trade_return_bps_std"],
            entry["holdout_trade_return_bps_n"]) == (12.5, 40.0, 77)
