"""
automation/strategies/opening_range_breakout.py
=================================================
OpeningRangeBreakoutStrategy — Issue #690 (SPEC_02).

Regime: Momentum-Ignition am Tagesbeginn (Opening-Range-Breakout). Die ersten `or_bars`
Bars eines neuen Kalendertags (erkannt über `pd.Timestamp(bar.ts_init).day`, identisch zur
Basisklasse) definieren eine Range (Hoch/Tief). Bricht der Kurs danach über das Range-Hoch
(+ATR-Puffer), ist das ein Long-Signal; unter das Range-Tief analog Short.

24/7-Bars-Hinweis: ein "Tag" ist hier ein Kalendertag (24 Bars), nicht die US-RTH-Session —
die ersten `or_bars` Bars können in ruhige Nacht-Ticks fallen. Für eine RTH-basierte Variante
müsste der Tageswechsel auf die Session-Öffnung (`session_open_hour`) statt auf `.day`
referenziert werden.

Exit-Logik (via HourlyStrategyBase): ATR-Trailing-Stop + Zeit-Exit (~1 Handelstag).
"""
import pandas as pd
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import AverageTrueRange

from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig
from automation.momentum_ls_allocator import MomentumLSAllocator


class OpeningRangeBreakoutConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    or_bars: int = 3
    or_atr_buffer: float = 0.25
    allow_short: bool = False
    cooldown_bars: int = 6
    atr_period: int = 14
    atr_trailing_multiplier: float = 2.0
    max_bars_in_trade: int = 24
    max_daily_trades: int | None = 2
    trade_amount_pct: float = 15.0


class OpeningRangeBreakoutStrategy(HourlyStrategyBase):
    """
    Opening-Range-Breakout (ORB) mit ATR-Puffer, ATR-Trailing-Stop und ~1-Handelstag-Zeit-Exit.
    """

    def __init__(self, config: OpeningRangeBreakoutConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.atr = AverageTrueRange(config.atr_period)
        self.current_signal: str | None = None
        self.bars_since_last_signal: int = 9999
        self._or_day: int | None = None
        self._or_bar_count: int = 0
        self._or_high: float | None = None
        self._or_low: float | None = None

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte OpeningRangeBreakout-Strategie auf {self.instrument_id}", LogColor.GREEN)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bars_since_last_signal += 1
        self.atr.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return

        day = pd.Timestamp(bar.ts_init).day
        if day != self._or_day:
            self._or_day = day
            self._or_bar_count = 0
            self._or_high = None
            self._or_low = None

        high, low, close = float(bar.high), float(bar.low), float(bar.close)
        if self._or_bar_count < self.config.or_bars:
            self._or_high = high if self._or_high is None else max(self._or_high, high)
            self._or_low = low if self._or_low is None else min(self._or_low, low)
            self._or_bar_count += 1
            return

        if not self.atr.initialized:
            return
        can_signal = self.current_signal is None or self.bars_since_last_signal >= self.config.cooldown_bars
        if not can_signal:
            return

        buf = self.config.or_atr_buffer * self.atr.value
        if close > self._or_high + buf:
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (Opening Range Breakout Up) | Close: {close:.2f} | "
                f"OR High: {self._or_high:.2f} | Buffer: {buf:.4f}",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)
        elif close < self._or_low - buf and self.config.allow_short:
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (Opening Range Breakout Down) | Close: {close:.2f} | "
                f"OR Low: {self._or_low:.2f} | Buffer: {buf:.4f}",
                LogColor.RED,
            )
            self._on_sell_signal(bar)

    # ── Order helpers ──────────────────────────────────────────────────────────

    def _on_buy_signal(self, bar: Bar) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            if pos.side == PositionSide.LONG:
                return
            self._close_position_base(pos)
            self.current_signal = None
            return
        if self.cache.orders_open(instrument_id=self.instrument_id):
            return
        if len(self.cache.positions_open()) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return
        self.current_signal = "BUY"
        self.bars_since_last_signal = 0
        order = self.order_factory.market(
            instrument_id=self.instrument_id, order_side=OrderSide.BUY,
            quantity=qty, time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    def _on_sell_signal(self, bar: Bar) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            if pos.side == PositionSide.SHORT:
                return
            self._close_position_base(pos)
            self.current_signal = None
            return
        if self.cache.orders_open(instrument_id=self.instrument_id):
            return
        if len(self.cache.positions_open()) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return
        self.current_signal = "SELL"
        self.bars_since_last_signal = 0
        order = self.order_factory.market(
            instrument_id=self.instrument_id, order_side=OrderSide.SELL,
            quantity=qty, time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    # ── Lifecycle callbacks ────────────────────────────────────────────────────

    def on_position_closed(self, event) -> None:
        super().on_position_closed(event)
        self.current_signal = None

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
