from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

# Nautilus Indikatoren importieren
from nautilus_trader.indicators.sma import SimpleMovingAverage
from nautilus_trader.indicators.macd import MovingAverageConvergenceDivergence
from nautilus_trader.indicators.bollinger_bands import BollingerBands

class TeslaComboConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    sma_period: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    bb_period: int = 20

class TeslaComboStrategy(Strategy):
    def __init__(self, config: TeslaComboConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        
        # Indikatoren initialisieren
        self.sma = SimpleMovingAverage(config.sma_period)
        self.macd = MovingAverageConvergenceDivergence(config.macd_fast, config.macd_slow)
        self.bb = BollingerBands(config.bb_period, 2.0)
        
        # Manueller Intraday-VWAP (benötigt Volumen-Daten aus den Bars)
        self.cumulative_vp = 0.0
        self.cumulative_volume = 0.0
        self.current_vwap = 0.0
        self.current_day = None

        self.current_signal = None

    def on_start(self):
        self._log.info(f"🚀 Starte Tesla Combo Strategie auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        # 1. Indikatoren füttern
        self.sma.handle_bar(bar)
        self.macd.handle_bar(bar)
        self.bb.handle_bar(bar)
        
        # 2. VWAP Berechnung (täglich zurücksetzen)
        bar_day = bar.ts_event // 86400000000000  # Grober Check für neuen Tag (Nano-Sekunden)
        if self.current_day != bar_day:
            self.cumulative_vp = 0.0
            self.cumulative_volume = 0.0
            self.current_day = bar_day
            
        typical_price = float(bar.high + bar.low + bar.close) / 3.0
        volume = float(bar.volume)
        
        # Sicherstellen, dass Volumen vorhanden ist
        if volume > 0:
            self.cumulative_vp += typical_price * volume
            self.cumulative_volume += volume
            self.current_vwap = self.cumulative_vp / self.cumulative_volume

        # 3. Warten bis alle Indikatoren berechnet sind
        if not (self.sma.initialized and self.macd.initialized and self.bb.initialized):
            return
            
        close_price = float(bar.close)

        # ─── Handelslogik (Die Kombination) ───────────────────────────
        
        # Beispiel Long-Konditionen:
        # 1. Trend stimmt (Preis über SMA 50)
        trend_bullish = close_price > self.sma.value
        # 2. Momentum ist positiv (MACD kreuzt Signallinie)
        momentum_bullish = self.macd.macd > self.macd.signal
        # 3. Einstieg: Wir sind am unteren Bollinger Band abgeprallt (Preis nahe dem unteren Band)
        entry_trigger = close_price <= self.bb.lower_band * 1.005 # 0.5% Toleranz
        # 4. Bestätigung: Preis muss über dem VWAP liegen, sonst ist der Intraday-Druck zu stark
        vwap_confirmed = close_price > self.current_vwap

        if trend_bullish and momentum_bullish and entry_trigger and vwap_confirmed and self.current_signal != "BUY":
            self._log.info(f"🟢 BUY SIGNAL: Alle 4 Indikatoren bestätigt bei {close_price:.2f}")
            self.current_signal = "BUY"
            # Hier später: self.submit_order(...) implementieren

    def on_stop(self):
        self.unsubscribe_bars(self.bar_type)