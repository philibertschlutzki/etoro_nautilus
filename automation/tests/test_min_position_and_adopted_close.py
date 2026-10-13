"""eToro-Mindestbetrag (Forex 1000 USD) und Close übernommener Positionen."""
from __future__ import annotations

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from automation import min_position
from automation.momentum_ls_allocator import MomentumLSAllocator

ERR = ("error_720: Initial Leveraged Position Amount is under the minimum defined Leveraged Amount in the system. "
       "leveraged InitialPositionAmount: 197.92 MinimumPositionAmount: 1000 (Dollars)")


class _Cache:
    def positions_open(self, instrument_id=None):
        return []


@pytest.fixture
def fx(monkeypatch, tmp_path):
    from automation.strategies import hourly_strategy_base as hsb
    monkeypatch.setattr(hsb, "_read_instrument_asset_class",
                        lambda i: "FOREX" if i.startswith("AUD") else "EQUITY")
    monkeypatch.setattr(min_position, "LEARNED_PATH", tmp_path / "m.json")


def test_parse_and_learn(tmp_path):
    path = tmp_path / "m.json"
    assert min_position.parse_minimum(ERR) == 1000.0
    assert min_position.parse_minimum("HTTP 500") is None
    assert min_position.learn_from_rejection("XAUUSD.ETORO", ERR.replace("1000", "50"), path) == 50.0
    assert min_position.min_notional("XAUUSD.ETORO", "COMMODITY", path) == 50.0
    assert min_position.min_notional("AUDJPY.ETORO", "FOREX", path) == 1000.0
    assert min_position.min_notional("TSLA.ETORO", "EQUITY", path) == 0.0


def test_allocation_raised_to_forex_minimum_when_budget_allows(fx):
    alloc = MomentumLSAllocator(["AUDJPY.ETORO", "TSLA.ETORO"], max_total_exposure_fraction=0.60,
                                max_symbol_exposure_fraction=0.02)
    fx_alloc = alloc.get_allocation(InstrumentId.from_str("AUDJPY.ETORO"), _Cache(), 10000.0)
    assert fx_alloc == pytest.approx(1010.0)                    # 1000 USD + 1 % Sicherheit, 10,1 % der Equity
    assert alloc.get_allocation(InstrumentId.from_str("TSLA.ETORO"), _Cache(), 10000.0) == pytest.approx(200.0)


def test_allocation_skipped_when_minimum_exceeds_concentration_limit(fx):
    alloc = MomentumLSAllocator(["AUDJPY.ETORO"], max_total_exposure_fraction=0.60, max_symbol_exposure_fraction=0.02)
    assert alloc.get_allocation(InstrumentId.from_str("AUDJPY.ETORO"), _Cache(), 5000.0) == 0.0   # 1010 > 15 % von 5000


def test_close_of_adopted_position_is_booked_on_that_position_and_resets_strategy_state():
    from unittest.mock import MagicMock, PropertyMock, patch
    from nautilus_trader.model.enums import PositionSide
    from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy

    strategy = Rsi2ReversionStrategy(config=Rsi2ReversionConfig(
        instrument_id="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL"))
    strategy.submit_order = MagicMock()
    order = MagicMock()
    order.client_order_id = "O-CLOSE-1"
    factory = MagicMock()
    factory.market.return_value = order
    adopted = MagicMock()
    adopted.side = PositionSide.LONG
    adopted.strategy_id = "EXTERNAL"
    cache = MagicMock()
    cache.positions_open.return_value = [adopted]
    with patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=cache), \
            patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=factory):
        strategy._execute_market_close(adopted)
        strategy.submit_order.assert_called_once_with(order, position_id=adopted.id)
        strategy._in_position = True
        strategy._exit_pending = "SIGNAL"
        strategy.cancel_all_orders = MagicMock()
        cache.positions_open.return_value = []            # Position nach dem Fill geschlossen
        event = MagicMock()
        event.client_order_id = "O-CLOSE-1"
        strategy.on_order_filled(event)
    assert strategy._in_position is False and strategy._exit_pending is None
    assert not strategy._adopted_close_order_ids


def test_close_of_own_position_keeps_plain_submit():
    from unittest.mock import MagicMock, PropertyMock, patch
    from nautilus_trader.model.enums import PositionSide
    from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy

    strategy = Rsi2ReversionStrategy(config=Rsi2ReversionConfig(
        instrument_id="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL"))
    strategy.submit_order = MagicMock()
    factory = MagicMock()
    own = MagicMock()
    own.side = PositionSide.LONG
    own.strategy_id = "Rsi2Reversion-000"
    with patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=factory):
        strategy._execute_market_close(own)
    strategy.submit_order.assert_called_once_with(factory.market.return_value)
