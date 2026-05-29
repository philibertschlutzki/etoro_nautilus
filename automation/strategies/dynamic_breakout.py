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
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from automation.strategies.hourly_strategy_base import HourlyStrategyBase
from automation.momentum_ls_allocator import MomentumLSAllocator


class DynamicBreakoutConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    price_breakout_period: int = 10
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


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

        self.current_signal: str | None = None

    def on_start(self):
        super().on_start()
        self._log.info(
            f"Starte Dynamic Breakout (Price-Range) auf {self.instrument_id}", LogColor.GREEN
        )
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        high = float(bar.high)
        low = float(bar.low)
        close_price = float(bar.close)

        self.high_history.append(high)
        self.low_history.append(low)

        if self._check_exits_and_update(bar):
            return

        if len(self.high_history) < self.config.price_breakout_period:
            return

        period_high = max(self.high_history)
        period_low = min(self.low_history)

        self._log.info(
            f"[{self.instrument_id}] BAR GESCHLOSSEN | Close: {close_price:.2f} | "
            f"High({self.config.price_breakout_period}): {period_high:.2f} | "
            f"Low({self.config.price_breakout_period}): {period_low:.2f}"
        )

        if close_price >= period_high and self.current_signal != "BUY":
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (Price Breakout High)",
                LogColor.GREEN,
            )
            self.current_signal = "BUY"
            self._on_buy_signal(bar)

        elif close_price <= period_low and self.current_signal != "SELL":
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (Price Breakout Low)",
                LogColor.RED,
            )
            self.current_signal = "SELL"
            self._on_sell_signal(bar)

    # ── Order helpers ──────────────────────────────────────────────────────────

    def _on_buy_signal(self, bar: Bar) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            if pos.side == PositionSide.LONG:
                return
            self._close_position(pos)
            self.current_signal = None
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
        self.submit_order(order)

    def _on_sell_signal(self, bar: Bar) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            if pos.side == PositionSide.SHORT:
                return
            self._close_position(pos)
            self.current_signal = None
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
        self.submit_order(order)

    def _close_position(self, pos) -> None:
        exit_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=exit_side,
            quantity=pos.quantity,
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    # ── Lifecycle callbacks ────────────────────────────────────────────────────

    def on_order_filled(self, event) -> None:
        self._log.info(
            f"[{self.instrument_id}] OrderFilled: {event}", LogColor.GREEN
        )

    def on_order_rejected(self, event) -> None:
        self._log.warning(
            f"[{self.instrument_id}] OrderRejected: {event}", LogColor.RED
        )

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
