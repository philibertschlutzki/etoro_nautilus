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
    from nautilus_trader.model.data import Bar, BarType, BarSpecification
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.enums import PriceType, BarAggregation, AggregationSource
except ImportError as e:
    print(f"❌ Fehler: Nautilus Trader konnte nicht geladen werden: {e}")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BarAggregator")


def discover_instruments_from_catalog(catalog_path: str) -> list[str]:
    tick_dir = os.path.join(catalog_path, "data", "quote_tick")
    instruments = []
    if os.path.exists(tick_dir):
        for entry in os.listdir(tick_dir):
            if os.path.isdir(os.path.join(tick_dir, entry)):
                instruments.append(entry.replace("instrument_id=", ""))
    return sorted(instruments)


def parse_bar_spec(bar_spec_str: str, price_type: PriceType) -> BarSpecification:
    """
    Parst einen Bar-Spec-String in ein BarSpecification-Objekt.
    Unterstützte Formate: "1m", "5m", "15m", "30m", "1h", "4h", "1d"
    """
    bar_spec_str = bar_spec_str.lower().strip()

    aggregation_map = {
        "m": BarAggregation.MINUTE,
        "h": BarAggregation.HOUR,
        "d": BarAggregation.DAY,
    }

    for suffix, aggregation in aggregation_map.items():
        if bar_spec_str.endswith(suffix):
            step_str = bar_spec_str[:-len(suffix)]
            try:
                step = int(step_str)
            except ValueError:
                raise ValueError(f"Ungültiger Step-Wert in bar_spec: '{bar_spec_str}'")
            return BarSpecification(step, aggregation, price_type)

    raise ValueError(f"Unbekanntes bar_spec-Format: '{bar_spec_str}'. "
                     f"Erwartet z.B. '1m', '5m', '1h', '4h', '1d'.")


def bar_spec_to_pandas_freq(bar_spec_str: str) -> str:
    """Konvertiert bar_spec-String in pandas-Resample-Frequenz."""
    bar_spec_str = bar_spec_str.lower().strip()

    freq_map = {
        "m": "min",
        "h": "h",
        "d": "D",
    }

    for suffix, pd_unit in freq_map.items():
        if bar_spec_str.endswith(suffix):
            step = bar_spec_str[:-len(suffix)]
            return f"{step}{pd_unit}"

    raise ValueError(f"Unbekanntes bar_spec-Format für pandas: '{bar_spec_str}'")


def aggregate_ticks_to_bars(
    catalog_path: str,
    instrument_id_str: str,
    bar_spec_str: str,
    price_type_str: str,
    start_year: int,
    end_year: int,
) -> None:
    catalog = ParquetDataCatalog(catalog_path)

    # Preistyp auflösen
    p_type = getattr(PriceType, price_type_str.upper(), PriceType.MID)

    # BarType aufbauen
    try:
        inst_id = InstrumentId.from_str(instrument_id_str)
        bar_spec = parse_bar_spec(bar_spec_str, p_type)
        bar_type = BarType(
            instrument_id=inst_id,
            bar_spec=bar_spec,
            aggregation_source=AggregationSource.EXTERNAL,
        )
    except Exception as e:
        logger.error(f"❌ BarType-Fehler für {instrument_id_str}: {e}")
        return

    logger.info(f"⏳ Verarbeite {instrument_id_str}...")

    # Ticks laden
    try:
        ticks = catalog.quote_ticks(instrument_ids=[instrument_id_str])
    except Exception as e:
        logger.error(f"❌ Fehler beim Laden von Ticks für {instrument_id_str}: {e}")
        return

    if not ticks:
        logger.warning(f"⚠️  Keine Ticks gefunden für {instrument_id_str}, überspringe.")
        return

    # pandas-Frequenz für Resampling
    try:
        freq = bar_spec_to_pandas_freq(bar_spec_str)
    except ValueError as e:
        logger.error(f"❌ Frequenz-Fehler für {instrument_id_str}: {e}")
        return

    # DataFrame aufbauen
    df = pd.DataFrame([{
        'ts':    t.ts_event,
        'price': (float(t.bid_price) + float(t.ask_price)) / 2,
        'vol':   float(t.bid_size)  + float(t.ask_size),
    } for t in ticks])

    df['dt'] = pd.to_datetime(df['ts'], unit='ns', utc=True)
    df.set_index('dt', inplace=True)

    # Optionaler Jahreszeitraum-Filter
    start_ts = pd.Timestamp(f"{start_year}-01-01", tz="UTC")
    end_ts   = pd.Timestamp(f"{end_year}-12-31 23:59:59", tz="UTC")
    df = df.loc[start_ts:end_ts]

    if df.empty:
        logger.warning(f"⚠️  Nach Zeitfilter keine Daten für {instrument_id_str}, überspringe.")
        return

    # OHLCV resamplen
    ohlc    = df['price'].resample(freq).ohlc()
    volume  = df['vol'].resample(freq).sum().rename('vol')
    resampled = ohlc.join(volume).dropna()

    if resampled.empty:
        logger.warning(f"⚠️  Resampling lieferte keine Bars für {instrument_id_str}, überspringe.")
        return

    # Bar-Objekte erzeugen
    bars = [
        Bar(
            bar_type,
            Price(row.open,  5),
            Price(row.high,  5),
            Price(row.low,   5),
            Price(row.close, 5),
            Quantity(row.vol, 0),
            int(dt.value),   # ts_event  (nanosekunden)
            int(dt.value),   # ts_init
        )
        for dt, row in resampled.iterrows()
    ]

    catalog.write_data(bars)
    logger.info(f"✅ {instrument_id_str}: {len(bars)} Bars geschrieben.")


def main():
    parser = argparse.ArgumentParser(
        description="Aggregiert Quote-Ticks aus dem ParquetDataCatalog zu OHLCV-Bars."
    )
    parser.add_argument(
        "--catalog-path",
        type=str,
        default="./data/nautilus",
        help="Pfad zum ParquetDataCatalog-Verzeichnis",
    )
    parser.add_argument(
        "--bar-spec",
        type=str,
        default="1m",
        help="Bar-Spezifikation, z.B. '1m', '5m', '15m', '1h', '4h', '1d'",
    )
    parser.add_argument(
        "--price-type",
        type=str,
        default="MID",
        choices=["BID", "ASK", "MID", "LAST"],
        help="Preistyp für die Bar-Berechnung",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2022,
        help="Startjahr für den Zeitfilter",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2026,
        help="Endjahr für den Zeitfilter",
    )
    args = parser.parse_args()

    instruments = discover_instruments_from_catalog(args.catalog_path)

    if not instruments:
        logger.warning("⚠️  Keine Instrumente im Katalog gefunden.")
        return

    logger.info(f"📋 {len(instruments)} Instrumente gefunden, starte Aggregation "
                f"({args.bar_spec}, {args.price_type})...")

    for inst in instruments:
        aggregate_ticks_to_bars(
            catalog_path=args.catalog_path,
            instrument_id_str=inst,
            bar_spec_str=args.bar_spec,
            price_type_str=args.price_type,
            start_year=args.start_year,
            end_year=args.end_year,
        )

    logger.info("🏁 Aggregation abgeschlossen.")


if __name__ == "__main__":
    main()