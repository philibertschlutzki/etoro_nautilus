"""
automation/strategies/squeeze_breakout.py
==========================================
SqueezeBreakoutStrategy — Issue #689 (SPEC_01).

Regime: Volatilitäts-Expansion nach Kontraktion (TTM-Squeeze-Prinzip). Solange die
Bollinger-Bänder vollständig innerhalb der Keltner-Kanäle liegen, ist die Volatilität
komprimiert ("Squeeze"). Verlässt die BB den Keltner-Kanal nach einer ausreichend langen
Squeeze-Phase, ist das der Expansions-Trigger; die Richtung ergibt sich aus der Lage von
`close` relativ zur BB-Mittellinie.

Exit-Logik (via HourlyStrategyBase): ATR-Trailing-Stop + Zeit-Exit (~1 Handelstag).
"""
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import BollingerBands, KeltnerChannel

from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig
from automation.momentum_ls_allocator import MomentumLSAllocator


class SqueezeBreakoutConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    bb_period: int = 20
    bb_std_dev: float = 2.0
    keltner_period: int = 20
    keltner_multiplier: float = 1.5
    min_squeeze_bars: int = 6
    allow_short: bool = False
    cooldown_bars: int = 6
    atr_period: int = 14
    atr_trailing_multiplier: float = 2.0
    max_bars_in_trade: int = 24
    trade_amount_pct: float = 15.0


class SqueezeBreakoutStrategy(HourlyStrategyBase):
    """
    Volatilitäts-Squeeze-Release-Strategie (Bollinger-innerhalb-Keltner) mit ATR-Trailing-Stop
    und ~1-Handelstag-Zeit-Exit.
    """

    def __init__(self, config: SqueezeBreakoutConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self.keltner = KeltnerChannel(period=config.keltner_period, k_multiplier=config.keltner_multiplier)
        self.current_signal: str | None = None
        self.bars_since_last_signal: int = 9999
        self._squeeze_count: int = 0

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte SqueezeBreakout-Strategie auf {self.instrument_id}", LogColor.GREEN)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bars_since_last_signal += 1
        self.bb.handle_bar(bar)
        self.keltner.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return
        if not (self.bb.initialized and self.keltner.initialized):
            return

        close = float(bar.close)
        squeeze_on = (self.bb.lower > self.keltner.lower) and (self.bb.upper < self.keltner.upper)

        # Release VOR dem Zähler-Update prüfen (Pitfall: sonst verschiebt sich min_squeeze_bars um 1).
        released = (self._squeeze_count >= self.config.min_squeeze_bars) and (not squeeze_on)
        self._squeeze_count = self._squeeze_count + 1 if squeeze_on else 0

        can_signal = self.current_signal is None or self.bars_since_last_signal >= self.config.cooldown_bars
        if not (released and can_signal):
            return

        if close > self.bb.middle:
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (Squeeze Release Up) | Close: {close:.2f} | "
                f"BB middle: {self.bb.middle:.2f}",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)
        elif close < self.bb.middle and self.config.allow_short:
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (Squeeze Release Down) | Close: {close:.2f} | "
                f"BB middle: {self.bb.middle:.2f}",
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
