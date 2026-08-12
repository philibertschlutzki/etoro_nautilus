"""
automation/strategies/adx_atr_momentum.py
===========================================
AdxAtrMomentumStrategy — Issue #699.

Regime-Filter: Option B (EMA-Steigung) ist AKTIV. Der #691-Trockenlauf (echter NautilusTrader-
Engine-Lauf, siehe donchian_regime_breakout.py) hat verifiziert, dass `DirectionalMovement(period)
.value` in der installierten NautilusTrader-Version (1.230.0) konstant `0.0` bleibt (nie
initialisiert im Sinne eines ADX-Werts). Das ursprüngliche Regime-Gate `adx_value > 25` war daher
IMMER `False` — die Strategie eröffnete NIE eine Position (0 evaluable OOS-Trades, exakt das in
strategies.json dokumentierte "ADX-Initialisierungsproblem", #669/#699). Fix: derselbe Weg wie bei
DonchianRegimeBreakout (#691) — das tote ADX-Gate durch eine EMA-Steigungs-Bestätigung ersetzt.
Option A (ADX) bleibt auskommentiert als Referenz/Re-Evaluationspunkt, falls eine künftige
NautilusTrader-Version `DirectionalMovement.value` korrekt berechnet.
"""
from nautilus_trader.common.enums import LogColor
from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig, ExitReason
from automation.momentum_ls_allocator import MomentumLSAllocator

from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import DirectionalMovement


class AdxAtrMomentumConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    adx_period: int = 14
    ema_period: int = 50
    atr_multiplier: float = 2.0


class AdxAtrMomentumStrategy(HourlyStrategyBase):
    """
    Trendfolge-Strategie basierend auf EMA (Trendrichtung + -stärke via Steigung) und
    ATR (Trailing Stop). Mit echter Orderausführung.
    """

    def __init__(self, config: AdxAtrMomentumConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.adx = DirectionalMovement(config.adx_period)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.atr = AverageTrueRange(config.atr_period)
        self._ema_prev: float | None = None

        # Issue #860 — Bookkeeping fuer den Flip-Reset in _on_buy_signal/_on_sell_signal (die
        # tatsaechliche Positions-/Exit-Verwaltung liegt vollstaendig in HourlyStrategyBase).
        self.current_position: str | None = None
        self.entry_price = 0.0
        self.trailing_stop = 0.0

    def on_start(self):
        self._log.info(
            f"Starte ADX+ATR Momentum Strategie auf {self.instrument_id}", LogColor.GREEN
        )
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.adx.handle_bar(bar)
        self.ema.handle_bar(bar)
        self.atr.handle_bar(bar)

        # Issue #860 — der Basisklassen-Exit-Check (Zeitbox, ATR-Trailing-Stop, Watchdog) muss die
        # ERSTE Anweisung nach dem Indikator-Update sein, VOR dem Indikator-Guard darunter. Vorher
        # rief diese Strategie `_check_exits_and_update` NIRGENDS auf (0 von 15 Strategien-Guards
        # verletzten diesen Vertrag ausser dieser) — während einer Indikator-Warmup-Phase (z. B.
        # nach einem Fold-Wechsel oder Reconnect mit rehydrierter Position, #717/GR-04) lief weder
        # der Bar-Zähler noch der Zeit-Exit. Folgenlos, solange die Strategie 0 Positionen eröffnet
        # (#870); sobald das behoben ist, wird dieser Defekt aktiv.
        if self._check_exits_and_update(bar):
            return

        if not (self.adx.initialized and self.ema.initialized and self.atr.initialized):
            return

        close_price = float(bar.close)
        ema_value = self.ema.value
        if self._ema_prev is None:
            self._ema_prev = ema_value
            return
        # Regime-Filter Option B (AKTIV — siehe Modul-Docstring): EMA-Steigung als
        # Trendstärke-Bestätigung statt des toten ADX-Werts.
        rising_ema = ema_value > self._ema_prev
        falling_ema = ema_value < self._ema_prev
        # Option A (ADX, deaktiviert — `DirectionalMovement.value` liefert konstant 0.0 in der
        # installierten NautilusTrader-Version, siehe Modul-Docstring):
        #   adx_value = self.adx.value
        #   rising_ema = falling_ema = adx_value > 25
        self._ema_prev = ema_value

        # Issue #860 Fix Punkt 3 — der historische, EIGENE Trailing-Stop-Pfad (vor #699/vor der
        # HourlyStrategyBase-Migration) ist entfernt: er referenzierte `self._close_position`, eine
        # auf HourlyStrategyBase nie existierende Methode (jeder Aufruf hätte mit AttributeError
        # abgebrochen), und der ATR-Trailing-Stop/Zeit-Exit laufen bereits vollständig über
        # `_check_exits_and_update` oben. Die Positions-/Flip-Behandlung bleibt wie bei allen
        # übrigen Strategien (siehe rsi2_reversion.py) Aufgabe von `_on_buy_signal`/
        # `_on_sell_signal` selbst (cache-basiert), kein zusätzliches Gate hier.

        # Entry logic
        if close_price > ema_value and rising_ema:
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL AdxAtrMomentum | "
                f"Close: {close_price:.2f} > EMA: {ema_value:.2f} (rising)",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)

        elif close_price < ema_value and falling_ema:
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL AdxAtrMomentum | "
                f"Close: {close_price:.2f} < EMA: {ema_value:.2f} (falling)",
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
            # State zurücksetzen — ermöglicht Neueinstieg auf nächster Bar (kein Flat-Lock)
            self.current_position = None
            self.entry_price = 0.0
            self.trailing_stop = 0.0
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
            # State zurücksetzen — ermöglicht Neueinstieg auf nächster Bar (kein Flat-Lock)
            self.current_position = None
            self.entry_price = 0.0
            self.trailing_stop = 0.0
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
