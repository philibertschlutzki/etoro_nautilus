"""
automation/strategies/opening_range_breakout.py
=================================================
OpeningRangeBreakoutStrategy — Issue #690 (SPEC_02).

Regime: Momentum-Ignition am Tagesbeginn (Opening-Range-Breakout). Die ersten `or_bars`
Bars eines neuen Kalendertags (erkannt über `pd.Timestamp(bar.ts_init).day`, identisch zur
Basisklasse) definieren eine Range (Hoch/Tief). Bricht der Kurs danach über das Range-Hoch
(+ATR-Puffer), ist das ein Long-Signal; unter das Range-Tief analog Short.

Handelstag-Anker (Issue #922, ersetzt durch Issue #1356 / GH #1252): ``opening_range_session_anchor=
'trading_day'`` (Default) bestimmt den Handelstag in BÖRSEN-LOKALZEIT aus dem Config-Feld
``session_window`` (``{"tz": "America/New_York", "open": "09:30", "close": "16:00"}``, vom Backtest-Runner
bzw. Live-Bot aus ``backtest.json`` aufgelöst): die Range-Startkerze ist je Handelstag die Kerze, die den
lokalen Open überlappt (EDT: 13:00-UTC-Kerze, EST: 14:00-UTC-Kerze), Kerzen ausserhalb der Session
(Pre-/Post-Market, Wochenende, Feiertag) bilden keine Range. Die frühere UTC-Stunden-Konstante
(``opening_range_session_open_hour``, 13) war NYSE-Open nur in EDT — im Winter bildete die Range sich aus
08:00-11:00 ET — und entfällt ersatzlos. Ohne ``session_window`` (24/7-Märkte) ist der Handelstag der
UTC-Kalendertag (identisch zu ``'calendar_day'``, dem bit-identischen Alt-Anker).

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
from automation.session_windows import (
    SessionWindow,
    session_window_from_param,
    trading_day_of_candle,
)


def session_day_key(ts_ns: int, *, anchor: str, session_window: SessionWindow | None = None,
                    bar_interval_ns: int = 0):
    """Reine Funktion (ohne NautilusTrader-Objekte testbar): zwei Bars gehören zur selben "Opening Range",
    wenn sie denselben Schlüssel liefern. ``ts_ns`` ist der ZEITSTEMPEL des Bar-ENDES (NautilusTrader-Zeitbars
    tragen ``ts_event`` = Kerzenschluss); die Kerze ist ``[ts_ns − bar_interval_ns, ts_ns)``.

    ``anchor == 'calendar_day'`` — bit-identisches Alt-Verhalten: ``pd.Timestamp(ts_ns).day`` (Wechsel um
    Mitternacht UTC, unabhängig von jeder Handelszeit).

    ``anchor == 'trading_day'`` (Issue #1356) — mit ``session_window``: das LOKALE Datum des Handelstags, dessen
    Session die Kerze schneidet; ``None``, wenn die Kerze keine Session schneidet (der Aufrufer bildet dann
    keine Range). Ohne ``session_window`` ⇒ wie ``'calendar_day'``.

    ``'session_open_hour'`` (UTC-Stunden-Konstante, DST-blind) entfällt ⇒ ``ValueError``."""
    if anchor == "session_open_hour":
        raise ValueError(
            "opening_range_session_anchor='session_open_hour' entfällt (Issue #1356): eine UTC-Stunde ist "
            "NYSE-Open nur in EDT — 'trading_day' mit session_window (Börsen-Lokalzeit) verwenden.")
    if anchor not in ("calendar_day", "trading_day"):
        raise ValueError(f"opening_range_session_anchor={anchor!r} unbekannt (calendar_day|trading_day).")
    if anchor == "trading_day" and session_window is not None:
        return trading_day_of_candle(ts_ns - bar_interval_ns, ts_ns, session_window)
    return pd.Timestamp(ts_ns).day


class OpeningRangeBreakoutConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    or_bars: int = 3
    or_atr_buffer: float = 0.25
    allow_short: bool = False
    cooldown_bars: int = 6
    atr_period: int = 14
    atr_trailing_multiplier: float = 2.0
    max_bars_in_trade: int = 24
    max_daily_trades: int | None = 2
    trade_amount_pct: float = 15.0
    # Issue #1356 (GH #1252) — 'trading_day' (Default): Handelstag in Börsen-Lokalzeit aus dem Basis-Feld
    # ``session_window`` (Range-Startkerze = Kerze, die den lokalen Open überlappt; ohne Fenster ⇒ UTC-
    # Kalendertag). 'calendar_day' = bit-identisches Alt-Verhalten (``pd.Timestamp(ts).day``). Die
    # UTC-Stunden-Konstante ``opening_range_session_open_hour`` (+ Anker 'session_open_hour') entfällt.
    opening_range_session_anchor: str = "trading_day"


class OpeningRangeBreakoutStrategy(HourlyStrategyBase):
    """
    Opening-Range-Breakout (ORB) mit ATR-Puffer, ATR-Trailing-Stop und ~1-Handelstag-Zeit-Exit.
    """

    def __init__(self, config: OpeningRangeBreakoutConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.atr = AverageTrueRange(config.atr_period)
        self.current_signal: str | None = None
        self.bars_since_last_signal: int = 9999
        self._or_day = None   # Schlüssel von ``session_day_key`` (int | date)
        self._or_bar_count: int = 0
        self._or_high: float | None = None
        self._or_low: float | None = None

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte OpeningRangeBreakout-Strategie auf {self.instrument_id}", LogColor.GREEN)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bars_since_last_signal += 1
        self.atr.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return

        day = session_day_key(
            bar.ts_event, anchor=self.config.opening_range_session_anchor,
            session_window=self._session_window, bar_interval_ns=self._bar_interval_ns)
        if day is None:
            # Issue #1356 — die Kerze schneidet keine Session (Pre-/Post-Market, Wochenende, Feiertag):
            # sie bildet weder Range noch Signal.
            return
        if day != self._or_day:
            self._or_day = day
            self._or_bar_count = 0
            self._or_high = None
            self._or_low = None

        high, low, close = float(bar.high), float(bar.low), float(bar.close)
        if self._or_bar_count < self.config.or_bars:
            self._or_high = high if self._or_high is None else max(self._or_high, high)
            self._or_low = low if self._or_low is None else min(self._or_low, low)
            self._or_bar_count += 1
            return

        if not self.atr.initialized:
            return
        can_signal = self.current_signal is None or self.bars_since_last_signal >= self.config.cooldown_bars
        if not can_signal:
            return

        buf = self.config.or_atr_buffer * self.atr.value
        if close > self._or_high + buf:
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (Opening Range Breakout Up) | Close: {close:.2f} | "
                f"OR High: {self._or_high:.2f} | Buffer: {buf:.4f}",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)
        elif close < self._or_low - buf and self.config.allow_short:
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (Opening Range Breakout Down) | Close: {close:.2f} | "
                f"OR Low: {self._or_low:.2f} | Buffer: {buf:.4f}",
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
            self._close_position_base(pos, exit_kind=ExitReason.SIGNAL_REVERSAL)
            self.current_signal = None
            return
        if self.cache.orders_open(instrument_id=self.instrument_id):
            return
        if len(self.cache.positions_open(instrument_id=self.instrument_id)) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return
        self.current_signal = "BUY"
        self.bars_since_last_signal = 0
        order = self.order_factory.market(
            instrument_id=self.instrument_id, order_side=OrderSide.BUY,
            quantity=qty, time_in_force=TimeInForce.GTC,
            tags=self._entry_order_tags(bar),
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
        if len(self.cache.positions_open(instrument_id=self.instrument_id)) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return
        self.current_signal = "SELL"
        self.bars_since_last_signal = 0
        order = self.order_factory.market(
            instrument_id=self.instrument_id, order_side=OrderSide.SELL,
            quantity=qty, time_in_force=TimeInForce.GTC,
            tags=self._entry_order_tags(bar),
        )
        self.submit_order(order)

    # ── Lifecycle callbacks ────────────────────────────────────────────────────

    def on_position_closed(self, event) -> None:
        super().on_position_closed(event)
        self.current_signal = None

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
