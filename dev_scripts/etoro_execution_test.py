import asyncio
import os
import sys
import logging
from decimal import Decimal
from pathlib import Path

from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

# Projektpfad einbinden
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.setups import ETORO_API_TEST
from adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from adapters.etoro_execution import EToroExecClientConfig, EToroLiveExecClientFactory
from adapters.instrument_map import ETORO_INSTRUMENTS
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

class ApiOrderTestStrategy(Strategy):
    """Kauft ADA und verkauft es sofort nach dem Fill wieder."""
    def __init__(self, config: StrategyConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(ETORO_API_TEST["symbol"])
        self.usd_amount = Decimal(str(ETORO_API_TEST["trade_amount_usd"]))
        
        self.buy_submitted = False
        self.position_closed = False
        self.buy_order_id = None

    def on_start(self):
        self.log.info(f"Test gestartet. Warte auf Preis-Tick für {self.instrument_id}...")
        self.subscribe_quote_ticks(self.instrument_id)

    def on_quote_tick(self, tick: QuoteTick):
        if not self.buy_submitted:
            quantity = (self.usd_amount / tick.ask_price).quantize(Decimal("0.0001"))
            self.log.info(f"Sende BUY: {quantity} Units @ Ask {tick.ask_price} (~{self.usd_amount} USD)")
            
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
        self.log.info(f"Fill Event: {event.quantity} Units gekauft.")
        
        if event.client_order_id == self.buy_order_id and not self.position_closed:
            self.log.info("Sende sofortige SELL Order zum Schließen...")
            close_order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=event.quantity,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(close_order)
            self.position_closed = True

    def on_order_closed(self, event):
        if self.position_closed and event.client_order_id != self.buy_order_id:
            self.log.info("SELL abgeschlossen. Test erfolgreich!")

async def main():
    if not API_KEY or not USER_KEY:
        print("FEHLER: API Keys fehlen in der .env.")
        sys.exit(1)

    etoro_id = None
    for k, v in ETORO_INSTRUMENTS.items():
        if v == ETORO_API_TEST["symbol"]:
            etoro_id = k
            break
            
    log_config = LoggingConfig(log_level="INFO", log_directory="logs")
    config = TradingNodeConfig(
        logging=log_config,
        data_clients={
            "ETORO_WS_CLIENT": EToroDataClientConfig(
                api_key=API_KEY, user_key=USER_KEY, instrument_ids=[etoro_id]
            )
        },
        exec_clients={
            "ETORO": EToroExecClientConfig(
                api_key=API_KEY, user_key=USER_KEY, 
                environment=ETORO_API_TEST["environment"], 
                dry_run=ETORO_API_TEST["dry_run"], enable_trailing_stop=False
            )
        },
    )
    
    node = TradingNode(config=config)
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)
    node.add_exec_client_factory("ETORO", EToroLiveExecClientFactory)

    strategy = ApiOrderTestStrategy(config=StrategyConfig())
    node.trader.add_strategy(strategy)

    print("Starte Trading Node (asynchron)...")
    node.build()
    
    # 1. Starte den Node als asynchronen Task
    # start() existiert auf der Node-Klasse (nicht auf der Node-Instanz in alten Versionen),
    # aber node.start() oder das Laufenlassen in einer Schleife ist nötig:
    
    asyncio.create_task(node.run_async() if hasattr(node, 'run_async') else asyncio.to_thread(node.run))

    # 2. Warte, bis der Node wirklich LIVE ist (Reconciliation beendet)
    print("Warte auf Startup (inkl. 10s Reconciliation Delay)...")
    await asyncio.sleep(15) 
    
    # 3. Warte maximal 60 weitere Sekunden auf den Abschluss des Trades
    timeout = 60
    while timeout > 0 and not strategy.position_closed:
        await asyncio.sleep(1)
        timeout -= 1

    if strategy.position_closed:
        print("Trade Ping-Pong erfolgreich. Beende Node.")
    else:
        print("Timeout! Position wurde nicht schnell genug geschlossen.")

    # 4. Sauber beenden
    node.stop()

if __name__ == "__main__":
    confirm = input("Achtung: LIVE eToro API-Test! Orders werden platziert. Weiter? (j/N): ")
    if confirm.lower() == 'j':
        asyncio.run(main())c