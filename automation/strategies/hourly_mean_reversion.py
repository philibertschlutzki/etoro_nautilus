from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import KeltnerChannel

from automation.strategies.hourly_strategy_base import HourlyStrategyBase
from automation.momentum_ls_allocator import MomentumLSAllocator


class HourlyMeanReversionConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    keltner_period: int = 10
    keltner_atr_period: int = 10
    keltner_multiplier: float = 1.5


class HourlyMeanReversionStrategy(HourlyStrategyBase):
    """
    Mean Reversion / Keltner Channel Strategie mit abweichenden Defaults (10/10/1.5).
    """

    def __init__(self, config: HourlyMeanReversionConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.keltner = KeltnerChannel(
            period=config.keltner_period,
            k_multiplier=config.keltner_multiplier,
        )
        self.current_signal = None

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte Hourly Mean Reversion auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)


    def on_bar(self, bar: Bar):
        self.keltner.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return

        if not self.keltner.initialized:
            return

        close_price = float(bar.close)
        upper_band = self.keltner.upper
        lower_band = self.keltner.lower

        self._log.debug(
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
        self.unsubscribe_bars(self.bar_type)
