import sys
import unittest
from unittest.mock import patch

class TestLiveExecutionDefaults(unittest.TestCase):
    @patch('os.getenv')
    def test_default_execution_parameters_are_safe(self, mock_getenv):
        """Ensures that missing .env parameters strictly default to safe demo mode."""
        # Setup mock to simulate missing env vars
        mock_getenv.side_effect = lambda key, default=None: default

        if 'automation.momentum_ls_run' in sys.modules:
            del sys.modules['automation.momentum_ls_run']

        from automation.momentum_ls_run import ETORO_EXECUTION

        self.assertEqual(ETORO_EXECUTION["environment"], "demo")
        self.assertTrue(ETORO_EXECUTION["dry_run"])
        self.assertFalse(ETORO_EXECUTION["enable_trailing_stop"])

if __name__ == '__main__':
    unittest.main()

import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.data import BarType, Bar

from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig

class DummyStrategyConfig(HourlyStrategyConfig):
    pass

class DummyStrategy(HourlyStrategyBase):
    def __init__(self, config):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

    def on_start(self):
        super().on_start()

def test_trend_filter_warmup(mocker):
    config = DummyStrategyConfig(
        instrument_id="BTC.ETORO",
        bar_type="BTC.ETORO-1-HOUR-LAST-EXTERNAL",
        trend_filter_period=5
    )
    strategy = DummyStrategy(config=config)
    mock_cache = MagicMock()
    mock_cache.accounts.return_value = []
    mocker.patch.object(DummyStrategy, 'cache', new_callable=mocker.PropertyMock, return_value=mock_cache)

    # Create fake parquet dataframe
    dates = pd.date_range(start="2024-01-01", periods=30, freq="1h")
    df = pd.DataFrame({
        'ts_event': dates.astype('int64') * 10**9,
        'bid_price': [100.0 + i for i in range(30)],
        'ask_price': [100.0 + i for i in range(30)]
    })

    # Mock Path and ParquetDataset
    mock_path_exists = mocker.patch("automation.strategies.hourly_strategy_base.Path.exists", return_value=True)
    mock_pq = mocker.patch("automation.strategies.hourly_strategy_base.pq.ParquetDataset")
    mock_table = MagicMock()
    mock_table.to_pandas.return_value = df
    mock_pq.return_value.read.return_value = mock_table

    # Test warmup
    strategy.on_start()

    print(strategy._trend_filter_ready)
    assert strategy._trend_filter_ready == True
    assert strategy.trend_filter_sma.initialized == True

    # Test can_go_long block
    bar_low = Bar(

        bar_type=strategy.bar_type,
        open=Price(100.0, 2),
        high=Price(100.0, 2),
        low=Price(100.0, 2),
        close=Price(100.0, 2),
        volume=Quantity(1.0, 2),
        ts_event=1,
        ts_init=1
    )
    # Since prices were 104-109, SMA should be ~ 107. 100 is below 107, should block
    assert strategy.can_go_long(bar_low) == False

    bar_high = Bar(

        bar_type=strategy.bar_type,
        open=Price(150.0, 2),
        high=Price(150.0, 2),
        low=Price(150.0, 2),
        close=Price(150.0, 2),
        volume=Quantity(1.0, 2),
        ts_event=1,
        ts_init=1
    )
    # 150 is above 107
    assert strategy.can_go_long(bar_high) == True

def test_trend_filter_insufficient_data(mocker):
    config = DummyStrategyConfig(
        instrument_id="ETH.ETORO",
        bar_type="ETH.ETORO-1-HOUR-LAST-EXTERNAL",
        trend_filter_period=200
    )
    strategy = DummyStrategy(config=config)
    mock_cache = MagicMock()
    mock_cache.accounts.return_value = []
    mocker.patch.object(DummyStrategy, 'cache', new_callable=mocker.PropertyMock, return_value=mock_cache)

    # Create fake parquet dataframe with only 150 rows
    dates = pd.date_range(start="2024-01-01", periods=150, freq="1h")
    df = pd.DataFrame({
        'ts_event': dates.astype('int64') * 10**9,
        'bid_price': [100.0 + i for i in range(150)],
        'ask_price': [100.0 + i for i in range(150)]
    })

    mocker.patch("automation.strategies.hourly_strategy_base.Path.exists", return_value=True)
    mock_pq = mocker.patch("automation.strategies.hourly_strategy_base.pq.ParquetDataset")
    mock_table = MagicMock()
    mock_table.to_pandas.return_value = df
    mock_pq.return_value.read.return_value = mock_table

    strategy.on_start()

    assert strategy._trend_filter_ready == False

    bar = Bar(
        bar_type=strategy.bar_type,
        open=Price(150.0, 2),
        high=Price(150.0, 2),
        low=Price(150.0, 2),
        close=Price(150.0, 2),
        volume=Quantity(1.0, 2),
        ts_event=1,
        ts_init=1
    )
    # Blocked due to insufficient data
    assert strategy.can_go_long(bar) == False

def test_trend_filter_missing_data(mocker):
    config = DummyStrategyConfig(
        instrument_id="ETH.ETORO",
        bar_type="ETH.ETORO-1-HOUR-LAST-EXTERNAL",
        trend_filter_period=5
    )
    strategy = DummyStrategy(config=config)
    mock_cache = MagicMock()
    mock_cache.accounts.return_value = []
    mocker.patch.object(DummyStrategy, 'cache', new_callable=mocker.PropertyMock, return_value=mock_cache)

    # Missing directory
    mocker.patch("automation.strategies.hourly_strategy_base.Path.exists", return_value=False)

    strategy.on_start()

    assert strategy._trend_filter_ready == False

    bar = Bar(

        bar_type=strategy.bar_type,
        open=Price(150.0, 2),
        high=Price(150.0, 2),
        low=Price(150.0, 2),
        close=Price(150.0, 2),
        volume=Quantity(1.0, 2),
        ts_event=1,
        ts_init=1
    )
    # Missing data blocks everything if trend_filter_period > 0
    assert strategy.can_go_long(bar) == False
