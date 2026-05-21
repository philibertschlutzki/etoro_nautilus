#!/usr/bin/env python3
"""
analyze_bars.py

Analysiert die aggregierten OHLCV-Bars im Nautilus-Katalog.
Schreibt Metadaten, Zeilenanzahl und Daten-Snippets in ein Logfile.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq

class DualLogger:
    def __init__(self, filepath: str) -> None:
        self.terminal = sys.stdout
        self.log = open(filepath, "a", encoding="utf-8")

    def write(self, message: str) -> None:
        self.terminal.write(message)
        self.log.write(message)

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()

def analyze_bar_catalog(catalog_path: str, logs_dir: str):
    bar_base_dir = Path(catalog_path) / "data" / "bar"
    
    if not bar_base_dir.exists():
        print(f"❌ Verzeichnis nicht gefunden: {bar_base_dir}")
        return

    # Log-Setup
    os.makedirs(logs_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(logs_dir, f"bar_analysis_{timestamp}.log")
    
    sys.stdout = DualLogger(log_file)
    sys.stderr = DualLogger(log_file)

    print(f"🔍 Starte Massen-Analyse im Bar-Verzeichnis: {bar_base_dir}")
    print("=" * 70)

    bar_dirs = sorted([d for d in bar_base_dir.iterdir() if d.is_dir()])
    
    total_files_checked = 0
    empty_files = 0
    valid_files = 0

    for b_dir in bar_dirs:
        parquet_files = list(b_dir.glob("*.parquet"))
        if not parquet_files:
            continue
            
        print(f"\n🟢 {b_dir.name} |")
        
        for pf in parquet_files:
            total_files_checked += 1
            print(f"Datei: {pf.name}")
            
            try:
                # Schema & Metadaten lesen
                schema = pq.read_schema(str(pf))
                meta = schema.metadata or {}
                
                # Dekodiere Metadaten falls vorhanden
                clean_meta = {}
                for k, v in meta.items():
                    key_str = k.decode('utf-8') if isinstance(k, bytes) else str(k)
                    val_str = v.decode('utf-8') if isinstance(v, bytes) else str(v)
                    clean_meta[key_str] = val_str

                # Spaltentypen analysieren
                types_info = []
                for field in schema:
                    types_info.append(f"{field.name} ({field.type})")
                print(f"   - Spalten: {', '.join(types_info)}")
                if clean_meta:
                    print(f"   - Metadaten: {clean_meta}")

                # Inhalt lesen
                table = pq.read_table(str(pf))
                df = table.to_pandas()
                
                row_count = len(df)
                print(f"   - Anzahl Bars in dieser Datei: {row_count}")
                
                if row_count == 0:
                    empty_files += 1
                    print("   ⚠️ DATEI IST LEER")
                else:
                    valid_files += 1
                    # Zeige die ersten 3 Zeilen als repräsentatives Snippet
                    print("   - Daten-Snippet (erste 3 Zeilen):")
                    snippet = df.head(3).to_string(index=True)
                    # Einrücken für bessere Lesbarkeit im Log
                    for line in snippet.split('\n'):
                        print(f"     {line}")
                        
            except Exception as e:
                print(f"   ❌ Fehler beim Lesen der Datei: {e}")

        print("-" * 70)

    # Zusammenfassung
    print("\n============================================================")
    print("📊 ZUSAMMENFASSUNG DER BAR-ANALYSE")
    print("============================================================")
    print(f"Total .parquet Dateien geprüft: {total_files_checked}")
    print(f"Leere Dateien (0 Bars):         {empty_files}")
    print(f"Dateien MIT echten Daten:       {valid_files}")
    print("============================================================")
    print(f"💾 Analyse abgeschlossen. Log gespeichert in:\n   {log_file}\n")

if __name__ == "__main__":
    # Pfade relativ zur Projektwurzel auflösen
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
    
    CATALOG = os.path.join(PROJECT_ROOT, "data", "nautilus")
    LOGS = os.path.join(PROJECT_ROOT, "logs")
    
    analyze_bar_catalog(CATALOG, LOGS)