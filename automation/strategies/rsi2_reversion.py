"""
automation/strategies/rsi2_reversion.py
=========================================
Rsi2ReversionStrategy — Issue #692 (SPEC_04).

Regime: Kurzfrist-Reversion im übergeordneten Trend (Connors-RSI-2-Prinzip). Ein sehr
kurzer RSI misst kurzfristige Überdehnung, ein langer EMA-Filter gibt die übergeordnete
Richtung vor. Kauf bei tiefem RSI im Aufwärts-Regime (Pullback), Verkauf bei hohem RSI im
Abwärts-Regime (Bounce, symmetrisch zum Long-Fall).

Exit-Logik (via HourlyStrategyBase): ATR-Trailing-Stop + Zeit-Exit (~1 Handelstag).
"""
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import RelativeStrengthIndex, ExponentialMovingAverage

from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig
from automation.momentum_ls_allocator import MomentumLSAllocator


class Rsi2ReversionConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    rsi_period: int = 2
    rsi_oversold: float = 10.0
    rsi_overbought: float = 90.0
    ema_period: int = 100
    allow_short: bool = False
    cooldown_bars: int = 3
    atr_period: int = 14
    atr_trailing_multiplier: float = 1.5
    max_bars_in_trade: int = 24
    trade_amount_pct: float = 15.0


class Rsi2ReversionStrategy(HourlyStrategyBase):
    """
    Connors-Stil RSI(2)-Pullback-/Bounce-Reversion mit EMA-Trend-Filter, ATR-Trailing-Stop
    und ~1-Handelstag-Zeit-Exit.
    """

    def __init__(self, config: Rsi2ReversionConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.current_signal: str | None = None
        self.bars_since_last_signal: int = 9999

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte Rsi2Reversion-Strategie auf {self.instrument_id}", LogColor.GREEN)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bars_since_last_signal += 1
        self.rsi.handle_bar(bar)
        self.ema.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return
        if not (self.rsi.initialized and self.ema.initialized):
            return

        close = float(bar.close)
        r = self.rsi.value
        up_trend = close > self.ema.value

        can_signal = self.current_signal is None or self.bars_since_last_signal >= self.config.cooldown_bars
        if not can_signal:
            return

        if r <= self.config.rsi_oversold and up_trend:
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (RSI2 Pullback) | Close: {close:.2f} | "
                f"RSI: {r:.1f} | EMA: {self.ema.value:.2f}",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)
        elif r >= self.config.rsi_overbought and (not up_trend) and self.config.allow_short:
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (RSI2 Bounce) | Close: {close:.2f} | "
                f"RSI: {r:.1f} | EMA: {self.ema.value:.2f}",
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
