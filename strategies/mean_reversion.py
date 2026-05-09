from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.indicators import KeltnerChannel

class MeanReversionConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    keltner_period: int = 20
    keltner_atr_period: int = 20
    keltner_multiplier: float = 2.0


class MeanReversionStrategy(Strategy):
    """
    Mean Reversion / Keltner Channel Strategie.
    Nutzt Range-Bound-Märkte aus. Kauf am unteren Band, Verkauf am oberen Band.
    """
    def __init__(self, config: MeanReversionConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        # Using correct instantiation for Keltner Channel in Nautilus Trader
        self.keltner = KeltnerChannel(
            period=config.keltner_period,
            k_multiplier=config.keltner_multiplier,
        )
        self.current_signal = None

    def on_start(self):
        self._log.info(f"🚀 Starte Mean Reversion auf {self.instrument_id}")
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_quote_tick(self, tick: QuoteTick):
        pass

    def on_bar(self, bar: Bar):
        self.keltner.handle_bar(bar)

        if not self.keltner.initialized:
            return

        close_price = float(bar.close)
        upper_band = self.keltner.upper
        lower_band = self.keltner.lower
        middle_band = self.keltner.middle

        self._log.info(
            f"📊 [{self.instrument_id}] BAR GESCHLOSSEN | Close: {close_price:.2f} | "
            f"Keltner({self.config.keltner_period}): Upper {upper_band:.2f}, Lower {lower_band:.2f}"
        )

        # Mean Reversion: Buy when price drops below the lower band
        if close_price < lower_band and self.current_signal != "BUY":
            self._log.info(f"🟢 [{self.instrument_id}] BUY SIGNAL (Close < Keltner Lower Band)")
            self.current_signal = "BUY"

        # Mean Reversion: Sell when price rises above the upper band
        elif close_price > upper_band and self.current_signal != "SELL":
            self._log.info(f"🔴 [{self.instrument_id}] SELL SIGNAL (Close > Keltner Upper Band)")
            self.current_signal = "SELL"

    def on_stop(self):
        self._log.info(f"🛑 Strategie auf {self.instrument_id} gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
