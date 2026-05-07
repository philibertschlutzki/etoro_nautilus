import os
import time
import asyncio
from dotenv import load_dotenv
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.common.providers import InstrumentProvider

# Import des benutzerdefinierten eToro Adapters
from adapters.etoro_data import EToroDataClient

# 1. Lade Umgebungsvariablen
load_dotenv()

API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

if __name__ == "__main__":
    print("Starting Nautilus Trader Setup...")

    # 2. Node initialisieren
    node = TradingNode(config=TradingNodeConfig(trader_id="eToro-Bot-01"))

    # 3. InstrumentProvider und Client instanziieren
    etoro_instrument_provider = InstrumentProvider()

    etoro_client = EToroDataClient(
        loop=asyncio.get_event_loop(),
        msgbus=node.kernel.msgbus,
        cache=node.kernel.cache,
        clock=node.kernel.clock,
        instrument_provider=etoro_instrument_provider,
        api_key=API_KEY,
        user_key=USER_KEY
    )
    
    # 4. Den Client zur Engine hinzufügen
    node.kernel.data_engine.register_client(etoro_client)

    # 5. FIX: Das System muss vor dem Start gebaut werden
    print("Baue System-Komponenten...")
    node.build()

    # 6. System starten
    print("✅ System wird gestartet... Warte auf eToro Live-Daten (TSLA)...")
    node.run()
    
    print("Press Ctrl+C to exit.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nFahre System sicher herunter...")
        node.stop()
