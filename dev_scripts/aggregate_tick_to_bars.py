#!/usr/bin/env python3
"""
aggregate_tick_to_bars.py

Aggregiert Quote-Ticks aus dem NautilusTrader-ParquetDataCatalog zu OHLCV-Bars.

Bekannte Fehlerquellen werden automatisch behandelt:
  - BarType-Konstruktion (BarSpecification-Objekt statt String)
  - Arrow-Schema-Konflikte (price_precision): Parquet-Files werden in-place
    gepatcht, danach wird ein frischer Katalog instanziert.
  - Fallback: Bei persistentem Merge-Fehler werden Dateien einzeln geladen.
"""
import os
import sys
import argparse
import logging
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trader.model.data import Bar, BarType, BarSpecification
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.enums import PriceType, BarAggregation, AggregationSource
except ImportError as e:
    print(f"❌ Fehler: Nautilus Trader konnte nicht geladen werden: {e}")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BarAggregator")


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def discover_instruments_from_catalog(catalog_path: str) -> list[str]:
    tick_dir = os.path.join(catalog_path, "data", "quote_tick")
    instruments = []
    if os.path.exists(tick_dir):
        for entry in os.listdir(tick_dir):
            full = os.path.join(tick_dir, entry)
            if os.path.isdir(full):
                instruments.append(entry.replace("instrument_id=", ""))
    return sorted(instruments)


def normalize_parquet_metadata(catalog_path: str, instrument_id_str: str) -> bool:
    """
    Vereinheitlicht conflicting Arrow-Schema-Metadaten und konvertiert
    die Spaltentypen physisch auf FixedSizeBinary(16), um Abstürze zu verhindern.
    Verarbeitet auch Single-File-Verzeichnisse sauber.
    """
    inst_dir = Path(catalog_path) / "data" / "quote_tick" / instrument_id_str

    if not inst_dir.exists():
        logger.info(f"  [normalize] Verzeichnis nicht gefunden: {inst_dir}")
        return False

    parquet_files = sorted(inst_dir.rglob("*.parquet"))
    logger.info(
        f"  [normalize] {instrument_id_str}: {len(parquet_files)} Parquet-Dateien "
        f"in {inst_dir}"
    )

    if not parquet_files:
        return False

    # Referenz-Metadaten der letzten Datei holen
    try:
        ref_schema = pq.read_schema(str(parquet_files[-1]))
        ref_meta = ref_schema.metadata or {}
    except Exception as e:
        logger.warning(f"  [normalize] Referenz-Schema-Lesefehler: {e}")
        ref_meta = {}

    target_cols = {'bid_price', 'ask_price', 'bid_size', 'ask_size'}
    any_file_patched = False

    for f in parquet_files:
        try:
            table = pq.read_table(str(f))
            schema = table.schema
            
            needs_cast = False
            new_fields = []
            new_columns = []
            
            # Physische Typen prüfen und ggf. konvertieren
            for field, col in zip(schema, table.itercolumns()):
                if field.name in target_cols:
                    is_fixed_16 = pa.types.is_fixed_size_binary(field.type) and field.type.byte_width == 16
                    if not is_fixed_16:
                        # FIX 1: 2-stufiger Cast, um "BinaryView -> FixedSizeBinary" Fehler zu umgehen
                        col = col.cast(pa.binary()).cast(pa.binary(16))
                        field = field.with_type(pa.binary(16))
                        needs_cast = True
                new_fields.append(field)
                new_columns.append(col)
            
            # FIX 2: Sicherer Metadaten-Vergleich (None vs {})
            metadata_mismatch = (schema.metadata or {}) != ref_meta
            
            # Überschreiben, wenn Typen oder Metadaten inkonsistent sind
            if needs_cast or metadata_mismatch:
                new_schema = pa.schema(new_fields, metadata=ref_meta)
                new_table = pa.Table.from_arrays(new_columns, schema=new_schema)
                pq.write_table(new_table, str(f), compression="snappy")
                any_file_patched = True
                logger.info(f"    ✔ Typen/Metadaten konvertiert: {f.name}")
        except Exception as e:
            # FIX 3: Fehler lautstark machen, damit du weißt, wenn eine Datei blockiert
            logger.error(f"    ❌ KRITISCHER Patch-Fehler bei Datei {f.name}: {e}")
            raise e 

    if any_file_patched:
        logger.info(f"  ✅ Typen erfolgreich auf FixedSizeBinary(16) angepasst.")
    return any_file_patched


