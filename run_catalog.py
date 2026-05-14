import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Nautilus Importe
from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.identifiers import InstrumentId

# Adapter & Config Importe
from adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from config.setups import ACTIVE_BOTS

# Lade API Keys
load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

# Pfad für die Parquet-Katalog Dateien
CATALOG_PATH = Path(__file__).parent / "data" / "nautilus"

def main():
    CATALOG_PATH.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("DataRecorder")

    if not API_KEY or not USER_KEY:
        log.error("FEHLER: API_KEY oder USER_KEY in .env nicht gefunden!")
        sys.exit(1)

    # 1. Sammle alle benötigten eToro IDs & Symbole dynamisch aus der config/setups.py
    required_etoro_ids = []
    symbols_to_record = []
    for bot in ACTIVE_BOTS:
        if "etoro_id" in bot and "symbol" in bot:
            required_etoro_ids.append(bot["etoro_id"])
            symbols_to_record.append(bot["symbol"])

    # Duplikate entfernen
    required_etoro_ids = list(set(required_etoro_ids))
    symbols_to_record = list(set(symbols_to_record))

    if not required_etoro_ids:
        log.error("FEHLER: Keine Instrumente in config/setups.py gefunden.")
        sys.exit(1)

    # 2. Nautilus Node Konfigurieren (Hier als passiver Recorder)
    config = TradingNodeConfig(
        trader_id="eToro-Data-Recorder",
        logging=LoggingConfig(log_level="INFO"),
        data_clients={
            "ETORO_WS_CLIENT": EToroDataClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
                instrument_ids=required_etoro_ids,
            )
        }
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)

    # 3. Parquet Data Catalog initialisieren und an den Data Engine anhängen
    catalog = ParquetDataCatalog(str(CATALOG_PATH))
    node.data_engine.add_catalog(catalog)

    # 4. Node aufbauen
    node.build()

    # 5. WICHTIG: Ticks explizit in der Data Engine abonnieren!
    # Obwohl die Websocket-Verbindung sie ohnehin liefert, muss Nautilus 
    # wissen, dass diese Ticks in den Katalog geschrieben werden sollen.
    for sym in symbols_to_record:
        instr_id = InstrumentId.from_str(sym)
        node.data_engine.subscribe_quote_ticks(instr_id)
        log.info(f"Datenaufzeichnung aktiviert für: {sym}")

    log.info(f"Starte passiven eToro Datenrekorder für {len(symbols_to_record)} Instrumente.")
    log.info(f"Die Parquet-Daten werden unter {CATALOG_PATH} gespeichert.")
    log.info("Drücke Ctrl+C zum Beenden.")

    # 6. Rekorder starten
    try:
        node.run()
    except KeyboardInterrupt:
        log.warning("Datenrekorder wird heruntergefahren...")
    except Exception as e:
        log.error(f"Laufzeitfehler: {e}")
    finally:
        node.stop()
        log.info("Datenrekorder erfolgreich beendet und Parquet-Dateien geflusht.")

if __name__ == "__main__":
    main()