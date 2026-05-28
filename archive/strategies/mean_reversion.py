from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.indicators import KeltnerChannel


class MeanReversionConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    keltner_period: int = 20
    keltner_atr_period: int = 20
    keltner_multiplier: float = 2.0
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


class MeanReversionStrategy(Strategy):
    """
    Mean Reversion / Keltner Channel Strategie.
    Nutzt Range-Bound-Märkte aus. Kauf am unteren Band, Verkauf am oberen Band.
    """

    def __init__(self, config: MeanReversionConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.keltner = KeltnerChannel(
            period=config.keltner_period,
            k_multiplier=config.keltner_multiplier,
        )
        self.current_signal = None

    def on_start(self):
        self._log.info(f"Starte Mean Reversion auf {self.instrument_id}")
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        pass

    def on_bar(self, bar: Bar):
        self.keltner.handle_bar(bar)

        if not self.keltner.initialized:
            return

        close_price = float(bar.close)
        upper_band = self.keltner.upper
        lower_band = self.keltner.lower

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

    def _compute_quantity(self, bar: Bar) -> Quantity | None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self._log.error(f"[{self.instrument_id}] Instrument nicht im Cache")
            return None
        units = self.config.trade_amount_usd / float(bar.close)
        # Pre-check: Equity-Instrumente (size_precision=0) erfordern mindestens 1 ganze Einheit.
        # Nautilus wirft einen harten ValueError bei make_qty() wenn das gerundete Ergebnis 0 ergibt —
        # auch mit round_down=True. Pre-check verhindert den Aufruf; try/except ist zusätzliche Absicherung.
        if units < float(instrument.size_increment):
            self._log.warning(
                f"[{self.instrument_id}] Zu wenig Kapital für 1 Einheit "
                f"(units={units:.6f}, size_increment={instrument.size_increment}) "
                f"— Signal übersprungen"
            )
            return None
        try:
            qty = instrument.make_qty(units, round_down=True)
        except ValueError as e:
            self._log.warning(
                f"[{self.instrument_id}] make_qty ValueError: {e} — Signal übersprungen"
            )
            return None
        if qty == 0:
            self._log.warning(
                f"[{self.instrument_id}] Quantity=0 nach Rundung "
                f"(units={units:.6f}) — Signal übersprungen"
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
