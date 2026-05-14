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
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.model.data import QuoteTick

# Adapter & Config Importe
from adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from config.setups import ACTIVE_BOTS

# Lade API Keys
load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

# Pfad für die Parquet-Katalog Dateien
CATALOG_PATH = Path(__file__).parent / "data" / "nautilus"

# 1. Definieren der Aufzeichnungs-Strategie (Data Recorder)
class DataRecorderConfig(StrategyConfig, frozen=True):
    catalog_path: str
    instrument_ids: list[str]
    buffer_size: int = 1000  # Wie viele Ticks gesammelt werden, bevor auf die Festplatte geschrieben wird

class DataRecorderStrategy(Strategy):
    def __init__(self, config: DataRecorderConfig):
        super().__init__(config)
        self.catalog_path = config.catalog_path
        self.instrument_ids = config.instrument_ids
        self.buffer_size = config.buffer_size
        self.ticks_buffer = []
        self.catalog = None

    def on_start(self):
        # Katalog initialisieren
        self.catalog = ParquetDataCatalog(self.catalog_path)
        
        # Alle Instrumente abonnieren
        for sym in self.instrument_ids:
            instr_id = InstrumentId.from_str(sym)
            self.subscribe_quote_ticks(instr_id)
            self.log.info(f"Abonniert für Aufzeichnung: {instr_id}")

    def on_quote_tick(self, tick: QuoteTick):
        self.ticks_buffer.append(tick)
        # Wenn der Puffer voll ist -> auf Festplatte schreiben
        if len(self.ticks_buffer) >= self.buffer_size:
            self._flush()

    def on_stop(self):
        # Beim Herunterfahren noch die restlichen Ticks wegschreiben
        self._flush()

    def _flush(self):
        if self.ticks_buffer:
            self.catalog.write_data(self.ticks_buffer)
            self.log.info(f"{len(self.ticks_buffer)} Ticks erfolgreich in den Katalog ({self.catalog_path}) geschrieben.")
            self.ticks_buffer.clear()

def main():
    CATALOG_PATH.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("DataRecorder")

    if not API_KEY or not USER_KEY:
        log.error("FEHLER: API_KEY oder USER_KEY in .env nicht gefunden!")
        sys.exit(1)

    # 2. Sammle alle benötigten eToro IDs & Symbole dynamisch aus der config/setups.py
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

    # 3. Nautilus Node Konfigurieren
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

    # 4. Data Recorder Strategie konfigurieren und zum Node hinzufügen
    recorder_config = DataRecorderConfig(
        catalog_path=str(CATALOG_PATH),
        instrument_ids=symbols_to_record,
        buffer_size=1000  # Alle 1000 Ticks wird auf die SSD geflusht
    )
    recorder = DataRecorderStrategy(config=recorder_config)
    node.trader.add_strategy(recorder)

    # 5. Node aufbauen und starten
    node.build()

    log.info(f"Starte passiven eToro Datenrekorder für {len(symbols_to_record)} Instrumente.")
    log.info(f"Die Parquet-Daten werden unter {CATALOG_PATH} gespeichert.")
    log.info("Drücke Ctrl+C zum Beenden.")

    try:
        node.run()
    except KeyboardInterrupt:
        log.warning("Datenrekorder wird heruntergefahren...")
    except Exception as e:
        log.error(f"Laufzeitfehler: {e}")
    finally:
        node.stop()
        log.info("Datenrekorder erfolgreich beendet.")

if __name__ == "__main__":
    main()