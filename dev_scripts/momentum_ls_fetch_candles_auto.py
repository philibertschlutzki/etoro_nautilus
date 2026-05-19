import argparse
import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import pandas as pd
from dotenv import load_dotenv

# Damit wir das Modul aus dem übergeordneten Ordner importieren können
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from adapters.instrument_map import ETORO_INSTRUMENTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# eToro Rate Limit (max 60 Requests pro Minute)
RATE_LIMIT_DELAY = 1.1


def _make_headers(api_key: str, user_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


def format_nautilus_timestamp(ts_ns: int) -> str:
    """Konvertiert Nanosekunden-Zeitstempel in das Nautilus Zeitstempel-Format."""
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    base_time = dt.strftime('%Y-%m-%dT%H-%M-%S')
    ns_remainder = int(ts_ns % 1_000_000_000)
    return f"{base_time}-{ns_remainder:09d}Z"


async def fetch_candles_chunk(
    session: aiohttp.ClientSession,
    instrument_id: int,
    end_time: datetime,
    api_key: str,
    user_key: str,
    interval: str
) -> list[dict]:
    count = 1000
    url = f"https://public-api.etoro.com/api/v1/market-data/instruments/{instrument_id}/history/candles/desc/{interval}/{count}"

    params = {
        "endTime": end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    logger.info(f"[{instrument_id}] Fetching max {count} ({interval}) candles before {params['endTime']}...")

    headers = _make_headers(api_key, user_key)

    for attempt in range(3):
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    raw_data = await resp.json()

                    if isinstance(raw_data, dict):
                        inner = raw_data.get("candles") or raw_data.get("Candles") or raw_data.get("data")
                        if inner is not None:
                            raw_data = inner

                    if isinstance(raw_data, list) and len(raw_data) > 0:
                        first = raw_data[0]
                        if isinstance(first, dict):
                            inner_candles = first.get("candles") or first.get("Candles")
                            if inner_candles is not None and isinstance(inner_candles, list):
                                return inner_candles

                    return raw_data if isinstance(raw_data, list) else []

                elif resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning(f"[{instrument_id}] Rate limit exceeded (HTTP 429). Waiting for {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    continue

                else:
                    body = await resp.text()
                    logger.warning(f"[{instrument_id}] Failed to fetch candles: HTTP {resp.status} - {body[:200]}")
                    return []

        except Exception as e:
            logger.warning(f"[{instrument_id}] Network error (Attempt {attempt+1}): {e}")
            await asyncio.sleep(5)

    return []


def _get_key_case_insensitive(row: dict, possible_keys: list) -> str:
    row_keys_lower = {k.lower(): k for k in row.keys()}
    for pk in possible_keys:
        if pk.lower() in row_keys_lower:
            return row_keys_lower[pk.lower()]
    return None


def _ensure_instrument_registered(catalog, instrument_id_str: str, price_precision: int = 5) -> None:
    """
    Registriert ein Mock-Instrument im Katalog, falls noch nicht vorhanden.
    NautilusTrader benötigt Instrument-Metadaten im Katalog bevor quote_ticks()
    aufgerufen werden kann — sonst Rust-Panic mit MissingMetadata("instrument_id").
    """
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.model.instruments import Equity
    from nautilus_trader.model.currencies import USD

    inst_id = InstrumentId.from_str(instrument_id_str)
    increment = round(10 ** (-price_precision), price_precision)
    instrument = Equity(
        instrument_id=inst_id,
        raw_symbol=inst_id.symbol,
        currency=USD,
        price_precision=price_precision,
        price_increment=Price(increment, precision=price_precision),
        lot_size=Quantity(1, precision=0),
        ts_event=0,
        ts_init=0,
    )
    try:
        catalog.write_data([instrument])
    except Exception as e:
        logger.debug(f"Instrument {instrument_id_str} already registered or write skipped: {e}")


def write_candles_to_catalog(
    candles: list[dict],
    instrument_id_str: str,
    catalog_path: str,
    price_precision: int = 5,
) -> int:
    """
    Schreibt historische Candle-Daten als QuoteTick-Objekte in den
    NautilusTrader ParquetDataCatalog. Gibt die Anzahl geschriebener Ticks zurück.

    Mapping: Low → bid_price, High → ask_price (für Backtest-kompatibilität)
    """
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    inst_id = InstrumentId.from_str(instrument_id_str)
    catalog = ParquetDataCatalog(catalog_path)

    ticks = []
    for c in candles:
        try:
            date_key = _get_key_case_insensitive(c, ['fromdate', 'startdate', 'date', 'timestamp', 'time', 'start'])
            low_key = _get_key_case_insensitive(c, ['low', 'l'])
            high_key = _get_key_case_insensitive(c, ['high', 'h'])
            if not date_key or not low_key or not high_key:
                continue

            ts_dt = pd.to_datetime(c[date_key])
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.tz_localize('UTC')
            ts_ns = int(ts_dt.timestamp() * 1e9)

            bid = float(c[low_key])
            ask = float(c[high_key])

            # Precision dynamisch aus dem Wert ableiten
            prec = len(str(bid).rstrip('0').split('.')[-1]) if '.' in str(bid) else 0
            prec = min(max(prec, 2), 8)

            tick = QuoteTick(
                instrument_id=inst_id,
                bid_price=Price(bid, precision=prec),
                ask_price=Price(ask, precision=prec),
                bid_size=Quantity(1, precision=0),
                ask_size=Quantity(1, precision=0),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
            ticks.append(tick)
        except Exception as e:
            logger.debug(f"Skipping candle for {instrument_id_str}: {e}")
            continue

    if ticks:
        # Instrument im Katalog registrieren (Pflicht vor quote_ticks()-Aufruf)
        prec = ticks[0].bid_price.precision if ticks else price_precision
        _ensure_instrument_registered(catalog, instrument_id_str, prec)

        # Deduplizierung: vorhandene ts_event prüfen (nur wenn Daten-Ordner existiert)
        symbol_dir = os.path.join(catalog_path, "data", "quote_tick", instrument_id_str)
        existing = []
        try:
            if os.path.isdir(symbol_dir):
                existing = catalog.quote_ticks(instrument_ids=[instrument_id_str])
            existing_ts = {t.ts_event for t in existing}
            ticks = [t for t in ticks if t.ts_event not in existing_ts]
        except Exception as e:
            logger.debug(f"Could not load existing ticks for dedup ({instrument_id_str}): {e}")

        if ticks:
            catalog.write_data(ticks)

    return len(ticks)


async def process_instrument(
    session: aiohttp.ClientSession,
    etoro_id: int,
    symbol: str,
    target_start_date: datetime,
    api_key: str,
    user_key: str,
    intervals_cascade: list[str],
    output_dir: str,
    catalog_path: str,
) -> dict:
    """Lade historische Kerzen herunter und retourniere einen Summary-Eintrag."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(catalog_path)

    # Delta-Update Logik: Finde neuesten Zeitstempel im Katalog
    # Nur prüfen wenn der Daten-Ordner bereits existiert (vermeidet Rust-Panic)
    adjusted_start = target_start_date
    has_existing_data = False
    symbol_dir = os.path.join(catalog_path, "data", "quote_tick", symbol)
    if os.path.isdir(symbol_dir):
        try:
            _ensure_instrument_registered(catalog, symbol)
            existing_ticks = catalog.quote_ticks(instrument_ids=[symbol])
            if existing_ticks:
                has_existing_data = True
                max_ts_ns = max(t.ts_event for t in existing_ticks)
                latest_dt = datetime.fromtimestamp(max_ts_ns / 1e9, tz=timezone.utc)
                if latest_dt > target_start_date:
                    adjusted_start = latest_dt
                    logger.info(f"[{symbol}] Delta update: Fetching only records newer than {adjusted_start}")
        except Exception as e:
            logger.warning(f"[{symbol}] Could not check existing catalog data: {e}")

    target_start_date = adjusted_start

    logger.info(f"=== Starting API download for {symbol} (ID: {etoro_id}) ===")

    current_end_time = datetime.now(timezone.utc)
    all_candles = []
    intervals_used = []

    for current_interval in intervals_cascade:
        logger.info(f"[{symbol}] Attempting download with interval: {current_interval}")
        last_fetched_oldest_timestamp = None

        while current_end_time > target_start_date:
            chunk = await fetch_candles_chunk(
                session, etoro_id, current_end_time, api_key, user_key, current_interval
            )

            if not chunk:
                logger.info(f"[{symbol}] No more candles available for interval {current_interval}. Falling back to larger interval if available.")
                break

            chunk_df = pd.DataFrame(chunk)

            possible_date_cols = ['fromdate', 'startdate', 'date', 'timestamp', 'time', 'start']
            date_col = next((col for col in chunk_df.columns if col.lower() in possible_date_cols), None)

            if not date_col:
                logger.error(f"[{symbol}] API response format changed. Date column not found.")
                break

            chunk_df['parsed_date'] = pd.to_datetime(chunk_df[date_col])
            if chunk_df['parsed_date'].dt.tz is None:
                chunk_df['parsed_date'] = chunk_df['parsed_date'].dt.tz_localize('UTC')

            oldest_candle_time = chunk_df['parsed_date'].min()

            if last_fetched_oldest_timestamp == oldest_candle_time:
                logger.warning(f"[{symbol}] Hit historical depth limit for {current_interval}. Falling back to next interval.")
                break

            all_candles.extend(chunk)
            if current_interval not in intervals_used:
                intervals_used.append(current_interval)

            last_fetched_oldest_timestamp = oldest_candle_time
            current_end_time = oldest_candle_time - timedelta(seconds=1)

            logger.info(f"[{symbol}] ({current_interval}) Chunk fetched. Oldest record: {oldest_candle_time}. Accumulated: {len(all_candles)}")
            await asyncio.sleep(RATE_LIMIT_DELAY)

        if current_end_time <= target_start_date:
            logger.info(f"[{symbol}] Reached target start date ({target_start_date}).")
            break

    # Schreiben via Katalog
    new_count = 0
    if all_candles:
        new_count = write_candles_to_catalog(all_candles, symbol, catalog_path)
        logger.info(f"[{symbol}] Wrote {new_count} new ticks to catalog.")

    if new_count == 0 and not has_existing_data:
        logger.warning(f"[{symbol}] No data available (neither existing nor new). Moving to next.")
        return {"symbol": symbol, "status": "No Data", "intervals": "-", "rows": 0, "range": "N/A"}

    if new_count == 0 and has_existing_data:
        logger.info(f"[{symbol}] No new data fetched. Existing data is already up to date.")

    # Statistiken aus Katalog abfragen (nur wenn Ordner existiert)
    total_rows = 0
    date_range = "N/A"
    if os.path.isdir(symbol_dir):
        try:
            all_ticks = catalog.quote_ticks(instrument_ids=[symbol])
            total_rows = len(all_ticks)
            if all_ticks:
                min_ts = min(t.ts_event for t in all_ticks)
                max_ts = max(t.ts_event for t in all_ticks)
                min_dt = datetime.fromtimestamp(min_ts / 1e9, tz=timezone.utc)
                max_dt = datetime.fromtimestamp(max_ts / 1e9, tz=timezone.utc)
                date_range = f"{min_dt.strftime('%Y-%m-%d')} to {max_dt.strftime('%Y-%m-%d')}"
        except Exception as e:
            logger.warning(f"[{symbol}] Could not query catalog for stats: {e}")

    status = "Updated/New" if new_count > 0 else "Up-to-date"
    return {
        "symbol": symbol,
        "status": status,
        "intervals": ", ".join(intervals_used) if intervals_used else "-",
        "rows": total_rows,
        "range": date_range,
    }


async def main():
    parser = argparse.ArgumentParser(description="Fetch historical data for eToro instruments.")
    parser.add_argument("--symbol", type=str, default=None, help="Optional: Nautilus symbol to fetch.")
    parser.add_argument("--months", type=int, default=12, help="Number of months to fetch (initial load depth)")
    parser.add_argument("--intervals", type=str, default="OneHour,OneDay,OneWeek", help="Comma-separated intervals")
    parser.add_argument("--output-dir", type=str, default="data/nautilus/data/quote_tick",
                        help="Legacy output directory for summary reports (kept for backwards compatibility)")
    parser.add_argument("--catalog-path", type=str, default="data/nautilus",
                        help="Root path of NautilusTrader ParquetDataCatalog (same as run_catalog.py)")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("ETORO_API_KEY")
    user_key = os.getenv("ETORO_USER_KEY")

    if not api_key or not user_key:
        logger.error("Missing ETORO_API_KEY or ETORO_USER_KEY in environment.")
        sys.exit(1)

    intervals_list = [i.strip() for i in args.intervals.split(",")]

    if args.symbol:
        instruments_to_fetch = {k: v for k, v in ETORO_INSTRUMENTS.items() if v == args.symbol}
    else:
        instruments_to_fetch = ETORO_INSTRUMENTS

    logger.info(f"Starting bulk download/update using intervals {intervals_list} ...")

    target_start_date = datetime.now(timezone.utc) - timedelta(days=30 * args.months)

    summary_results = []

    timeout = aiohttp.ClientTimeout(total=20.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for etoro_id_str, symbol in instruments_to_fetch.items():
            try:
                etoro_id = int(etoro_id_str)
                result = await process_instrument(
                    session=session,
                    etoro_id=etoro_id,
                    symbol=symbol,
                    target_start_date=target_start_date,
                    api_key=api_key,
                    user_key=user_key,
                    intervals_cascade=intervals_list,
                    output_dir=args.output_dir,
                    catalog_path=args.catalog_path,
                )
                if result:
                    summary_results.append(result)
            except Exception as e:
                logger.error(f"Unexpected error processing {symbol}: {e}")
                summary_results.append({"symbol": symbol, "status": "Failed", "intervals": "-", "rows": 0, "range": "N/A"})

            await asyncio.sleep(RATE_LIMIT_DELAY)

    # ==========================================
    # REPORTING & ZUSAMMENFASSUNG
    # ==========================================
    logger.info("\n=== Bulk update process completed ===")

    if summary_results:
        report_lines = []
        report_lines.append("=== DOWNLOAD SUMMARY REPORT ===")
        report_lines.append(f"{'Symbol':<15} | {'Status':<12} | {'Intervals Used':<25} | {'Total Rows':<10} | {'Date Range'}")
        report_lines.append("-" * 105)

        for res in summary_results:
            report_lines.append(f"{res['symbol']:<15} | {res['status']:<12} | {res['intervals']:<25} | {res['rows']:<10} | {res['range']}")

        report_text = "\n".join(report_lines)
        print(f"\n{report_text}\n")

        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_path = Path(args.output_dir) / f"download_summary_{timestamp_str}.txt"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)

        logger.info(f"Summary report was successfully saved to: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
