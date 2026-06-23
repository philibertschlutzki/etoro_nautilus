"""A4.1 — instrument_overrides resolution (optimizer + legacy/matrix resolver).

Pure functions, no backtest / no I/O beyond the tmp_path JSON fixtures.
Precedence: strategy_defaults < strategies.json[params] < instrument_overrides[symbol] < sampled.
"""
from automation.optimizer.resolve import resolve_params
from automation.backtest_runner import resolve_strategy_params


# --- optimizer-side resolve_params ----------------------------------------
def test_optimizer_override_precedence(tmp_path):
    (tmp_path / "strategy_defaults.json").write_text(
        '{"VwapExhaustionStrategy":{"vwap_period":24,"cooldown_bars":3}}', "utf-8")
    (tmp_path / "strategies.json").write_text(
        '{"strategies":[{"strategy_class":"VwapExhaustionStrategy","params":{"vwap_period":20},'
        '"instrument_overrides":{"TSLA.ETORO":{"vwap_period":32,"cooldown_bars":5}}}]}', "utf-8")
    out = resolve_params("VwapExhaustionStrategy", {}, tmp_path, instrument="TSLA.ETORO")
    assert out["vwap_period"] == 32      # override beats params/defaults
    assert out["cooldown_bars"] == 5
    # sampled stays highest precedence:
    out2 = resolve_params("VwapExhaustionStrategy", {"vwap_period": 99}, tmp_path,
                          instrument="TSLA.ETORO")
    assert out2["vwap_period"] == 99


def test_optimizer_no_instrument_is_legacy(tmp_path):
    (tmp_path / "strategy_defaults.json").write_text('{"X":{"a":1}}', "utf-8")
    (tmp_path / "strategies.json").write_text(
        '{"strategies":[{"strategy_class":"X","params":{"a":2},'
        '"instrument_overrides":{"S":{"a":3}}}]}', "utf-8")
    assert resolve_params("X", {}, tmp_path)["a"] == 2   # without instrument: no override


def test_optimizer_unknown_instrument_falls_back_to_params(tmp_path):
    (tmp_path / "strategy_defaults.json").write_text('{"X":{"a":1}}', "utf-8")
    (tmp_path / "strategies.json").write_text(
        '{"strategies":[{"strategy_class":"X","params":{"a":2},'
        '"instrument_overrides":{"S":{"a":3}}}]}', "utf-8")
    # instrument set but no override for it -> just params
    assert resolve_params("X", {}, tmp_path, instrument="OTHER.ETORO")["a"] == 2


# --- legacy/matrix resolve_strategy_params --------------------------------
def test_runner_manifest_never_overrides():
    e = {"params": {"a": 1}, "instrument_overrides": {"S": {"a": 9}}}
    assert resolve_strategy_params(e, {"a": 0}, is_manifest=True, instrument="S") == {"a": 1}


def test_runner_legacy_applies_override():
    e = {"params": {"a": 1}, "instrument_overrides": {"S": {"a": 9}}}
    assert resolve_strategy_params(e, {"b": 5}, is_manifest=False, instrument="S") == {"a": 9, "b": 5}
    # no override without instrument (bit-identical legacy behaviour, HI-2):
    assert resolve_strategy_params(e, {"b": 5}, is_manifest=False) == {"a": 1, "b": 5}


def test_runner_legacy_unknown_instrument_is_legacy():
    e = {"params": {"a": 1}, "instrument_overrides": {"S": {"a": 9}}}
    assert resolve_strategy_params(e, {"b": 5}, is_manifest=False, instrument="OTHER") == {"a": 1, "b": 5}
