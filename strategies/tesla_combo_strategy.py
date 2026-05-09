from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

# Nautilus Indikatoren importieren
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.indicators import MovingAverageConvergenceDivergence
from nautilus_trader.indicators import BollingerBands
from nautilus_trader.indicators import AverageTrueRange

class ComboTrendVwapConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    sma_period: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    bb_period: int = 20
    bb_std_dev: float = 2.0
    atr_period: int = 14
    atr_multiplier: float = 0.5


class ComboTrendVwapStrategy(Strategy):
    """Generische Trend+Momentum+Volatilitäts+VWAP-Strategie für ein einzelnes Instrument."""

    def __init__(self, config: ComboTrendVwapConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Indikatoren initialisieren
        self.sma = SimpleMovingAverage(config.sma_period)
        self.macd = MovingAverageConvergenceDivergence(config.macd_fast, config.macd_slow)
        self.bb = BollingerBands(config.bb_period, config.bb_std_dev)
        self.atr = AverageTrueRange(config.atr_period)

        # Manueller Intraday-VWAP (benötigt Volumen-Daten aus den Bars)
        self.cumulative_vp = 0.0
        self.cumulative_volume = 0.0
        self.current_vwap = 0.0
        self.current_day = None

        self.current_signal = None

    def on_start(self):
        self._log.info(f"🚀 Starte ComboTrendVWAP-Strategie auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        # 1. Indikatoren füttern
        self.sma.handle_bar(bar)
        self.macd.handle_bar(bar)
        self.bb.handle_bar(bar)
        self.atr.handle_bar(bar)

        # 2. VWAP-Berechnung (täglich zurücksetzen)
        bar_day = bar.ts_event // 86400000000000  # Grober Check für neuen Tag (Nano-Sekunden)
        if self.current_day != bar_day:
            self.cumulative_vp = 0.0
            self.cumulative_volume = 0.0
            self.current_day = bar_day

        typical_price = float(bar.high + bar.low + bar.close) / 3.0
        volume = float(bar.volume)

        # Sicherstellen, dass Volumen vorhanden ist, Vermeidung von Zero Division
        if volume > 0:
            self.cumulative_vp += typical_price * volume
            self.cumulative_volume += volume

        if self.cumulative_volume > 0:
            self.current_vwap = self.cumulative_vp / self.cumulative_volume

        # 3. Warten bis alle Indikatoren berechnet sind
        if not (self.sma.initialized and self.macd.initialized and self.bb.initialized and self.atr.initialized):
            return

        close_price = float(bar.close)

        # ─── Handelslogik (Die Kombination) ───────────────────────────

        # 1. Trend stimmt (Preis über SMA)
        trend_bullish = close_price > self.sma.value
        # 2. Momentum ist positiv (MACD kreuzt Signallinie)
        momentum_bullish = self.macd.macd > self.macd.signal
        # 3. Einstieg: Preis nahe dem unteren Bollinger Band (dynamisch mit ATR)
        atr_tolerance = self.atr.value * self.config.atr_multiplier
        entry_trigger = close_price <= (self.bb.lower_band + atr_tolerance)
        # 4. Bestätigung: Preis muss über dem VWAP liegen, und VWAP muss valide sein
        vwap_confirmed = self.cumulative_volume > 0 and close_price > self.current_vwap

        if trend_bullish and momentum_bullish and entry_trigger and vwap_confirmed and self.current_signal != "BUY":
            self._log.info(
                f"🟢 [{self.instrument_id}] BUY SIGNAL ComboTrendVWAP | "
                f"Close: {close_price:.2f} | SMA({self.config.sma_period}): {self.sma.value:.2f} | "
                f"MACD: {self.macd.macd:.4f} / {self.macd.signal:.4f} | "
                f"BB lower: {self.bb.lower_band:.2f} | VWAP: {self.current_vwap:.2f}"
            )
            self.current_signal = "BUY"

    def on_stop(self):
        self._log.info(f"🛑 Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
