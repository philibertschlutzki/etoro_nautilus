import pytest
from unittest.mock import MagicMock
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import PositionSide
from automation.strategies.vwap_exhaustion import VwapExhaustionStrategy, VwapExhaustionConfig

def test_vwap_exhaustion_early_return_no_state_mutation(mocker):
    """
    Testet Issue #211: Signal-State darf nicht mutiert werden, wenn ein Early-Return triggert.
    """
    config = VwapExhaustionConfig(
        instrument_id="AAPL.ETORO",
        bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        max_open_positions=1
    )

    strategy = VwapExhaustionStrategy(config=config)

    # Mock cache
    mock_cache = MagicMock()
    mocker.patch.object(VwapExhaustionStrategy, 'cache', new_callable=mocker.PropertyMock, return_value=mock_cache)

    # Mock submit_order um sicherzustellen, dass keine Order gesendet wird
    strategy.submit_order = MagicMock()
    strategy.close_position = MagicMock()
    strategy._compute_quantity = MagicMock()

    # Bar Mock
    bar = MagicMock(spec=Bar)

    # Initial state
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    # --- Szenario 1: Early Return durch offene Orders ---
    mock_cache.positions_open.return_value = []
    mock_cache.orders_open.return_value = [MagicMock()]

    strategy._on_buy_signal(bar)
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    strategy._on_sell_signal(bar)
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    # --- Szenario 2: Early Return durch existierende Position gleicher Richtung ---
    mock_cache.orders_open.return_value = []

    long_pos = MagicMock()
    long_pos.side = PositionSide.LONG
    mock_cache.positions_open.return_value = [long_pos]

    strategy._on_buy_signal(bar) # Should early return for LONG
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    short_pos = MagicMock()
    short_pos.side = PositionSide.SHORT
    mock_cache.positions_open.return_value = [short_pos]

    strategy._on_sell_signal(bar) # Should early return for SHORT
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    # --- Szenario 3: Flip-Close (Gegenposition) ---
    mock_cache.positions_open.return_value = [short_pos] # Test BUY with open SHORT
    strategy._on_buy_signal(bar)
    strategy.close_position.assert_called_once_with(short_pos)
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    strategy.close_position.reset_mock()

    mock_cache.positions_open.return_value = [long_pos] # Test SELL with open LONG
    strategy._on_sell_signal(bar)
    strategy.close_position.assert_called_once_with(long_pos)
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    # --- Szenario 4: max_open_positions Guard ---
    mock_cache.positions_open.return_value = [MagicMock(), MagicMock()] # Len 2 >= 1
    # Mock position sides so it doesn't trigger same/flip-close logic
    mock_cache.positions_open.return_value[0].side = None

    strategy._on_buy_signal(bar)
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    # --- Szenario 5: _compute_quantity liefert None ---
    mock_cache.positions_open.return_value = []
    strategy._compute_quantity.return_value = None

    strategy._on_buy_signal(bar)
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999
