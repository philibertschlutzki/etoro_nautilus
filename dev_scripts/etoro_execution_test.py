import os
import sys
from decimal import Decimal
import logging

# Nautilus Imports
from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

# Pfad anpassen, damit Module gefunden werden
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.setups import ETORO_API_TEST
from adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from adapters.etoro_execution import EToroExecClientConfig, EToroLiveExecClientFactory
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

class ApiOrderTestStrategy(Strategy):
    """
    Ping-Pong Strategie: Kauft das definierte Asset und verkauft es sofort nach Fill.
    Zweck: End-to-End Test der eToro Execution API.
    """
    def __init__(self, config: StrategyConfig, node: TradingNode):
        super().__init__(config)
        self.node = node # Referenz auf den Node, um ihn am Ende stoppen zu können
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

    def on_order_filled(self, event):
        self.log.info(f"Fill Event empfangen: {event.quantity} Units gefüllt.")
        
        # Prüfen, ob es der Fill für unseren initialen Kauf ist
        if event.client_order_id == self.buy_order_id and not self.position_closed:
            self.log.info("Sende sofortige SELL Order zum Schliessen der Position...")
            
            close_order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=event.quantity,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(close_order)
            self.position_closed = True

    def on_order_closed(self, event):
        # Wird getriggert, wenn die SELL-Order (und damit die Position) vollständig abgeschlossen ist
        if self.position_closed and event.client_order_id != self.buy_order_id:
            self.log.info("SELL Order erfolgreich abgeschlossen. API Ping-Pong Test beendet.")
            # Beende den Node sauber
            self.log.info("Initiiere Shutdown des Nodes...")
            # Ein kleiner Hack für Nautilus Trader, um den blockierenden .run() Call zu beenden:
            self.node.stop()

def run_test():
    if not API_KEY or not USER_KEY:
        print("FEHLER: API Keys fehlen in der .env.")
        sys.exit(1)
        
    print(f"Starte Test auf Environment: {ETORO_API_TEST['environment']}")
    if ETORO_API_TEST['environment'] == "real":
        confirm = input("Achtung: Du testest auf einem ECHTEN Account. Orders werden live abgesetzt. Weiter? (j/N): ")
        if confirm.lower() != 'j':
            sys.exit(0)

    # 1. Logging Setup
    log_config = LoggingConfig(log_level="INFO", log_directory="logs")
    
    # Hier brauchen wir die eToro ID (die numerische) für das gewählte Symbol, 
    # damit der Web-Socket Client sich verbinden kann. 
    # Bei dir heisst es z.B. "ADA.ETORO", aber die Adapter brauchen die ID.
    # Für ADA ist es oft "21". Im Notfall schaut der Adapter in die Map.
    from adapters.instrument_map import ETORO_INSTRUMENTS
    etoro_id = None
    for k, v in ETORO_INSTRUMENTS.items():
        if v == ETORO_API_TEST["symbol"]:
            etoro_id = k
            break
            
    if not etoro_id:
        print(f"Konnte numerische ID für {ETORO_API_TEST['symbol']} nicht finden.")
        sys.exit(1)

    # 2. Node Config & Erstellung
    config = TradingNodeConfig(
        logging=log_config,
        data_clients={
            "ETORO_WS_CLIENT": EToroDataClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
                instrument_ids=[etoro_id],
            )
        },
        exec_clients={
            "ETORO": EToroExecClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
                environment=ETORO_API_TEST["environment"],
                dry_run=ETORO_API_TEST["dry_run"],
                enable_trailing_stop=False,
            )
        },
    )
    
    node = TradingNode(config=config)
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)
    node.add_exec_client_factory("ETORO", EToroLiveExecClientFactory)

    # 3. Strategie laden
    strategy = ApiOrderTestStrategy(config=StrategyConfig(), node=node)
    node.trader.add_strategy(strategy)

    # 4. Start und Run
    node.build()
    
    try:
        node.run()
    except KeyboardInterrupt:
        print("Test manuell abgebrochen.")
    finally:
        node.stop()

if __name__ == "__main__":
    run_test()