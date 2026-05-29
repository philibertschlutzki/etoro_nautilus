"""
automation/strategies/vwap_exhaustion.py
=========================================
VWAP Exhaustion (Price-Deviation only) — volume multiplier condition removed.

Entry logic (price deviation only):
  - BUY when (close - vwap) / vwap <= -deviation_threshold  (oversold vs. VWAP)
  - SELL when (close - vwap) / vwap >= +deviation_threshold  (overbought vs. VWAP)

Volume multiplier condition has been removed because synthetic bars built from
1h QuoteTicks always have volume=1.0, which makes the volume condition (1.0 > 1.0 x 2.5)
never true.

Note: With synthetic bars (volume=1.0), VWAP approximates typical price, which is
acceptable for the price-deviation signal.

Exit logic (via HourlyStrategyBase):
  - ATR Trailing Stop: 1.5x ATR
  - Time-based exit: 48 bars
"""
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from automation.strategies.hourly_strategy_base import HourlyStrategyBase


class VwapExhaustionConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    deviation_threshold: float = 0.03
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


class VwapExhaustionStrategy(HourlyStrategyBase):
    """
    VWAP Exhaustion (price-deviation only) with ATR Trailing Stop and 48h Time-Exit.
    """

    def __init__(self, config: VwapExhaustionConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.cumulative_vp = 0.0
        self.cumulative_volume = 0.0
        self.current_vwap = 0.0
        self.current_day: int | None = None

    def on_start(self):
        super().on_start()
        self._log.info(
            f"Starte VwapExhaustion-Strategie auf {self.instrument_id}", LogColor.GREEN
        )
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        volume = float(bar.volume)
        close_price = float(bar.close)
        typical_price = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0

        bar_day = bar.ts_event // 86400000000000
        if self.current_day != bar_day:
            self.cumulative_vp = 0.0
            self.cumulative_volume = 0.0
            self.current_day = bar_day

        if volume > 0:
            self.cumulative_vp += typical_price * volume
            self.cumulative_volume += volume

        if self.cumulative_volume > 0:
            self.current_vwap = self.cumulative_vp / self.cumulative_volume

        if self._check_exits_and_update(bar):
            return

        if self.current_vwap <= 0:
            return

        deviation = (close_price - self.current_vwap) / self.current_vwap

        if deviation <= -self.config.deviation_threshold:
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (VWAP Deviation Bottom) | "
                f"Close: {close_price:.2f} | VWAP: {self.current_vwap:.2f} | "
                f"Dev: {deviation * 100:.2f}%",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)

        elif deviation >= self.config.deviation_threshold:
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (VWAP Deviation Top) | "
                f"Close: {close_price:.2f} | VWAP: {self.current_vwap:.2f} | "
                f"Dev: {deviation * 100:.2f}%",
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
