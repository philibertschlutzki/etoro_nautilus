from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import RelativeStrengthIndex

class TrendPullbackConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    ema_period: int = 200
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0


class TrendPullbackStrategy(Strategy):
    """
    Trend & Pullback Strategie für stabile Assets.
    Bestimmt den übergeordneten Trend (z.B. EMA 200) und kauft bei kurzfristigen Pullbacks (z.B. RSI überverkauft).
    """
    def __init__(self, config: TrendPullbackConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.current_signal = None

    def on_start(self):
        self._log.info(f"🚀 Starte Trend & Pullback auf {self.instrument_id}")
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        pass

    def on_bar(self, bar: Bar):
        self.ema.handle_bar(bar)
        self.rsi.handle_bar(bar)

        if not self.ema.initialized or not self.rsi.initialized:
            return

        close_price = float(bar.close)
        ema_val = self.ema.value
        rsi_val = self.rsi.value

        self._log.info(
            f"📊 [{self.instrument_id}] BAR GESCHLOSSEN | Close: {close_price:.2f} | "
            f"EMA({self.config.ema_period}): {ema_val:.2f} | "
            f"RSI({self.config.rsi_period}): {rsi_val:.2f}"
        )

        # Aufwärtstrend: Close > EMA. Wir suchen Pullbacks: RSI < oversold
        if close_price > ema_val and rsi_val < self.config.rsi_oversold and self.current_signal != "BUY":
            self._log.info(f"🟢 [{self.instrument_id}] BUY SIGNAL (Trend Up & RSI Oversold)")
            self.current_signal = "BUY"

        # Abwärtstrend: Close < EMA. Wir suchen Bounces: RSI > overbought
        elif close_price < ema_val and rsi_val > self.config.rsi_overbought and self.current_signal != "SELL":
            self._log.info(f"🔴 [{self.instrument_id}] SELL SIGNAL (Trend Down & RSI Overbought)")
            self.current_signal = "SELL"

    def on_stop(self):
        self._log.info(f"🛑 Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
