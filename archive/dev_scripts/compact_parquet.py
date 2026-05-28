import os
import glob
import logging
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path("/opt/etoro_nautilus/data/nautilus/data/quote_tick")
LOG_DIR = Path("/opt/etoro_nautilus/logs")
LOG_FILE = LOG_DIR / "compact_parquet.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ParquetCompactor")


def format_nautilus_timestamp(ts_ns: int) -> str:
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    base_time = dt.strftime('%Y-%m-%dT%H-%M-%S')
    ns_remainder = int(ts_ns % 1_000_000_000)
    return f"{base_time}-{ns_remainder:09d}Z"


def _extract_nautilus_metadata(parquet_files: list[Path]) -> dict | None:
    """
    Extrahiert die Nautilus-spezifischen Arrow-Schema-Metadaten (instrument_id,
    price_precision, size_precision) aus der ersten Quelldatei, die diese enthält.

    Hintergrund: compact_parquet.py nutzte bisher pd.read_parquet() +
    df.to_parquet(), was den Arrow-Schema-Blob vollständig durch pandas-eigene
    Metadaten ersetzt. Dadurch verliert ParquetDataCatalog beim Backtesting die
    Zuordnung zum Instrument.

    Returns:
        dict mit bytes-Keys wie b'instrument_id', b'price_precision', etc.
        oder None, wenn keine Nautilus-Metadaten gefunden wurden.
    """
    nautilus_keys = {b"instrument_id", b"price_precision", b"size_precision"}
    for f in parquet_files:
        try:
            schema = pq.read_schema(str(f))
            meta = schema.metadata
            if meta and nautilus_keys.issubset(set(meta.keys())):
                # Nur die Nautilus-Keys zurückgeben, pandas-Keys weglassen
                return {k: v for k, v in meta.items() if k in nautilus_keys}
        except Exception as e:
            logger.warning(f"Konnte Schema aus {f.name} nicht lesen: {e}")
    return None


def _resolve_price_precision(parquet_files: list[Path]) -> bytes | None:
    """
    Bestimmt die kanonische price_precision für ein Instrument.

    Problem: momentum_ls_fetch_candles_auto.py berechnet die Precision dynamisch
    pro Candle (min(max(prec, 2), 8)), was zu inkonsistenten Werten führt (z.B.
    AVAX: mal 2, mal 5). Nautilus erwartet beim Deserialisieren eine konsistente
    Precision über alle Dateien eines Instruments.

    Strategie: Nimm die häufigste Precision (Mehrheitsentscheid). Bei Gleichstand
    wird die höhere Precision bevorzugt, um Rundungsverluste zu vermeiden.
    """
    from collections import Counter
    precisions = []
    for f in parquet_files:
        try:
            schema = pq.read_schema(str(f))
            meta = schema.metadata or {}
            if b"price_precision" in meta:
                precisions.append(meta[b"price_precision"])
        except Exception:
            pass

    if not precisions:
        return None

    counts = Counter(precisions)
    # Mehrheitsentscheid; bei Gleichstand: höchste Precision gewinnt
    max_count = max(counts.values())
    candidates = [p for p, c in counts.items() if c == max_count]
    return max(candidates, key=lambda x: int(x))


def compact_directory(symbol_dir: Path):
    parquet_files = list(symbol_dir.glob("*.parquet"))

    if len(parquet_files) <= 1:
        return

    logger.info(f"[{symbol_dir.name}] Starte Kompaktierung von {len(parquet_files)} Dateien...")

    # --- Schritt 1: Nautilus-Metadaten vor dem Lesen sichern ---
    nautilus_meta = _extract_nautilus_metadata(parquet_files)
    if nautilus_meta is None:
        logger.warning(
            f"[{symbol_dir.name}] Keine Nautilus-Schema-Metadaten gefunden. "
            f"Kompaktierung wird trotzdem durchgeführt, aber Backtesting-Kompatibilität "
            f"ist nicht garantiert."
        )
    else:
        # Kanonische Precision auflösen und in die Metadaten schreiben
        canonical_precision = _resolve_price_precision(parquet_files)
        if canonical_precision is not None:
            original_precision = nautilus_meta.get(b"price_precision")
            if original_precision != canonical_precision:
                logger.warning(
                    f"[{symbol_dir.name}] price_precision-Konflikt entdeckt. "
                    f"Setze kanonischen Wert: {canonical_precision.decode()} "
                    f"(war: {original_precision.decode() if original_precision else 'unbekannt'})"
                )
            nautilus_meta[b"price_precision"] = canonical_precision

    # --- Schritt 2: Alle Dateien lesen und kombinieren ---
    dfs = []
    for file in parquet_files:
        try:
            dfs.append(pd.read_parquet(file))
        except Exception as e:
            logger.error(f"[{symbol_dir.name}] Fehler beim Lesen von {file.name}: {e}")

    if not dfs:
        return

    combined_df = pd.concat(dfs, ignore_index=True)

    if "ts_event" not in combined_df.columns:
        logger.error(f"[{symbol_dir.name}] Spalte 'ts_event' fehlt. Überspringe.")
        return

    combined_df = combined_df.drop_duplicates(subset=["ts_event"])
    combined_df = combined_df.sort_values(by="ts_event").reset_index(drop=True)

    min_ts = combined_df["ts_event"].min()
    max_ts = combined_df["ts_event"].max()

    new_filename = (
        f"{format_nautilus_timestamp(min_ts)}_{format_nautilus_timestamp(max_ts)}.parquet"
    )
    new_filepath = symbol_dir / new_filename

    try:
        # --- Schritt 3: Mit PyArrow schreiben, Nautilus-Metadaten übertragen ---
        # pd.to_parquet() würde hier die Nautilus-Metadaten durch pandas-Metadaten
        # ersetzen. Stattdessen: DataFrame → PyArrow Table → Metadaten patchen →
        # pq.write_table().
        arrow_table = pa.Table.from_pandas(combined_df, preserve_index=False)

        if nautilus_meta is not None:
            # Bestehende Schema-Metadaten (pandas) mit Nautilus-Keys mergen.
            # Nautilus-Keys haben Vorrang, da sie für den Catalog zwingend sind.
            existing_meta = arrow_table.schema.metadata or {}
            merged_meta = {**existing_meta, **nautilus_meta}
            arrow_table = arrow_table.replace_schema_metadata(merged_meta)

        pq.write_table(arrow_table, str(new_filepath))

        # Alte Dateien erst nach erfolgreichem Schreiben löschen
        deleted_count = 0
        for file in parquet_files:
            if file.name != new_filename:
                file.unlink()
                deleted_count += 1

        logger.info(
            f"[{symbol_dir.name}] Erfolgreich kombiniert zu: {new_filename} "
            f"(Zeilen: {len(combined_df)}, Nautilus-Meta: {'ja' if nautilus_meta else 'nein'}). "
            f"{deleted_count} alte Dateien gelöscht."
        )

    except Exception as e:
        logger.error(
            f"[{symbol_dir.name}] Kritischer Fehler beim Schreiben oder Bereinigen: {e}"
        )
        # Neue (möglicherweise korrupte) Datei entfernen, falls sie existiert
        if new_filepath.exists():
            new_filepath.unlink()


if __name__ == "__main__":
    logger.info("=== Parquet Compaction Run Started ===")

    if not DATA_DIR.exists():
        logger.error(f"Datenverzeichnis {DATA_DIR} nicht gefunden.")
        exit(1)

    for symbol_folder in DATA_DIR.iterdir():
        if symbol_folder.is_dir():
            compact_directory(symbol_folder)

    logger.info("=== Parquet Compaction Run Finished ===\n")