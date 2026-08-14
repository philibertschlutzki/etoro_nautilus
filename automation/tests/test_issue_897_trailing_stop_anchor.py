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
               atr_floor_bps: float = 2.0, anchor_resolution: str = "close",
               trailing_min_atr_frac: float = 0.5):
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO",
            bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_bars_in_trade=100,
            exit_close_max_bars=100,
            atr_trailing_multiplier=atr_trailing_multiplier,
            trailing_stop_anchor=anchor,
            atr_floor_bps=atr_floor_bps,
            trailing_stop_anchor_resolution=anchor_resolution,
            trailing_min_atr_frac=trailing_min_atr_frac,
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


def test_price_extreme_anchor_ratchets_and_does_not_recover_when_atr_normalises_again(strategy_env):
    """Issue #1094 (Katalog #927) — REVIDIERT das #897-Verhalten: "price_extreme" ist jetzt
    MONOTON (ein Stop, der nachgeben kann, ist keine Verlustobergrenze). Eine einzelne ruhige Bar
    (ATR=5bps) darf den Stop trotzdem nicht auf den blossen Schlusskurs klemmen (Pitfall #285) —
    das wird stattdessen ueber die trailing_min_atr_frac-Untergrenze (nicht ueber den Verzicht auf
    Monotonie) verhindert: der Stop zieht sich auf der ruhigen Bar nach, bleibt aber danach stehen,
    selbst wenn die ATR wieder auf 40bps steigt."""
    strategy = strategy_env(anchor="price_extreme", atr_trailing_multiplier=1.0)
    _feed_atr_series(strategy, [40.0, 40.0], price=100.0)
    stop_before_quiet_bar = strategy._trailing_stop_price
    assert stop_before_quiet_bar == pytest.approx(99.6)

    _feed_atr_series(strategy, [5.0], price=100.0)
    stop_after_quiet_bar = strategy._trailing_stop_price
    # Nicht auf den Schlusskurs geklemmt (die #897-Pitfall-#285-Gefahr) UND nicht schlechter als
    # zuvor (Monotonie).
    assert stop_after_quiet_bar < 100.0
    assert stop_after_quiet_bar >= stop_before_quiet_bar

    _feed_atr_series(strategy, [40.0, 40.0], price=100.0)
    stop_after_atr_recovers = strategy._trailing_stop_price
    # Der Stop gibt NICHT nach, obwohl die ATR wieder auf 40bps steigt (Kernbehauptung #1094).
    assert stop_after_atr_recovers == pytest.approx(stop_after_quiet_bar)


def test_price_extreme_anchor_ratchets_on_price_not_volatility(strategy_env):
    """The stop must still tighten as price makes new highs (the mechanism it is supposed to
    protect), independent of the ATR-anchor fix. Issue #1092 (Katalog #925) — der Anker
    verwendet per Default den SCHLUSSKURS (nicht bar.high), damit er auf derselben Aufloesung
    wie der Ausloeser liegt."""
    strategy = strategy_env(anchor="price_extreme", atr_trailing_multiplier=1.0)
    strategy._exit_atr.value = 2.0  # 2.0 abs units
    strategy._check_exits_and_update(_bar(ts_ns=1_000 * _NS_PER_HOUR, close=100.0, high=100.0, low=100.0))
    stop_1 = strategy._trailing_stop_price
    assert stop_1 == pytest.approx(98.0)

    strategy._check_exits_and_update(_bar(ts_ns=1_001 * _NS_PER_HOUR, close=110.0, high=112.0, low=109.0))
    stop_2 = strategy._trailing_stop_price
    assert stop_2 == pytest.approx(110.0 - 2.0)  # Anker = close (110), nicht high (112).
    assert stop_2 > stop_1


def test_price_extreme_anchor_resolution_intrabar_reproduces_legacy_high_low_tracking(strategy_env):
    """Issue #1092 (Katalog #925) — trailing_stop_anchor_resolution='intrabar' reproduziert das
    Alt-Verhalten (Anker auf bar.high/bar.low) bit-identisch, fuer den A/B-Kalibrierlauf."""
    strategy = strategy_env(anchor="price_extreme", atr_trailing_multiplier=1.0,
                            anchor_resolution="intrabar")
    strategy._exit_atr.value = 2.0
    strategy._check_exits_and_update(_bar(ts_ns=1_000 * _NS_PER_HOUR, close=100.0, high=100.0, low=100.0))
    strategy._check_exits_and_update(_bar(ts_ns=1_001 * _NS_PER_HOUR, close=110.0, high=112.0, low=109.0))
    assert strategy._trailing_stop_price == pytest.approx(112.0 - 2.0)  # Anker = high (112).


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


def test_short_side_price_extreme_uses_close_and_never_loosens():
    """Issue #1092/#1094 (Katalog #925/#927), SHORT-Spiegelbild von
    test_price_extreme_anchor_ratchets_and_does_not_recover_when_atr_normalises_again: der Anker
    verwendet per Default den Schlusskurs (nicht bar.low), und der Stop darf nach einer Verengung
    NICHT mehr nachgeben (nicht-steigend statt zurueck auf den ATR-2.0-Abstand)."""
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
        stop_1 = strategy._trailing_stop_price
        assert stop_1 == pytest.approx(90.0 + 2.0)  # Anker = close (90), nicht low (88).

        strategy._exit_atr.value = 0.25
        strategy._check_exits_and_update(_bar(ts_ns=1_001 * _NS_PER_HOUR, close=90.0, high=91.0, low=90.0))
        stop_2 = strategy._trailing_stop_price
        assert stop_2 <= stop_1  # SHORT: "besser" heisst niedriger.

        strategy._exit_atr.value = 2.0
        strategy._check_exits_and_update(_bar(ts_ns=1_002 * _NS_PER_HOUR, close=90.0, high=91.0, low=90.0))
        # Der Stop gibt NICHT nach, obwohl die ATR wieder auf 2.0 steigt (Kernbehauptung #1094).
        assert strategy._trailing_stop_price == pytest.approx(stop_2)


def test_ratchet_floored_atr_value_uses_background_ewma(strategy_env):
    """Issue #1094 (Katalog #927) — trailing_min_atr_frac begrenzt den ATR-Anteil GENAU der
    Ratsche nach unten, sobald eine Hintergrundschaetzung (_atr_ewma_long) existiert."""
    strategy = strategy_env(anchor="price_extreme", trailing_min_atr_frac=0.5)
    strategy._atr_ewma_long = 4.0
    # ATR (2.0) liegt UNTER der Haelfte der Hintergrundschaetzung (0.5 * 4.0 = 2.0) -> gebunden.
    assert strategy._ratchet_floored_atr_value(1.0, 100.0) == pytest.approx(2.0)
    # ATR (5.0) liegt UEBER der Untergrenze -> unveraendert.
    assert strategy._ratchet_floored_atr_value(5.0, 100.0) == pytest.approx(5.0)


def test_ratchet_floored_atr_value_without_background_estimate_falls_back_to_effective_atr(strategy_env):
    strategy = strategy_env(atr_floor_bps=2.0)
    assert strategy._atr_ewma_long is None
    assert strategy._ratchet_floored_atr_value(0.0, 100.0) == pytest.approx(0.02)
