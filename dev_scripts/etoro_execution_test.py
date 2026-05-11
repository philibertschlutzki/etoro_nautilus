import asyncio
from decimal import Decimal

from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

# TODO: Importiere hier deine eToro-Clients aus deinen Adaptern!
# from adapters.etoro_execution import eToroExecutionClient
# from adapters.etoro_data import eToroDataClient

class EtoroPingPongConfig(StrategyConfig):
    instrument_id: InstrumentId
    trade_size: Decimal

class EtoroPingPongStrategy(Strategy):
    """
    Test-Strategie: Kauft die konfigurierte Menge und verkauft sie unmittelbar 
    nach dem Fill-Event wieder zum aktuellen Marktpreis.
    """
    def __init__(self, config: EtoroPingPongConfig):
        super().__init__(config)
        self.instrument_id = config.instrument_id
        self.trade_size = config.trade_size
        self.bought = False
        self.sold = False
        self.buy_order_id = None

    def on_start(self):
        self.log.info(f"Starte Live-Test auf {self.instrument_id}...")
        self.subscribe_quote_ticks(self.instrument_id)

    def on_quote_tick(self, tick: QuoteTick):
        if not self.bought:
            self.log.info(f"Preis empfangen (Bid: {tick.bid_price} / Ask: {tick.ask_price}). Sende BUY-Order für {self.trade_size} Units...")
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.trade_size,
                time_in_force=TimeInForce.GTC,
            )
            self.buy_order_id = order.client_order_id
            self.submit_order(order)
            self.bought = True

    def on_order_filled(self, event):
        self.log.info(f"Order Fill-Event erhalten: {event}")
        # Wenn der Kauf durchgegangen ist, schicke sofort den Sell ab
        if event.client_order_id == self.buy_order_id and not self.sold:
            self.log.info("BUY abgeschlossen. Sende sofortige SELL-Order zum Schliessen...")
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.trade_size,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            self.sold = True
            
    def on_order_closed(self, event):
        self.log.info(f"Order geschlossen: {event}")
        if self.sold and event.client_order_id != self.buy_order_id:
            self.log.info("Live Execution API-Test erfolgreich abgeschlossen.")

async def main():
    # 1. Konfiguration des Live-Nodes
    config = TradingNodeConfig(
        log_level="INFO",
        data_clients={
            # "ETORO": eToroDataClient(...),  # Muss mit deinen Parametern instanziiert werden
        },
        execution_clients={
            # "ETORO": eToroExecutionClient(...), # Hier greift der produktive API-Key
        },
    )
    
    node = TradingNode(config=config)
    
    # 2. Strategie konfigurieren
    strat_config = EtoroPingPongConfig(
        instrument_id=InstrumentId.from_str("DOGE.ETORO"),
        trade_size=Decimal("100") # Beispielmenge
    )
    strategy = EtoroPingPongStrategy(config=strat_config)
    node.trader.add_strategy(strategy)
    
    # 3. Node hochfahren
    await node.start()
    
    # Das Skript für 60 Sekunden laufen lassen, damit Kaufen und Verkaufen durchgehen
    await asyncio.sleep(60)
    
    # Sicher herunterfahren
    await node.stop()

if __name__ == "__main__":
    asyncio.run(main())