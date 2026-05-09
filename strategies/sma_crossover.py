from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.indicators import SimpleMovingAverage


class SmaCrossoverConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    sma_period: int = 5


class SmaCrossoverStrategy(Strategy):
    """
    Klassische SMA Crossover Strategie.
    """
    def __init__(self, config: SmaCrossoverConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.sma = SimpleMovingAverage(config.sma_period)
        self.current_signal = None

    def on_start(self):
        self._log.info(f"🚀 Starte SMA Crossover auf {self.instrument_id}")
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        pass

    def on_bar(self, bar: Bar):
        self.sma.handle_bar(bar)

        if not self.sma.initialized:
            return
            
        close_price = float(bar.close)
        
        self._log.info(
            f"📊 [{self.instrument_id}] BAR GESCHLOSSEN | Close: {close_price:.2f} | "
            f"SMA({self.config.sma_period}): {self.sma.value:.2f}"
        )
        
        # Best Practice ist Portfolio, aber mangels Execution nutzen wir hier den internen State
        if close_price > self.sma.value and self.current_signal != "BUY":
            self._log.info(f"🟢 [{self.instrument_id}] BUY SIGNAL (Close > SMA)")
            self.current_signal = "BUY"
            
        elif close_price < self.sma.value and self.current_signal != "SELL":
            self._log.info(f"🔴 [{self.instrument_id}] SELL SIGNAL (Close < SMA)")
            self.current_signal = "SELL"

    def on_stop(self):
        self._log.info(f"🛑 Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
