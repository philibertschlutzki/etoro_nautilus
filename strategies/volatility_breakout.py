from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from nautilus_trader.indicators import BollingerBands

class VolatilityBreakoutConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    bb_period: int = 20
    bb_std_dev: float = 2.0

class VolatilityBreakoutPumpStrategy(Strategy):
    """
    Kauft Ausbrüche nach oben (Pumps / Gaps), sobald der Preis das obere Bollinger Band 
    mit Schwung durchbricht. Schliesst die Position bei Rückkehr zum gleitenden Durchschnitt.
    """
    def __init__(self, config: VolatilityBreakoutConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        
        self.bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self.current_signal = None

    def on_start(self):
        self._log.info(f"📈 Starte Breakout Pump Rider auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bb.handle_bar(bar)

        if not self.bb.initialized:
            return
            
        close_price = float(bar.close)
        
        # Ausbruch nach oben (Pump/Gap) (FIXED)
        breakout_up = close_price > self.bb.upper
        # Momentum bricht zusammen (Rückfall auf die Mittellinie) (FIXED)
        momentum_lost = close_price < self.bb.middle

        if breakout_up and self.current_signal != "BUY":
            self._log.info(f"🟢 [{self.instrument_id}] VOLATILITY BREAKOUT (PUMP) | Close: {close_price:.5f}")
            self.current_signal = "BUY"
            
        elif momentum_lost and self.current_signal == "BUY":
            self._log.info(f"🔴 [{self.instrument_id}] MOMENTUM LOST. SELL SIGNAL | Close: {close_price:.5f}")
            self.current_signal = "SELL"
            
        elif self.current_signal == "SELL":
            self.current_signal = None