"""
automation/strategies/dynamic_breakout.py
==========================================
Price-Range Breakout strategy — no volume dependency.

Entry logic:
  - BUY when close >= max(high) over last price_breakout_period bars
  - SELL when close <= min(low) over last price_breakout_period bars

Volume spike condition has been removed because synthetic bars built from
1h QuoteTicks always have volume=1.0, which makes volume-based filters never fire.

Exit logic (via HourlyStrategyBase):
  - ATR Trailing Stop: 1.5x ATR
  - Time-based exit: 48 bars
"""
from collections import deque

from nautilus_trader.common.enums import LogColor
from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig
from automation.momentum_ls_allocator import MomentumLSAllocator


class DynamicBreakoutConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    price_breakout_period: int = 10
    cooldown_bars: int = 12


class DynamicBreakoutStrategy(HourlyStrategyBase):
    """
    Price-Range Breakout strategy with ATR Trailing Stop and 48h Time-Exit.
    Buys when close breaks above the recent price high; sells on a break below the recent low.
    """

    def __init__(self, config: DynamicBreakoutConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.high_history: deque = deque(maxlen=config.price_breakout_period)
        self.low_history: deque = deque(maxlen=config.price_breakout_period)

        self.bars_since_last_signal: int = 9999

    def on_start(self):
        super().on_start()
        self._log.info(
            f"Starte Dynamic Breakout (Price-Range) auf {self.instrument_id}", LogColor.GREEN
        )
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bars_since_last_signal += 1

        if self._check_exits_and_update(bar):
            # Fall 1: Wir fügen den aktuellen Bar trotzdem am Ende in die History ein,
            # weil er Teil der Preisentwicklung ist.
            self.high_history.append(float(bar.high))
            self.low_history.append(float(bar.low))
            return

        # Historische Werte (OHNE den aktuellen Bar!)
        if len(self.high_history) < self.config.price_breakout_period:
            self.high_history.append(float(bar.high))
            self.low_history.append(float(bar.low))
            return

        period_high = max(self.high_history)
        period_low = min(self.low_history)

        close_price = float(bar.close)

        self._log.debug(
            f"[{self.instrument_id}] BAR GESCHLOSSEN | Close: {close_price:.2f} | "
            f"High({self.config.price_breakout_period}): {period_high:.2f} | "
            f"Low({self.config.price_breakout_period}): {period_low:.2f}"
        )

        # Signal Logic
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        current_side = positions[0].side if positions else None
        can_signal = self.bars_since_last_signal >= self.config.cooldown_bars

        if close_price >= period_high and current_side != PositionSide.LONG and can_signal:
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (Price Breakout High)",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)

        elif close_price <= period_low and current_side != PositionSide.SHORT and can_signal:
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (Price Breakout Low)",
                LogColor.RED,
            )
            self._on_sell_signal(bar)

        # History Contamination Fix: Den aktuellen Bar ERST JETZT der History hinzufügen.
        self.high_history.append(float(bar.high))
        self.low_history.append(float(bar.low))

    # ── Order helpers ──────────────────────────────────────────────────────────

    def _on_buy_signal(self, bar: Bar) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            if pos.side == PositionSide.LONG:
                return
            self._close_position_base(pos)
            return
        if len(self.cache.positions_open()) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=qty,
            time_in_force=TimeInForce.GTC,
        )
        self.bars_since_last_signal = 0
        self.submit_order(order)

    def _on_sell_signal(self, bar: Bar) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            if pos.side == PositionSide.SHORT:
                return
            self._close_position_base(pos)
            return
        if len(self.cache.positions_open()) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=qty,
            time_in_force=TimeInForce.GTC,
        )
        self.bars_since_last_signal = 0
        self.submit_order(order)

    # ── Lifecycle callbacks ────────────────────────────────────────────────────

    def on_position_closed(self, event):
        super().on_position_closed(event)
        self.bars_since_last_signal = self.config.cooldown_bars

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
