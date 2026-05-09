import os
import sys
import pandas as pd

def analyze_parquet(file_path: str):
    """Liest eine Parquet-Datei ein und gibt deren Inhalt übersichtlich aus."""
    if not os.path.exists(file_path):
        print(f"❌ Fehler: Datei oder Verzeichnis nicht gefunden: {file_path}")
        return

    try:
        # Lese die Parquet-Datei in ein Pandas DataFrame
        df = pd.read_parquet(file_path)
        
        print("\n" + "="*60)
        print(f"📄 DATEI: {os.path.basename(file_path)}")
        print("="*60)
        
        print(f"📊 Metadaten:")
        print(f"   - Anzahl der Zeilen (Kerzen/Ticks): {len(df)}")
        print(f"   - Anzahl der Spalten: {len(df.columns)}")
        print(f"   - Spaltennamen: {', '.join(df.columns)}")
        print("-" * 60)
        
        if len(df) > 0:
            print("📈 Daten-Vorschau (erste 5 Zeilen):")
            # Wir formatieren die Ausgabe für eine bessere Lesbarkeit im Terminal
            pd.set_option('display.max_columns', None)
            pd.set_option('display.width', 1000)
            print(df.head())
            print("="*60 + "\n")
        else:
            print("⚠️ Diese Datei enthält exakt 0 Zeilen Daten.")
            print("   (Es ist lediglich eine 'leere' Schema-Datei, die vom System angelegt wurde.)")
            print("="*60 + "\n")
            
    except Exception as e:
        print(f"❌ Fehler beim Lesen der Datei: {e}")
        print("💡 Tipp: Stelle sicher, dass 'pyarrow' oder 'fastparquet' installiert ist:")
        print("   pip install pyarrow pandas")

if __name__ == "__main__":
    # Prüfe, ob ein Pfad beim Aufruf übergeben wurde
    if len(sys.argv) > 1:
        target_path = sys.argv[1]
    else:
        print("\nℹ️  Tipp: Du kannst den Pfad zur Datei direkt beim Aufruf mitgeben:")
        print("   Beispiel: python3 read_parquet.py ./data/meine_datei.parquet\n")
        
        # Fallback auf einen festen Pfad für schnelle Tests (passe diesen an!)
        target_path = input("Bitte den Pfad zur .parquet Datei eingeben: ").strip()

    # Wenn es ein Ordner ist, warnen wir kurz, da Pandas direkt die Parquet-Datei braucht
    if os.path.isdir(target_path):
        print(f"\n⚠️  Du hast einen Ordner angegeben. Bitte gib den Pfad zu einer spezifischen .parquet Datei an.")
        print("Beispielhafte Dateien in diesem Ordner:")
        for file in os.listdir(target_path)[:5]:
            print(f"  - {os.path.join(target_path, file)}")
    else:
        analyze_parquet(target_path)
