from automation.utils import _fallback_precisions
from automation.backtest_runner import create_mock_instrument
from automation.strategies.adx_atr_momentum import AdxAtrMomentumStrategy
from automation.strategies.trend_pullback import TrendPullbackStrategy
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.objects import Quantity, Price
from nautilus_trader.model.identifiers import InstrumentId
from automation.backtest_runner import load_ticks_from_catalog
import pytest
from unittest.mock import MagicMock

def test_fallback_precisions_equity():
    assert _fallback_precisions("AAPL") == (2, 2)

def test_create_mock_instrument_zero_precision():
    # Because backtest_runner defaults to 8 for backward compatibility with Nautilus defaults
    # if it's not correctly written in parquet yet.
    inst = create_mock_instrument("AAPL.ETORO", price_precision=2, size_precision=0)
    assert inst.size_precision == 8
    assert float(inst.size_increment) == 1e-08

from automation.strategies.sma_crossover import SmaCrossoverStrategy
from automation.strategies.mean_reversion import MeanReversionStrategy
from automation.strategies.dynamic_breakout import DynamicBreakoutStrategy
from automation.strategies.flash_crash_reversal import FlashCrashReversalStrategy
from automation.strategies.volatility_breakout import VolatilityBreakoutPumpStrategy
from automation.strategies.tesla_combo_strategy import ComboTrendVwapStrategy
from automation.strategies.vwap_exhaustion import VwapExhaustionStrategy
from automation.strategies.mean_reversion import MeanReversionStrategy as HourlyMeanReversionStrategy

def test_strategy_inheritance():
    active_strategies = [
        SmaCrossoverStrategy,
        MeanReversionStrategy,
        DynamicBreakoutStrategy,
        FlashCrashReversalStrategy,
        VolatilityBreakoutPumpStrategy,
        ComboTrendVwapStrategy,
        VwapExhaustionStrategy,
        HourlyMeanReversionStrategy
    ]

    for strategy_class in active_strategies:
        assert "HourlyStrategyBase" in [b.__name__ for b in strategy_class.__mro__], f"{strategy_class.__name__} does not inherit from HourlyStrategyBase"
        assert "_compute_quantity" not in strategy_class.__dict__, f"{strategy_class.__name__} has its own _compute_quantity"

    assert "HourlyStrategyBase" in [b.__name__ for b in AdxAtrMomentumStrategy.__bases__]
    assert "HourlyStrategyBase" in [b.__name__ for b in TrendPullbackStrategy.__bases__]
    assert "_compute_quantity" not in AdxAtrMomentumStrategy.__dict__
    assert "_compute_quantity" not in TrendPullbackStrategy.__dict__

def test_load_ticks_from_catalog_normalizes_precision():
    catalog_mock = MagicMock()
    iid = InstrumentId.from_str("AAPL.ETORO")

    # Create ticks with precision=0
    tick = QuoteTick(
        instrument_id=iid,
        bid_price=Price(100.0, 2),
        ask_price=Price(101.0, 2),
        bid_size=Quantity(1.0, 0),
        ask_size=Quantity(1.0, 0),
        ts_event=1000,
        ts_init=1000
    )
    catalog_mock.quote_ticks.return_value = [tick]

    normalized_ticks = load_ticks_from_catalog(catalog_mock, "AAPL.ETORO", None, None)

    assert len(normalized_ticks) == 1
    assert normalized_ticks[0].bid_size.precision == 8
    assert normalized_ticks[0].ask_size.precision == 8
