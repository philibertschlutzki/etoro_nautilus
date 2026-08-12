from nautilus_trader.common.enums import LogColor
from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.indicators import MovingAverageConvergenceDivergence
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import BollingerBands
from nautilus_trader.indicators import AverageTrueRange

from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig, ExitReason
from automation.momentum_ls_allocator import MomentumLSAllocator


import collections

class ComboTrendVwapConfig(HourlyStrategyConfig, kw_only=True, frozen=True):
    sma_period: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal_period: int = 9
    bb_period: int = 20
    bb_std_dev: float = 2.0
    atr_period: int = 14
    atr_multiplier: float = 0.5
    bb_touch_window: int = 24
    cooldown_bars: int = 12
    allow_short: bool = False
    vwap_period: int = 20
    require_vwap_confirmation: bool = True
    require_bb_touch: bool = True
    # Issue #446 — zuvor gesampelt, aber weder Config-Feld noch verdrahtet (Phantom). Jetzt echtes
    # Feld: Toleranzband um die SMA für die Trend-Gates. Default 0.02 reproduziert exakt das frühere
    # Hardcoding (close > sma*0.98 bzw. close < sma*1.02).
    trend_tolerance_pct: float = 0.02


class ComboTrendVwapStrategy(HourlyStrategyBase):
    """Generische Trend+Momentum+Volatilitäts+VWAP-Strategie mit ATR Trailing Stop / 48h Time-Exit."""

    def __init__(self, config: ComboTrendVwapConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config, allocator)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.sma = SimpleMovingAverage(config.sma_period)
        self.macd = MovingAverageConvergenceDivergence(config.macd_fast, config.macd_slow)
        self.macd_signal = ExponentialMovingAverage(config.macd_signal_period)
        self.bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self.atr = AverageTrueRange(config.atr_period)

        self.current_signal: str | None = None
        self.bars_since_last_signal: int = 999
        self.vwap_queue = collections.deque(maxlen=self.config.vwap_period)
        self._running_vp = 0.0
        self._running_vol = 0.0
        self.current_vwap = 0.0
        self.bars_since_bb_touch: int = 999

    def on_start(self):
        super().on_start()
        self._log.info(
            f"Starte ComboTrendVwapStrategy auf {self.instrument_id}", LogColor.GREEN
        )
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bars_since_last_signal += 1
        typical_price = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
        volume = float(bar.volume)

        if volume > 0:
            if len(self.vwap_queue) == self.config.vwap_period:
                old_vp, old_vol = self.vwap_queue.popleft()
                self._running_vp -= old_vp
                self._running_vol -= old_vol

            new_vp = typical_price * volume
            self.vwap_queue.append((new_vp, volume))
            self._running_vp += new_vp
            self._running_vol += volume

        if self._running_vol > 0:
            self.current_vwap = self._running_vp / self._running_vol

        self.sma.handle_bar(bar)
        self.macd.handle_bar(bar)
        if self.macd.initialized:
            self.macd_signal.update_raw(self.macd.value)
        self.bb.handle_bar(bar)
        self.atr.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return

        if not (
            self.sma.initialized
            and self.macd.initialized
            and self.macd_signal.initialized
            and self.bb.initialized
            and self.atr.initialized
        ):
            return

        close_price = float(bar.close)

        # Issue #446 — Trend-Toleranzband aus dem (jetzt verdrahteten) Config-Feld statt hartem
        # 0.98/1.02. Default 0.02 ⇒ verhaltensidentisch zur bisherigen Logik.
        trend_tol = self.config.trend_tolerance_pct
        trend_bullish = close_price > (self.sma.value * (1.0 - trend_tol))
        momentum_bullish = self.macd.value > self.macd_signal.value
        atr_tolerance = self.atr.value * self.config.atr_multiplier

        trend_bearish = close_price < (self.sma.value * (1.0 + trend_tol))
        momentum_bearish = self.macd.value < self.macd_signal.value

        if close_price <= (self.bb.lower + atr_tolerance) or close_price >= (self.bb.upper - atr_tolerance):
            self.bars_since_bb_touch = 0
        else:
            self.bars_since_bb_touch += 1

        vwap_ready = len(self.vwap_queue) == self.config.vwap_period

        vwap_confirmed = vwap_ready and self._running_vol > 0 and close_price > self.current_vwap

        vwap_bearish_confirmed = vwap_ready and self._running_vol > 0 and close_price < self.current_vwap

        vwap_ok = (not self.config.require_vwap_confirmation) or vwap_confirmed
        vwap_bearish_ok = (not self.config.require_vwap_confirmation) or vwap_bearish_confirmed
        bb_ok = (not self.config.require_bb_touch) or (self.bars_since_bb_touch <= self.config.bb_touch_window)

        if (
            trend_bullish
            and momentum_bullish
            and bb_ok
            and vwap_ok
            and self.current_signal != "BUY"
            and self.bars_since_last_signal >= self.config.cooldown_bars
        ):
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL ComboTrendVWAP | "
                f"Close: {close_price:.2f} | SMA({self.config.sma_period}): {self.sma.value:.2f} | "
                f"MACD: {self.macd.value:.4f} / {self.macd_signal.value:.4f} | "
                f"BB lower: {self.bb.lower:.2f} | VWAP: {self.current_vwap:.2f}",
                LogColor.GREEN,
            )
            self._on_buy_signal(bar)

        elif (
            self.config.allow_short
            and trend_bearish
            and momentum_bearish
            and bb_ok
            and vwap_bearish_ok
            and self.current_signal != "SELL"
            and self.bars_since_last_signal >= self.config.cooldown_bars
        ):
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL ComboTrendVWAP | "
                f"Close: {close_price:.2f} | SMA({self.config.sma_period}): {self.sma.value:.2f} | "
                f"MACD: {self.macd.value:.4f} / {self.macd_signal.value:.4f} | "
                f"BB upper: {self.bb.upper:.2f} | VWAP: {self.current_vwap:.2f}",
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
        if len(self.cache.positions_open()) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return

        self.current_signal = "BUY"
        self.bars_since_last_signal = 0
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
            self.current_signal = None
            return
        if len(self.cache.positions_open()) >= self.config.max_open_positions:
            return
        qty = self._compute_quantity(bar)
        if qty is None:
            return

        self.current_signal = "SELL"
        self.bars_since_last_signal = 0
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=qty,
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    # ── Lifecycle callbacks ────────────────────────────────────────────────────

    def on_position_closed(self, event) -> None:
        super().on_position_closed(event)
        self.current_signal = None

    def on_stop(self):
        self._log.info(f"Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
