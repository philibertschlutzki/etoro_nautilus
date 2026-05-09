from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

# Nautilus Indikatoren importieren
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import DirectionalMovement


class AdxAtrMomentumConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    adx_period: int = 14
    ema_period: int = 50
    atr_period: int = 14
    atr_multiplier: float = 2.0


class AdxAtrMomentumStrategy(Strategy):
    """
    Trendfolge-Strategie für EV-Aktien und AI,
    basierend auf ADX (Trendstärke), EMA (Trendrichtung) und ATR (Trailing Stop).
    """

    def __init__(self, config: AdxAtrMomentumConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Indikatoren initialisieren
        self.adx = DirectionalMovement(config.adx_period)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.atr = AverageTrueRange(config.atr_period)

        self.current_position = None
        self.entry_price = 0.0
        self.trailing_stop = 0.0

    def on_start(self):
        self._log.info(f"🚀 Starte AdxAtrMomentum-Strategie auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        # 1. Indikatoren füttern
        self.adx.handle_bar(bar)
        self.ema.handle_bar(bar)
        self.atr.handle_bar(bar)

        # 2. Warten bis alle Indikatoren berechnet sind
        if not (self.adx.initialized and self.ema.initialized and self.atr.initialized):
            return

        close_price = float(bar.close)
        adx_value = float(self.adx.adx)
        ema_value = float(self.ema.value)
        atr_value = float(self.atr.value)

        # 3. Trailing Stop Logik
        if self.current_position == "BUY":
            # Trailing Stop aktualisieren (nur nach oben ziehen)
            new_stop = close_price - (atr_value * self.config.atr_multiplier)
            self.trailing_stop = max(self.trailing_stop, new_stop)

            # Exit Logik
            if close_price < self.trailing_stop:
                self._log.info(
                    f"🔴 [{self.instrument_id}] EXIT BUY | Close: {close_price:.2f} < Stop: {self.trailing_stop:.2f}"
                )
                self.current_position = None
                self.trailing_stop = 0.0

        elif self.current_position == "SELL":
            # Falls Shorting aktiv ist, Trailing Stop nach unten ziehen
            new_stop = close_price + (atr_value * self.config.atr_multiplier)
            self.trailing_stop = min(self.trailing_stop, new_stop) if self.trailing_stop > 0 else new_stop

            # Exit Logik
            if close_price > self.trailing_stop:
                self._log.info(
                    f"🟢 [{self.instrument_id}] EXIT SELL | Close: {close_price:.2f} > Stop: {self.trailing_stop:.2f}"
                )
                self.current_position = None
                self.trailing_stop = 0.0

        # 4. Einstiegslogik
        if self.current_position is None:
            if close_price > ema_value and adx_value > 25:
                self.current_position = "BUY"
                self.entry_price = close_price
                self.trailing_stop = close_price - (atr_value * self.config.atr_multiplier)
                self._log.info(
                    f"🟢 [{self.instrument_id}] BUY SIGNAL AdxAtrMomentum | "
                    f"Close: {close_price:.2f} > EMA({self.config.ema_period}): {ema_value:.2f} | "
                    f"ADX: {adx_value:.2f} | ATR: {atr_value:.2f} | Stop: {self.trailing_stop:.2f}"
                )
            elif close_price < ema_value and adx_value > 25:
                self.current_position = "SELL"
                self.entry_price = close_price
                self.trailing_stop = close_price + (atr_value * self.config.atr_multiplier)
                self._log.info(
                    f"🔴 [{self.instrument_id}] SELL SIGNAL AdxAtrMomentum | "
                    f"Close: {close_price:.2f} < EMA({self.config.ema_period}): {ema_value:.2f} | "
                    f"ADX: {adx_value:.2f} | ATR: {atr_value:.2f} | Stop: {self.trailing_stop:.2f}"
                )

    def on_stop(self):
        self._log.info(f"🛑 Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_bars(self.bar_type)
