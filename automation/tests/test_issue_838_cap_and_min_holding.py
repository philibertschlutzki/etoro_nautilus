"""Issue #838 (P1) — Zwei latente Duplikate derselben Fehlerklasse im selben Vertrag.

(a) `max_trades_cap` sperrte in `_check_exits_and_update` per Early-Return VOR jeder
Trailing-Stop-/Zeit-Exit-/ATR-Auswertung — ein Cap darf ausschliesslich Entries sperren, niemals
Exits. Der Check wandert in `_entry_allowed()`, konsumiert von `_compute_quantity` (derselbe
Ort, an dem bereits `max_daily_trades` greift).

(b) `min_holding_time > max_bars_in_trade` konnte den Zeit-Exit dauerhaft unterdrücken. Der
Konstruktor klemmt jetzt hart auf `max_bars_in_trade - 1`, UND die (jetzt Enum-basierte statt
stringbasierte) Exit-Klassifikation wendet `min_holding_time` gar nicht mehr auf TRAILING_STOP/
TIME_BOX an.

Akzeptanzkriterien:
- AK-1: max_trades_cap erreicht, Position offen ⇒ Zeit-Exit feuert trotzdem, kein neuer Entry.
- AK-2: min_holding_time=48, max_bars_in_trade=24 ⇒ Konstruktor klemmt auf 23, WARNING wird
  emittiert, Zeit-Exit feuert bei Bar 24.
- AK-3: HourlyStrategyBase._check_exits_and_update.__doc__ is not None.
"""
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


def test_ak3_docstring_survives_introspection():
    assert HourlyStrategyBase._check_exits_and_update.__doc__ is not None
    assert "Called at the START" in HourlyStrategyBase._check_exits_and_update.__doc__


def test_ak2_constructor_clamps_min_holding_time_below_max_bars_and_warns():
    mock_log = MagicMock()
    with patch.object(Rsi2ReversionStrategy, "_log", new_callable=PropertyMock, return_value=mock_log):
        config = Rsi2ReversionConfig(
            instrument_id="AAPL.ETORO",
            bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
            min_holding_time=48,
            max_bars_in_trade=24,
        )
        strategy = Rsi2ReversionStrategy(config=config)

    assert strategy._min_holding_time == 23
    mock_log.warning.assert_called_once()
    warning_msg = mock_log.warning.call_args[0][0]
    assert "min_holding_time" in warning_msg
    assert "48" in warning_msg
    assert "23" in warning_msg


def test_min_holding_time_below_max_bars_is_left_untouched():
    config = Rsi2ReversionConfig(
        instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        min_holding_time=5, max_bars_in_trade=24,
    )
    strategy = Rsi2ReversionStrategy(config=config)
    assert strategy._min_holding_time == 5


@pytest.fixture
def strategy_env():
    def _build(max_trades_cap=None, executed_trades=0, max_bars_in_trade=3):
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO",
            bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_trades_cap=max_trades_cap,
            max_bars_in_trade=max_bars_in_trade,
        )
        strategy = Rsi2ReversionStrategy(config=config)
        strategy.submit_order = MagicMock()
        strategy.cancel_order = MagicMock()
        strategy._executed_trades = executed_trades

        mock_order_factory = MagicMock()
        mock_market_order = MagicMock()
        mock_market_order.client_order_id = "O-MARKET"
        mock_order_factory.market.return_value = mock_market_order

        mock_cache = MagicMock()
        pos = MagicMock()
        pos.side = PositionSide.LONG
        pos.quantity = 10
        mock_cache.positions_open.return_value = [pos]
        mock_cache.orders_open.return_value = []
        mock_cache.quote_tick.return_value = None
        mock_instrument = MagicMock()
        mock_instrument.size_increment = 0.01
        mock_instrument.size_precision = 2
        mock_instrument.make_qty = lambda q: q
        mock_cache.instrument.return_value = mock_instrument

        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        stack.enter_context(patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory))

        strategy._exit_atr = MagicMock()
        strategy._exit_atr.initialized = True
        strategy._exit_atr.value = 0.001  # Trailing-Stop greift praktisch nie

        return strategy, mock_cache, pos

    with ExitStack() as stack:
        yield _build


def test_ak1_max_trades_cap_does_not_block_time_exit(strategy_env):
    """max_trades_cap erreicht + Position offen ⇒ der Zeit-Exit MUSS trotzdem feuern (früher: der
    Early-Return in _check_exits_and_update sperrte auch Exits, die Position wurde bis
    Datenreihenende gehalten)."""
    max_bars = 3
    strategy, mock_cache, pos = strategy_env(max_trades_cap=1, executed_trades=1, max_bars_in_trade=max_bars)
    strategy._in_position = True
    strategy._trailing_initialised = True
    strategy._trailing_stop_side = "LONG"
    strategy._trailing_stop_price = 0.01
    strategy._bars_in_position = max_bars - 1

    result = strategy._check_exits_and_update(_bar(ts_ns=1_000 * _NS_PER_HOUR))

    assert result is True
    strategy.submit_order.assert_called_once()  # Zeit-Exit feuert trotz erreichtem Cap


def test_ak1_max_trades_cap_blocks_new_entry(strategy_env):
    strategy, mock_cache, pos = strategy_env(max_trades_cap=1, executed_trades=1)
    mock_cache.positions_open.return_value = []  # kein offenes Signal-Konflikt

    qty = strategy._compute_quantity(_bar(ts_ns=1_000 * _NS_PER_HOUR))

    assert qty is None


def test_entry_allowed_true_when_cap_not_set():
    config = Rsi2ReversionConfig(instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL")
    strategy = Rsi2ReversionStrategy(config=config)
    assert strategy._entry_allowed() is True


def test_entry_allowed_false_when_cap_reached():
    config = Rsi2ReversionConfig(
        instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL", max_trades_cap=2,
    )
    strategy = Rsi2ReversionStrategy(config=config)
    strategy._executed_trades = 2
    assert strategy._entry_allowed() is False
