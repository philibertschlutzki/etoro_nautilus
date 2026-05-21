#!/usr/bin/env python3
import os
import sys
import argparse
import logging
import pandas as pd
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.enums import PriceType
except ImportError as e:
    print(f"❌ Fehler: Nautilus Trader konnte nicht geladen werden: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("BarAggregator")

def discover_instruments_from_catalog(catalog_path: str) -> list[str]:
    tick_dir = os.path.join(catalog_path, "data", "quote_tick")
    instruments = []
    if os.path.exists(tick_dir):
        for entry in os.listdir(tick_dir):
            if os.path.isdir(os.path.join(tick_dir, entry)):
                instruments.append(entry.replace("instrument_id=", ""))
    return sorted(instruments)

def aggregate_ticks_to_bars(catalog_path, instrument_id_str, bar_spec, price_type_str, start_year, end_year):
    catalog = ParquetDataCatalog(catalog_path)
    
    # Preistyp sicher auflösen
    try:
        if hasattr(PriceType, price_type_str.upper()):
            p_type = getattr(PriceType, price_type_str.upper())
        else:
            p_type = PriceType.MID
            
        inst_id = InstrumentId.from_str(instrument_id_str)
        bar_type = BarType(spec=bar_spec, price_type=p_type, instrument_id=inst_id)
    except Exception as e:
        logger.error(f"❌ BarType-Fehler für {instrument_id_str}: {e}")
        return

    logger.info(f"⏳ Verarbeite {instrument_id_str}...")
    
    # Resampling
    freq = "1min" if "1m" in bar_spec else "1h"
    
    # Lade Ticks (vereinfachter Chunk-Ansatz)
    try:
        ticks = catalog.quote_ticks(instrument_ids=[instrument_id_str])
    except Exception as e:
        logger.error(f"❌ Fehler beim Laden von Ticks für {instrument_id_str}: {e}")
        return
        
    if not ticks:
        return

    df = pd.DataFrame([{'ts': t.ts_event, 'price': (float(t.bid_price) + float(t.ask_price))/2, 'vol': float(t.bid_size) + float(t.ask_size)} for t in ticks])
    df['dt'] = pd.to_datetime(df['ts'], unit='ns', utc=True)
    df.set_index('dt', inplace=True)
    
    resampled = df['price'].resample(freq).ohlc().join(df['vol'].resample(freq).sum()).dropna()
    
    bars = [Bar(bar_type, Price(r.open, 5), Price(r.high, 5), Price(r.low, 5), Price(r.close, 5), Quantity(r.vol), int(dt.value), int(dt.value)) 
            for dt, r in resampled.iterrows()]
            
    if bars:
        catalog.write_data(bars)
        logger.info(f"✅ {instrument_id_str}: {len(bars)} Bars geschrieben.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-path", type=str, default="./data/nautilus")
    parser.add_argument("--bar-spec", type=str, default="1m")
    args = parser.parse_args()
    
    instruments = discover_instruments_from_catalog(args.catalog_path)
    for inst in instruments:
        aggregate_ticks_to_bars(args.catalog_path, inst, args.bar_spec, "MID", 2022, 2026)

if __name__ == "__main__":
    main()