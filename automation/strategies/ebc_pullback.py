"""
automation/strategies/ebc_pullback.py
=====================================
EbcPullbackStrategy (Archetyp M, EBC-H1 §6.1) — Rücksetzer im Trend (RSI2 unter SMA200-Filter).

Long, wenn ``C_t > SMA200`` und ``RSI2 < rsi_threshold``; Short (nur mit ``allow_short``) bei ``C_t < SMA200`` und
``RSI2 > 100 − rsi_threshold``. Exit: Schluss über (Long) bzw. unter (Short) dem SMA5, Zeitstopp ``max_bars_in_trade``,
Schutz-Trailing-Stop ``atr_trailing_multiplier`` · ATR20.
"""
from __future__ import annotations

from collections import deque

from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import MovingAverageType
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId

from automation.momentum_ls_allocator import MomentumLSAllocator
from automation.strategies.ebc_common import SideRuleMixin, WilderRsi
from automation.strategies.hourly_strategy_base import ExitReason, HourlyStrategyBase, HourlyStrategyConfig


class EbcPullbackConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    rsi_period: int = 2
    rsi_threshold: float = 10.0
    trend_period: int = 200
    exit_sma_period: int = 5
    atr_period: int = 20
    atr_trailing_multiplier: float = 3.0
    max_bars_in_trade: int = 5
    cooldown_bars: int = 0
    allow_short: bool = False
    short_regime_gate: bool = False


class EbcPullbackStrategy(SideRuleMixin, HourlyStrategyBase):
    def __init__(self, config: EbcPullbackConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self._exit_atr = AverageTrueRange(config.atr_period, MovingAverageType.WILDER)
        self._rsi = WilderRsi(config.rsi_period)
        self._closes: deque[float] = deque(maxlen=config.trend_period)
        self._sma_hist: deque[float] = deque(maxlen=21)

    def on_start(self):
        super().on_start()
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        close = float(bar.close)
        # Indikatoren werden IMMER gefüttert (AGENTS.md-Vertrag), auch an Exit-Bars.
        self._rsi.update(close)
        self._closes.append(close)
        sma_trend = sum(self._closes) / len(self._closes) if len(self._closes) == self.config.trend_period else None
        if sma_trend is not None:
            self._sma_hist.append(sma_trend)
        sma_ago = self._sma_hist[0] if len(self._sma_hist) == 21 else None
        if self._check_exits_and_update(bar):
            return
        if sma_trend is None or not self._rsi.initialized or not self._exit_atr.initialized:
            return

        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            n = self.config.exit_sma_period
            sma_exit = sum(list(self._closes)[-n:]) / n
            if pos.side == PositionSide.LONG and close > sma_exit:
                self._close_position_base(pos, exit_kind=ExitReason.SIGNAL_REVERSAL)
            elif pos.side == PositionSide.SHORT and close < sma_exit:
                self._close_position_base(pos, exit_kind=ExitReason.SIGNAL_REVERSAL)
            return

        if close > sma_trend and self._rsi.value < self.config.rsi_threshold:
            self._enter(bar, OrderSide.BUY)
        elif (close < sma_trend and self._rsi.value > 100.0 - self.config.rsi_threshold
              and self._short_allowed(close, sma_trend, sma_ago)):
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
