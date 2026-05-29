from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from automation.strategies.hourly_strategy_base import HourlyStrategyBase
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import RelativeStrengthIndex


class TrendPullbackConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    ema_period: int = 200
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


class TrendPullbackStrategy(HourlyStrategyBase):
    """
    Trend & Pullback Strategie für stabile Assets.
    Bestimmt den übergeordneten Trend (EMA 200) und kauft bei kurzfristigen Pullbacks (RSI überverkauft).
    """

    def __init__(self, config: TrendPullbackConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.current_signal = None

    def on_start(self):
        self._log.info(f"Starte Trend & Pullback auf {self.instrument_id}")
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        pass

    def on_bar(self, bar: Bar):
        self.ema.handle_bar(bar)
        self.rsi.handle_bar(bar)

        if not self.ema.initialized or not self.rsi.initialized:
            return

        close_price = float(bar.close)
        ema_val = self.ema.value
        rsi_val = self.rsi.value

        self._log.info(
            f"[{self.instrument_id}] BAR | Close: {close_price:.2f} | "
            f"EMA({self.config.ema_period}): {ema_val:.2f} | "
            f"RSI({self.config.rsi_period}): {rsi_val:.2f}"
        )

        if close_price > ema_val and rsi_val < self.config.rsi_oversold and self.current_signal != "BUY":
            self._log.info(f"[{self.instrument_id}] BUY SIGNAL (Trend Up & RSI Oversold)")
            self.current_signal = "BUY"
            self._on_buy_signal(bar)

        elif close_price < ema_val and rsi_val > self.config.rsi_overbought and self.current_signal != "SELL":
            self._log.info(f"[{self.instrument_id}] SELL SIGNAL (Trend Down & RSI Overbought)")
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
            self.current_signal = None  # Signal-State zurücksetzen — verhindert Flat-Lock auf nächster Bar
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
            self.current_signal = None  # Signal-State zurücksetzen — verhindert Flat-Lock auf nächster Bar
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
        self._log.info(f"[{self.instrument_id}] OrderFilled: {event}")

    def on_order_rejected(self, event) -> None:
        self._log.warning(f"[{self.instrument_id}] OrderRejected: {event}")

    def on_position_opened(self, event) -> None:
        self._log.info(f"[{self.instrument_id}] PositionOpened: {event}")

    def on_position_closed(self, event) -> None:
        self._log.info(f"[{self.instrument_id}] PositionClosed: {event}")

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
