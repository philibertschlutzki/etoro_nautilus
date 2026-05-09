from collections import deque
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

class DynamicBreakoutConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    volume_sma_period: int = 20
    volume_multiplier: float = 2.5
    price_breakout_period: int = 20


class DynamicBreakoutStrategy(Strategy):
    """
    Dynamische Breakout-Strategie für Forex/Rohstoffe (NATGAS),
    basierend auf extremen Volumen-Spikes und Price-Breakout.
    """
    def __init__(self, config: DynamicBreakoutConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        self.volume_history = deque(maxlen=config.volume_sma_period)
        self.high_history = deque(maxlen=config.price_breakout_period)
        self.low_history = deque(maxlen=config.price_breakout_period)

        self.current_signal = None

    def on_start(self):
        self._log.info(f"🚀 Starte Dynamic Breakout auf {self.instrument_id}")
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        pass

    def on_bar(self, bar: Bar):
        volume = float(bar.volume)
        high = float(bar.high)
        low = float(bar.low)
        close_price = float(bar.close)

        self.volume_history.append(volume)
        self.high_history.append(high)
        self.low_history.append(low)

        if len(self.volume_history) < self.config.volume_sma_period or len(self.high_history) < self.config.price_breakout_period:
            return

        avg_volume = sum(self.volume_history) / len(self.volume_history)
        period_high = max(self.high_history)
        period_low = min(self.low_history)

        self._log.info(
            f"📊 [{self.instrument_id}] BAR GESCHLOSSEN | Close: {close_price:.2f} | "
            f"Volume: {volume:.2f} (Avg: {avg_volume:.2f}) | "
            f"Highs({self.config.price_breakout_period}): {period_high:.2f} | "
            f"Lows({self.config.price_breakout_period}): {period_low:.2f}"
        )

        volume_spike = volume > (avg_volume * self.config.volume_multiplier)

        if volume_spike and close_price >= period_high and self.current_signal != "BUY":
            self._log.info(f"🟢 [{self.instrument_id}] BUY SIGNAL (Volume Spike & Price Breakout High)")
            self.current_signal = "BUY"

        elif volume_spike and close_price <= period_low and self.current_signal != "SELL":
            self._log.info(f"🔴 [{self.instrument_id}] SELL SIGNAL (Volume Spike & Price Breakout Low)")
            self.current_signal = "SELL"

    def on_stop(self):
        self._log.info(f"🛑 Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
