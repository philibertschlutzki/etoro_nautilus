import os
from dotenv import load_dotenv
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from adapters.etoro_data import EToroDataClient
from nautilus_trader.common.providers import InstrumentProvider

# Umgebungsvariablen laden
load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

def main():
    # 1. Nautilus Node initialisieren
    config = TradingNodeConfig(trader_id="eToro-Bot-01")
    node = TradingNode(config=config)

    # 2. InstrumentProvider instanziieren (v1.226.0: keine Argumente im Konstruktor)
    instrument_provider = InstrumentProvider()

    # 3. eToro Data Client instanziieren
    data_client = EToroDataClient(
        loop=node.kernel.loop,
        msgbus=node.kernel.msgbus,
        cache=node.kernel.cache,
        clock=node.kernel.clock,
        instrument_provider=instrument_provider,
        api_key=API_KEY,
        user_key=USER_KEY
    )

    # 4. Client über die DataEngine im KERNEL registrieren [FIXED]
    # In v1.226.0 erfolgt der Zugriff über node.kernel.data_engine
    node.kernel.data_engine.add_client(data_client)
    
    # 5. System bauen
    node.build()

    print(f"✅ System für {config.trader_id} erfolgreich konfiguriert.")
    print("🚀 Starte eToro-Bot... (Drücke Ctrl+C zum Beenden)")
    
    try:
        node.run() 
    except KeyboardInterrupt:
        print("\n⚠️ Herunterfahren eingeleitet...")
    finally:
        # Ressourcen sauber freigeben
        node.stop()
        print("🤖 Bot erfolgreich beendet.")

if __name__ == "__main__":
    main()
