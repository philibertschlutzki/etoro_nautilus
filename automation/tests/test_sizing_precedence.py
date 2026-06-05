import pytest
from unittest.mock import MagicMock
from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig
from nautilus_trader.model.data import Bar

class DummyConfig(HourlyStrategyConfig):
    pass

class DummyStrategy(HourlyStrategyBase):
    def on_bar(self, bar: Bar) -> None:
        pass

def test_sizing_precedence_usd_over_pct(mocker):
    """
    Testet Issue #182: Wenn trade_amount_usd gesetzt ist (und kein Allocator existiert),
    hat dies Vorrang vor trade_amount_pct. self._get_current_balance() darf in
    diesem Fall exakt 0 Mal aufgerufen werden.
    """
    config = DummyConfig(
        instrument_id="AAPL.NASDAQ",
        bar_type="1-HOUR-MID-INTERNAL",
        trade_amount_usd=500.0,
        trade_amount_pct=10.0,
        atr_period=14,
        atr_trailing_multiplier=1.5,
        max_bars_in_trade=48
    )

    strategy = DummyStrategy(config=config)
    strategy.instrument_id = config.instrument_id

    # Mock the cache and instrument using mocker.patch.object
    mock_cache = MagicMock()
    mock_instrument = MagicMock()
    mock_instrument.make_qty.return_value = MagicMock()
    mock_instrument.size_increment = 1.0
    mock_instrument.size_precision = 0
    mock_instrument.make_qty.return_value.__float__ = lambda x: 5.0
    mock_cache.instrument.return_value = mock_instrument
    mocker.patch.object(DummyStrategy, "cache", new_callable=mocker.PropertyMock, return_value=mock_cache)

    # Mock _get_current_balance
    mock_get_balance = mocker.patch.object(strategy, "_get_current_balance", return_value=10000.0)

    # Create a dummy bar
    mock_bar = MagicMock(spec=Bar)
    mock_bar.close = 100.0

    # Execute
    qty = strategy._compute_quantity(mock_bar)

    # Verify _get_current_balance was never called
    assert mock_get_balance.call_count == 0, "_get_current_balance sollte bei explizitem trade_amount_usd nicht aufgerufen werden."

    # Verify make_qty was called correctly (500.0 USD / 100.0 Price = 5.0 Units)
    mock_instrument.make_qty.assert_called_once_with(5.0)

def test_sizing_precedence_allocator(mocker):
    """
    Testet, dass der Allocator höchste Priorität hat und _get_current_balance() aufgerufen wird.
    """
    config = DummyConfig(
        instrument_id="AAPL.NASDAQ",
        bar_type="1-HOUR-MID-INTERNAL",
        trade_amount_usd=500.0,
        trade_amount_pct=10.0,
        atr_period=14,
        atr_trailing_multiplier=1.5,
        max_bars_in_trade=48
    )

    strategy = DummyStrategy(config=config)
    strategy.instrument_id = config.instrument_id
    strategy.allocator = MagicMock()
    strategy.allocator.get_allocation.return_value = 200.0

    mock_cache = MagicMock()
    mock_instrument = MagicMock()
    mock_instrument.make_qty.return_value = MagicMock()
    mock_instrument.size_increment = 1.0
    mock_instrument.size_precision = 0
    mock_instrument.make_qty.return_value.__float__ = lambda x: 2.0
    mock_cache.instrument.return_value = mock_instrument
    mocker.patch.object(DummyStrategy, "cache", new_callable=mocker.PropertyMock, return_value=mock_cache)

    mock_get_balance = mocker.patch.object(strategy, "_get_current_balance", return_value=10000.0)

    mock_bar = MagicMock(spec=Bar)
    mock_bar.close = 100.0

    strategy._compute_quantity(mock_bar)

    assert mock_get_balance.call_count == 1
    mock_instrument.make_qty.assert_called_once_with(2.0)

def test_sizing_precedence_pct(mocker):
    """
    Testet, dass pct genutzt wird, wenn usd fehlt und kein Allocator da ist.
    """
    config = DummyConfig(
        instrument_id="AAPL.NASDAQ",
        bar_type="1-HOUR-MID-INTERNAL",
        trade_amount_pct=10.0,
        atr_period=14,
        atr_trailing_multiplier=1.5,
        max_bars_in_trade=48,
        trade_amount_usd=0.0 # Force to 0 so it skips the USD check
    )

    strategy = DummyStrategy(config=config)
    strategy.instrument_id = config.instrument_id

    mock_cache = MagicMock()
    mock_instrument = MagicMock()
    mock_instrument.make_qty.return_value = MagicMock()
    mock_instrument.size_increment = 1.0
    mock_instrument.size_precision = 0
    mock_instrument.make_qty.return_value.__float__ = lambda x: 10.0
    mock_cache.instrument.return_value = mock_instrument
    mocker.patch.object(DummyStrategy, "cache", new_callable=mocker.PropertyMock, return_value=mock_cache)

    mock_get_balance = mocker.patch.object(strategy, "_get_current_balance", return_value=10000.0)

    mock_bar = MagicMock(spec=Bar)
    mock_bar.close = 100.0

    strategy._compute_quantity(mock_bar)

    assert mock_get_balance.call_count == 1
    # 10000 * 0.1 = 1000 / 100 = 10
    mock_instrument.make_qty.assert_called_once_with(10.0)
