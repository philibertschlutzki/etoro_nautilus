"""A4.8 — live integration of instrument_overrides (matrix + momentum-LS).

Mocked / pure (HI-7): no real backtest, no live trading. Verifies the two
call-sites apply the override when present and stay bit-identical without one (HI-2).
"""
from automation.backtest_runner import resolve_strategy_params
from automation.momentum_ls_run import _build_bots_config


# --- Matrix path (backtest_runner dispatch call-site) ----------------------
def test_matrix_callsite_applies_override():
    # `strat` as it exists in the dispatch loop: params already merged by apply_strategy_defaults.
    strat = {"strategy_class": "X", "params": {"a": 2, "b": 5},
             "instrument_overrides": {"TSLA.ETORO": {"a": 9}}}
    out = resolve_strategy_params(strat, {}, is_manifest=False, instrument="TSLA.ETORO")
    assert out == {"a": 9, "b": 5}   # override applied on top of the merged params


def test_matrix_callsite_no_override_is_identical():
    strat = {"strategy_class": "X", "params": {"a": 2, "b": 5},
             "instrument_overrides": {"OTHER.ETORO": {"a": 9}}}
    # no override for THIS symbol -> identical (HI-2)
    assert resolve_strategy_params(strat, {}, is_manifest=False, instrument="TSLA.ETORO") == {"a": 2, "b": 5}
    # the manifest path never overrides (Pitfall #61)
    assert resolve_strategy_params(strat, {}, is_manifest=True, instrument="TSLA.ETORO") == {"a": 2, "b": 5}


# --- Live path (momentum_ls_run._build_bots_config) ------------------------
# Issue #1360 (GH #1256) — die Zulassung laeuft ausschliesslich ueber die Deployment-Grenze
# (``deployment_gate.admitted``); jeder Whitelist-Eintrag traegt Fingerabdruck + promoviertes Override
# (so, wie ihn ``daily_orchestrator.phase5_live_deployment`` schreibt).
def _inputs(instrument_overrides=None, *, admitted=True):
    from automation.live_params import live_params_sha256, resolve_live_params
    strat_entry = {"strategy_class": "SmaCrossoverStrategy", "params": {"sma_period": 10}}
    if instrument_overrides is not None:
        strat_entry["instrument_overrides"] = instrument_overrides
    defaults = {"SmaCrossoverStrategy": {"sma_period": 5, "trade_amount_pct": 15.0}}
    live = resolve_live_params("SmaCrossoverStrategy", "TSLA.ETORO", defaults, [strat_entry])
    return dict(
        universe_data={"universe": [{"symbol": "TSLA.ETORO"}]},
        tournament_data={"per_symbol_winners": {"TSLA.ETORO": {
            "strategy": "SmaCrossoverStrategy", "oos_eligible": True, "oos_evaluated": True,
            "deployment_gate": {"admitted": admitted},
            "proposed_instrument_override": (instrument_overrides or {}).get("TSLA.ETORO", {}),
            "live_params_sha256": live_params_sha256(live)}}},
        registry={"SmaCrossoverStrategy": ("automation.strategies.sma_crossover",
                                           "SmaCrossoverStrategy", "SmaCrossoverConfig")},
        defaults=defaults,
        strategies_raw=[strat_entry],
        symbol_to_etoro_id={"TSLA.ETORO": "1001"},
    )


def test_momentum_ls_registers_with_override():
    syms, bots = _build_bots_config(**_inputs(instrument_overrides={"TSLA.ETORO": {"sma_period": 33}}))
    assert syms == ["TSLA.ETORO"]
    assert bots[0]["params"]["sma_period"] == 33   # override wins over params/defaults


def test_no_override_is_identical_behavior():
    # (a) no instrument_overrides key at all
    _, bots_a = _build_bots_config(**_inputs())
    # (b) override exists but for a different symbol
    _, bots_b = _build_bots_config(**_inputs(instrument_overrides={"OTHER.ETORO": {"sma_period": 99}}))
    assert bots_a[0]["params"]["sma_period"] == 10   # params override only, no instrument override
    assert bots_b[0]["params"]["sma_period"] == 10   # override for another symbol -> bit-identical


def test_not_admitted_pair_stays_excluded_even_with_override():
    """Pitfall #60 (seit #1360 ueber die Deployment-Grenze): ein nicht zugelassenes Paar bleibt
    ausgeschlossen, auch wenn ein Override existiert."""
    inp = _inputs(instrument_overrides={"TSLA.ETORO": {"sma_period": 33}}, admitted=False)
    syms, bots = _build_bots_config(**inp)
    assert syms == [] and bots == []
