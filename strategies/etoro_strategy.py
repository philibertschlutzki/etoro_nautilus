from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


# ── Config ────────────────────────────────────────────────────────────────────

class EToroStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str = "TSLA.ETORO"


# ── Strategy ──────────────────────────────────────────────────────────────────

class EToroStrategy(Strategy):
    """
    Basis-Strategy für den eToro WebSocket DataClient.
    Abonniert TSLA.ETORO QuoteTicks und loggt jeden eingehenden Tick.
    Erweiterbar für Signallogik und Order-Placement.
    """

    def __init__(self, config: EToroStrategyConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)

    def on_start(self):
        self._log.info(f"Strategy gestartet. Abonniere Ticks für {self.instrument_id}")
        self.subscribe_quote_ticks(self.instrument_id)

    def on_quote_tick(self, tick: QuoteTick):
        spread = float(tick.ask_price) - float(tick.bid_price)
        self._log.info(
            f"QuoteTick | bid={tick.bid_price} ask={tick.ask_price} "
            f"spread={spread:.5f} | {tick.instrument_id}"
        )
        # TODO: Handelslogik hier implementieren

    def on_stop(self):
        self._log.info("Strategy gestoppt.")
        self.unsubscribe_quote_ticks(self.instrument_id)
