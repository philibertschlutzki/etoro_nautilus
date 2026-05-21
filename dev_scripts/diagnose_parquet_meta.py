#!/usr/bin/env python3
"""
diagnose_parquet_meta.py  –  Zeigt Arrow-Schema-Metadaten aller Parquet-Dateien
eines Instruments an. Die Ausgabe wird zusätzlich in etoro_nautilus/logs gespeichert.
"""
import argparse
import os
import sys
from pathlib import Path
import pyarrow.parquet as pq

# Konfiguration für Log-Datei
LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "diagnose_output.txt"

def log_print(message: str, file_handle=None):
    """Hilfsfunktion für Ausgabe in Konsole und Datei."""
    print(message)
    if file_handle:
        file_handle.write(message + "\n")

def diagnose_instrument(catalog_path: str, instrument_id_str: str, file_handle) -> None:
    inst_dir = Path(catalog_path) / "data" / "quote_tick" / instrument_id_str

    if not inst_dir.exists():
        log_print(f"[ERROR] Verzeichnis existiert nicht: {inst_dir}", file_handle)
        return

    parquet_files = sorted(inst_dir.rglob("*.parquet"))
    log_print(f"\n{'='*70}", file_handle)
    log_print(f"Instrument : {instrument_id_str}", file_handle)
    log_print(f"Verzeichnis: {inst_dir}", file_handle)
    log_print(f"Dateien    : {len(parquet_files)} (rekursiv)", file_handle)
    log_print(f"{'='*70}", file_handle)

    if not parquet_files:
        log_print("  [WARN] Keine Parquet-Dateien gefunden!", file_handle)
        return

    for f in parquet_files:
        log_print(f"\n  Datei: {f.relative_to(inst_dir)}", file_handle)
        try:
            schema = pq.read_schema(str(f))
            meta = schema.metadata
            if meta is None:
                log_print("    schema.metadata = None", file_handle)
            else:
                log_print(f"    schema.metadata ({len(meta)} Einträge):", file_handle)
                for k, v in sorted(meta.items()):
                    dk = k.decode() if isinstance(k, bytes) else str(k)
                    dv = v.decode() if isinstance(v, bytes) else str(v)
                    log_print(f"      [{type(k).__name__}] {dk!r:30s} = {dv!r}", file_handle)
        except Exception as e:
            log_print(f"    [ERROR] pq.read_schema() fehlgeschlagen: {e}", file_handle)

        try:
            pf_meta = pq.read_metadata(str(f))
            kv_meta = pf_meta.metadata
            if kv_meta:
                log_print(f"    pq.read_metadata().metadata ({len(kv_meta)} Einträge):", file_handle)
                for k, v in sorted(kv_meta.items()):
                    dk = k.decode() if isinstance(k, bytes) else str(k)
                    dv = v.decode() if isinstance(v, bytes) else str(v)
                    log_print(f"      [{type(k).__name__}] {dk!r:30s} = {dv!r}", file_handle)
            else:
                log_print("    pq.read_metadata().metadata = None/leer", file_handle)
        except Exception as e:
            log_print(f"    [ERROR] pq.read_metadata() fehlgeschlagen: {e}", file_handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-path", default="./data/nautilus")
    parser.add_argument("--instrument", default=None, help="z.B. ACN.ETORO")
    parser.add_argument("--all", action="store_true", help="Alle Instrumente mit Konflikten")
    args = parser.parse_args()

    # Log-Verzeichnis sicherstellen
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        tick_root = Path(args.catalog_path) / "data" / "quote_tick"

        if args.instrument:
            diagnose_instrument(args.catalog_path, args.instrument, f)
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
                    diagnose_instrument(args.catalog_path, inst, f)
            return

        parser.print_help()

if __name__ == "__main__":
    main()