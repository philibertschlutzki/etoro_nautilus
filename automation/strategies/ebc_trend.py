"""
automation/strategies/ebc_trend.py
==================================
EbcTrendStrategy (Archetyp T, EBC-H1 §6.1) — Trend-Ausbruch auf der Tagesachse.

Long, wenn ``C_t`` über dem höchsten Schluss der letzten ``lookback`` Tage liegt; Short (nur mit ``allow_short``) beim
Tiefst-Ausbruch. Exit: ATR-Trailing-Stop (``atr_trailing_multiplier`` · ATR20, Basisklasse), Signal-Exit beim Bruch des
``exit_lookback``-Tage-Gegenextrems, Zeitstopp ``max_bars_in_trade``. Parameter in ATR- und Handelstag-Einheiten.
"""
from __future__ import annotations

from collections import deque

from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import MovingAverageType
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId

from automation.momentum_ls_allocator import MomentumLSAllocator
from automation.strategies.ebc_common import SideRuleMixin
from automation.strategies.hourly_strategy_base import ExitReason, HourlyStrategyBase, HourlyStrategyConfig


class EbcTrendConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    lookback: int = 50
    exit_lookback: int = 20
    atr_period: int = 20
    atr_trailing_multiplier: float = 3.0
    max_bars_in_trade: int = 60
    cooldown_bars: int = 0
    allow_short: bool = False
    short_regime_gate: bool = False


class EbcTrendStrategy(SideRuleMixin, HourlyStrategyBase):
    def __init__(self, config: EbcTrendConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self._exit_atr = AverageTrueRange(config.atr_period, MovingAverageType.WILDER)
        self._closes: deque[float] = deque(maxlen=max(config.lookback, config.exit_lookback, 220) + 1)
        self._sma200_hist: deque[float] = deque(maxlen=21)

    def on_start(self):
        super().on_start()
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        if self._check_exits_and_update(bar):
            self._closes.append(float(bar.close))
            return
        close = float(bar.close)
        prior = list(self._closes)
        self._closes.append(close)
        sma200 = None
        if len(self._closes) >= 200:
            window = list(self._closes)[-200:]
            sma200 = sum(window) / 200.0
            self._sma200_hist.append(sma200)
        sma200_ago = self._sma200_hist[0] if len(self._sma200_hist) == 21 else None
        if len(prior) < self.config.lookback or not self._exit_atr.initialized:
            return

        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            tail = prior[-self.config.exit_lookback:]
            if pos.side == PositionSide.LONG and close < min(tail):
                self._close_position_base(pos, exit_kind=ExitReason.SIGNAL_REVERSAL)
            elif pos.side == PositionSide.SHORT and close > max(tail):
                self._close_position_base(pos, exit_kind=ExitReason.SIGNAL_REVERSAL)
            return

        window = prior[-self.config.lookback:]
        if close > max(window):
            self._enter(bar, OrderSide.BUY)
        elif close < min(window) and self._short_allowed(close, sma200, sma200_ago):
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
