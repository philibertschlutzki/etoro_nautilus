"""
automation/strategies/ebc_squeeze.py
====================================
EbcSqueezeStrategy (Archetyp V, EBC-H1 §6.1) — Volatilitäts-Ausbruch nach Kompression (Bollinger innerhalb Keltner).

Kompression am Tag t: ``BB_oben < KC_oben`` und ``BB_unten > KC_unten`` (BB(20; 2,0), KC = EMA20 ± 1,5 · ATR20). Long, wenn
die ``squeeze_bars`` Tage bis t−1 durchgehend komprimiert waren und ``C_t > BB_oben``; Short (nur mit ``allow_short``)
bei ``C_t < BB_unten``. Exit: ATR-Trailing-Stop ``atr_trailing_multiplier`` · ATR20, Zeitstopp ``max_bars_in_trade``.
"""
from __future__ import annotations

from collections import deque

from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import MovingAverageType
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId

from automation.momentum_ls_allocator import MomentumLSAllocator
from automation.strategies.ebc_common import Ema, RollingWindow, SideRuleMixin
from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig


class EbcSqueezeConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    squeeze_bars: int = 5
    bb_period: int = 20
    bb_std_dev: float = 2.0
    kc_period: int = 20
    kc_multiplier: float = 1.5
    atr_period: int = 20
    atr_trailing_multiplier: float = 2.5
    max_bars_in_trade: int = 20
    cooldown_bars: int = 0
    allow_short: bool = False
    short_regime_gate: bool = False


class EbcSqueezeStrategy(SideRuleMixin, HourlyStrategyBase):
    def __init__(self, config: EbcSqueezeConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self._exit_atr = AverageTrueRange(config.atr_period, MovingAverageType.WILDER)
        self._bb = RollingWindow(config.bb_period)
        self._ema = Ema(config.kc_period)
        self._squeeze: deque[bool] = deque(maxlen=config.squeeze_bars)
        self._closes: deque[float] = deque(maxlen=220)
        self._sma_hist: deque[float] = deque(maxlen=21)

    def on_start(self):
        super().on_start()
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        close = float(bar.close)
        self._bb.push(close)
        self._ema.update(close)
        self._closes.append(close)
        sma200 = sum(list(self._closes)[-200:]) / 200.0 if len(self._closes) >= 200 else None
        if sma200 is not None:
            self._sma_hist.append(sma200)
        sma_ago = self._sma_hist[0] if len(self._sma_hist) == 21 else None
        exited = self._check_exits_and_update(bar)   # füttert ``_exit_atr`` (Wilder, ATR20)
        ready = self._bb.full and self._ema.initialized and self._exit_atr.initialized
        # Zustand des Vortags VOR der Aktualisierung mit dem heutigen Kompressionswert auswerten
        prior_squeeze = len(self._squeeze) == self.config.squeeze_bars and all(self._squeeze)
        if ready:
            mid, sd = self._bb.mean(), self._bb.std()
            bb_up, bb_lo = mid + self.config.bb_std_dev * sd, mid - self.config.bb_std_dev * sd
            atr = float(self._exit_atr.value)
            kc_up = self._ema.value + self.config.kc_multiplier * atr
            kc_lo = self._ema.value - self.config.kc_multiplier * atr
            self._squeeze.append(bb_up < kc_up and bb_lo > kc_lo)
        else:
            self._squeeze.append(False)
            return
        if exited:
            return
        if self.cache.positions_open(instrument_id=self.instrument_id):
            return
        if not prior_squeeze:
            return
        if close > bb_up:
            self._enter(bar, OrderSide.BUY)
        elif close < bb_lo and self._short_allowed(close, sma200, sma_ago):
            self._enter(bar, OrderSide.SELL)

    def _enter(self, bar: Bar, side: OrderSide) -> None:
        if not self._entry_allowed():
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return
        order = self.order_factory.market(instrument_id=self.instrument_id, order_side=side, quantity=qty,
                                          time_in_force=TimeInForce.GTC, tags=self._entry_order_tags(bar))
        self.submit_order(order)

    def on_stop(self):
        self.unsubscribe_bars(self.bar_type)
