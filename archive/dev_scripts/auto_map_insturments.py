#!/usr/bin/env python3
import json
import os
import sys
import requests
from pathlib import Path

# Projekt-Root in den Pfad aufnehmen, um adapters importieren zu können
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from archive.adapters.instrument_map import ETORO_INSTRUMENTS

UNIVERSE_FILE = PROJECT_ROOT / "data" / "universe" / "momentum_ls.json"
MAP_FILE = PROJECT_ROOT / "adapters" / "instrument_map.py"

def fetch_etoro_metadata() -> dict:
    """
    Lädt das komplette Lexikon aller eToro-Instrumente herunter.
    Gibt ein Dictionary { "InstrumentID": "SymbolFull" } zurück.
    """
    url = "https://api.etorostatic.com/sapi/instrumentsmetadata/V1.1/instruments"
    print("🌐 Lade eToro Instrumenten-Datenbank herunter...")
    
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # Mapping von ID zu Ticker-Symbol erstellen
        instrument_dict = {
            str(inst["InstrumentID"]): inst["SymbolFull"] 
            for inst in data.get("InstrumentDisplayDatas", [])
        }
        return instrument_dict
    except Exception as e:
        print(f"❌ Fehler beim Abrufen der eToro Metadaten: {e}")
        return {}

def main():
    if not UNIVERSE_FILE.exists():
        print(f"❌ Universum-Datei nicht gefunden: {UNIVERSE_FILE}")
        return

    # 1. momentum_ls.json auslesen und unbekannte IDs finden
    with open(UNIVERSE_FILE, "r") as f:
        universe_data = json.load(f)
        
    missing_ids = set()
    for item in universe_data.get("universe", []):
        if item.get("symbol") is None:
            missing_ids.add(str(item["etoro_id"]))
            
    if not missing_ids:
        print("✅ Keine fehlenden Symbole im Universum gefunden. instrument_map.py ist aktuell.")
        return

    print(f"🔍 {len(missing_ids)} unbekannte IDs im Universum gefunden: {', '.join(missing_ids)}")
    
    # 2. Metadaten abrufen und IDs auflösen
    etoro_meta = fetch_etoro_metadata()
    if not etoro_meta:
        return
        
    new_mappings = {}
    for eid in missing_ids:
        if eid in etoro_meta:
            new_mappings[eid] = etoro_meta[eid]
        else:
            print(f"⚠️ Warnung: ID {eid} existiert nicht in der eToro Datenbank.")
            
    if not new_mappings:
        print("❌ Keine der fehlenden IDs konnte aufgelöst werden.")
        return
        
    # 3. Das bestehende Dictionary aktualisieren
    updated_map = ETORO_INSTRUMENTS.copy()
    for eid, sym in new_mappings.items():
        nautilus_symbol = f"{sym}.ETORO"
        updated_map[eid] = nautilus_symbol
        print(f"✨ Neues Asset erkannt: ID {eid} -> {nautilus_symbol}")
        
    # 4. Die Datei adapters/instrument_map.py neu schreiben
    print(f"💾 Speichere neues Mapping in {MAP_FILE.name}...")
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        f.write("# adapters/instrument_map.py\n")
        f.write('"""\nZentrale Zuordnung der eToro internen IDs zu den Nautilus InstrumentIds.\n')
        f.write('Wird automatisch durch dev_scripts/auto_map_instruments.py aktualisiert.\n')
        f.write('"""\n\n')
        f.write("ETORO_INSTRUMENTS = {\n")
        
        # Sortiert nach ID für eine saubere Datei
        for k in sorted(updated_map.keys(), key=lambda x: int(x)):
            f.write(f'    "{k}": "{updated_map[k]}",\n')
        f.write("}\n")
        
    print("🚀 Erfolgreich abgeschlossen! Du kannst nun das Universum neu laden.")

if __name__ == "__main__":
    main()