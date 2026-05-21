#!/usr/bin/env python3
import os
import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd

# Fügt das Projekt-Wurzelverzeichnis zum sys.path hinzu, genau wie in run_backtest.py
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trader.model.data import Bar, BarType  # ✨ Hier korrigiert: BarType aus .data importiert
    from nautilus_trader.model.objects import Price, Quantity
except ImportError as e:
    print(f"❌ Fehler beim Importieren von Nautilus Trader: {e}")
    print("Bitte stelle sicher, dass deine Nautilus-Virtuelle-Umgebung aktiv ist.")
    sys.exit(1)

# Logging-Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BarAggregator")


def discover_instruments_from_catalog(catalog_path: str) -> list[str]:
    """Sucht verfügbare Instrument-IDs direkt aus dem quote_tick Verzeichnis des Katalogs."""
    tick_dir = os.path.join(catalog_path, "data", "quote_tick")
    instruments = []
    if os.path.exists(tick_dir):
        for entry in os.listdir(tick_dir):
            full = os.path.join(tick_dir, entry)
            if os.path.isdir(full) and not entry.startswith('.'):
                clean = entry.replace("instrument_id=", "")
                if '.' in clean or clean.endswith('.ETORO'):
                    instruments.append(clean)
    return sorted(instruments)


def infer_precision_from_ticks(ticks: list) -> int:
    """Ermittelt die Nachkommastellen-Präzision basierend auf den ersten Ticks."""
    if not ticks:
        return 5
    precisions = []
    for t in ticks[:10]:
        if hasattr(t.bid_price, 'precision'):
            precisions.append(t.bid_price.precision)
    return int(max(precisions)) if precisions else 5


def parse_bar_spec_to_freq(bar_spec: str) -> str:
    """Maps Nautilus Bar Specifications to standard Pandas resampling frequencies."""
    bs_upper = bar_spec.upper()
    if "MINUTE" in bs_upper or "1M" in bs_upper:
        return "1min"
    elif "HOUR" in bs_upper or "1H" in bs_upper:
        return "1h"
    elif "DAY" in bs_upper or "1D" in bs_upper:
        return "1D"
    else:
        # Fallback auf Standard-Minuten-Intervall
        logger.warning(f"⚠️ Unbekannte Bar-Specification '{bar_spec}'. Nutze Fallback '1min'.")
        return "1min"


