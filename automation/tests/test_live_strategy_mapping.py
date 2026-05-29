import pytest
from automation.momentum_ls_run import _build_strategy_registry, _build_bots_config

def test_build_strategy_registry(tmp_path):
    strategies_data = """{
      "strategies": [
        {
          "active": true,
          "strategy_module": "automation.strategies.mean_reversion",
          "strategy_class": "MeanReversionStrategy",
          "config_class": "MeanReversionConfig",
          "params": {}
        },
        {
          "active": false,
          "strategy_module": "automation.strategies.adx_atr_momentum",
          "strategy_class": "AdxAtrMomentumStrategy",
          "config_class": "AdxAtrMomentumConfig",
          "params": {}
        }
      ]
    }"""
    cfg_file = tmp_path / "strategies.json"
    cfg_file.write_text(strategies_data)

    registry = _build_strategy_registry(str(cfg_file))

    assert "MeanReversionStrategy" in registry
    assert "AdxAtrMomentumStrategy" not in registry
    assert "MomentumLSSmaStrategy" not in registry

def test_build_bots_config():
    universe_data = {
        "universe": [
            {"symbol": "AAPL.ETORO"},
            {"symbol": "TSLA.ETORO"},
            {"symbol": "INVALID.ETORO"}
        ]
    }

    tournament_data = {
        "per_symbol_winners": {
            "AAPL.ETORO": {"strategy": "MeanReversionStrategy"},
            "TSLA.ETORO": {"strategy": "UnknownStrategy"},
            "INVALID.ETORO": {"strategy": "MeanReversionStrategy"}
        }
    }

    registry = {
        "MeanReversionStrategy": ("module", "MeanReversionStrategy", "MeanReversionConfig")
    }

    defaults = {
        "MeanReversionStrategy": {
            "keltner_period": 10,
            "trade_amount_usd": 1500.0
        }
    }

    strategies_raw = [
        {
            "strategy_class": "MeanReversionStrategy",
            "params": {"keltner_period": 20, "max_open_positions": 2}
        }
    ]

    symbol_to_etoro_id = {
        "AAPL.ETORO": "1001",
        "TSLA.ETORO": "1002"
    }

    active_symbols, bots_config = _build_bots_config(
        universe_data,
        tournament_data,
        registry,
        defaults,
        strategies_raw,
        symbol_to_etoro_id
    )

    # AAPL.ETORO should be valid
    # TSLA.ETORO has UnknownStrategy -> should be skipped
    # INVALID.ETORO has no etoro_id -> should be skipped
    assert active_symbols == ["AAPL.ETORO"]
    assert len(bots_config) == 1

    bot_spec = bots_config[0]
    assert bot_spec["strategy_class"] == "MeanReversionStrategy"
    assert bot_spec["symbol"] == "AAPL.ETORO"
    assert bot_spec["etoro_id"] == "1001"
    assert bot_spec["bar_type"] == "AAPL.ETORO-1-HOUR-MID-INTERNAL"

    params = bot_spec["params"]
    assert "trade_amount_usd" not in params
    assert params["keltner_period"] == 20  # Overriden from raw
    assert bot_spec["max_open_positions"] == 2
    assert "max_open_positions" not in params # Should be extracted

from automation.momentum_ls_run import _instantiate_strategy
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.data import BarType
from automation.momentum_ls_allocator import MomentumLSAllocator
from automation.strategies.sma_crossover import SmaCrossoverStrategy
from automation.strategies.mean_reversion import MeanReversionStrategy

def test_strategy_instantiation():
    registry = {
        "SmaCrossoverStrategy": ("automation.strategies.sma_crossover", "SmaCrossoverStrategy", "SmaCrossoverConfig"),
        "MeanReversionStrategy": ("automation.strategies.mean_reversion", "MeanReversionStrategy", "MeanReversionConfig")
    }

    allocator = MomentumLSAllocator(["AAPL.ETORO", "TSLA.ETORO"])

    # Test SMA
    sma_spec = {
        "strategy_class": "SmaCrossoverStrategy",
        "symbol": "AAPL.ETORO",
        "bar_type": "AAPL.ETORO-1-HOUR-MID-INTERNAL",
        "params": {"sma_period": 5},
        "max_open_positions": 1
    }
    sma_strategy = _instantiate_strategy(sma_spec, registry, allocator, 0)
    assert isinstance(sma_strategy, SmaCrossoverStrategy)
    assert sma_strategy.config.instrument_id == "AAPL.ETORO"
    assert sma_strategy.config.bar_type == "AAPL.ETORO-1-HOUR-MID-INTERNAL"
    assert sma_strategy.instrument_id == InstrumentId.from_str("AAPL.ETORO")
    assert sma_strategy.bar_type == BarType.from_str("AAPL.ETORO-1-HOUR-MID-INTERNAL")
    assert sma_strategy.allocator is allocator

    # Test Mean Reversion
    mr_spec = {
        "strategy_class": "MeanReversionStrategy",
        "symbol": "TSLA.ETORO",
        "bar_type": "TSLA.ETORO-1-HOUR-MID-INTERNAL",
        "params": {"keltner_period": 10},
        "max_open_positions": 2
    }
    mr_strategy = _instantiate_strategy(mr_spec, registry, allocator, 1)
    assert isinstance(mr_strategy, MeanReversionStrategy)
    assert mr_strategy.config.instrument_id == "TSLA.ETORO"
    assert mr_strategy.config.bar_type == "TSLA.ETORO-1-HOUR-MID-INTERNAL"
    assert mr_strategy.instrument_id == InstrumentId.from_str("TSLA.ETORO")
    assert mr_strategy.bar_type == BarType.from_str("TSLA.ETORO-1-HOUR-MID-INTERNAL")
    assert mr_strategy.allocator is allocator

def test_strategy_instantiation_fails_with_core_objects():
    registry = {
        "SmaCrossoverStrategy": ("automation.strategies.sma_crossover", "SmaCrossoverStrategy", "SmaCrossoverConfig")
    }
    allocator = MomentumLSAllocator(["AAPL.ETORO"])

    invalid_spec = {
        "strategy_class": "SmaCrossoverStrategy",
        "symbol": InstrumentId.from_str("AAPL.ETORO"), # Intentional typo to trigger failure if object passed
        "bar_type": BarType.from_str("AAPL.ETORO-1-HOUR-MID-INTERNAL"),
        "params": {"sma_period": 5}
    }

    # Passing Core Objects should fail when creating ConfigClass because ConfigClass expects strings for validation
    with pytest.raises(Exception):
        _instantiate_strategy(invalid_spec, registry, allocator, 0)
