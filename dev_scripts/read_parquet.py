import os
import sys
import pandas as pd

def analyze_all_parquets(base_dir: str):
    """Durchsucht rekursiv ein Verzeichnis nach Parquet-Dateien und analysiert sie."""
    if not os.path.exists(base_dir):
        print(f"❌ Fehler: Verzeichnis nicht gefunden: {base_dir}")
        return

    print(f"🔍 Starte Massen-Analyse im Verzeichnis: {base_dir} ...\n")
    
    total_files = 0
    empty_files = 0
    files_with_data = []

    # Durchsuche alle Unterordner (z.B. BTC.ETORO, ADA.ETORO etc.)
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith(".parquet"):
                total_files += 1
                file_path = os.path.join(root, file)
                
                try:
                    df = pd.read_parquet(file_path)
                    
                    if len(df) > 0:
                        files_with_data.append((file_path, df))
                    else:
                        empty_files += 1
                except Exception as e:
                    print(f"❌ Fehler beim Lesen der Datei {file}: {e}")

    # --- ZUSAMMENFASSUNG AUSGEBEN ---
    print("="*60)
    print("📊 ZUSAMMENFASSUNG DER MASSEN-ANALYSE")
    print("="*60)
    print(f"Total .parquet Dateien geprüft: {total_files}")
    print(f"Leere Dateien (0 Ticks/Zeilen): {empty_files}")
    print(f"Dateien MIT echten Daten:       {len(files_with_data)}")
    print("="*60 + "\n")

    # --- NUR DIE ECHTEN DATEN ANZEIGEN ---
    if len(files_with_data) > 0:
        print("🚀 HIER SIND DIE DATEIEN MIT INHALT:\n")
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 1000)
        
        for file_path, df in files_with_data:
            folder_name = os.path.basename(os.path.dirname(file_path))
            file_name = os.path.basename(file_path)
            
            print(f"🟢 {folder_name} | Datei: {file_name}")
            print(f"   - Anzahl Ticks in dieser Datei: {len(df)}")
            print(df.head(3)) # Zeige nur die ersten 3 Ticks zur Übersicht
            print("-" * 60)
    else:
        print("⚠️ ERGEBNIS: Keine einzige der geprüften Dateien enthält Daten.")
        print("   Alle Dateien bestehen nur aus dem leeren Daten-Schema (ca. 879 Bytes).")

if __name__ == "__main__":
    # Standardpfad setzen, falls nichts übergeben wird
    target_path = "data/nautilus/data/quote_tick/"
    
    # Prüfe, ob ein Pfad beim Aufruf übergeben wurde (Überschreibt den Standard)
    if len(sys.argv) > 1:
        target_path = sys.argv[1]

    if not os.path.isdir(target_path):
        print(f"⚠️ Warnung: '{target_path}' ist kein gültiges Verzeichnis.")
        print("Bitte gib den Pfad zum Ordner 'quote_tick' an.")
    else:
        analyze_all_parquets(target_path)