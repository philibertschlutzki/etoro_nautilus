"""Issue #897 (P0) — Der Trailing-Stop rastet auf der Volatilitätsschätzung statt auf dem
Preis-Extremum; der effektive Stop-Abstand ist ~6–10× kleiner als der konfigurierte.

Root-Cause: ``self._trailing_stop_price = max(self._trailing_stop_price, close - k*atr)`` rastet
nicht nur auf dem Kurs, sondern auch auf der ATR-Schätzung — sinkt ATR für eine einzige Bar, zieht
der Stop dauerhaft an den Kurs heran, ohne dass sich der Kurs günstig bewegt hätte. Der effektive
Stop-Abstand ist damit `k · min(ATR)` über die Haltedauer statt `k · ATR_t`.

Fix: neuer Anker ``trailing_stop_anchor`` ∈ {``price_extreme`` (Default, Chandelier-Formulierung,
rastet ausschliesslich auf dem seit Einstieg erreichten Kurs-Extremum), ``close_ratchet`` (Alt-
Verhalten, bit-identisch erhalten für den A/B-Kalibrierlauf)}. Zusätzlich ein ``atr_floor_bps``, der
verhindert, dass eine ATR von exakt 0 den Stop auf den Schlusskurs setzt.
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Price, Quantity

from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy

_BAR_TYPE = BarType.from_str("TSLA.ETORO-1-HOUR-MID-INTERNAL")
_NS_PER_HOUR = 3_600_000_000_000


def _bar(ts_ns: int, close: float, high: float | None = None, low: float | None = None) -> Bar:
    high = close if high is None else high
    low = close if low is None else low
    return Bar(
        bar_type=_BAR_TYPE,
        open=Price(close, 4), high=Price(high, 4), low=Price(low, 4), close=Price(close, 4),
        volume=Quantity(1.0, 4),
        ts_event=ts_ns, ts_init=ts_ns,
    )


@pytest.fixture
def strategy_env():
    def _build(anchor: str = "price_extreme", atr_trailing_multiplier: float = 1.5,
               atr_floor_bps: float = 2.0):
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO",
            bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_bars_in_trade=100,
            exit_close_max_bars=100,
            atr_trailing_multiplier=atr_trailing_multiplier,
            trailing_stop_anchor=anchor,
            atr_floor_bps=atr_floor_bps,
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
        mock_cache.orders_open.return_value = []

        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        stack.enter_context(patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory))

        strategy._exit_atr = MagicMock()
        strategy._exit_atr.initialized = True

        # Simulate on_position_opened's entry-price seed without going through the full event.
        entry_event = MagicMock()
        entry_event.avg_px_open = 100.0
        entry_event.side = PositionSide.LONG
        strategy.on_position_opened(entry_event)

        return strategy

    with ExitStack() as stack:
        yield _build


def _feed_atr_series(strategy, atr_bps_series: list[float], price: float = 100.0):
    """Feeds one bar per ATR value (bps of `price`), price held constant (high=low=close)."""
    for i, atr_bps in enumerate(atr_bps_series):
        strategy._exit_atr.value = (atr_bps / 10_000.0) * price
        strategy._check_exits_and_update(_bar(ts_ns=(1_000 + i) * _NS_PER_HOUR, close=price))


def test_close_ratchet_locks_onto_atr_minimum_and_never_recovers():
    """Reproduces the #897 bug under the legacy `close_ratchet` anchor: a single quiet bar
    (ATR=5bps) permanently tightens the stop even though ATR later returns to 40bps."""
    with ExitStack() as stack:
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_bars_in_trade=100, exit_close_max_bars=100, atr_trailing_multiplier=1.0,
            trailing_stop_anchor="close_ratchet",
        )
        strategy = Rsi2ReversionStrategy(config=config)
        strategy.submit_order = MagicMock()
        mock_cache = MagicMock()
        pos = MagicMock()
        pos.side = PositionSide.LONG
        mock_cache.positions_open.return_value = [pos]
        mock_cache.orders_open.return_value = []
        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        stack.enter_context(patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=MagicMock()))
        strategy._exit_atr = MagicMock()
        strategy._exit_atr.initialized = True
        entry_event = MagicMock(avg_px_open=100.0, side=PositionSide.LONG)
        strategy.on_position_opened(entry_event)

        _feed_atr_series(strategy, [40.0, 40.0, 5.0, 40.0, 40.0])

    # Bug: stuck at close - 1.0 * 5bps, even though ATR recovered to 40bps.
    expected_stuck = 100.0 - 1.0 * (5.0 / 10_000.0) * 100.0
    assert strategy._trailing_stop_price == pytest.approx(expected_stuck)


def test_price_extreme_anchor_recovers_when_atr_normalises_again(strategy_env):
    """Fix: under `price_extreme`, the stop is recomputed from the current ATR every bar (no
    ratchet against a stale, ATR-driven stop value) — a single quiet bar does not permanently
    tighten it."""
    strategy = strategy_env(anchor="price_extreme", atr_trailing_multiplier=1.0)
    _feed_atr_series(strategy, [40.0, 40.0, 5.0, 40.0, 40.0])

    expected_recovered = 100.0 - 1.0 * (40.0 / 10_000.0) * 100.0
    assert strategy._trailing_stop_price == pytest.approx(expected_recovered)


def test_price_extreme_anchor_ratchets_on_price_not_volatility(strategy_env):
    """The stop must still tighten as price makes new highs (the mechanism it is supposed to
    protect), independent of the ATR-anchor fix."""
    strategy = strategy_env(anchor="price_extreme", atr_trailing_multiplier=1.0)
    strategy._exit_atr.value = 2.0  # 2.0 abs units
    strategy._check_exits_and_update(_bar(ts_ns=1_000 * _NS_PER_HOUR, close=100.0, high=100.0, low=100.0))
    stop_1 = strategy._trailing_stop_price
    assert stop_1 == pytest.approx(98.0)

    strategy._check_exits_and_update(_bar(ts_ns=1_001 * _NS_PER_HOUR, close=110.0, high=112.0, low=109.0))
    stop_2 = strategy._trailing_stop_price
    assert stop_2 == pytest.approx(112.0 - 2.0)
    assert stop_2 > stop_1


def test_atr_floor_prevents_zero_distance_stop(strategy_env):
    """Issue #897 Fix 4 — an ATR of exactly 0 (degenerate bar) must not collapse the stop onto the
    close price; the floor keeps a positive distance so the position is not closed on the next
    tick."""
    strategy = strategy_env(anchor="price_extreme", atr_trailing_multiplier=1.0, atr_floor_bps=2.0)
    strategy._exit_atr.value = 0.0
    for i in range(3):
        result = strategy._check_exits_and_update(
            _bar(ts_ns=(1_000 + i) * _NS_PER_HOUR, close=100.0, high=100.0, low=100.0)
        )
        assert result is False  # position not closed by a zero-distance stop

    floor_distance = (2.0 / 10_000.0) * 100.0
    assert strategy._trailing_stop_price == pytest.approx(100.0 - floor_distance)
    assert strategy._trailing_stop_price < 100.0


def test_effective_atr_value_floors_zero(strategy_env):
    strategy = strategy_env(atr_floor_bps=2.0)
    assert strategy._effective_atr_value(0.0, 100.0) == pytest.approx(0.02)
    assert strategy._effective_atr_value(5.0, 100.0) == pytest.approx(5.0)


def test_short_side_price_extreme_uses_low_and_recovers():
    with ExitStack() as stack:
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_bars_in_trade=100, exit_close_max_bars=100, atr_trailing_multiplier=1.0,
            trailing_stop_anchor="price_extreme",
        )
        strategy = Rsi2ReversionStrategy(config=config)
        strategy.submit_order = MagicMock()
        mock_cache = MagicMock()
        pos = MagicMock()
        pos.side = PositionSide.SHORT
        mock_cache.positions_open.return_value = [pos]
        mock_cache.orders_open.return_value = []
        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        stack.enter_context(patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=MagicMock()))
        strategy._exit_atr = MagicMock()
        strategy._exit_atr.initialized = True
        entry_event = MagicMock(avg_px_open=100.0, side=PositionSide.SHORT)
        strategy.on_position_opened(entry_event)

        strategy._exit_atr.value = 2.0
        strategy._check_exits_and_update(_bar(ts_ns=1_000 * _NS_PER_HOUR, close=90.0, high=91.0, low=88.0))
        assert strategy._trailing_stop_price == pytest.approx(88.0 + 2.0)

        strategy._exit_atr.value = 0.25
        strategy._check_exits_and_update(_bar(ts_ns=1_001 * _NS_PER_HOUR, close=90.0, high=91.0, low=90.0))
        tight_stop = strategy._trailing_stop_price
        assert tight_stop == pytest.approx(88.0 + 0.25)

        strategy._exit_atr.value = 2.0
        strategy._check_exits_and_update(_bar(ts_ns=1_002 * _NS_PER_HOUR, close=90.0, high=91.0, low=90.0))
        assert strategy._trailing_stop_price == pytest.approx(88.0 + 2.0)  # recovered, not stuck
