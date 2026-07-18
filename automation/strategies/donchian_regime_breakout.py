"""
automation/strategies/donchian_regime_breakout.py
===================================================
DonchianRegimeBreakoutStrategy — Issue #691 (SPEC_03).

Regime: Trend-Fortsetzung, aber nur bei aktivem Trend-Regime. Klassischer Donchian-Kanal-
Ausbruch (Kurs über das Hoch der letzten `donchian_period` Bars → Long), gegated durch ein
Trend-Regime-Filter (ADX über Schwelle, Option A). Der Kanal wird gegen die VORANGEGANGENEN
Bars geprüft (der aktuelle Bar wird erst NACH der Prüfung angehängt), sonst triggert jeder
neue Höchststand-Bar sich selbst.

Regime-Filter: Option B (EMA-Steigung) ist AKTIV. Der Trockenlauf (Issue #691-Validierung, echter
NautilusTrader-Engine-Lauf) hat verifiziert, dass `DirectionalMovement(period).value` in der
installierten NautilusTrader-Version (1.230.0) konstant `0.0` bleibt (nie initialisiert im Sinne
eines ADX-Werts — `.pos`/`.neg` liefern dagegen plausible, trend-abhängige Werte). Ein Regime-Gate
auf `adx.value >= adx_threshold` wäre daher IMMER `False` und die Strategie würde nie feuern —
exakt das bereits dokumentierte `AdxAtrMomentumStrategy`-"ADX-Initialisierungsproblem"
(`strategies.json`). Pitfall #9 des Implementierungs-Leitfadens (#688) sieht genau diesen Fall vor:
"ADX-Regime-Filter durch EMA-Steigung ersetzen". Option A (ADX) bleibt auskommentiert als
Referenz/Re-Evaluationspunkt, falls eine künftige NautilusTrader-Version `DirectionalMovement.value`
korrekt berechnet.

Exit-Logik (via HourlyStrategyBase): ATR-Trailing-Stop + Zeit-Exit (~1 Handelstag).
"""
import collections
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import DirectionalMovement, ExponentialMovingAverage

from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig
from automation.momentum_ls_allocator import MomentumLSAllocator


class DonchianRegimeBreakoutConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    donchian_period: int = 20
    adx_period: int = 14
    adx_threshold: float = 20.0
    ema_period: int = 50
    allow_short: bool = False
    cooldown_bars: int = 6
    atr_period: int = 14
    atr_trailing_multiplier: float = 2.5
    max_bars_in_trade: int = 24
    trade_amount_pct: float = 15.0


class DonchianRegimeBreakoutStrategy(HourlyStrategyBase):
    """
    ADX-gegateter Donchian-Kanal-Ausbruch mit ATR-Trailing-Stop und ~1-Handelstag-Zeit-Exit.
    """

    def __init__(self, config: DonchianRegimeBreakoutConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.adx = DirectionalMovement(config.adx_period)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self._ema_prev: float | None = None
        self._high_q = collections.deque(maxlen=config.donchian_period)
        self._low_q = collections.deque(maxlen=config.donchian_period)
        self.current_signal: str | None = None
        self.bars_since_last_signal: int = 9999

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte DonchianRegimeBreakout-Strategie auf {self.instrument_id}", LogColor.GREEN)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bars_since_last_signal += 1
        self.adx.handle_bar(bar)
        self.ema.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return

        high, low, close = float(bar.high), float(bar.low), float(bar.close)

        # Kanal der VORHERIGEN Bars — vor Anhängen des aktuellen Bars prüfen.
        prev_high = max(self._high_q) if len(self._high_q) == self.config.donchian_period else None
        prev_low = min(self._low_q) if len(self._low_q) == self.config.donchian_period else None
        self._high_q.append(high)
        self._low_q.append(low)

        if prev_high is None or not self.ema.initialized or self._ema_prev is None:
            self._ema_prev = self.ema.value if self.ema.initialized else self._ema_prev
            return

        # Regime-Filter Option B (AKTIV — siehe Modul-Docstring): EMA-Steigung.
        long_regime = self.ema.value > self._ema_prev
        short_regime = self.ema.value < self._ema_prev
        # Option A (ADX, deaktiviert — `DirectionalMovement.value` liefert konstant 0.0 in der
        # installierten NautilusTrader-Version, siehe Modul-Docstring):
        #   regime_ok = self.adx.value >= self.config.adx_threshold
        #   long_regime = short_regime = regime_ok
        self._ema_prev = self.ema.value

        can_signal = self.current_signal is None or self.bars_since_last_signal >= self.config.cooldown_bars
        if not can_signal:
            return

        if close > prev_high and long_regime:
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (Donchian Breakout, Regime ok) | Close: {close:.2f} | "
                f"Prev High: {prev_high:.2f} | EMA: {self.ema.value:.2f}",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)
        elif close < prev_low and short_regime and self.config.allow_short:
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (Donchian Breakdown, Regime ok) | Close: {close:.2f} | "
                f"Prev Low: {prev_low:.2f} | EMA: {self.ema.value:.2f}",
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
            self._close_position_base(pos)
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
            self._close_position_base(pos)
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
