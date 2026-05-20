from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.indicators import MovingAverageConvergenceDivergence
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import BollingerBands
from nautilus_trader.indicators import AverageTrueRange


class ComboTrendVwapConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    sma_period: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal_period: int = 9
    bb_period: int = 20
    bb_std_dev: float = 2.0
    atr_period: int = 14
    atr_multiplier: float = 0.5
    bb_entry_tolerance: float = 0.001
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


class ComboTrendVwapStrategy(Strategy):
    """Generische Trend+Momentum+Volatilitäts+VWAP-Strategie mit echter Orderausführung."""

    def __init__(self, config: ComboTrendVwapConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.sma = SimpleMovingAverage(config.sma_period)
        self.macd = MovingAverageConvergenceDivergence(config.macd_fast, config.macd_slow)
        self.macd_signal = ExponentialMovingAverage(config.macd_signal_period)
        self.bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self.atr = AverageTrueRange(config.atr_period)

        self.current_signal: str | None = None
        self.cumulative_typical_volume = 0.0
        self.cumulative_volume = 0.0
        self.current_vwap = 0.0

    def on_start(self):
        self._log.info(
            f"Starte ComboTrendVwapStrategy auf {self.instrument_id}", LogColor.GREEN
        )
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        typical_price = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
        volume = float(bar.volume)

        self.cumulative_typical_volume += typical_price * volume
        self.cumulative_volume += volume

        if self.cumulative_volume > 0:
            self.current_vwap = self.cumulative_typical_volume / self.cumulative_volume

        self.sma.handle_bar(bar)
        self.macd.handle_bar(bar)
        if self.macd.initialized:
            self.macd_signal.update_raw(self.macd.value)
        self.bb.handle_bar(bar)
        self.atr.handle_bar(bar)

        if not (
            self.sma.initialized
            and self.macd.initialized
            and self.macd_signal.initialized
            and self.bb.initialized
            and self.atr.initialized
        ):
            return

        close_price = float(bar.close)

        trend_bullish = close_price > self.sma.value
        momentum_bullish = self.macd.value > self.macd_signal.value
        atr_tolerance = self.atr.value * self.config.atr_multiplier
        entry_trigger = close_price <= (self.bb.lower + atr_tolerance)
        vwap_confirmed = self.cumulative_volume > 0 and close_price > self.current_vwap

        if (
            trend_bullish
            and momentum_bullish
            and entry_trigger
            and vwap_confirmed
            and self.current_signal != "BUY"
        ):
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL ComboTrendVWAP | "
                f"Close: {close_price:.2f} | SMA({self.config.sma_period}): {self.sma.value:.2f} | "
                f"MACD: {self.macd.value:.4f} / {self.macd_signal.value:.4f} | "
                f"BB lower: {self.bb.lower:.2f} | VWAP: {self.current_vwap:.2f}",
                LogColor.GREEN,
            )
            self.current_signal = "BUY"
            self._on_buy_signal(bar)

        elif (
            (close_price < self.sma.value or self.macd.value < self.macd_signal.value)
            and self.current_signal == "BUY"
        ):
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL ComboTrendVWAP | "
                "Trend oder Momentum gebrochen.",
                LogColor.RED,
            )
            self.current_signal = "SELL"
            self._on_sell_signal(bar)

        elif self.current_signal == "SELL":
            self.current_signal = None

    # ── Order helpers ──────────────────────────────────────────────────────────

    def _compute_quantity(self, bar: Bar) -> Quantity | None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self._log.error(f"[{self.instrument_id}] Instrument nicht im Cache")
            return None
        units = self.config.trade_amount_usd / float(bar.close)
        qty = instrument.make_qty(units, round_down=True)
        if qty == 0:
            self._log.warning(
                f"[{self.instrument_id}] Berechnete Quantity=0 "
                f"(units={units:.6f}, Kapital={self.config.trade_amount_usd} USD) "
                f"— Signal übersprungen (Kapital für 1 Einheit unzureichend)"
            )
            return None
        return qty

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
