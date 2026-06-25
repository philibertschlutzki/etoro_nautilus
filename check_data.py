import pyarrow.parquet as pq

file_path = "data/nautilus/data/quote_tick/TSLA.ETORO/data.parquet"
try:
    pf = pq.ParquetFile(file_path)
    print(f"Schema Spalten: {pf.schema.names}")
    print(f"Anzahl Row Groups: {pf.metadata.num_row_groups}")
    
    if "ts_event" in pf.schema.names:
        idx = pf.schema.names.index("ts_event")
        oldest = newest = None
        for rg in range(pf.metadata.num_row_groups):
            st = pf.metadata.row_group(rg).column(idx).statistics
            print(f"Metadaten Row Group {rg}: {st}")
            if st and hasattr(st, "min") and hasattr(st, "max"):
                lo, hi = int(st.min), int(st.max)
                oldest = lo if oldest is None else min(oldest, lo)
                newest = hi if newest is None else max(newest, hi)
        
        if oldest is not None and newest is not None:
            hours = (newest - oldest) / (3600 * 1_000_000_000)
            print(f"Berechnete 1h-Kerzen: {int(hours)}")
            print(f"Ausreichend fuer Gate 1 (ca. 1400 noetig)? {'Ja' if hours > 1400 else 'Nein'}")
        else:
            print("Kritisch: Keine min/max Statistiken in der Datei gefunden!")
    else:
        print("Kritisch: Spalte ts_event existiert nicht!")
except Exception as e:
    print(f"Absturz beim Lesen: {e}")
