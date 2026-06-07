"""
tests/test_vwap_exhaustion_state.py
=====================================
Tests Issue #211 / Pitfall #41: Signal-State darf nicht mutiert werden,
wenn ein Early-Return in on_buy_signal / on_sell_signal greift.
(AGENTS.md §16, Pitfall #41)
"""
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import PositionSide

from automation.strategies.vwap_exhaustion import VwapExhaustionConfig, VwapExhaustionStrategy


def _make_strategy(mocker_cache: MagicMock) -> VwapExhaustionStrategy:
    config = VwapExhaustionConfig(
        instrument_id="AAPL.ETORO",
        bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        max_open_positions=1,
    )
    strategy = VwapExhaustionStrategy(config=config)
    strategy.submit_order = MagicMock()
    strategy.close_position = MagicMock()
    strategy._compute_quantity = MagicMock()
    return strategy


def test_vwap_exhaustion_early_return_no_state_mutation():
    """
    Issue #211: Signal-State darf nicht mutiert werden, wenn Early-Return triggert.
    """
    mock_cache = MagicMock()
    config = VwapExhaustionConfig(
        instrument_id="AAPL.ETORO",
        bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        max_open_positions=1,
    )
    strategy = VwapExhaustionStrategy(config=config)
    strategy.submit_order = MagicMock()
    strategy.close_position = MagicMock()
    strategy._compute_quantity = MagicMock()

    bar = MagicMock(spec=Bar)

    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    # Szenario 1: Early Return wegen offener Order
    with patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache):
        mock_cache.positions_open.return_value = []
        mock_cache.orders_open.return_value = [MagicMock()]

        strategy._on_buy_signal(bar)
        assert strategy.current_signal is None
        assert strategy.bars_since_last_signal == 9999

        strategy._on_sell_signal(bar)
        assert strategy.current_signal is None
        assert strategy.bars_since_last_signal == 9999

        # Szenario 2: Early Return wegen gleicher Positionsrichtung
        mock_cache.orders_open.return_value = []

        long_pos = MagicMock()
        long_pos.side = PositionSide.LONG
        mock_cache.positions_open.return_value = [long_pos]

        strategy._on_buy_signal(bar)
        assert strategy.current_signal is None
        assert strategy.bars_since_last_signal == 9999

        short_pos = MagicMock()
        short_pos.side = PositionSide.SHORT
        mock_cache.positions_open.return_value = [short_pos]

        strategy._on_sell_signal(bar)
        assert strategy.current_signal is None
        assert strategy.bars_since_last_signal == 9999

        # Szenario 3: Flip-Close (Gegenposition)
        mock_cache.positions_open.return_value = [short_pos]
        strategy._on_buy_signal(bar)
        strategy.close_position.assert_called_once_with(short_pos)
        assert strategy.current_signal is None
        assert strategy.bars_since_last_signal == 9999

        strategy.close_position.reset_mock()

        mock_cache.positions_open.return_value = [long_pos]
        strategy._on_sell_signal(bar)
        strategy.close_position.assert_called_once_with(long_pos)
        assert strategy.current_signal is None
        assert strategy.bars_since_last_signal == 9999

        # Szenario 4: max_open_positions Guard
        pos1, pos2 = MagicMock(), MagicMock()
        pos1.side = None
        pos2.side = None
        mock_cache.positions_open.return_value = [pos1, pos2]

        strategy._on_buy_signal(bar)
        assert strategy.current_signal is None
        assert strategy.bars_since_last_signal == 9999

        # Szenario 5: _compute_quantity liefert None
        mock_cache.positions_open.return_value = []
        strategy._compute_quantity.return_value = None

        strategy._on_buy_signal(bar)
        assert strategy.current_signal is None
        assert strategy.bars_since_last_signal == 9999
