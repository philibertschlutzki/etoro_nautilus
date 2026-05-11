import asyncio
import logging
from decimal import Decimal

from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

# Importiere deine Projekt-Komponenten
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.setups import ETORO_API_TEST

# TODO: Hier deine spezifischen eToro-Clients importieren
# from adapters.etoro_execution import eToroExecutionClient
# from adapters.etoro_data import eToroDataClient

class ApiOrderTestStrategy(Strategy):
    """
    Ping-Pong Strategie: Kauft das definierte Asset und verkauft es sofort nach Fill.
    Zweck: End-to-End Test der eToro Execution API.
    """
    def __init__(self, config: StrategyConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(ETORO_API_TEST["symbol"])
        self.usd_amount = Decimal(str(ETORO_API_TEST["trade_amount_usd"]))
        
        # State Tracker
        self.buy_submitted = False
        self.position_closed = False
        self.buy_order_id = None

    def on_start(self):
        self.log.info(f"API-Test gestartet. Warte auf ersten Preis-Tick für {self.instrument_id}...")
        self.subscribe_quote_ticks(self.instrument_id)

    def on_quote_tick(self, tick: QuoteTick):
        # Nur einen einzigen Kauf auslösen
        if not self.buy_submitted:
            # Berechne die Menge basierend auf dem Ask-Preis (Kaufpreis)
            # quantize() stellt sicher, dass wir nicht zu viele Nachkommastellen senden
            quantity = (self.usd_amount / tick.ask_price).quantize(Decimal("0.0001"))
            
            self.log.info(f"Sende BUY Order: {quantity} Units @ {tick.ask_price} (~{self.usd_amount} USD)")
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
                time_in_force=TimeInForce.GTC
            )
            self.buy_order_id = order.client_order_id
            self.submit_order(order)
            self.buy_submitted = True

    def _extract_position_id(self, event) -> str:
        """
        Hilfsfunktion: Extrahiert die eToro Position ID aus dem OrderFilled Event.
        Je nachdem, wie etoro_execution.py gebaut ist, liegt die ID im trade_id 
        oder in den broker_id Feldern des Events.
        """
        # Fall 1: Adapter mappt die eToro Position ID auf die Nautilus trade_id
        if event.trade_id:
            return event.trade_id.value
            
        # Fall 2: Fallback auf provider_order_id, falls trade_id nicht gesetzt wird
        if getattr(event, "provider_order_id", None):
            return event.provider_order_id.value
            
        return "UNKNOWN_POSITION_ID"

    def on_order_filled(self, event):
        self.log.info(f"Fill Event empfangen: {event.quantity} Units gefüllt.")
        
        # Prüfen, ob es der Fill für unseren initialen Kauf ist
        if event.client_order_id == self.buy_order_id and not self.position_closed:
            position_id = self._extract_position_id(event)
            self.log.info(f"Extrahierte eToro Position ID: {position_id}")
            self.log.info("Sende sofortige SELL Order zum Schliessen der Position...")
            
            # Wir übergeben die Position ID über die Tags an den Adapter
            tags = [f"POSITION_ID:{position_id}"] if position_id != "UNKNOWN_POSITION_ID" else []
            
            close_order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=event.quantity,
                time_in_force=TimeInForce.GTC,
                tags=tags
            )
            self.submit_order(close_order)
            self.position_closed = True

    def on_order_closed(self, event):
        # Wird getriggert, wenn der Broker die Order als vollständig abgeschlossen meldet
        if self.position_closed and event.client_order_id != self.buy_order_id:
            self.log.info("SELL Order erfolgreich abgeschlossen. API Ping-Pong Test beendet.")

async def run_test():
    logging.basicConfig(level=logging.INFO)
    
    # Nutzt dein bestehendes Logging-Verzeichnis (wie in den normalen Bot-Runs)
    log_config = LoggingConfig(log_level="INFO", log_directory="logs")
    node_config = TradingNodeConfig(logging=log_config)
    node = TradingNode(config=node_config)
    
    # TODO: Binde hier den Data- und Execution-Client ein, bevor der Node startet.
    # Nutze dafür ETORO_API_TEST['environment'] (welches "real" sein muss)
    
    # exec_client = eToroExecutionClient(environment=ETORO_API_TEST["environment"], ...)
    # data_client = eToroDataClient(...)
    # node.add_execution_client("ETORO", exec_client)
    # node.add_data_client("ETORO", data_client)

    # Strategie laden
    strategy = ApiOrderTestStrategy(StrategyConfig())
    node.trader.add_strategy(strategy)

    await node.start()
    
    # Node läuft maximal 60 Sekunden, um den schnellen In-und-Out Trade durchzuführen
    timeout = 60
    while timeout > 0 and not strategy.position_closed:
        await asyncio.sleep(1)
        timeout -= 1
        
    await asyncio.sleep(2) # Kurzer Puffer für letzte asynchrone Logs
    await node.stop()

if __name__ == "__main__":
    asyncio.run(run_test())
