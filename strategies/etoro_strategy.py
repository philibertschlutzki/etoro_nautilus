from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.indicators import SimpleMovingAverage


# ── Config ────────────────────────────────────────────────────────────────────

class EToroStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str = "TSLA.ETORO"
    # Definiert die Kerze: 1 Minute, basierend auf dem MID-Preis (Mitte zwischen Bid und Ask)
    bar_type: str = "TSLA.ETORO-1-MINUTE-MID-INTERNAL" 
    sma_period: int = 5  # Wir nutzen einen kurzen 5-Minuten-SMA für schnelles Feedback


# ── Strategy ──────────────────────────────────────────────────────────────────

class EToroStrategy(Strategy):
    """
    Erweiterte Strategy für den eToro WebSocket DataClient.
    Aggregiert Ticks in 1-Minuten-Bars und generiert Signale basierend auf
    einem Simple Moving Average (SMA).
    """

    def __init__(self, config: EToroStrategyConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        
        # Indikator initialisieren
        self.sma = SimpleMovingAverage(config.sma_period)
        
        # State-Tracking, damit wir nicht mehrmals dasselbe Signal spammen
        self.current_signal = None

    def on_start(self):
        self._log.info(f"🚀 Strategie gestartet. Abonniere Ticks & Bars für {self.instrument_id}")
        
        # 1. Ticks abonnieren (Startet den eToro-WebSocket Datenstrom)
        self.subscribe_quote_ticks(self.instrument_id)
        
        # 2. Bars abonnieren. Da eToro nur Ticks liefert, aktiviert das 'INTERNAL' Flag
        # in Nautilus die automatische Tick-zu-Bar Aggregation in der DataEngine.
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        # Wir lassen die Tick-Verarbeitung komplett leer. 
        # Nautilus verarbeitet diese im Hintergrund für unsere Bars.
        # So bleibt unser Log sauber.
        pass

    def on_bar(self, bar: Bar):
        """
        Wird exakt dann aufgerufen, wenn eine 1-Minuten-Kerze abgeschlossen ist.
        """
        # Preis als Float für den Indikator extrahieren
        close_price = float(bar.close)
        
        # Indikator mit dem neuen Schlusskurs updaten
        self.sma.update(close_price)
        
        # Logge die fertige Kerze
        self._log.info(
            f"📊 1-MIN BAR GESCHLOSSEN | Close: {close_price:.2f} | "
            f"SMA({self.config.sma_period}): {self.sma.value:.2f}"
        )

        # Signallogik erst ausführen, wenn der SMA genug Daten hat (hier: 5 Bars)
        if not self.sma.initialized:
            self._log.debug(f"Warte auf SMA Initialisierung... ({self.sma.count}/{self.config.sma_period})")
            return

        # ─── Handelslogik (Crossover) ─────────────────────────────────────────
        
        if close_price > self.sma.value and self.current_signal != "BUY":
            self._log.info(f"🟢 BUY SIGNAL: Preis ({close_price:.2f}) kreuzt ÜBER den SMA ({self.sma.value:.2f})")
            self.current_signal = "BUY"
            # TODO: Hier in Zukunft self.submit_order(...) aufrufen
            
        elif close_price < self.sma.value and self.current_signal != "SELL":
            self._log.info(f"🔴 SELL SIGNAL: Preis ({close_price:.2f}) kreuzt UNTER den SMA ({self.sma.value:.2f})")
            self.current_signal = "SELL"
            # TODO: Hier in Zukunft self.submit_order(...) aufrufen

    def on_stop(self):
        self._log.info("🛑 Strategie gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)