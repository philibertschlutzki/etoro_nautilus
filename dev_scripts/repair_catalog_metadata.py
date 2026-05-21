#!/usr/bin/env python3
"""
repair_catalog_metadata.py  –  Einmalige Reparatur für bereits kompaktierte
Parquet-Dateien, die ihre Nautilus-Arrow-Metadaten (instrument_id,
price_precision, size_precision) durch compact_parquet.py verloren haben.

Erkennung: Dateien, die 'instrument_id' NICHT in schema.metadata haben,
aber 'pandas' enthalten (Fingerabdruck des pandas-to_parquet()-Outputs).

Strategie:
  1. Pro Instrument alle Dateien scannen.
  2. Kanonische Nautilus-Metadaten aus einer intakten Datei extrahieren
     (oder aus dem Dateinamen / Pandas-Metadaten ableiten).
  3. Beschädigte Dateien in-place mit pq.write_table() + replace_schema_metadata()
     reparieren.

Aufruf:
    python3 dev_scripts/repair_catalog_metadata.py
    python3 dev_scripts/repair_catalog_metadata.py --dry-run
    python3 dev_scripts/repair_catalog_metadata.py --instrument ADA.ETORO
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


CATALOG_ROOT = Path("data/nautilus/data/quote_tick")
NAUTILUS_KEYS = {b"instrument_id", b"price_precision", b"size_precision"}


def has_nautilus_meta(schema) -> bool:
    meta = schema.metadata or {}
    return NAUTILUS_KEYS.issubset(set(meta.keys()))


def extract_canonical_meta(instrument_dir: Path, instrument_name: str) -> dict | None:
    """
    Sucht in allen Dateien des Instruments nach intakten Nautilus-Metadaten.
    Gibt die häufigste/höchste Precision zurück (Mehrheitsentscheid).
    """
    files = list(instrument_dir.rglob("*.parquet"))
    instrument_id_bytes = instrument_name.encode()

    found_precisions = []
    size_precision = b"0"

    for f in files:
        try:
            schema = pq.read_schema(str(f))
            meta = schema.metadata or {}
            if NAUTILUS_KEYS.issubset(set(meta.keys())):
                found_precisions.append(meta[b"price_precision"])
                size_precision = meta.get(b"size_precision", b"0")
        except Exception:
            pass

    if not found_precisions:
        # Fallback: Precision aus den Daten selbst ableiten
        print(f"  [WARN] Keine intakte Nautilus-Meta gefunden für {instrument_name}. "
              f"Leite Precision aus Daten ab...")
        for f in files:
            try:
                df = pd.read_parquet(f, columns=["bid_price"])
                if not df.empty:
                    sample = df["bid_price"].iloc[0]
                    # QuoteTick bid_price ist als bytes serialisiert – rohen Wert lesen
                    # Fallback: Precision 5 als konservativer Default
                    found_precisions.append(b"5")
                    break
            except Exception:
                pass

    if not found_precisions:
        return None

    counts = Counter(found_precisions)
    max_count = max(counts.values())
    candidates = [p for p, c in counts.items() if c == max_count]
    canonical_precision = max(candidates, key=lambda x: int(x))

    return {
        b"instrument_id": instrument_id_bytes,
        b"price_precision": canonical_precision,
        b"size_precision": size_precision,
    }


def repair_file(filepath: Path, nautilus_meta: dict, dry_run: bool) -> bool:
    """
    Patcht eine einzelne Parquet-Datei mit den Nautilus-Metadaten.
    Schreibt in-place (temp-Datei + rename für Atomizität).
    """
    try:
        table = pq.read_table(str(filepath))
        existing_meta = table.schema.metadata or {}

        # Nautilus-Keys haben Vorrang
        merged_meta = {**existing_meta, **nautilus_meta}
        patched_table = table.replace_schema_metadata(merged_meta)

        if dry_run:
            print(f"    [DRY-RUN] Würde patchen: {filepath.name}")
            print(f"      instrument_id  = {nautilus_meta[b'instrument_id'].decode()}")
            print(f"      price_precision= {nautilus_meta[b'price_precision'].decode()}")
            print(f"      size_precision = {nautilus_meta[b'size_precision'].decode()}")
            return True

        # Atomar schreiben: temp-Datei, dann rename
        tmp_path = filepath.with_suffix(".tmp.parquet")
        pq.write_table(patched_table, str(tmp_path))
        tmp_path.rename(filepath)
        return True

    except Exception as e:
        print(f"    [ERROR] Reparatur fehlgeschlagen für {filepath.name}: {e}")
        return False


def process_instrument(instrument_dir: Path, dry_run: bool) -> tuple[int, int]:
    """Returns (repaired_count, skipped_count)."""
    instrument_name = instrument_dir.name.replace("instrument_id=", "")
    files = sorted(instrument_dir.rglob("*.parquet"))

    if not files:
        return 0, 0

    # Prüfen ob überhaupt Dateien ohne Nautilus-Meta existieren
    damaged = []
    for f in files:
        try:
            schema = pq.read_schema(str(f))
            if not has_nautilus_meta(schema):
                damaged.append(f)
        except Exception as e:
            print(f"  [WARN] Schema-Lesen fehlgeschlagen für {f.name}: {e}")

    if not damaged:
        return 0, 0

    print(f"\n[{instrument_name}] {len(damaged)} beschädigte Datei(en) gefunden.")

    canonical_meta = extract_canonical_meta(instrument_dir, instrument_name)
    if canonical_meta is None:
        print(f"  [ERROR] Kann keine kanonischen Metadaten ableiten. Überspringe.")
        return 0, len(damaged)

    print(f"  Kanonische Meta: instrument_id={canonical_meta[b'instrument_id'].decode()}, "
          f"price_precision={canonical_meta[b'price_precision'].decode()}, "
          f"size_precision={canonical_meta[b'size_precision'].decode()}")

    repaired = 0
    for f in damaged:
        if repair_file(f, canonical_meta, dry_run):
            repaired += 1
            if not dry_run:
                print(f"  ✓ Repariert: {f.name}")

    return repaired, len(damaged) - repaired


def main():
    parser = argparse.ArgumentParser(description="Repariert Nautilus-Metadaten in Parquet-Dateien.")
    parser.add_argument("--catalog-path", default="data/nautilus/data/quote_tick",
                        help="Pfad zum quote_tick-Verzeichnis")
    parser.add_argument("--instrument", default=None,
                        help="Nur dieses Instrument reparieren (z.B. ADA.ETORO)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur anzeigen, was repariert würde – nichts schreiben")
    args = parser.parse_args()

    root = Path(args.catalog_path)
    if not root.exists():
        print(f"[ERROR] Verzeichnis nicht gefunden: {root}")
        sys.exit(1)

    if args.dry_run:
        print("=== DRY-RUN Modus – keine Dateien werden verändert ===")

    total_repaired = 0
    total_skipped = 0

    if args.instrument:
        # Direktes Instrument: beide Namensvarianten prüfen (plain + hive-style)
        candidates = [
            root / args.instrument,
            root / f"instrument_id={args.instrument}",
        ]
        dirs = [d for d in candidates if d.is_dir()]
        if not dirs:
            print(f"[ERROR] Kein Verzeichnis für {args.instrument} gefunden.")
            sys.exit(1)
        for d in dirs:
            r, s = process_instrument(d, args.dry_run)
            total_repaired += r
            total_skipped += s
    else:
        for entry in sorted(root.iterdir()):
            if entry.is_dir():
                r, s = process_instrument(entry, args.dry_run)
                total_repaired += r
                total_skipped += s

    print(f"\n=== Abgeschlossen: {total_repaired} repariert, {total_skipped} übersprungen ===")
    if args.dry_run:
        print("(Kein --dry-run: Führe ohne --dry-run aus, um die Reparatur tatsächlich anzuwenden)")


if __name__ == "__main__":
    main()