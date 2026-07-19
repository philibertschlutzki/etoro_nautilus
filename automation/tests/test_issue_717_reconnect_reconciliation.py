"""Issue #717 (P0) — GR-04: Reconnect-Reconciliation + Offline-Ablauf-Schliessung.

Strategie-seitige Rehydrierung: nach einem Reconnect/Neustart ist der In-Memory-
_bars_in_position-Zähler verloren (#714-Root-Cause). `on_start()` rekonstruiert ihn aus dem
Bar-Cache relativ zu Nautilus' nativem `pos.ts_opened` und liquidiert sofort, falls die 24-Bar-
Zeitbox bereits (offline) abgelaufen ist; ohne Bar-Historie greift der Wall-Clock-Fallback (>=24h).

Akzeptanzkriterien:
- Nach simuliertem Reconnect wird eine Position mit bars_seit_entry >= max_bars_in_trade SOFORT
  geschlossen; ist die Bar-Historie leer, greift der Wall-Clock-Fallback bei >= 24h.
- Überlebende Positionen haben nach Reconnect einen korrekt rehydrierten _bars_in_position-Zähler.
- Alte State-Dateien ohne entry_ns/entry_bar_seq laden fehlerfrei (siehe
  test_issue_717_state_manager_migration.py).
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Price, Quantity

from automation.strategies.hourly_strategy_base import HourlyStrategyBase
from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy

_NS_PER_HOUR = 3_600_000_000_000
_BAR_TYPE = BarType.from_str("TSLA.ETORO-1-HOUR-MID-INTERNAL")


class _FakeBar:
    def __init__(self, ts_event: int):
        self.ts_event = ts_event


def _real_bar(ts_ns: int, close: float = 100.0) -> Bar:
    price = Price(close, 4)
    return Bar(
        bar_type=_BAR_TYPE,
        open=price, high=price, low=price, close=price,
        volume=Quantity(1.0, 4),
        ts_event=ts_ns, ts_init=ts_ns,
    )


@pytest.fixture
def strategy_env():
    def _build(max_bars_in_trade: int = 24):
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO",
            bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_bars_in_trade=max_bars_in_trade,
        )
        strategy = Rsi2ReversionStrategy(config=config)
        strategy.submit_order = MagicMock()
        strategy.cancel_order = MagicMock()
        # HourlyStrategyBase.on_start() ist unter Test — die Rsi2Reversion-eigene on_start()
        # ruft zusätzlich subscribe_bars() auf, was einen bei einem Trader REGISTRIERTEN Actor
        # voraussetzt (hier bewusst NICHT vorhanden, siehe Modul-Docstring der anderen
        # #712/#715/#716-Tests, die exakt denselben unregistrierten-Actor-Ansatz nutzen).
        strategy.on_start = lambda: HourlyStrategyBase.on_start(strategy)
        mock_order_factory = MagicMock()
        mock_msgbus = MagicMock()
        mock_clock = MagicMock()
        mock_clock.timestamp_ns.return_value = 100 * _NS_PER_HOUR

        mock_cache = MagicMock()
        mock_cache.orders_open.return_value = []

        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        stack.enter_context(patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory))
        stack.enter_context(patch.object(type(strategy), "msgbus", new_callable=PropertyMock, return_value=mock_msgbus))
        stack.enter_context(patch.object(type(strategy), "clock", new_callable=PropertyMock, return_value=mock_clock))
        return strategy, mock_cache

    with ExitStack() as stack:
        yield _build


def _make_position(side=PositionSide.LONG, ts_opened=0, quantity=10):
    pos = MagicMock()
    pos.side = side
    pos.ts_opened = ts_opened
    pos.quantity = quantity
    return pos


def test_expired_position_liquidated_immediately_via_bar_cache(strategy_env):
    """bars_seit_entry (aus dem Bar-Cache rekonstruiert) >= max_bars_in_trade ⇒ SOFORTIGE
    Liquidierung bei on_start()."""
    strategy, mock_cache = strategy_env(max_bars_in_trade=24)
    entry_ns = 0
    pos = _make_position(ts_opened=entry_ns)
    mock_cache.positions_open.return_value = [pos]
    # 30 Bars nach Entry in der Cache-Historie -> abgelaufen (>= 24).
    mock_cache.bars.return_value = [_FakeBar(ts_event=(i + 1) * _NS_PER_HOUR) for i in range(30)]

    strategy.on_start()

    strategy.submit_order.assert_called_once()  # Market-Close-Order abgesetzt


def test_survivor_position_rehydrates_bars_in_position(strategy_env):
    """Nicht abgelaufene Position: kein Close, aber _bars_in_position_rehydrated korrekt gesetzt,
    und wird vom nächsten _check_exits_and_update-Aufruf übernommen (nicht auf 0 zurückgesetzt)."""
    strategy, mock_cache = strategy_env(max_bars_in_trade=24)
    entry_ns = 0
    pos = _make_position(ts_opened=entry_ns)
    mock_cache.positions_open.return_value = [pos]
    # 10 Bars seit Entry -> deutlich unter der 24-Bar-Deadline.
    mock_cache.bars.return_value = [_FakeBar(ts_event=(i + 1) * _NS_PER_HOUR) for i in range(10)]

    strategy.on_start()

    strategy.submit_order.assert_not_called()
    assert strategy._bars_in_position_rehydrated == 10

    # Der nächste _check_exits_and_update-Aufruf übernimmt den rehydrierten Wert statt auf 0
    # zurückzusetzen (+1 für den gerade verarbeiteten Bar). ATR ist naturgemäss noch nicht
    # initialisiert (kein Bar verarbeitet) — irrelevant für diesen Test.
    strategy._check_exits_and_update(_real_bar(ts_ns=11 * _NS_PER_HOUR))
    assert strategy._bars_in_position == 11
    assert strategy._bars_in_position_rehydrated is None  # einmalig konsumiert


def test_no_position_on_start_is_a_no_op(strategy_env):
    strategy, mock_cache = strategy_env()
    mock_cache.positions_open.return_value = []

    strategy.on_start()  # darf nicht crashen

    strategy.submit_order.assert_not_called()
    assert strategy._bars_in_position_rehydrated is None


def test_empty_bar_cache_falls_back_to_wall_clock_expired(strategy_env):
    """Bar-Historie leer (Kaltstart) ⇒ Wall-Clock-Fallback: now - entry_ns >= 24h ⇒ Liquidierung."""
    strategy, mock_cache = strategy_env(max_bars_in_trade=24)
    now_ns = strategy.clock.timestamp_ns()
    entry_ns = now_ns - 25 * _NS_PER_HOUR  # 25h alt
    pos = _make_position(ts_opened=entry_ns)
    mock_cache.positions_open.return_value = [pos]
    mock_cache.bars.return_value = []  # Kaltstart: keine Bar-Historie

    strategy.on_start()

    strategy.submit_order.assert_called_once()


def test_empty_bar_cache_wall_clock_not_yet_expired(strategy_env):
    strategy, mock_cache = strategy_env(max_bars_in_trade=24)
    now_ns = strategy.clock.timestamp_ns()
    entry_ns = now_ns - 5 * _NS_PER_HOUR  # erst 5h alt
    pos = _make_position(ts_opened=entry_ns)
    mock_cache.positions_open.return_value = [pos]
    mock_cache.bars.return_value = []

    strategy.on_start()

    strategy.submit_order.assert_not_called()
    assert strategy._bars_in_position_rehydrated == 0


def test_bar_cache_exception_falls_back_to_wall_clock(strategy_env):
    """cache.bars() wirft (z. B. unbekannter BarType) ⇒ derselbe sichere Wall-Clock-Fallback."""
    strategy, mock_cache = strategy_env(max_bars_in_trade=24)
    now_ns = strategy.clock.timestamp_ns()
    entry_ns = now_ns - 25 * _NS_PER_HOUR
    pos = _make_position(ts_opened=entry_ns)
    mock_cache.positions_open.return_value = [pos]
    mock_cache.bars.side_effect = RuntimeError("boom")

    strategy.on_start()

    strategy.submit_order.assert_called_once()


# ── msgbus GR-04 Close-Request-Handler ────────────────────────────────────────────────────────
def test_subscribes_to_gr04_close_request_topic_once(strategy_env):
    strategy, mock_cache = strategy_env()
    mock_cache.positions_open.return_value = []
    mock_msgbus = strategy.msgbus

    strategy.on_start()
    strategy.on_start()  # zweiter Aufruf (z. B. erneuter Reconnect) darf NICHT doppelt abonnieren

    assert mock_msgbus.subscribe.call_count == 1
    call_kwargs = mock_msgbus.subscribe.call_args.kwargs
    assert call_kwargs["topic"] == f"events.gr04_close_request.{strategy.instrument_id}"


def test_gr04_close_request_handler_closes_open_position(strategy_env):
    strategy, mock_cache = strategy_env()
    pos = _make_position()
    mock_cache.positions_open.return_value = [pos]

    strategy._on_gr04_close_request({"reason": "phantom", "position_id": "POS-1"})

    strategy.submit_order.assert_called_once()


def test_gr04_close_request_handler_no_position_is_noop(strategy_env):
    strategy, mock_cache = strategy_env()
    mock_cache.positions_open.return_value = []

    strategy._on_gr04_close_request({"reason": "phantom", "position_id": "POS-1"})

    strategy.submit_order.assert_not_called()
