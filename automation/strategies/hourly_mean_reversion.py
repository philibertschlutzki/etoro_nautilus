"""
automation/strategies/hourly_mean_reversion.py
===============================================
HourlyMeanReversionStrategy — optimised for 1h candle data.

Entry logic:
  - BUY when close < Keltner lower band (oversold, expect reversion to mean)
  - SELL when close > Keltner upper band (overbought, expect reversion to mean)

Exit logic (via HourlyStrategyBase):
  - ATR Trailing Stop: 1.5x ATR
  - Time-based exit: 48 bars (= 48 hours with 1h candles)

Parameters (tuned for hourly data):
  - keltner_period: 10 (vs 20 in MeanReversionStrategy)
  - keltner_multiplier: 1.5
  - keltner_atr_period: 10
"""
from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.indicators import KeltnerChannel

from automation.strategies.hourly_strategy_base import HourlyStrategyBase


class HourlyMeanReversionConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    keltner_period: int = 10
    keltner_atr_period: int = 10
    keltner_multiplier: float = 1.5
    trade_amount_usd: float = 1500.0
    max_open_positions: int = 1


class HourlyMeanReversionStrategy(HourlyStrategyBase):
    """
    Mean Reversion strategy optimised for 1h candles using Keltner Channel(10, 1.5).
    Inherits ATR Trailing Stop (1.5x) and 48-bar Time-based Exit from HourlyStrategyBase.
    """

    def __init__(self, config: HourlyMeanReversionConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.keltner = KeltnerChannel(
            period=config.keltner_period,
            k_multiplier=config.keltner_multiplier,
        )
        self.current_signal: str | None = None

    def on_start(self):
        super().on_start()
        self._log.info(f"Starte HourlyMeanReversion auf {self.instrument_id}")
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.keltner.handle_bar(bar)

        if self._check_exits_and_update(bar):
            return

        if not self.keltner.initialized:
            return

        close_price = float(bar.close)
        lower_band = self.keltner.lower
        upper_band = self.keltner.upper

        if close_price < lower_band and self.current_signal != "BUY":
            self._log.info(
                f"[{self.instrument_id}] BUY SIGNAL (Close < Keltner Lower) "
                f"Close: {close_price:.2f} Lower: {lower_band:.2f}"
            )
            self.current_signal = "BUY"
            self._on_buy_signal(bar)

        elif close_price > upper_band and self.current_signal != "SELL":
            self._log.info(
                f"[{self.instrument_id}] SELL SIGNAL (Close > Keltner Upper) "
                f"Close: {close_price:.2f} Upper: {upper_band:.2f}"
            )
            self.current_signal = "SELL"
            self._on_sell_signal(bar)

    def _on_buy_signal(self, bar: Bar) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if positions:
            pos = positions[0]
            if pos.side == PositionSide.LONG:
                return
            self._close_position(pos)
            self.current_signal = None
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
            self.current_signal = None
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

    def on_order_filled(self, event) -> None:
        self._log.info(f"[{self.instrument_id}] OrderFilled: {event}")

    def on_order_rejected(self, event) -> None:
        self._log.warning(f"[{self.instrument_id}] OrderRejected: {event}")

    def on_stop(self):
        self._log.info(f"HourlyMeanReversion auf {self.instrument_id} gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
