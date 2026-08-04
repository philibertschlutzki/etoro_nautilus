"""Issue #837 (P0) — Optimistischer Zustands-Reset setzt Bar-Zähler und Trailing-Stop zurück.

`_check_exits_and_update` durfte `_in_position`/`_bars_in_position`/`_trailing_stop_price` nicht
mehr zurücksetzen, sobald ein Exit ausgelöst wird — der Close ist asynchron (frühestens Fill am
nächsten Bar-Open), und ein optimistischer Reset lässt den Init-Block im nächsten Bar erneut
laufen: der Bar-Zähler startet neu, UND der Trailing-Stop wird am aktuellen (womöglich
schlechteren) Close neu verankert, was die Monotonie-Garantie aufhebt.

Zustand, der eine BESTÄTIGTE Transaktion beschreibt, wird jetzt ausschliesslich in
`on_position_closed`/`on_position_opened` gesetzt.

Akzeptanzkriterien:
- AK-1: Exit ausgelöst, Close verzögert sich um 3 Bars ⇒ _bars_in_position ist
  max_bars_in_trade + 3, nicht 3.
- AK-2: Trailing-Stop einer Long-Position bleibt über die gesamte Positionsdauer monoton
  nicht-fallend, auch über einen fehlgeschlagenen/verzögerten Exit-Versuch hinweg.
- AK-3: on_position_closed ist der einzige Ort, der den EXIT-TRIGGER-Block selbst NICHT mit
  optimistischen Zustands-Resets verunreinigt (Grep-Nachweis im Test).
"""
import inspect
from contextlib import ExitStack
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Price, Quantity

from automation.strategies.hourly_strategy_base import HourlyStrategyBase
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


@pytest.fixture
def strategy_env():
    def _build(max_bars_in_trade: int = 3, exit_close_max_bars: int = 100):
        # exit_close_max_bars gross gewaehlt, damit der #836-Watchdog in diesen Tests nicht
        # dazwischenfunkt -- hier geht es ausschliesslich um den Zustand waehrend der Wartezeit.
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
        mock_cache.orders_open.return_value = []  # sofortiger Close-Versuch, keine Order-Warteschleife

        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        stack.enter_context(patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory))

        strategy._exit_atr = MagicMock()
        strategy._exit_atr.initialized = True
        strategy._exit_atr.value = 2.0

        return strategy, mock_cache, pos

    with ExitStack() as stack:
        yield _build


def test_ak1_bar_counter_keeps_counting_through_delayed_close(strategy_env):
    max_bars = 3
    strategy, mock_cache, pos = strategy_env(max_bars_in_trade=max_bars)

    strategy._in_position = True
    strategy._trailing_initialised = True
    strategy._trailing_stop_side = "LONG"
    strategy._trailing_stop_price = 1.0  # greift nie
    strategy._bars_in_position = max_bars - 1

    # Trigger-Bar: bars_in_position erreicht max_bars -> Zeitbox-Exit.
    result = strategy._check_exits_and_update(_bar(ts_ns=1_000 * _NS_PER_HOUR))
    assert result is True
    assert strategy._bars_in_position == max_bars
    assert strategy._in_position is True  # NICHT zurückgesetzt (Close ist asynchron)

    # Der (simulierte) Fill des Market-Close-Orders trifft ERST 3 Bars später ein — die Cache-Mock
    # zeigt die Position bis dahin weiterhin als offen.
    for i in range(1, 4):
        result = strategy._check_exits_and_update(_bar(ts_ns=(1_000 + i) * _NS_PER_HOUR))
        assert result is True

    assert strategy._bars_in_position == max_bars + 3
    strategy.submit_order.assert_called_once()  # kein Order-Spam während der Wartezeit


def test_ak2_trailing_stop_monotonically_nondecreasing_through_stalled_exit(strategy_env):
    strategy, mock_cache, pos = strategy_env(max_bars_in_trade=100)  # Zeitbox irrelevant hier

    strategy._in_position = True
    strategy._trailing_initialised = True
    strategy._trailing_stop_side = "LONG"
    strategy._trailing_stop_price = 97.0  # verankert bei Entry (close=100, ATR=2, mult=1.5)
    strategy._bars_in_position = 5

    # Preis steigt -> Trailing-Stop MUSS mitziehen (nach oben).
    strategy._check_exits_and_update(_bar(ts_ns=2_000 * _NS_PER_HOUR, close=110.0))
    assert strategy._trailing_stop_price == pytest.approx(110.0 - 1.5 * 2.0)
    stop_after_rise = strategy._trailing_stop_price

    # Jetzt einen Exit manuell simulieren (z. B. Trailing-Stop-Hit) und NICHT bestätigen lassen —
    # die Position bleibt laut Cache offen (asynchroner Close, Fill steht noch aus).
    strategy._exit_pending = "manual-test-exit"
    from automation.strategies.hourly_strategy_base import ExitReason
    strategy._exit_pending_kind = ExitReason.TRAILING_STOP
    strategy._exit_pending_bars = 0
    strategy._exit_market_close_submitted = True  # Close bereits abgesetzt, wartet auf Fill

    # Ein harter Preis-Einbruch waehrend der Wartezeit darf den (hoeheren) Trailing-Stop NICHT
    # auf einen neu verankerten, niedrigeren Wert zuruecksetzen (das war der #837-Bug: der
    # Init-Block lief erneut, weil _in_position faelschlich auf False gesetzt worden war).
    strategy._check_exits_and_update(_bar(ts_ns=2_001 * _NS_PER_HOUR, close=50.0))

    assert strategy._trailing_stop_price == stop_after_rise  # unveraendert, NICHT neu verankert
    assert strategy._trailing_stop_price >= 97.0  # niemals unter den urspruenglichen Anker gefallen


def test_ak3_exit_trigger_block_contains_no_optimistic_state_reset():
    """Grep-Nachweis: der Exit-Trigger-Block in _check_exits_and_update (der Codepfad, der
    exit_reason auswertet und den Close einleitet) darf _in_position/_bars_in_position/
    _trailing_stop_price/_trailing_stop_side/_take_profit_price NICHT mehr zuruecksetzen — das
    ist nach dem Fix ausschliesslich Aufgabe von on_position_closed()/on_position_opened()."""
    source = inspect.getsource(HourlyStrategyBase._check_exits_and_update)
    trigger_block_start = source.index("if exit_reason:")
    trigger_block = source[trigger_block_start:]

    forbidden = [
        "self._in_position = False",
        "self._bars_in_position = 0",
        "self._trailing_stop_price = None",
        "self._trailing_stop_side = None",
        "self._take_profit_price = None",
    ]
    for snippet in forbidden:
        assert snippet not in trigger_block, (
            f"Optimistischer Zustands-Reset '{snippet}' im Exit-Trigger-Block gefunden (Issue #837)"
        )


def test_on_position_closed_is_the_confirmation_point_for_in_position_false():
    """on_position_opened() und on_position_closed() sind die einzigen Orte, die _in_position auf
    False setzen (ausserhalb des defensiven Cache-Resync-Zweigs fuer 'keine offene Position mehr
    im Cache', der unabhaengig vom Exit-Trigger-Pfad existiert)."""
    closed_source = inspect.getsource(HourlyStrategyBase.on_position_closed)
    assert "self._in_position = False" in closed_source
