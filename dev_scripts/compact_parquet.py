import os
import glob
import logging
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path("/opt/etoro_nautilus/data/nautilus/data/quote_tick")
LOG_DIR = Path("/opt/etoro_nautilus/logs")
LOG_FILE = LOG_DIR / "compact_parquet.log"

# Erstelle das Log-Verzeichnis, falls es noch nicht existiert
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Logging-Konfiguration initialisieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()  # Gibt es zusätzlich in der Shell aus
    ]
)
logger = logging.getLogger("ParquetCompactor")

def format_nautilus_timestamp(ts_ns: int) -> str:
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    base_time = dt.strftime('%Y-%m-%dT%H-%M-%S')
    ns_remainder = int(ts_ns % 1_000_000_000)
    return f"{base_time}-{ns_remainder:09d}Z"

def compact_directory(symbol_dir: Path):
    parquet_files = list(symbol_dir.glob("*.parquet"))
    
    # Nichts tun, wenn es nur 1 oder gar keine Datei gibt
    if len(parquet_files) <= 1:
        return

    logger.info(f"[{symbol_dir.name}] Starte Kompaktierung von {len(parquet_files)} Dateien...")
    
    dfs = []
    for file in parquet_files:
        try:
            dfs.append(pd.read_parquet(file))
        except Exception as e:
            logger.error(f"[{symbol_dir.name}] Fehler beim Lesen von {file.name}: {e}")

    if not dfs:
        return

    # Kombinieren, Duplikate entfernen und sortieren
    combined_df = pd.concat(dfs, ignore_index=True)
    if "ts_event" in combined_df.columns:
        combined_df = combined_df.drop_duplicates(subset=["ts_event"])
        combined_df = combined_df.sort_values(by="ts_event").reset_index(drop=True)
        
        min_ts = combined_df["ts_event"].min()
        max_ts = combined_df["ts_event"].max()
        
        new_filename = f"{format_nautilus_timestamp(min_ts)}_{format_nautilus_timestamp(max_ts)}.parquet"
        new_filepath = symbol_dir / new_filename
        
        try:
            # Neue kombinierte Datei speichern
            combined_df.to_parquet(new_filepath)
            
            # Alte, kleine Dateien erst NACH erfolgreichem Schreiben löschen
            deleted_count = 0
            for file in parquet_files:
                if file.name != new_filename:
                    file.unlink()
                    deleted_count += 1
                    
            logger.info(f"[{symbol_dir.name}] Erfolgreich kombiniert zu: {new_filename} (Zeilen: {len(combined_df)}). {deleted_count} alte Dateien gelöscht.")
        except Exception as e:
            logger.error(f"[{symbol_dir.name}] Kritischer Fehler beim Schreiben oder Bereinigen: {e}")

if __name__ == "__main__":
    logger.info("=== Parquet Compaction Run Started ===")
    
    if not DATA_DIR.exists():
        logger.error(f"Datenverzeichnis {DATA_DIR} nicht gefunden.")
        exit(1)
        
    for symbol_folder in DATA_DIR.iterdir():
        if symbol_folder.is_dir():
            compact_directory(symbol_folder)
            
    logger.info("=== Parquet Compaction Run Finished ===\n")