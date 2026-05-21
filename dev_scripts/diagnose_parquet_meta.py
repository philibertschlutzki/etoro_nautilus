#!/usr/bin/env python3
"""
diagnose_parquet_meta.py  –  Zeigt Arrow-Schema-Metadaten aller Parquet-Dateien
eines Instruments an. Vor dem eigentlichen Fix ausführen um zu verstehen
welche Metadaten-Keys tatsächlich in den Dateien stecken.

Aufruf:
    python3 dev_scripts/diagnose_parquet_meta.py --instrument ACN.ETORO
    python3 dev_scripts/diagnose_parquet_meta.py --all
"""
import argparse
import os
import sys
from pathlib import Path
import pyarrow.parquet as pq


def diagnose_instrument(catalog_path: str, instrument_id_str: str) -> None:
    inst_dir = Path(catalog_path) / "data" / "quote_tick" / instrument_id_str

    if not inst_dir.exists():
        print(f"[ERROR] Verzeichnis existiert nicht: {inst_dir}")
        return

    # Rekursive Suche
    parquet_files = sorted(inst_dir.rglob("*.parquet"))
    print(f"\n{'='*70}")
    print(f"Instrument : {instrument_id_str}")
    print(f"Verzeichnis: {inst_dir}")
    print(f"Dateien    : {len(parquet_files)} (rekursiv)")
    print(f"{'='*70}")

    if not parquet_files:
        print("  [WARN] Keine Parquet-Dateien gefunden!")
        return

    for f in parquet_files:
        print(f"\n  Datei: {f.relative_to(inst_dir)}")
        try:
            schema = pq.read_schema(str(f))
            meta = schema.metadata
            if meta is None:
                print("    schema.metadata = None")
            else:
                print(f"    schema.metadata ({len(meta)} Einträge):")
                for k, v in sorted(meta.items()):
                    # Decode bytes für Lesbarkeit
                    dk = k.decode() if isinstance(k, bytes) else str(k)
                    dv = v.decode() if isinstance(v, bytes) else str(v)
                    print(f"      [{type(k).__name__}] {dk!r:30s} = {dv!r}")
        except Exception as e:
            print(f"    [ERROR] pq.read_schema() fehlgeschlagen: {e}")

        try:
            pf_meta = pq.read_metadata(str(f))
            kv_meta = pf_meta.metadata
            if kv_meta:
                print(f"    pq.read_metadata().metadata ({len(kv_meta)} Einträge):")
                for k, v in sorted(kv_meta.items()):
                    dk = k.decode() if isinstance(k, bytes) else str(k)
                    dv = v.decode() if isinstance(v, bytes) else str(v)
                    print(f"      [{type(k).__name__}] {dk!r:30s} = {dv!r}")
            else:
                print("    pq.read_metadata().metadata = None/leer")
        except Exception as e:
            print(f"    [ERROR] pq.read_metadata() fehlgeschlagen: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-path", default="./data/nautilus")
    parser.add_argument("--instrument", default=None, help="z.B. ACN.ETORO")
    parser.add_argument("--all", action="store_true", help="Alle Instrumente mit Konflikten")
    args = parser.parse_args()

    tick_root = Path(args.catalog_path) / "data" / "quote_tick"

    if args.instrument:
        diagnose_instrument(args.catalog_path, args.instrument)
        return

    if args.all:
        if not tick_root.exists():
            print(f"[ERROR] {tick_root} existiert nicht")
            sys.exit(1)

        for entry in sorted(tick_root.iterdir()):
            if not entry.is_dir():
                continue
            inst = entry.name.replace("instrument_id=", "")
            files = sorted(entry.rglob("*.parquet"))
            if len(files) > 1:
                # Nur Instrumente mit mehreren Dateien (potentielle Konflikte)
                diagnose_instrument(args.catalog_path, inst)
        return

    parser.print_help()


if __name__ == "__main__":
    main()