def aggregate_ticks_to_bars(
    catalog_path: str,
    instrument_id_str: str,
    bar_spec: str,
    price_type: str,
    start_year: int,
    end_year: int
) -> None:
    """Verarbeitet rohe Ticks für ein einzelnes Instrument in monatlichen Chunks."""
    catalog = ParquetDataCatalog(catalog_path)
    
    # Generiere Monats-Chunks zur Vermeidung von OOM-Crashes
    date_range = pd.date_range(
        start=f"{start_year}-01-01",
        end=f"{end_year}-12-31",
        freq="MS",
        tz="UTC"
    )
    
    bar_type_str = f"{instrument_id_str}-{bar_spec}-{price_type}"
    try:
        bar_type = BarType.from_str(bar_type_str)
    except Exception as e:
        logger.error(f"❌ Ungültiges BarType-Format '{bar_type_str}': {e}")
        return

    logger.info(f"⏳ Starte Aggregation für {instrument_id_str} -> {bar_type_str}")
    total_bars_saved = 0
    resample_freq = parse_bar_spec_to_freq(bar_spec)

    for i in range(len(date_range) - 1):
        start_chunk = pd.Timestamp(date_range[i])
        end_chunk = pd.Timestamp(date_range[i+1])
        
        try:
            ticks = catalog.quote_ticks(
                instrument_ids=[instrument_id_str],
                start=start_chunk,
                end=end_chunk
            )
        except Exception as e:
            logger.error(f"   ❌ Fehler beim Laden aus Katalog für {instrument_id_str} ({start_chunk.date()}): {e}")
            continue
            
        if not ticks:
            continue
            
        precision = infer_precision_from_ticks(ticks)
        
        # Extrahiere Datenpunkte für Pandas DataFrame
        tick_list = []
        for t in ticks:
            # Berechne den Mid-Preis aus Geld- und Briefkurs
            mid_price = (float(t.bid_price) + float(t.ask_price)) / 2.0
            
            # Verwende die summierten Sizes als Proxy-Volumen
            vol = float(t.bid_size) + float(t.ask_size)
            
            tick_list.append({
                'timestamp': t.ts_event,
                'price': mid_price,
                'volume': vol
            })
            
        df = pd.DataFrame(tick_list)
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ns', utc=True)
        df.set_index('datetime', inplace=True)
        
        # Führe das OHLCV Resampling durch
        resampled_ohlc = df['price'].resample(resample_freq).ohlc()
        resampled_vol = df['volume'].resample(resample_freq).sum()
        
        resampled = resampled_ohlc.join(resampled_vol)
        resampled.dropna(subset=['open'], inplace=True)
        
        if resampled.empty:
            continue
            
        # Generiere Nautilus Bar Objekte
        bars_to_write = []
        for dt, row in resampled.iterrows():
            ts_event = int(dt.value)  # Nanosekunden-Zeitstempel
            
            bars_to_write.append(Bar(
                bar_type=bar_type,
                open=Price(row['open'], precision=precision),
                high=Price(row['high'], precision=precision),
                low=Price(row['low'], precision=precision),
                close=Price(row['close'], precision=precision),
                volume=Quantity(row['volume']),
                ts_event=ts_event,
                ts_init=ts_event
            ))
            
        if bars_to_write:
            try:
                catalog.write_data(bars_to_write)
                total_bars_saved += len(bars_to_write)
            except Exception as e:
                logger.error(f"   ❌ Fehler beim Schreiben der Bars in den Katalog: {e}")
                
    if total_bars_saved > 0:
        logger.info(f"✅ {instrument_id_str}: {total_bars_saved} Bars erfolgreich geschrieben.")
    else:
        logger.info(f"ℹ️ {instrument_id_str}: Keine Daten verarbeitet.")


def main():
    parser = argparse.ArgumentParser(description="NautilusTrader Offline Tick-to-Bar Aggregator")
    parser.add_argument("--catalog-path", type=str, default="./data/nautilus", help="Pfad zum lokalen Datenkatalog")
    parser.add_argument("--bar-spec", type=str, default="1-MINUTE", help="Nautilus Intervall (z.B. 1-MINUTE, 1-HOUR, 1-DAY)")
    parser.add_argument("--price-type", type=str, default="MID", help="Preistyp (MID, QUOTE, TRADE)")
    parser.add_argument("--start-year", type=int, default=2022, help="Startjahr für die Aggregations-Filterung")
    parser.add_argument("--end-year", type=int, default=2026, help="Endjahr für die Aggregations-Filterung")
    parser.add_argument("--symbol", type=str, default=None, help="Spezifisches Symbol aggregieren (falls None, werden alle verarbeitet)")
    args = parser.parse_args()

    if not os.path.exists(args.catalog_path):
        logger.error(f"❌ Katalog-Verzeichnis existiert nicht: {args.catalog_path}")
        sys.exit(1)

    # Ermittle Assets im Katalog
    if args.symbol:
        instruments = [args.symbol]
    else:
        instruments = discover_instruments_from_catalog(args.catalog_path)

    if not instruments:
        logger.error(f"⚠️ Keine Tick-Daten unter {args.catalog_path}/data/quote_tick/ gefunden.")
        sys.exit(1)

    logger.info(f"🚀 Starte Batch-Aggregation für {len(instruments)} Assets.")
    logger.info(f"📅 Zeitraum: {args.start_year} bis {args.end_year} | Ziel-Intervall: {args.bar_spec}")

    for idx, inst_id in enumerate(instruments, 1):
        print(f"\n[{idx}/{len(instruments)}]:")
        aggregate_ticks_to_bars(
            catalog_path=args.catalog_path,
            instrument_id_str=inst_id,
            bar_spec=args.bar_spec,
            price_type=args.price_type,
            start_year=args.start_year,
            end_year=args.end_year
        )

    print("\n✅ Aggregations-Prozess vollständig abgeschlossen!")


if __name__ == "__main__":
    main()