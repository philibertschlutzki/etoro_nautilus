"""
automation/strategies/trend_pullback.py
=========================================
TrendPullbackStrategy — Issue #699.

Root-Cause (Issue #699, strategies.json-Note "0 FIFO-Schliessungen in allen Tests"): `on_bar()`
rief entgegen dem verbindlichen `HourlyStrategyBase`-Vertrag (siehe hourly_strategy_base.py-
Moduldocstring: "The on_bar() override calls `_check_exits_and_update()` automatically at the
START of each bar") NIEMALS `self._check_exits_and_update(bar)` auf — die Strategie hatte dadurch
WEDER einen ATR-Trailing-Stop NOCH einen Time-Exit (`max_bars_in_trade`). Eine offene Position
schloss ausschliesslich, wenn irgendwann ein GEGENLÄUFIGES Entry-Signal feuerte (EMA(200)+RSI-
Extrem) — bei einem 200-Bar-Trendfilter strukturell selten. Ergebnis: 0 realisierte Round-Trips
im OOS-Fenster (`HOLDOUT_NO_ELIGIBLE_TRIALS`), unabhängig von den Suchraum-Bounds — kein Bounds-
Kalibrierungsproblem, sondern ein fehlender Exit-Mechanismus. Fix: `_check_exits_and_update(bar)`
am Anfang von `on_bar()` verdrahtet (der Standard-Aufruf, den jede andere Strategie in diesem
Modul bereits nutzt) — die Strategie erhält damit den regulären ATR-Trailing-Stop +
`max_bars_in_trade`-Time-Exit.
"""
from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig, ExitReason
from automation.momentum_ls_allocator import MomentumLSAllocator
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import RelativeStrengthIndex


class TrendPullbackConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    ema_period: int = 200
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0


class TrendPullbackStrategy(HourlyStrategyBase):
    """
    Trend & Pullback Strategie für stabile Assets.
    Bestimmt den übergeordneten Trend (EMA 200) und kauft bei kurzfristigen Pullbacks (RSI überverkauft).
    """

    def __init__(self, config: TrendPullbackConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.current_signal = None

    def on_start(self):
        super().on_start()  # Issue #699 — REQUIRED (siehe HourlyStrategyBase-Moduldocstring).
        self._log.info(f"Starte Trend & Pullback auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)


    def on_bar(self, bar: Bar):
        # Issue #699 — ATR-Trailing-Stop + max_bars_in_trade-Time-Exit (Modul-Docstring-Fix,
        # Root-Cause der "0 FIFO-Schliessungen"-Note in strategies.json).
        if self._check_exits_and_update(bar):
            return

        self.ema.handle_bar(bar)
        self.rsi.handle_bar(bar)

        if not self.ema.initialized or not self.rsi.initialized:
            return

        close_price = float(bar.close)
        ema_val = self.ema.value
        rsi_val = self.rsi.value

        self._log.debug(
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
            self._close_position_base(pos, exit_kind=ExitReason.SIGNAL_REVERSAL)
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
            self._close_position_base(pos, exit_kind=ExitReason.SIGNAL_REVERSAL)
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

    # ── Lifecycle callbacks ────────────────────────────────────────────────────

    def on_position_closed(self, event) -> None:
        # Issue #699 — der neu verdrahtete `_check_exits_and_update`-Trailing-Stop/Time-Exit
        # schliesst Positionen jetzt AUCH ausserhalb der `_on_buy_signal`/`_on_sell_signal`-Pfade
        # (die dort bereits `current_signal = None` setzen); ohne diesen Reset bliebe
        # `current_signal` nach einem Trailing-Stop-Exit auf "BUY"/"SELL" hängen und würde einen
        # Re-Entry in dieselbe Richtung dauerhaft blockieren (Flat-Lock).
        super().on_position_closed(event)
        self.current_signal = None

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
