import os
import pandas as pd
import numpy as np

def robust_convert(val):
    """Versucht alles in einen Float zu verwandeln. Bei Schrott -> NaN"""
    if isinstance(val, bytes):
        try:
            return float(val.decode('utf-8').strip())
        except Exception:
            return np.nan
    elif isinstance(val, str):
        try:
            return float(val.strip())
        except Exception:
            return np.nan
    else:
        try:
            return float(val)
        except Exception:
            return np.nan

def fix_parquet_data():
    tick_dir = "data/nautilus/data/quote_tick"
    
    if not os.path.exists(tick_dir):
        print(f"❌ Verzeichnis {tick_dir} nicht gefunden.")
        return

    print("🛠️ Starte ROBUSTE Datentyp-Konvertierung der Parquet-Dateien...")
    fixed_count = 0
    empty_count = 0
    error_count = 0

    for root, _, files in os.walk(tick_dir):
        for file in files:
            if file.endswith(".parquet"):
                filepath = os.path.join(root, file)
                try:
                    df = pd.read_parquet(filepath)
                    needs_fix = False
                    
                    cols_to_convert = ["bid_price", "ask_price", "bid_size", "ask_size"]
                    for col in cols_to_convert:
                        if col in df.columns and len(df) > 0:
                            # Prüfe, ob die Spalte noch den falschen Typ hat (z.B. object/bytes)
                            if df[col].dtype == object or isinstance(df[col].iloc[0], (bytes, str)):
                                df[col] = df[col].apply(robust_convert)
                                needs_fix = True

                    if needs_fix:
                        # Alle unlesbaren Zeilen entfernen
                        original_len = len(df)
                        df = df.dropna(subset=[c for c in cols_to_convert if c in df.columns])
                        dropped = original_len - len(df)
                        
                        if len(df) > 0:
                            df.to_parquet(filepath, index=False)
                            fixed_count += 1
                            print(f"✅ Repariert: {file} (Verworfene korrupte Ticks: {dropped})")
                        else:
                            print(f"⚠️ {file} bestand nur aus Datenmüll und ist nun leer.")
                            empty_count += 1
                            
                except Exception as e:
                    print(f"❌ Unerwarteter Fehler bei {file}: {e}")
                    error_count += 1

    print("\n============================================================")
    print(f"🎉 Fertig! {fixed_count} Dateien wurden endgültig repariert.")
    if empty_count > 0:
        print(f"🗑️ {empty_count} Dateien waren komplett korrupt und sind nun leer.")
    if error_count > 0:
        print(f"⚠️ {error_count} Dateien konnten gar nicht erst gelesen werden.")
    print("============================================================")

if __name__ == "__main__":
    fix_parquet_data()