#!/usr/bin/env python3
"""
repair_avax_precision.py  –  Löst den price_precision-Konflikt (2 vs 5) bei
AVAX.ETORO durch Neuschreiben der historischen Datei mit korrekter Precision.

Hintergrund: Die historische Datei wurde von momentum_ls_fetch_candles_auto.py
mit price_precision=2 geschrieben (Price-Objekte als bytes serialisiert).
Die neueren Dateien haben price_precision=5. Nautilus erwartet eine konsistente
Precision pro Instrument über alle Dateien.

Strategie:
  1. Historische Datei lesen und Rohdaten (bid/ask als Dezimalstrings) extrahieren.
  2. Neue QuoteTick-Objekte mit precision=5 erstellen.
  3. Via ParquetDataCatalog neu schreiben, alte Datei löschen.

Aufruf:
    python3 dev_scripts/repair_avax_precision.py
    python3 dev_scripts/repair_avax_precision.py --dry-run
    python3 dev_scripts/repair_avax_precision.py --catalog-path /opt/etoro_nautilus/data/nautilus
"""
import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

# Nautilus-Importe – müssen im Projektkontext verfügbar sein
try:
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    import pyarrow.parquet as pq
    import pandas as pd
    import pyarrow as pa
except ImportError as e:
    print(f"[ERROR] Import fehlgeschlagen: {e}")
    print("Stelle sicher, dass die nautilus_trader-Umgebung aktiv ist.")
    sys.exit(1)


TARGET_INSTRUMENT = "AVAX.ETORO"
TARGET_PRECISION = 5  # Kanonische Precision (Mehrheit)
CONFLICT_PRECISION = 2  # Precision der historischen Datei


def deserialize_price_bytes(price_bytes: bytes, old_precision: int) -> float:
    """
    Nautilus serialisiert Price-Objekte als bytes via FixedPrecision-Encoding.
    Das Format ist ein int64 (little-endian), der den Wert * 10^precision darstellt.
    """
    import struct
    raw_int = struct.unpack('<q', price_bytes)[0]
    return raw_int / (10 ** old_precision)


def find_conflict_file(symbol_dir: Path) -> Path | None:
    """Findet die Datei mit der abweichenden price_precision."""
    for f in symbol_dir.glob("*.parquet"):
        try:
            schema = pq.read_schema(str(f))
            meta = schema.metadata or {}
            if meta.get(b"price_precision") == str(CONFLICT_PRECISION).encode():
                return f
        except Exception:
            pass
    return None


def serialize_price(value: float, precision: int) -> bytes:
    """Price → Nautilus bytes (FixedPrecision int64 little-endian)."""
    import struct
    raw_int = round(value * (10 ** precision))
    return struct.pack('<q', raw_int)


def repair_avax(catalog_path: str, dry_run: bool):
    root = Path(catalog_path) / "data" / "quote_tick"

    # Beide Verzeichnisvarianten prüfen (plain + hive-style)
    candidates = [
        root / TARGET_INSTRUMENT,
        root / f"instrument_id={TARGET_INSTRUMENT}",
    ]
    symbol_dir = next((d for d in candidates if d.is_dir()), None)

    if symbol_dir is None:
        print(f"[ERROR] Verzeichnis für {TARGET_INSTRUMENT} nicht gefunden.")
        sys.exit(1)

    conflict_file = find_conflict_file(symbol_dir)
    if conflict_file is None:
        print(f"[OK] Kein Konflikt gefunden – {TARGET_INSTRUMENT} ist bereits konsistent.")
        return

    print(f"[{TARGET_INSTRUMENT}] Konflikt-Datei: {conflict_file.name}")
    print(f"  Lese {conflict_file.stat().st_size / 1024 / 1024:.1f} MB...")

    # Rohdaten lesen
    df = pd.read_parquet(conflict_file)
    print(f"  Zeilen: {len(df)}")
    print(f"  Spalten: {list(df.columns)}")

    if "ts_event" not in df.columns or "bid_price" not in df.columns:
        print("[ERROR] Erwartete Spalten fehlen. Abbruch.")
        sys.exit(1)

    if dry_run:
        print(f"\n[DRY-RUN] Würde {len(df)} Ticks mit precision={TARGET_PRECISION} neu schreiben.")
        print(f"[DRY-RUN] Quelldatei würde danach gelöscht: {conflict_file.name}")
        return

    print(f"  Erstelle neue QuoteTick-Objekte mit precision={TARGET_PRECISION}...")

    inst_id = InstrumentId.from_str(TARGET_INSTRUMENT)
    ticks = []
    errors = 0

    for _, row in df.iterrows():
        try:
            bid_raw = row["bid_price"]
            ask_raw = row["ask_price"]
            bid_size_raw = row.get("bid_size")
            ask_size_raw = row.get("ask_size")
            ts_event = int(row["ts_event"])
            ts_init = int(row.get("ts_init", ts_event))

            # Preis aus bytes deserialisieren (old precision)
            if isinstance(bid_raw, (bytes, bytearray)):
                bid_val = deserialize_price_bytes(bytes(bid_raw), CONFLICT_PRECISION)
                ask_val = deserialize_price_bytes(bytes(ask_raw), CONFLICT_PRECISION)
            else:
                # Falls bereits als float/int gespeichert
                bid_val = float(bid_raw)
                ask_val = float(ask_raw)

            # Bid/Ask-Size
            if bid_size_raw is not None and isinstance(bid_size_raw, (bytes, bytearray)):
                import struct
                bid_size_val = struct.unpack('<q', bytes(bid_size_raw))[0]
                ask_size_val = struct.unpack('<q', bytes(ask_size_raw))[0]
            else:
                bid_size_val = 1
                ask_size_val = 1

            tick = QuoteTick(
                instrument_id=inst_id,
                bid_price=Price(bid_val, precision=TARGET_PRECISION),
                ask_price=Price(ask_val, precision=TARGET_PRECISION),
                bid_size=Quantity(bid_size_val, precision=0),
                ask_size=Quantity(ask_size_val, precision=0),
                ts_event=ts_event,
                ts_init=ts_init,
            )
            ticks.append(tick)
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  [WARN] Fehler bei Zeile: {e}")

    if errors > 0:
        print(f"  [WARN] {errors} Zeilen konnten nicht konvertiert werden.")

    if not ticks:
        print("[ERROR] Keine Ticks konvertiert. Abbruch.")
        sys.exit(1)

    ticks.sort(key=lambda t: t.ts_init)
    print(f"  {len(ticks)} Ticks konvertiert. Schreibe in Katalog...")

    catalog = ParquetDataCatalog(catalog_path)
    catalog.write_data(ticks)

    print(f"  Lösche alte Konflikt-Datei: {conflict_file.name}")
    conflict_file.unlink()

    print(f"\n[OK] AVAX.ETORO Reparatur abgeschlossen.")
    print(f"     {len(ticks)} Ticks mit price_precision={TARGET_PRECISION} neu geschrieben.")
    print(f"     {errors} Fehler.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-path", default="data/nautilus")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"=== AVAX.ETORO Precision-Konflikt Repair ({CONFLICT_PRECISION} → {TARGET_PRECISION}) ===")
    if args.dry_run:
        print("=== DRY-RUN – keine Änderungen ===")

    repair_avax(args.catalog_path, args.dry_run)


if __name__ == "__main__":
    main()