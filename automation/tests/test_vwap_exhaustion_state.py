import pytest
from unittest.mock import MagicMock
from nautilus_trader.model.data import Bar
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

    # Mock cache - remember memory rule about PropertyMock
    mock_cache = MagicMock()
    mock_cache.positions_open.return_value = []

    # Simuliere Early Return, z.B. durch offene Orders
    mock_cache.orders_open.return_value = [MagicMock()]

    mocker.patch.object(VwapExhaustionStrategy, 'cache', new_callable=mocker.PropertyMock, return_value=mock_cache)

    # Mock submit_order um sicherzustellen, dass keine Order gesendet wird
    strategy.submit_order = MagicMock()

    # Bar Mock
    bar = MagicMock(spec=Bar)

    # Initial state
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    # Trigger Buy Signal (should early return)
    strategy._on_buy_signal(bar)

    # Assert state is NOT mutated
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999

    # Trigger Sell Signal (should early return)
    strategy._on_sell_signal(bar)

    # Assert state is NOT mutated
    assert strategy.current_signal is None
    assert strategy.bars_since_last_signal == 9999
