from collections import deque
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

class VwapExhaustionConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    volume_sma_period: int = 20
    deviation_threshold: float = 0.03
    volume_multiplier: float = 2.0


class VwapExhaustionStrategy(Strategy):
    """
    Mean-Reversion-Strategie für hochvolatile Retail-Assets (Meme-Coins, Krypto),
    basierend auf Abweichungen vom VWAP kombiniert mit Volumen-Spikes.
    """

    def __init__(self, config: VwapExhaustionConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Manuelle Volumen-SMA Berechnung
        self.volume_history = deque(maxlen=config.volume_sma_period)

        # Manueller Intraday-VWAP
        self.cumulative_vp = 0.0
        self.cumulative_volume = 0.0
        self.current_vwap = 0.0
        self.current_day = None

    def on_start(self):
        self._log.info(f"🚀 Starte VwapExhaustion-Strategie auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        volume = float(bar.volume)
        close_price = float(bar.close)
        typical_price = float(bar.high + bar.low + bar.close) / 3.0

        # 1. Volumen in SMA-Historie aufnehmen
        self.volume_history.append(volume)

        # 2. VWAP-Berechnung (täglich zurücksetzen)
        bar_day = bar.ts_event // 86400000000000  # Grober Check für neuen Tag (Nano-Sekunden)
        if self.current_day != bar_day:
            self.cumulative_vp = 0.0
            self.cumulative_volume = 0.0
            self.current_day = bar_day

        if volume > 0:
            self.cumulative_vp += typical_price * volume
            self.cumulative_volume += volume

        if self.cumulative_volume > 0:
            self.current_vwap = self.cumulative_vp / self.cumulative_volume

        # 3. Warten bis Volumen-SMA initialisiert ist
        if len(self.volume_history) < self.config.volume_sma_period:
            return

        if self.current_vwap <= 0:
            return

        # 4. Signale berechnen
        avg_volume = sum(self.volume_history) / len(self.volume_history)
        deviation = (close_price - self.current_vwap) / self.current_vwap

        if deviation <= -self.config.deviation_threshold and volume > (avg_volume * self.config.volume_multiplier):
            self._log.info(
                f"🟢 [{self.instrument_id}] BUY SIGNAL (VWAP Exhaustion Bottom) | "
                f"Close: {close_price:.2f} | VWAP: {self.current_vwap:.2f} | "
                f"Dev: {deviation*100:.2f}% | Vol: {volume:.2f} (Avg: {avg_volume:.2f})"
            )
        elif deviation >= self.config.deviation_threshold and volume > (avg_volume * self.config.volume_multiplier):
            self._log.info(
                f"🔴 [{self.instrument_id}] SELL SIGNAL (VWAP Exhaustion Top) | "
                f"Close: {close_price:.2f} | VWAP: {self.current_vwap:.2f} | "
                f"Dev: {deviation*100:.2f}% | Vol: {volume:.2f} (Avg: {avg_volume:.2f})"
            )

    def on_stop(self):
        self._log.info(f"🛑 Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
