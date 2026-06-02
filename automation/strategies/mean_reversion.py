from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import ExponentialMovingAverage, AverageTrueRange

from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig
from automation.momentum_ls_allocator import MomentumLSAllocator


class MeanReversionConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    keltner_period: int = 20
    keltner_atr_period: int = 20
    keltner_multiplier: float = 2.0


class MeanReversionStrategy(HourlyStrategyBase):
    """
    Mean Reversion / Keltner Channel Strategie mit ATR Trailing Stop und 48h Time-Exit.
    """

    def __init__(self, config: MeanReversionConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.keltner_period = config.keltner_period
        self.keltner_atr_period = config.keltner_atr_period
        self.keltner_multiplier = config.keltner_multiplier

        self.keltner_ma = ExponentialMovingAverage(self.keltner_period)
        self.keltner_atr = AverageTrueRange(self.keltner_atr_period)

        self.keltner_upper = 0.0
        self.keltner_lower = 0.0

        self.current_signal = None

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte Mean Reversion auf {self.instrument_id}")

        # Register indicators for automatic updates and proper warmup
        self.register_indicator_for_bars(self.bar_type, self.keltner_ma)
        self.register_indicator_for_bars(self.bar_type, self.keltner_atr)

        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        pass

    def on_bar(self, bar: Bar):
        # Indicators are automatically updated via `register_indicator_for_bars`

        if self._check_exits_and_update(bar):
            return

        if not (self.keltner_ma.initialized and self.keltner_atr.initialized):
            return

        middle_band = self.keltner_ma.value
        atr = self.keltner_atr.value

        instrument = self.cache.instrument(self.instrument_id)
        if instrument is not None:
            price_prec = instrument.price_precision
            self.keltner_upper = round(middle_band + (self.keltner_multiplier * atr), price_prec)
            self.keltner_lower = round(middle_band - (self.keltner_multiplier * atr), price_prec)
        else:
            self.keltner_upper = middle_band + (self.keltner_multiplier * atr)
            self.keltner_lower = middle_band - (self.keltner_multiplier * atr)

        close_price = float(bar.close)
        upper_band = self.keltner_upper
        lower_band = self.keltner_lower

        self._log.info(
            f"[{self.instrument_id}] BAR | Close: {close_price:.2f} | "
            f"Keltner({self.config.keltner_period}): Upper {upper_band:.2f}, Lower {lower_band:.2f}"
        )

        if close_price < lower_band and self.current_signal != "BUY":
            self._log.info(f"[{self.instrument_id}] BUY SIGNAL (Close < Keltner Lower Band)")
            self.current_signal = "BUY"
            self._on_buy_signal(bar)

        elif close_price > upper_band and self.current_signal != "SELL":
            self._log.info(f"[{self.instrument_id}] SELL SIGNAL (Close > Keltner Upper Band)")
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

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
