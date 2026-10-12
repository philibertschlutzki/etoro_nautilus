"""Übernommene eToro-Positionen müssen der Strategie des Instruments gehören (external_order_claims)."""
import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from automation import momentum_ls_run as run

CFG = Path(run.__file__).resolve().parent / "config" / "strategies.json"


def _spec(strategy_class, symbol):
    return {"strategy_class": strategy_class, "symbol": symbol, "bar_type": f"{symbol}-1-HOUR-MID-INTERNAL",
            "params": {}, "live_params_sha256": "x"}


def test_jede_strategie_config_kennt_external_order_claims():
    registry = run._build_strategy_registry(str(CFG))
    assert registry
    for name, (module_name, _cls, config_name) in registry.items():
        config_cls = getattr(importlib.import_module(module_name), config_name)
        assert "external_order_claims" in config_cls.__struct_fields__, name


@pytest.mark.parametrize("strategy_class", ["Rsi2ReversionStrategy", "SmaCrossoverStrategy", "VwapExhaustionStrategy"])
def test_instanziierte_strategie_beansprucht_ihr_instrument(strategy_class):
    registry = run._build_strategy_registry(str(CFG))
    allocator = MagicMock()
    strategy = run._instantiate_strategy(_spec(strategy_class, "BTC.ETORO"), registry, allocator, 3)
    assert list(strategy.config.external_order_claims) == ["BTC.ETORO"]
    claims = {str(i) for i in strategy.external_order_claims}
    assert claims == {"BTC.ETORO"}
