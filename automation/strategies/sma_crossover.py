from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import SimpleMovingAverage

from automation.strategies.hourly_strategy_base import HourlyStrategyBase
from automation.momentum_ls_allocator import MomentumLSAllocator


class SmaCrossoverConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    sma_period: int = 5
    cooldown_bars: int = 12
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


class SmaCrossoverStrategy(HourlyStrategyBase):
    """Klassische SMA Crossover Strategie mit ATR Trailing Stop und 48h Time-Exit."""

    def __init__(self, config: SmaCrossoverConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.sma = SimpleMovingAverage(config.sma_period)
        self.current_signal: str | None = None
        self.bars_since_last_signal: int = 0

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte SMA Crossover auf {self.instrument_id}", LogColor.GREEN)
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        pass

    def on_bar(self, bar: Bar):
        self.bars_since_last_signal += 1

        self.sma.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return

        if not self.sma.initialized:
            return

        close_price = float(bar.close)

        self._log.info(
            f"[{self.instrument_id}] BAR GESCHLOSSEN | Close: {close_price:.2f} | "
            f"SMA({self.config.sma_period}): {self.sma.value:.2f}"
        )

        can_signal = self.current_signal is None or self.bars_since_last_signal >= self.config.cooldown_bars

        if close_price > self.sma.value and (self.current_signal != "BUY" and can_signal):
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (Close > SMA)", LogColor.GREEN
            )
            self.current_signal = "BUY"
            self._on_buy_signal(bar)

        elif close_price < self.sma.value and (self.current_signal != "SELL" and can_signal):
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (Close < SMA)", LogColor.RED
            )
            self.current_signal = "SELL"
            self._on_sell_signal(bar)

    # ── Order helpers ──────────────────────────────────────────────────────────

    def _has_open_position(self) -> bool:
        return bool(self.cache.positions_open(instrument_id=self.instrument_id))

    def _on_buy_signal(self, bar: Bar) -> None:
        self.bars_since_last_signal = 0
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
            tags=["SL:0.10"],
        )
        self.submit_order(order)

    def _on_sell_signal(self, bar: Bar) -> None:
        self.bars_since_last_signal = 0
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
            tags=["SL:0.10"],
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
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.", LogColor.RED)
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
