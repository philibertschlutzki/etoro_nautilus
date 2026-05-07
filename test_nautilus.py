from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode

config = TradingNodeConfig(
    trader_id="eToro-Bot-01"
)

node = TradingNode(config=config)

print(f"✅ Nautilus Trader erfolgreich geladen!")
print(f"Bot-ID: {config.trader_id}") # <-- Hier einfach config statt node.config nutzen
