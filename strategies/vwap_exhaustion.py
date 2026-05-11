from collections import deque

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class VwapExhaustionConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    volume_sma_period: int = 20
    deviation_threshold: float = 0.03
    volume_multiplier: float = 2.0
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


class VwapExhaustionStrategy(Strategy):
    """
    Mean-Reversion-Strategie für hochvolatile Retail-Assets (Meme-Coins, Krypto),
    basierend auf Abweichungen vom VWAP kombiniert mit Volumen-Spikes.
    Mit echter Orderausführung.
    """

    def __init__(self, config: VwapExhaustionConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.volume_history: deque = deque(maxlen=config.volume_sma_period)

        self.cumulative_vp = 0.0
        self.cumulative_volume = 0.0
        self.current_vwap = 0.0
        self.current_day: int | None = None

    def on_start(self):
        self._log.info(
            f"Starte VwapExhaustion-Strategie auf {self.instrument_id}", LogColor.GREEN
        )
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        volume = float(bar.volume)
        close_price = float(bar.close)
        typical_price = float(bar.high + bar.low + bar.close) / 3.0

        self.volume_history.append(volume)

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

        if len(self.volume_history) < self.config.volume_sma_period:
            return

        if self.current_vwap <= 0:
            return

        avg_volume = sum(self.volume_history) / len(self.volume_history)
        deviation = (close_price - self.current_vwap) / self.current_vwap

        if (
            deviation <= -self.config.deviation_threshold
            and volume > avg_volume * self.config.volume_multiplier
        ):
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (VWAP Exhaustion Bottom) | "
                f"Close: {close_price:.2f} | VWAP: {self.current_vwap:.2f} | "
                f"Dev: {deviation*100:.2f}% | Vol: {volume:.2f} (Avg: {avg_volume:.2f})",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)

        elif (
            deviation >= self.config.deviation_threshold
            and volume > avg_volume * self.config.volume_multiplier
        ):
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (VWAP Exhaustion Top) | "
                f"Close: {close_price:.2f} | VWAP: {self.current_vwap:.2f} | "
                f"Dev: {deviation*100:.2f}% | Vol: {volume:.2f} (Avg: {avg_volume:.2f})",
                LogColor.RED,
            )
            self._on_sell_signal(bar)

    # ── Order helpers ──────────────────────────────────────────────────────────

    def _compute_quantity(self, bar: Bar) -> Quantity:
        instrument = self.cache.instrument(self.instrument_id)
        units = self.config.trade_amount_usd / float(bar.close)
        return instrument.make_qty(units)

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
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self._compute_quantity(bar),
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
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self._compute_quantity(bar),
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

    def on_position_opened(self, event) -> None:
        self._log.info(
            f"[{self.instrument_id}] PositionOpened: {event}", LogColor.GREEN
        )

    def on_position_closed(self, event) -> None:
        self._log.info(
            f"[{self.instrument_id}] PositionClosed: {event}", LogColor.RED
        )

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
