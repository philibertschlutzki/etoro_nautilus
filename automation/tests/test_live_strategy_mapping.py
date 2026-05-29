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
