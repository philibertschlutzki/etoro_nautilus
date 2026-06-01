from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import BollingerBands
from nautilus_trader.indicators import RelativeStrengthIndex

from automation.strategies.hourly_strategy_base import HourlyStrategyBase
from automation.momentum_ls_allocator import MomentumLSAllocator


class FlashCrashReversalConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    instrument_id: str
    bar_type: str
    bb_period: int = 20
    bb_std_dev: float = 2.5
    rsi_period: int = 14
    rsi_oversold: float = 25.0
    rsi_overbought: float = 70.0
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


class FlashCrashReversalStrategy(HourlyStrategyBase):
    """
    Flash Crash Reversal mit BB + RSI und ATR Trailing Stop / 48h Time-Exit.
    """

    def __init__(self, config: FlashCrashReversalConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.current_signal = None

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte Flash Crash Reversal auf {self.instrument_id}")
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bb.handle_bar(bar)
        self.rsi.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return

        if not (self.bb.initialized and self.rsi.initialized):
            return

        close_price = float(bar.close)

        is_crash = close_price < self.bb.lower
        is_oversold = self.rsi.value < self.config.rsi_oversold

        is_recovery = close_price > self.bb.upper
        is_overbought = self.rsi.value > self.config.rsi_overbought
        is_mean_reversion = close_price >= self.bb.middle

        if is_crash and is_oversold and self.current_signal != "BUY":
            self._log.info(
                f"[{self.instrument_id}] FLASH CRASH DETECTED | "
                f"Close: {close_price:.5f} | RSI: {self.rsi.value:.2f}"
            )
            self.current_signal = "BUY"
            self._on_buy_signal(bar)

        elif (is_recovery or is_overbought or is_mean_reversion) and self.current_signal == "BUY":
            self._log.info(
                f"[{self.instrument_id}] RECOVERY COMPLETE. SELL SIGNAL | "
                f"Close: {close_price:.5f} | RSI: {self.rsi.value:.2f}"
            )
            self.current_signal = "SELL"
            self._on_sell_signal(bar)
            self.current_signal = None

        elif self.current_signal == "SELL":
            self.current_signal = None

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
        open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
        if open_orders:
            for order in open_orders:
                self.cancel_order(order)

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
        self._log.info(f"[{self.instrument_id}] OrderFilled: {event}")

    def on_order_rejected(self, event) -> None:
        self._log.warning(f"[{self.instrument_id}] OrderRejected: {event}")

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
