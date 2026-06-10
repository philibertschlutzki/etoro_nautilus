from automation.backtest_runner import resolve_strategy_params

DEFAULTS = {"keltner_period": 99, "foo": 7}

def test_manifest_uses_params_verbatim():
    entry = {"params": {"keltner_period": 14, "bar": 1}}
    out = resolve_strategy_params(entry, DEFAULTS, is_manifest=True)
    assert out == {"keltner_period": 14, "bar": 1}     # kein 'foo' aus Defaults
    assert "foo" not in out

def test_legacy_merges_defaults():
    entry = {"params": {"keltner_period": 14, "bar": 1}}
    out = resolve_strategy_params(entry, DEFAULTS, is_manifest=False)
    assert out == {"keltner_period": 14, "foo": 7, "bar": 1}
