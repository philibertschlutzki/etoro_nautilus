from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from nautilus_trader.indicators import BollingerBands


class VolatilityBreakoutConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    bb_period: int = 20
    bb_std_dev: float = 2.0
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


class VolatilityBreakoutPumpStrategy(Strategy):
    """
    Kauft Ausbrüche nach oben (Pumps / Gaps), sobald der Preis das obere Bollinger Band
    mit Schwung durchbricht. Schliesst die Position bei Rückkehr zum gleitenden Durchschnitt.
    """

    def __init__(self, config: VolatilityBreakoutConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self.current_signal = None

    def on_start(self):
        self._log.info(f"Starte Breakout Pump Rider auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bb.handle_bar(bar)

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
        self._log.info(f"[{self.instrument_id}] OrderFilled: {event}")

    def on_order_rejected(self, event) -> None:
        self._log.warning(f"[{self.instrument_id}] OrderRejected: {event}")

    def on_position_opened(self, event) -> None:
        self._log.info(f"[{self.instrument_id}] PositionOpened: {event}")

    def on_position_closed(self, event) -> None:
        self._log.info(f"[{self.instrument_id}] PositionClosed: {event}")

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
