"""Issue #947 (Katalog B, P1) — die Simulation erlaubte negatives Eigenkapital: `EQUITY_NONPOSITIVE`
war eine reine Diagnose (post-hoc, nach dem vollständigen Lauf), keine Simulationsregel — der
Backtest rechnete über den Ruin hinaus weiter, Return-Berechnungen über einer nicht-positiven Basis
haben kein definiertes Vorzeichen.

Fix: `HourlyStrategyBase._check_exits_and_update` erzwingt jetzt einen harten Margin-Stop-out
(``equity <= stop_out_equity_frac * initial_equity`` ⇒ sofortiger Markt-Close aller Positionen,
kein weiterer Handel für den Rest des Trials — `_ruined` bleibt gesetzt).
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Price, Quantity

from automation.strategies.hourly_strategy_base import ExitReason
from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy

_BAR_TYPE = BarType.from_str("TSLA.ETORO-1-HOUR-MID-INTERNAL")
_NS_PER_HOUR = 3_600_000_000_000


def _bar(ts_ns: int, close: float = 100.0):
    price = Price(close, 4)
    return Bar(
        bar_type=_BAR_TYPE,
        open=price, high=price, low=price, close=price,
        volume=Quantity(1.0, 4),
        ts_event=ts_ns, ts_init=ts_ns,
    )


@pytest.fixture
def strategy_env():
    def _build(stop_out_equity_frac: float = 0.20, has_position: bool = True):
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO",
            bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_bars_in_trade=100,
            stop_out_equity_frac=stop_out_equity_frac,
        )
        strategy = Rsi2ReversionStrategy(config=config)
        strategy.submit_order = MagicMock()
        strategy.cancel_order = MagicMock()

        mock_order_factory = MagicMock()
        mock_market_order = MagicMock()
        mock_market_order.client_order_id = "O-MARKET"
        mock_order_factory.market.return_value = mock_market_order

        mock_cache = MagicMock()
        pos = MagicMock()
        pos.side = PositionSide.LONG
        pos.quantity = 10
        mock_cache.positions_open.return_value = [pos] if has_position else []
        mock_cache.orders_open.return_value = []

        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        stack.enter_context(patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory))

        strategy._exit_atr = MagicMock()
        strategy._exit_atr.initialized = True
        strategy._exit_atr.value = 0.001

        if has_position:
            strategy._in_position = True
            strategy._trailing_initialised = True
            strategy._trailing_stop_side = "LONG"
            strategy._trailing_stop_price = 1.0  # weit unter jedem Testpreis -> greift nie
            strategy._bars_in_position = 1

        return strategy, mock_cache, pos

    with ExitStack() as stack:
        yield _build


def test_healthy_equity_does_not_trigger_stopout(strategy_env):
    strategy, mock_cache, pos = strategy_env()
    strategy._get_current_balance = MagicMock(return_value=1000.0)  # konstant, gesund

    exited = strategy._check_exits_and_update(_bar(1 * _NS_PER_HOUR))
    assert strategy._ruined is False
    assert strategy._initial_equity == 1000.0
    strategy.submit_order.assert_not_called()


def test_equity_drop_below_threshold_forces_immediate_close(strategy_env):
    strategy, mock_cache, pos = strategy_env(stop_out_equity_frac=0.20)
    # Bar 1: Startkapital wird festgeschrieben (1000.0).
    strategy._get_current_balance = MagicMock(return_value=1000.0)
    strategy._check_exits_and_update(_bar(1 * _NS_PER_HOUR))
    assert strategy._initial_equity == 1000.0
    assert strategy._ruined is False

    # Bar 2: Equity faellt auf 15% des Startkapitals -> unter der 20%-Schwelle.
    strategy._get_current_balance = MagicMock(return_value=150.0)
    exited = strategy._check_exits_and_update(_bar(2 * _NS_PER_HOUR))

    assert exited is True
    assert strategy._ruined is True
    assert strategy._exit_pending_kind == ExitReason.EQUITY_STOPOUT
    strategy.submit_order.assert_called_once()


def test_ruined_state_blocks_all_further_trading_even_without_open_position(strategy_env):
    """Nach dem Ruin muss JEDER weitere Bar-Aufruf True liefern (Entry-Logik blockiert), auch
    wenn keine Position mehr offen ist (bereits zwangsgeschlossen)."""
    strategy, mock_cache, pos = strategy_env(stop_out_equity_frac=0.20)
    strategy._get_current_balance = MagicMock(return_value=1000.0)
    strategy._check_exits_and_update(_bar(1 * _NS_PER_HOUR))

    strategy._get_current_balance = MagicMock(return_value=100.0)
    strategy._check_exits_and_update(_bar(2 * _NS_PER_HOUR))
    assert strategy._ruined is True

    # Position jetzt geschlossen (kein offenes Positions-Objekt mehr im Cache).
    mock_cache.positions_open.return_value = []
    strategy._get_current_balance = MagicMock(return_value=100.0)
    exited_again = strategy._check_exits_and_update(_bar(3 * _NS_PER_HOUR))
    assert exited_again is True  # weiterhin blockiert, kein "Erholen"


def test_equity_never_reaches_zero_or_negative_due_to_stopout(strategy_env):
    """Der Kern-Anspruch aus #947: die Simulation darf equity nie unter die Stop-out-Schwelle
    OHNE Reaktion durchlaufen lassen -- ein Aufruf mit equity=0 (bzw. negativ) muss ebenfalls
    sofort ruinieren."""
    strategy, mock_cache, pos = strategy_env(stop_out_equity_frac=0.20)
    strategy._get_current_balance = MagicMock(return_value=1000.0)
    strategy._check_exits_and_update(_bar(1 * _NS_PER_HOUR))

    strategy._get_current_balance = MagicMock(return_value=0.0)
    exited = strategy._check_exits_and_update(_bar(2 * _NS_PER_HOUR))
    assert exited is True
    assert strategy._ruined is True


def test_stop_out_equity_frac_is_configurable(strategy_env):
    strategy, mock_cache, pos = strategy_env(stop_out_equity_frac=0.5)
    strategy._get_current_balance = MagicMock(return_value=1000.0)
    strategy._check_exits_and_update(_bar(1 * _NS_PER_HOUR))

    # 60% des Startkapitals -> UEBER der (hoeheren) 50%-Schwelle -> kein Ruin.
    strategy._get_current_balance = MagicMock(return_value=600.0)
    exited = strategy._check_exits_and_update(_bar(2 * _NS_PER_HOUR))
    assert strategy._ruined is False

    # 40% des Startkapitals -> UNTER der 50%-Schwelle -> Ruin.
    strategy._get_current_balance = MagicMock(return_value=400.0)
    exited = strategy._check_exits_and_update(_bar(3 * _NS_PER_HOUR))
    assert strategy._ruined is True
