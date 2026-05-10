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
    Trendfolge-Strategie basierend auf ADX (Trendstärke), EMA (Trendrichtung) und ATR (Trailing Stop).
    """
    def __init__(self, config: AdxAtrMomentumConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Indikatoren initialisieren (FIXED: Nutze DirectionalMovement)
        self.adx = DirectionalMovement(config.adx_period)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.atr = AverageTrueRange(config.atr_period)

        self.current_position = None
        self.entry_price = 0.0
        self.trailing_stop = 0.0

    def on_start(self):
        self._log.info(f"🚀 Starte ADX+ATR Momentum Strategie auf {self.instrument_id}")
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar):
        self.adx.handle_bar(bar)
        self.ema.handle_bar(bar)
        self.atr.handle_bar(bar)

        if not (self.adx.initialized and self.ema.initialized and self.atr.initialized):
            return

        close_price = float(bar.close)
        
        # Werte korrekt abrufen (FIXED: Zugriff auf .value.adx)
        adx_value = self.adx.value.adx 
        ema_value = self.ema.value
        atr_value = self.atr.value

        # Trailing Stop Logik
        if self.current_position == "BUY":
            new_stop = close_price - (atr_value * self.config.atr_multiplier)
            if new_stop > self.trailing_stop:
                self.trailing_stop = new_stop
            if close_price <= self.trailing_stop:
                self._log.info(f"🔴 [{self.instrument_id}] Trailing Stop LONG Hit! Close: {close_price:.2f} <= Stop: {self.trailing_stop:.2f}")
                self.current_position = None
                self.trailing_stop = 0.0

        elif self.current_position == "SELL":
            new_stop = close_price + (atr_value * self.config.atr_multiplier)
            if self.trailing_stop == 0.0 or new_stop < self.trailing_stop:
                self.trailing_stop = new_stop
            if close_price >= self.trailing_stop:
                self._log.info(f"🟢 [{self.instrument_id}] Trailing Stop SHORT Hit! Close: {close_price:.2f} >= Stop: {self.trailing_stop:.2f}")
                self.current_position = None
                self.trailing_stop = 0.0

        # Einstiegslogik
        if self.current_position is None:
            if close_price > ema_value and adx_value > 25:
                self.current_position = "BUY"
                self.entry_price = close_price
                self.trailing_stop = close_price - (atr_value * self.config.atr_multiplier)
                self._log.info(
                    f"🟢 [{self.instrument_id}] BUY SIGNAL AdxAtrMomentum | "
                    f"Close: {close_price:.2f} > EMA: {ema_value:.2f} | "
                    f"ADX: {adx_value:.2f} | ATR: {atr_value:.2f} | Stop: {self.trailing_stop:.2f}"
                )
            elif close_price < ema_value and adx_value > 25:
                self.current_position = "SELL"
                self.entry_price = close_price
                self.trailing_stop = close_price + (atr_value * self.config.atr_multiplier)
                self._log.info(
                    f"🔴 [{self.instrument_id}] SELL SIGNAL AdxAtrMomentum | "
                    f"Close: {close_price:.2f} < EMA: {ema_value:.2f} | "
                    f"ADX: {adx_value:.2f} | ATR: {atr_value:.2f} | Stop: {self.trailing_stop:.2f}"
                )