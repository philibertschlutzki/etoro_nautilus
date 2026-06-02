from nautilus_trader.common.enums import LogColor
from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig
from automation.momentum_ls_allocator import MomentumLSAllocator

from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import DirectionalMovement


class AdxAtrMomentumConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    adx_period: int = 14
    ema_period: int = 50
    atr_period: int = 14
    atr_multiplier: float = 2.0


class AdxAtrMomentumStrategy(HourlyStrategyBase):
    """
    Trendfolge-Strategie basierend auf ADX (Trendstärke), EMA (Trendrichtung) und
    ATR (Trailing Stop). Mit echter Orderausführung.
    """

    def __init__(self, config: AdxAtrMomentumConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.adx = DirectionalMovement(config.adx_period)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.atr = AverageTrueRange(config.atr_period)

        # Internal state for trailing-stop logic (kept for indicator calculations)
        self.current_position: str | None = None
        self.entry_price = 0.0
        self.trailing_stop = 0.0

    def on_start(self):
        self._log.info(
            f"Starte ADX+ATR Momentum Strategie auf {self.instrument_id}", LogColor.GREEN
        )
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.adx.handle_bar(bar)
        self.ema.handle_bar(bar)
        self.atr.handle_bar(bar)

        if not (self.adx.initialized and self.ema.initialized and self.atr.initialized):
            return

        close_price = float(bar.close)
        adx_value = self.adx.value
        ema_value = self.ema.value
        atr_value = self.atr.value

        # Trailing Stop Logik (uses internal current_position as signal state)
        if self.current_position == "BUY":
            new_stop = close_price - (atr_value * self.config.atr_multiplier)
            if new_stop > self.trailing_stop:
                self.trailing_stop = new_stop
            if close_price <= self.trailing_stop:
                self._log.info(
                    f"[{self.instrument_id}] Trailing Stop LONG Hit! "
                    f"Close: {close_price:.2f} <= Stop: {self.trailing_stop:.2f}",
                    LogColor.RED,
                )
                self.current_position = None
                self.trailing_stop = 0.0
                self._exit_open_position()

        elif self.current_position == "SELL":
            new_stop = close_price + (atr_value * self.config.atr_multiplier)
            if self.trailing_stop == 0.0 or new_stop < self.trailing_stop:
                self.trailing_stop = new_stop
            if close_price >= self.trailing_stop:
                self._log.info(
                    f"[{self.instrument_id}] Trailing Stop SHORT Hit! "
                    f"Close: {close_price:.2f} >= Stop: {self.trailing_stop:.2f}",
                    LogColor.GREEN,
                )
                self.current_position = None
                self.trailing_stop = 0.0
                self._exit_open_position()

        # Entry logic
        if self.current_position is None:
            if close_price > ema_value and adx_value > 25:
                self.current_position = "BUY"
                self.entry_price = close_price
                self.trailing_stop = close_price - (atr_value * self.config.atr_multiplier)
                self._log.info(
                    f"[{self.instrument_id}] BUY SIGNAL AdxAtrMomentum | "
                    f"Close: {close_price:.2f} > EMA: {ema_value:.2f} | "
                    f"ADX: {adx_value:.2f} | ATR: {atr_value:.2f} | Stop: {self.trailing_stop:.2f}",
                    LogColor.GREEN,
                )
                self._on_buy_signal(bar)

            elif close_price < ema_value and adx_value > 25:
                self.current_position = "SELL"
                self.entry_price = close_price
                self.trailing_stop = close_price + (atr_value * self.config.atr_multiplier)
                self._log.info(
                    f"[{self.instrument_id}] SELL SIGNAL AdxAtrMomentum | "
                    f"Close: {close_price:.2f} < EMA: {ema_value:.2f} | "
                    f"ADX: {adx_value:.2f} | ATR: {atr_value:.2f} | Stop: {self.trailing_stop:.2f}",
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
            self._close_position(pos)
            # State zurücksetzen — ermöglicht Neueinstieg auf nächster Bar (kein Flat-Lock)
            self.current_position = None
            self.entry_price = 0.0
            self.trailing_stop = 0.0
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
            # State zurücksetzen — ermöglicht Neueinstieg auf nächster Bar (kein Flat-Lock)
            self.current_position = None
            self.entry_price = 0.0
            self.trailing_stop = 0.0
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

    def _exit_open_position(self) -> None:
        """Close any open position on this instrument (trailing stop hit)."""
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if not positions:
            return
        self._close_position(positions[0])

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

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
