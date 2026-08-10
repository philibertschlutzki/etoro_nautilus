"""Issue #836 (P0) — Der Exit-Pfad vernichtet seinen eigenen Fortsetzungs-Token.

`_close_position_base` cancelt zunächst jede ruhende Order und wartet auf die
`OrderCanceled`/`OrderFilled`/`OrderRejected`-Bestätigung, bevor `_execute_market_close()` läuft.
Der Aufrufer in `_check_exits_and_update` durfte `_pending_cancels` (das Fortsetzungs-Token dieser
Warteschleife) NICHT mehr im selben Bar synchron leeren — sonst greift keine der drei gateten
Rückwege je wieder, und die Position bleibt für immer offen.

Akzeptanzkriterien (aus dem Issue):
- AK-1: Fake-Cache mit genau einer offenen Limit-Order; Zeitbox-Exit auslösen; OrderCanceled
  einspeisen ⇒ genau eine Market-Order wurde abgesetzt.
- AK-2: Derselbe Test für on_order_filled und on_order_rejected.
- AK-3 (Watchdog): Cancel-Bestätigung wird nie eingespeist ⇒ nach exit_close_max_bars Bars genau
  ein EXIT_CLOSE_STALLED-Event und ein erzwungener Markt-Close.
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Price, Quantity

from automation.strategies.hourly_strategy_base import ExitReason
from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy

_BAR_TYPE = BarType.from_str("TSLA.ETORO-1-HOUR-MID-INTERNAL")
_NS_PER_HOUR = 3_600_000_000_000


def _bar(ts_ns: int, close: float = 100.0):
    price = Price(close, 4)
    from nautilus_trader.model.data import Bar
    return Bar(
        bar_type=_BAR_TYPE,
        open=price, high=price, low=price, close=price,
        volume=Quantity(1.0, 4),
        ts_event=ts_ns, ts_init=ts_ns,
    )


@pytest.fixture
def strategy_env():
    def _build(max_bars_in_trade: int = 3, exit_close_max_bars: int = 2):
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO",
            bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_bars_in_trade=max_bars_in_trade,
            exit_close_max_bars=exit_close_max_bars,
            atr_trailing_multiplier=1.5,
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
        mock_cache.positions_open.return_value = [pos]

        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        stack.enter_context(patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory))

        # Kein Trailing-Stop-Rauschen: ATR "initialisiert" mit einem winzigen Wert, sodass der
        # Trailing-Stop nie vor dem gezielt getriggerten Zeitbox-Exit greift.
        strategy._exit_atr = MagicMock()
        strategy._exit_atr.initialized = True
        strategy._exit_atr.value = 0.001

        # Position bereits "eingerichtet" (Init-Block schon durchlaufen), kurz vor der Zeitbox.
        strategy._in_position = True
        strategy._trailing_initialised = True
        strategy._trailing_stop_side = "LONG"
        strategy._trailing_stop_price = 1.0  # weit unter jedem Testpreis -> greift nie
        strategy._bars_in_position = max_bars_in_trade - 1

        return strategy, mock_cache, pos

    with ExitStack() as stack:
        yield _build


def _resting_limit_order(client_order_id="O-LIMIT-1"):
    order = MagicMock()
    order.client_order_id = client_order_id
    return order


def _make_event(client_order_id):
    event = MagicMock()
    event.client_order_id = client_order_id
    return event


def _trigger_time_exit(strategy, mock_cache, resting_order):
    """Treibt genau einen Bar, der den Zeitbox-Exit auslöst; die Position trägt eine ruhende
    Limit-Order (z. B. der statische Take-Profit), sodass der Close asynchron wird."""
    mock_cache.orders_open.return_value = [resting_order]
    result = strategy._check_exits_and_update(_bar(ts_ns=1_000 * _NS_PER_HOUR, close=100.0))
    assert result is True
    assert strategy._exit_pending is not None
    assert strategy._exit_pending_kind == ExitReason.TIME_BOX
    strategy.cancel_order.assert_called_once()
    strategy.submit_order.assert_not_called()  # Market-Close noch NICHT abgesetzt (wartet auf Callback)


def test_ak1_order_canceled_continues_to_market_close(strategy_env):
    strategy, mock_cache, pos = strategy_env()
    resting_order = _resting_limit_order()
    _trigger_time_exit(strategy, mock_cache, resting_order)

    # Ohne den #836-Fix würde _pending_cancels bereits hier leer sein (im selben Bar synchron
    # geleert) — die folgende Bestätigung fände dann keinen Token mehr vor und
    # _execute_market_close() würde NIE erreicht.
    assert resting_order.client_order_id in strategy._pending_cancels

    strategy.on_order_canceled(_make_event(resting_order.client_order_id))

    strategy.submit_order.assert_called_once()  # genau EINE Market-Order


def test_ak2_order_filled_continues_to_market_close(strategy_env):
    """Die ruhende Limit-Order füllt statt zu canceln (z. B. Take-Profit greift während des
    Cancel-Versuchs); ein Partial-Fill-Rest bleibt offen -> der Close muss trotzdem fortgesetzt
    werden."""
    strategy, mock_cache, pos = strategy_env()
    resting_order = _resting_limit_order()
    _trigger_time_exit(strategy, mock_cache, resting_order)

    pos.quantity = 4  # Rest bleibt offen nach dem (Teil-)Fill der ruhenden Order
    strategy.on_order_filled(_make_event(resting_order.client_order_id))

    strategy.submit_order.assert_called_once()


def test_ak2_order_rejected_continues_to_market_close(strategy_env):
    strategy, mock_cache, pos = strategy_env()
    resting_order = _resting_limit_order()
    _trigger_time_exit(strategy, mock_cache, resting_order)

    strategy.on_order_rejected(_make_event(resting_order.client_order_id))

    strategy.submit_order.assert_called_once()


def test_ak3_watchdog_forces_close_after_stalled_cancel(strategy_env):
    """Cancel-Bestätigung wird nie eingespeist ⇒ nach exit_close_max_bars weiteren Bars erzwingt
    der Watchdog den Markt-Close und emittiert genau ein EXIT_CLOSE_STALLED-Event."""
    strategy, mock_cache, pos = strategy_env(exit_close_max_bars=2)
    resting_order = _resting_limit_order()
    _trigger_time_exit(strategy, mock_cache, resting_order)

    with patch("automation.strategies.hourly_strategy_base.emit_execution_event") as mock_emit:
        # Bar 1 nach dem Trigger: exit_pending_bars 0 -> 1, noch unter der Schwelle.
        result_1 = strategy._check_exits_and_update(_bar(ts_ns=1_001 * _NS_PER_HOUR, close=100.0))
        assert result_1 is True
        strategy.submit_order.assert_not_called()
        mock_emit.assert_not_called()

        # Bar 2 nach dem Trigger: exit_pending_bars 1 -> 2, erreicht exit_close_max_bars=2.
        result_2 = strategy._check_exits_and_update(_bar(ts_ns=1_002 * _NS_PER_HOUR, close=100.0))
        assert result_2 is True

    strategy.submit_order.assert_called_once()  # genau EIN erzwungener Markt-Close
    mock_emit.assert_called_once()
    args, kwargs = mock_emit.call_args
    assert args[1] == "EXIT_CLOSE_STALLED"
    payload = args[2]
    assert payload["exit_reason"] == strategy._exit_pending
    assert payload["bars_waited"] == 2

    # Ein weiterer Bar darf KEINEN zweiten erzwungenen Close mehr auslösen.
    strategy._check_exits_and_update(_bar(ts_ns=1_003 * _NS_PER_HOUR, close=100.0))
    strategy.submit_order.assert_called_once()


def test_no_open_orders_closes_immediately_without_watchdog(strategy_env):
    """Regressionswächter: existiert keine ruhende Order, bleibt der bereits funktionierende
    Sofort-Close-Pfad (kein Watchdog nötig) unverändert."""
    strategy, mock_cache, pos = strategy_env()
    mock_cache.orders_open.return_value = []

    result = strategy._check_exits_and_update(_bar(ts_ns=1_000 * _NS_PER_HOUR, close=100.0))

    assert result is True
    strategy.submit_order.assert_called_once()
    strategy.cancel_order.assert_not_called()
