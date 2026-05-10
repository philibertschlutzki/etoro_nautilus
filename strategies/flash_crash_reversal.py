from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

# Indikatoren
from nautilus_trader.indicators import BollingerBands
from nautilus_trader.indicators import RelativeStrengthIndex

class FlashCrashReversalConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    bb_period: int = 20
    bb_std_dev: float = 2.5  # Etwas weiter gefasst für volatile Werte
    rsi_period: int = 14
    rsi_oversold: float = 25.0 # Extremwert für Krypto-Crashes
    rsi_overbought: float = 70.0

class FlashCrashReversalStrategy(Strategy):
    """
    Kauft bei extremen Abverkäufen (Flash Crashes) unterhalb der Bollinger Bänder 
    mit Bestätigung durch einen stark überverkauften RSI.
    """
    def __init__(self, config: FlashCrashReversalConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        
        self.bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.current_signal = None

    def on_start(self):
        self._log.info(f"🌩️ Starte Flash Crash Reversal auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.bb.handle_bar(bar)
        self.rsi.handle_bar(bar)

        if not (self.bb.initialized and self.rsi.initialized):
            return
            
        close_price = float(bar.close)
        
        # Logik: Preis stürzt unter das untere Band UND RSI ist im Panik-Modus (FIXED)
        is_crash = close_price < self.bb.lower
        is_oversold = self.rsi.value < self.config.rsi_oversold
        
        # Exit: Preis erreicht das obere Band oder RSI ist stark überkauft (FIXED)
        is_recovery = close_price > self.bb.upper
        is_overbought = self.rsi.value > self.config.rsi_overbought

        if is_crash and is_oversold and self.current_signal != "BUY":
            self._log.info(
                f"🟢 [{self.instrument_id}] FLASH CRASH DETECTED | "
                f"Close: {close_price:.5f} | RSI: {self.rsi.value:.2f}"
            )
            self.current_signal = "BUY"
            
        elif (is_recovery or is_overbought) and self.current_signal == "BUY":
            self._log.info(
                f"🔴 [{self.instrument_id}] RECOVERY COMPLETE. SELL SIGNAL | "
                f"Close: {close_price:.5f} | RSI: {self.rsi.value:.2f}"
            )
            self.current_signal = "SELL"
            
        elif self.current_signal == "SELL":
            self.current_signal = None