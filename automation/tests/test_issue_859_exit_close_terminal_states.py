"""Issue #859 (P1, Katalog #856-#861, GitHub-Issue #758) — ein abgelehnter Markt-Close entwaffnet
den Watchdog dauerhaft; `on_order_denied` existiert nicht.

Root-Cause: `_exit_market_close_submitted` wurde beim ABSENDEN gesetzt und nur bei erfolgreicher
BESTÄTIGUNG geräumt (nirgends bei Ablehnung/Verweigerung) — der einzige Wiederholungsmechanismus
(`_exit_close_watchdog`) war damit dauerhaft entwaffnet, sobald ein Markt-Close abgelehnt oder vom
RiskEngine verweigert wurde. Die Markt-Close-Order fiel durch ALLE bestehenden `on_order_rejected`-
Zweige (steht weder in `_pending_cancels` noch ist sie die Dyn-TP-Order), und `on_order_denied` war
in der gesamten Klasse nicht implementiert.

Fix: `_exit_market_close_order_id` (ClientOrderId | None) ersetzt das Boolean-Flag — der Watchdog
gilt als entwaffnet, solange DIESE Order im Cache offen ist (`cache.order(id).is_open`), nicht ab
dem Absenden. `on_order_rejected`/`on_order_denied` räumen die ID bei einem Fehlschlag, sodass der
Watchdog im nächsten Bar erneut auslöst — bis zu `exit_close_max_retries` Versuchen, danach gilt
der Trial als terminal `EXIT_CLOSE_UNRECOVERABLE` (`backtest_runner._evaluate_oos_eligibility`
markiert ihn dann `oos_evaluated=False`, unabhängig von der Trade-Zahl).
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Price, Quantity

from automation.backtest_runner import _evaluate_oos_eligibility
from automation.strategies.hourly_strategy_base import ExitReason
from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy

_BAR_TYPE = BarType.from_str("TSLA.ETORO-1-HOUR-MID-INTERNAL")
_NS_PER_HOUR = 3_600_000_000_000


def _bar(ts_ns: int, close: float = 100.0) -> Bar:
    price = Price(close, 4)
    return Bar(
        bar_type=_BAR_TYPE,
        open=price, high=price, low=price, close=price,
        volume=Quantity(1.0, 4),
        ts_event=ts_ns, ts_init=ts_ns,
    )


def _make_event(client_order_id, reason="test"):
    event = MagicMock()
    event.client_order_id = client_order_id
    event.reason = reason
    return event


@pytest.fixture
def strategy_env():
    def _build(max_bars_in_trade: int = 3, exit_close_max_bars: int = 2,
               exit_close_max_retries: int = 3):
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO",
            bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_bars_in_trade=max_bars_in_trade,
            exit_close_max_bars=exit_close_max_bars,
            exit_close_max_retries=exit_close_max_retries,
            atr_trailing_multiplier=1.5,
        )
        strategy = Rsi2ReversionStrategy(config=config)
        strategy.submit_order = MagicMock()
        strategy.cancel_order = MagicMock()
        strategy.cancel_all_orders = MagicMock()

        # Jeder order_factory.market()-Aufruf liefert eine NEUE Order mit fortlaufender ID, damit
        # aufeinanderfolgende Markt-Close-Versuche unterscheidbar sind (wie im echten Order-Factory-
        # Verhalten -- jede Order hat eine eigene client_order_id).
        _order_counter = {"n": 0}

        def _new_market_order(**kwargs):
            _order_counter["n"] += 1
            order = MagicMock()
            order.client_order_id = f"O-MARKET-{_order_counter['n']}"
            return order

        mock_order_factory = MagicMock()
        mock_order_factory.market.side_effect = _new_market_order

        mock_cache = MagicMock()
        pos = MagicMock()
        pos.side = PositionSide.LONG
        pos.quantity = 10
        mock_cache.positions_open.return_value = [pos]
        mock_cache.orders_open.return_value = []  # sofortiger Close-Versuch

        # cache.order(id) liefert standardmaessig ein offenes Order-Objekt (truthy .is_open) --
        # Tests ueberschreiben das gezielt fuer "diese Order ist nicht mehr offen"-Szenarien.
        mock_cache.order.return_value = MagicMock(is_open=True)

        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        stack.enter_context(patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory))

        strategy._exit_atr = MagicMock()
        strategy._exit_atr.initialized = True
        strategy._exit_atr.value = 0.001

        strategy._in_position = True
        strategy._trailing_initialised = True
        strategy._trailing_stop_side = "LONG"
        strategy._trailing_stop_price = 1.0
        strategy._bars_in_position = max_bars_in_trade - 1

        return strategy, mock_cache, pos

    with ExitStack() as stack:
        yield _build


def _trigger_time_exit(strategy):
    result = strategy._check_exits_and_update(_bar(ts_ns=1_000 * _NS_PER_HOUR, close=100.0))
    assert result is True
    assert strategy._exit_pending_kind == ExitReason.TIME_BOX
    strategy.submit_order.assert_called_once()  # sofortiger Markt-Close (keine ruhende Order)
    return strategy.submit_order.call_args[0][0].client_order_id


# ── AK-1/AK-2: ein abgelehnter/verweigerter Markt-Close wird wiederholt ─────────────────────────
def test_rejected_market_close_is_retried_and_succeeds(strategy_env):
    strategy, mock_cache, pos = strategy_env(exit_close_max_bars=1)
    first_order_id = _trigger_time_exit(strategy)
    assert strategy._exit_market_close_order_id == first_order_id

    with patch("automation.strategies.hourly_strategy_base.emit_execution_event") as mock_emit:
        strategy.on_order_rejected(_make_event(first_order_id))

    assert strategy._exit_market_close_order_id is None  # geräumt, Watchdog kann erneut ausloesen
    assert strategy._exit_close_retries == 1
    assert strategy._exit_close_unrecoverable is False
    assert any(call.args[1] == "EXIT_CLOSE_REJECTED" for call in mock_emit.call_args_list)

    # Naechster Bar: der Watchdog (exit_pending ist weiterhin gesetzt) loest einen NEUEN Markt-
    # Close-Versuch aus, statt die Position bis zum Datenende offen zu halten.
    strategy._check_exits_and_update(_bar(ts_ns=1_001 * _NS_PER_HOUR, close=100.0))
    assert strategy.submit_order.call_count == 2
    second_order_id = strategy.submit_order.call_args[0][0].client_order_id
    assert second_order_id != first_order_id
    assert strategy._exit_market_close_order_id == second_order_id


def test_denied_market_close_is_retried_and_succeeds(strategy_env):
    """AK-2 mit OrderDenied statt OrderRejected -- identisches Verhalten (#859 Fix Punkt 3)."""
    strategy, mock_cache, pos = strategy_env(exit_close_max_bars=1)
    first_order_id = _trigger_time_exit(strategy)

    with patch("automation.strategies.hourly_strategy_base.emit_execution_event") as mock_emit:
        strategy.on_order_denied(_make_event(first_order_id))

    assert strategy._exit_market_close_order_id is None
    assert strategy._exit_close_retries == 1
    assert any(call.args[1] == "EXIT_CLOSE_DENIED" for call in mock_emit.call_args_list)

    strategy._check_exits_and_update(_bar(ts_ns=1_001 * _NS_PER_HOUR, close=100.0))
    assert strategy.submit_order.call_count == 2


def test_on_order_denied_ignores_unrelated_orders(strategy_env):
    """Ein OrderDenied fuer eine FREMDE Order (z. B. eine Entry-Order) darf den Exit-Zustand nicht
    beruehren."""
    strategy, mock_cache, pos = strategy_env()
    first_order_id = _trigger_time_exit(strategy)

    strategy.on_order_denied(_make_event("O-SOME-OTHER-ORDER"))

    assert strategy._exit_market_close_order_id == first_order_id
    assert strategy._exit_close_retries == 0


# ── AK-3: nach exit_close_max_retries traegt der Trial oos_evaluated=False ──────────────────────
def test_after_max_retries_trial_is_marked_unrecoverable(strategy_env):
    strategy, mock_cache, pos = strategy_env(exit_close_max_retries=3)
    order_id = _trigger_time_exit(strategy)

    with patch("automation.strategies.hourly_strategy_base.emit_execution_event") as mock_emit:
        for attempt in range(1, 4):
            strategy.on_order_rejected(_make_event(order_id))
            if attempt < 3:
                order_id = strategy._exit_market_close_order_id or f"retry-{attempt}"
                # Naechster Versuch wird erst beim naechsten _check_exits_and_update abgesetzt;
                # hier direkt die naechste Order-ID simulieren, da wir nur den Retry-Zaehler testen.
                strategy._exit_market_close_order_id = order_id

    assert strategy._exit_close_retries == 3
    assert strategy._exit_close_unrecoverable is True
    assert any(call.args[1] == "EXIT_CLOSE_UNRECOVERABLE" for call in mock_emit.call_args_list)


def test_watchdog_stops_retrying_once_unrecoverable(strategy_env):
    strategy, mock_cache, pos = strategy_env(exit_close_max_bars=1, exit_close_max_retries=1)
    _trigger_time_exit(strategy)
    strategy._exit_close_unrecoverable = True
    strategy.submit_order.reset_mock()

    # Watchdog-Bar: darf NICHT erneut versuchen, sobald der Trial als unrecoverable gilt.
    strategy._check_exits_and_update(_bar(ts_ns=1_001 * _NS_PER_HOUR, close=100.0))
    strategy.submit_order.assert_not_called()


# ── AK-4: on_position_closed raeumt verbliebene ruhende Orders ──────────────────────────────────
def test_position_closed_cancels_remaining_resting_orders(strategy_env):
    strategy, mock_cache, pos = strategy_env()
    event = MagicMock()
    event.avg_px_open = 100.0
    event.side = PositionSide.LONG

    strategy.on_position_closed(event)

    strategy.cancel_all_orders.assert_called_once_with(strategy.instrument_id)


def test_position_closed_resets_exit_close_retry_state_but_not_unrecoverable(strategy_env):
    """_exit_close_unrecoverable ist ein DAUERHAFTES Urteil ueber den gesamten Trial (der Backtest-
    Worker liest es erst NACH engine.run()) -- es darf nicht durch eine zwischenzeitliche
    PositionClosed-Bestaetigung verschwinden, sonst verliert #859 Fix Punkt 4 seine Grundlage."""
    strategy, mock_cache, pos = strategy_env()
    strategy._exit_market_close_order_id = "O-STALE"
    strategy._exit_close_retries = 2
    strategy._exit_close_unrecoverable = True

    event = MagicMock()
    event.avg_px_open = 100.0
    event.side = PositionSide.LONG
    strategy.on_position_closed(event)

    assert strategy._exit_market_close_order_id is None
    assert strategy._exit_close_retries == 0
    assert strategy._exit_close_unrecoverable is True


# ── backtest_runner._evaluate_oos_eligibility: EXIT_CLOSE_UNRECOVERABLE -> oos_evaluated=False ──
def test_evaluate_oos_eligibility_invalidates_trial_on_unrecoverable_diagnostic():
    oos_metrics = {
        "total_trades": 40, "win_rate": 0.6, "max_drawdown": 0.05, "total_return": 0.05,
        "sortino_ratio": 1.2, "profit_factor": 1.5,
        "inference_diagnostics": [{"code": "EXIT_CLOSE_UNRECOVERABLE", "detail": "x", "value": 3}],
    }
    result = _evaluate_oos_eligibility(oos_metrics, tournament_cfg={}, strat_params={})
    assert result["oos_evaluated"] is False
    assert result["oos_eligible"] is False


def test_evaluate_oos_eligibility_unaffected_by_unrelated_diagnostics():
    oos_metrics = {
        "total_trades": 40, "win_rate": 0.6, "max_drawdown": 0.05, "total_return": 0.05,
        "sortino_ratio": 1.2, "profit_factor": 1.5,
        "inference_diagnostics": [{"code": "SORTINO_GUARD_TRIPPED", "detail": "x", "value": 1}],
    }
    result = _evaluate_oos_eligibility(oos_metrics, tournament_cfg={}, strat_params={})
    assert result["oos_evaluated"] is True
