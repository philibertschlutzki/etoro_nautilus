from collections import deque

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class DynamicBreakoutConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    volume_sma_period: int = 20
    volume_multiplier: float = 2.5
    price_breakout_period: int = 20
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1


class DynamicBreakoutStrategy(Strategy):
    """
    Dynamische Breakout-Strategie für Forex/Rohstoffe (NATGAS),
    basierend auf extremen Volumen-Spikes und Price-Breakout.
    Mit echter Orderausführung.
    """

    def __init__(self, config: DynamicBreakoutConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.volume_history: deque = deque(maxlen=config.volume_sma_period)
        self.high_history: deque = deque(maxlen=config.price_breakout_period)
        self.low_history: deque = deque(maxlen=config.price_breakout_period)

        self.current_signal: str | None = None

    def on_start(self):
        self._log.info(
            f"Starte Dynamic Breakout auf {self.instrument_id}", LogColor.GREEN
        )
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        pass

    def on_bar(self, bar: Bar):
        volume = float(bar.volume)
        high = float(bar.high)
        low = float(bar.low)
        close_price = float(bar.close)

        self.volume_history.append(volume)
        self.high_history.append(high)
        self.low_history.append(low)

        if (
            len(self.volume_history) < self.config.volume_sma_period
            or len(self.high_history) < self.config.price_breakout_period
        ):
            return

        avg_volume = sum(self.volume_history) / len(self.volume_history)
        period_high = max(self.high_history)
        period_low = min(self.low_history)

        self._log.info(
            f"[{self.instrument_id}] BAR GESCHLOSSEN | Close: {close_price:.2f} | "
            f"Volume: {volume:.2f} (Avg: {avg_volume:.2f}) | "
            f"Highs({self.config.price_breakout_period}): {period_high:.2f} | "
            f"Lows({self.config.price_breakout_period}): {period_low:.2f}"
        )

        volume_spike = volume > avg_volume * self.config.volume_multiplier

        if volume_spike and close_price >= period_high and self.current_signal != "BUY":
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (Volume Spike & Price Breakout High)",
                LogColor.GREEN,
            )
            self.current_signal = "BUY"
            self._on_buy_signal(bar)

        elif volume_spike and close_price <= period_low and self.current_signal != "SELL":
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (Volume Spike & Price Breakout Low)",
                LogColor.RED,
            )
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
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
