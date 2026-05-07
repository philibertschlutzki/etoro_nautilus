import os
import time
import asyncio  # <-- NEU: Wichtig für den Event-Loop!
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode

# Deinen neuen Adapter importieren
from adapters.etoro_data import EToroDataClient

# 1. Keys SICHER aus dem Ubuntu-System laden
API_KEY = os.environ.get("ETORO_API_KEY")
USER_KEY = os.environ.get("ETORO_USER_KEY")

if not API_KEY or not USER_KEY:
    raise ValueError("FEHLER: API-Keys fehlen! Bitte setze ETORO_API_KEY und ETORO_USER_KEY in deinem Terminal.")

if __name__ == "__main__":
    print("Starte Nautilus Trader Setup...")

    # 2. Node Konfiguration
    config = TradingNodeConfig(
        trader_id="eToro-Bot-01"
    )

    # 3. Node initialisieren
    node = TradingNode(config=config)

# 4. Deinen eToro Data Client instanziieren
    # Wir greifen nun direkt auf den 'kernel' der Node zu, wo Nautilus die Engines versteckt hat!
    etoro_client = EToroDataClient(
        loop=asyncio.get_event_loop(),
        msgbus=node.kernel.msgbus,                 # <-- .kernel. hinzugefügt
        cache=node.kernel.cache,                   # <-- .kernel. hinzugefügt
        clock=node.kernel.clock,                   # <-- .kernel. hinzugefügt
        instrument_provider=node.kernel.instrument_provider, # <-- .kernel. hinzugefügt
        api_key=API_KEY,
        user_key=USER_KEY
    )
    
    # 5. Den Client in die Nautilus-Engine einklinken
    node.add_data_client(etoro_client)

    # 6. System feuern!
    node.start()
    print("✅ System läuft! Warte auf eToro Live-Daten (TSLA)...")
    print("Drücke Strg+C zum Beenden.")

    try:
        # Endlosschleife, damit das Programm offen bleibt
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nFahre das System sicher herunter...")
        node.stop()
        print("Bot beendet.")
