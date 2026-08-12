"""
automation/strategies/gap_continuation.py
===========================================
GapContinuationStrategy — Issue #693 (SPEC_05).

**Issue #809 — DEAKTIVIERT (``strategies.json['active'] = false``).** Regime: Overnight-/
Event-Gap-Fortsetzung. Springt der erste Bar eines neuen Kalendertags gegenüber dem Schlusskurs
des Vortags um mehr als `gap_threshold_pct`, handelt die Strategie in Gap-Richtung
(Aufwärts-Gap → Long, Abwärts-Gap → Short).

24/7-Boundary-Caveat (Variante A, siehe SPEC_05): die Daten sind 24/7-1h-Bars, ein "Gap" im
klassischen Session-Sinn existiert daher nur, wenn zwischen zwei Kalendertagen tatsächlich
eine Kurslücke liegt (z. B. Wochenend-/Feiertags-Gaps, starke Nacht-Moves) — auf den
synthetischen, kontinuierlichen Bars dieses Systems degeneriert das strukturell zur Differenz
zweier aufeinanderfolgender Bars, keine echte Continuation-Edge. Eine präzisere,
RTH-Session-basierte Variante B (`session_open_hour`) ist im SPEC skizziert, aber NICHT
implementierbar, solange dieses System keinen RTH-Session-Kalender besitzt (siehe
`strategies.json`'s `_note` für die volle #809-Begründung). Deaktiviert statt eines stillen
Laufzeit-Skips (`invalid_on_continuous_bars`, #698) — der Klassen-Code bleibt zur
Re-Aktivierung erhalten, sobald ein echter Session-Kalender verfügbar ist.

Exit-Logik (via HourlyStrategyBase): ATR-Trailing-Stop + Zeit-Exit (~1 Handelstag).
"""
import pandas as pd
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import AverageTrueRange

from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig, ExitReason
from automation.momentum_ls_allocator import MomentumLSAllocator


class GapContinuationConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    gap_threshold_pct: float = 0.015
    allow_short: bool = False
    cooldown_bars: int = 0
    atr_period: int = 14
    atr_trailing_multiplier: float = 2.0
    max_bars_in_trade: int = 24
    max_daily_trades: int | None = 1
    trade_amount_pct: float = 15.0


class GapContinuationStrategy(HourlyStrategyBase):
    """
    Overnight-/Event-Gap-Continuation (Variante A, Kalendertag-Grenze) mit ATR-Trailing-Stop
    und ~1-Handelstag-Zeit-Exit.
    """

    def __init__(self, config: GapContinuationConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.atr = AverageTrueRange(config.atr_period)
        self.current_signal: str | None = None
        self.bars_since_last_signal: int = 9999
        self._gap_day: int | None = None
        self._prev_day_close: float | None = None
        self._last_close: float | None = None
        self._gap_traded_today: bool = False

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte GapContinuation-Strategie auf {self.instrument_id}", LogColor.GREEN)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bars_since_last_signal += 1
        self.atr.handle_bar(bar)

        if self._check_exits_and_update(bar):
            # _last_close in JEDEM Pfad pflegen, sonst driftet der Vortagsschluss.
            self._last_close = float(bar.close)
            return

        close = float(bar.close)
        day = pd.Timestamp(bar.ts_init).day

        is_new_day = day != self._gap_day
        if is_new_day:
            self._prev_day_close = self._last_close
            self._gap_day = day
            self._gap_traded_today = False

        if (
            is_new_day
            and self._prev_day_close
            and self._prev_day_close > 0
            and not self._gap_traded_today
            and self.atr.initialized
        ):
            gap = (close - self._prev_day_close) / self._prev_day_close
            if gap >= self.config.gap_threshold_pct:
                self._gap_traded_today = True
                self._log.info(
                    f"[{self.instrument_id}] BUY SIGNAL (Gap Up Continuation) | Close: {close:.2f} | "
                    f"Prev Day Close: {self._prev_day_close:.2f} | Gap: {gap * 100:.2f}%",
                    LogColor.GREEN,
                )
                self._on_buy_signal(bar)
            elif gap <= -self.config.gap_threshold_pct and self.config.allow_short:
                self._gap_traded_today = True
                self._log.info(
                    f"[{self.instrument_id}] SELL SIGNAL (Gap Down Continuation) | Close: {close:.2f} | "
                    f"Prev Day Close: {self._prev_day_close:.2f} | Gap: {gap * 100:.2f}%",
                    LogColor.RED,
                )
                self._on_sell_signal(bar)

        self._last_close = close

    # ── Order helpers ──────────────────────────────────────────────────────────

    def _on_buy_signal(self, bar: Bar) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            if pos.side == PositionSide.LONG:
                return
            self._close_position_base(pos, exit_kind=ExitReason.SIGNAL_REVERSAL)
            self.current_signal = None
            return
        if self.cache.orders_open(instrument_id=self.instrument_id):
            return
        if len(self.cache.positions_open()) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return
        self.current_signal = "BUY"
        self.bars_since_last_signal = 0
        order = self.order_factory.market(
            instrument_id=self.instrument_id, order_side=OrderSide.BUY,
            quantity=qty, time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    def _on_sell_signal(self, bar: Bar) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            if pos.side == PositionSide.SHORT:
                return
            self._close_position_base(pos, exit_kind=ExitReason.SIGNAL_REVERSAL)
            self.current_signal = None
            return
        if self.cache.orders_open(instrument_id=self.instrument_id):
            return
        if len(self.cache.positions_open()) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return
        self.current_signal = "SELL"
        self.bars_since_last_signal = 0
        order = self.order_factory.market(
            instrument_id=self.instrument_id, order_side=OrderSide.SELL,
            quantity=qty, time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    # ── Lifecycle callbacks ────────────────────────────────────────────────────

    def on_position_closed(self, event) -> None:
        super().on_position_closed(event)
        self.current_signal = None

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