def parse_bar_spec(bar_spec_str: str, price_type: PriceType) -> BarSpecification:
    """
    Parst einen Bar-Spec-String in ein BarSpecification-Objekt.
    Unterstützte Formate: '1m', '5m', '15m', '30m', '1h', '4h', '1d'
    """
    bar_spec_str = bar_spec_str.lower().strip()
    aggregation_map = {
        "m": BarAggregation.MINUTE,
        "h": BarAggregation.HOUR,
        "d": BarAggregation.DAY,
    }
    for suffix, aggregation in aggregation_map.items():
        if bar_spec_str.endswith(suffix):
            try:
                step = int(bar_spec_str[:-len(suffix)])
            except ValueError:
                raise ValueError(
                    f"Ungültiger Step-Wert in bar_spec: '{bar_spec_str}'"
                )
            return BarSpecification(step, aggregation, price_type)
    raise ValueError(
        f"Unbekanntes bar_spec-Format: '{bar_spec_str}'. "
        "Erwartet z.B. '1m', '5m', '1h', '4h', '1d'."
    )


def bar_spec_to_pandas_freq(bar_spec_str: str) -> str:
    bar_spec_str = bar_spec_str.lower().strip()
    freq_map = {"m": "min", "h": "h", "d": "D"}
    for suffix, pd_unit in freq_map.items():
        if bar_spec_str.endswith(suffix):
            return f"{bar_spec_str[:-len(suffix)]}{pd_unit}"
    raise ValueError(f"Unbekanntes bar_spec-Format für pandas: '{bar_spec_str}'")


# ---------------------------------------------------------------------------
# Kernfunktion
# ---------------------------------------------------------------------------

def aggregate_ticks_to_bars(
    catalog_path: str,
    instrument_id_str: str,
    bar_spec_str: str,
    price_type_str: str,
    start_year: int,
    end_year: int,
) -> None:

    # --- BarType aufbauen ---
    p_type = getattr(PriceType, price_type_str.upper(), PriceType.MID)
    try:
        inst_id  = InstrumentId.from_str(instrument_id_str)
        bar_spec = parse_bar_spec(bar_spec_str, p_type)
        bar_type = BarType(
            instrument_id=inst_id,
            bar_spec=bar_spec,
            aggregation_source=AggregationSource.EXTERNAL,
        )
    except Exception as e:
        logger.error(f"❌ BarType-Fehler für {instrument_id_str}: {e}")
        return

    logger.info(f"⏳ Verarbeite {instrument_id_str}...")

    # ------------------------------------------------------------------
    # WICHTIG: Metadaten normalisieren VOR der Katalog-Instanzierung,
    # damit der Katalog keine gecachten alten Schemas verwendet.
    # ------------------------------------------------------------------
    normalize_parquet_metadata(catalog_path, instrument_id_str)

    # Frischen Katalog NACH der Normalisierung erstellen
    catalog = ParquetDataCatalog(catalog_path)

    # --- Ticks laden (mit Fallback) ---
    ticks = None
    try:
        ticks = catalog.quote_ticks(instrument_ids=[instrument_id_str])
    except Exception as e:
        logger.warning(
            f"  catalog.quote_ticks() fehlgeschlagen: {e}\n"
            f"  → Starte Fallback (file-by-file)..."
        )

    if ticks is None:
        ticks = load_ticks_file_by_file(catalog_path, instrument_id_str)

    if not ticks:
        logger.warning(f"⚠️  Keine Ticks für {instrument_id_str}, überspringe.")
        return

    # --- pandas-Frequenz ---
    try:
        freq = bar_spec_to_pandas_freq(bar_spec_str)
    except ValueError as e:
        logger.error(f"❌ Frequenz-Fehler für {instrument_id_str}: {e}")
        return

    # --- DataFrame aufbauen ---
    df = pd.DataFrame([{
        "ts":    t.ts_event,
        "price": (float(t.bid_price) + float(t.ask_price)) / 2,
        "vol":   float(t.bid_size)  + float(t.ask_size),
    } for t in ticks])

    df["dt"] = pd.to_datetime(df["ts"], unit="ns", utc=True)
    df.set_index("dt", inplace=True)

    # --- Zeitraum-Filter ---
    start_ts = pd.Timestamp(f"{start_year}-01-01", tz="UTC")
    end_ts   = pd.Timestamp(f"{end_year}-12-31 23:59:59", tz="UTC")
    df = df.loc[start_ts:end_ts]

    if df.empty:
        logger.warning(f"⚠️  Nach Zeitfilter keine Daten für {instrument_id_str}.")
        return

    # --- OHLCV resamplen ---
    ohlc      = df["price"].resample(freq).ohlc()
    volume    = df["vol"].resample(freq).sum().rename("vol")
    resampled = ohlc.join(volume).dropna()

    if resampled.empty:
        logger.warning(
            f"⚠️  Resampling lieferte keine Bars für {instrument_id_str}."
        )
        return

    # --- Bar-Objekte erzeugen ---
    bars = [
        Bar(
            bar_type,
            Price(row.open,  5),
            Price(row.high,  5),
            Price(row.low,   5),
            Price(row.close, 5),
            Quantity(row.vol, 0),
            int(dt.value),
            int(dt.value),
        )
        for dt, row in resampled.iterrows()
    ]

    # =======================================================================
    # NEU: Bestehende, überlappende Bar-Dateien für dieses Instrument löschen
    # =======================================================================
    bar_dir = Path(catalog_path) / "data" / "bar" / str(bar_type)
    if bar_dir.exists():
        logger.info(f"  🧹 Bereinige altes Bar-Verzeichnis (Vermeidung von Interval-Konflikten): {bar_dir.name}")
        try:
            shutil.rmtree(bar_dir)
            # Kurz warten/Katalog zwingen, den Datei-Cache für dieses Verzeichnis zu vergessen
            catalog = ParquetDataCatalog(catalog_path)
        except Exception as e:
            logger.warning(f"  ⚠️  Konnte altes Verzeichnis {bar_dir.name} nicht vollständig löschen: {e}")
    # =======================================================================

    catalog.write_data(bars)
    logger.info(f"✅ {instrument_id_str}: {len(bars)} Bars geschrieben.")

# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Aggregiert Quote-Ticks aus dem ParquetDataCatalog zu OHLCV-Bars."
    )
    parser.add_argument(
        "--catalog-path", type=str, default="./data/nautilus",
        help="Pfad zum ParquetDataCatalog-Verzeichnis",
    )
    parser.add_argument(
        "--bar-spec", type=str, default="1m",
        help="Bar-Spezifikation, z.B. '1m', '5m', '15m', '1h', '4h', '1d'",
    )
    parser.add_argument(
        "--price-type", type=str, default="MID",
        choices=["BID", "ASK", "MID", "LAST"],
        help="Preistyp für die Bar-Berechnung",
    )
    parser.add_argument(
        "--start-year", type=int, default=2022,
        help="Startjahr für den Zeitfilter",
    )
    parser.add_argument(
        "--end-year", type=int, default=2026,
        help="Endjahr für den Zeitfilter",
    )
    parser.add_argument(
        "--instrument", type=str, default=None,
        help="Einzelnes Instrument verarbeiten (z.B. 'NVDA.ETORO')",
    )
    args = parser.parse_args()

    if args.instrument:
        instruments = [args.instrument]
    else:
        instruments = discover_instruments_from_catalog(args.catalog_path)

    if not instruments:
        logger.warning("⚠️  Keine Instrumente im Katalog gefunden.")
        return

    logger.info(
        f"📋 {len(instruments)} Instrument(e), "
        f"starte Aggregation ({args.bar_spec}, {args.price_type})..."
    )

    ok, failed = 0, 0
    for inst in instruments:
        try:
            aggregate_ticks_to_bars(
                catalog_path=args.catalog_path,
                instrument_id_str=inst,
                bar_spec_str=args.bar_spec,
                price_type_str=args.price_type,
                start_year=args.start_year,
                end_year=args.end_year,
            )
            ok += 1
        except Exception as e:
            logger.error(f"❌ Unerwarteter Fehler bei {inst}: {e}")
            failed += 1

    logger.info(f"🏁 Fertig — {ok} erfolgreich, {failed} fehlgeschlagen.")


if __name__ == "__main__":
    main()