from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import BollingerBands

from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig
from automation.momentum_ls_allocator import MomentumLSAllocator


class VolatilityBreakoutConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    bb_period: int = 20
    bb_std_dev: float = 2.0


class VolatilityBreakoutPumpStrategy(HourlyStrategyBase):
    """
    Volatility Breakout (Pump) Strategie mit ATR Trailing Stop und 48h Time-Exit.
    """

    def __init__(self, config: VolatilityBreakoutConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self.current_signal = None

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte Breakout Pump Rider auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bb.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return

        if not self.bb.initialized:
            return

        close_price = float(bar.close)

        breakout_up = close_price > self.bb.upper
        momentum_lost = close_price < self.bb.middle

        if breakout_up and self.current_signal != "BUY":
            self._log.info(f"[{self.instrument_id}] VOLATILITY BREAKOUT (PUMP) | Close: {close_price:.5f}")
            self.current_signal = "BUY"
            self._on_buy_signal(bar)

        elif momentum_lost and self.current_signal == "BUY":
            self._log.info(f"[{self.instrument_id}] MOMENTUM LOST. SELL SIGNAL | Close: {close_price:.5f}")
            self.current_signal = "SELL"
            self._on_sell_signal(bar)

        elif self.current_signal == "SELL":
            self.current_signal = None

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
            self._close_position_base(pos)
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

    # ── Lifecycle callbacks ────────────────────────────────────────────────────

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
