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
    interval: str = "OneDay" # Standardintervall
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
                    
                    # Verschachtelte eToro-JSON Struktur entpacken
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
                
                elif resp.status == 429: # Rate Limit erreicht
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

def create_quote_ticks_dataframe(candles: list[dict]) -> pd.DataFrame:
    rows = []

    for c in candles:
        try:
            date_key = _get_key_case_insensitive(c, ['fromdate', 'startdate', 'date', 'timestamp', 'time', 'start'])
            low_key = _get_key_case_insensitive(c, ['low', 'l'])
            high_key = _get_key_case_insensitive(c, ['high', 'h'])

            if not date_key or not low_key or not high_key:
                continue

            date_str = c[date_key]
            ts_dt = pd.to_datetime(date_str)
            # Sicherstellen, dass die Zeitzone berücksichtigt wird (UTC)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.tz_localize('UTC')
                
            ts_ns = int(ts_dt.timestamp() * 1e9)

            low = str(c[low_key]).encode("utf-8")
            high = str(c[high_key]).encode("utf-8")
            one_sz = b"1"

            rows.append({
                "bid_price": low,
                "ask_price": high,
                "bid_size": one_sz,
                "ask_size": one_sz,
                "ts_event": pd.Series(ts_ns, dtype='uint64').iloc[0],
                "ts_init": pd.Series(ts_ns, dtype='uint64').iloc[0],
                "timestamp_dt": ts_dt 
            })
        except Exception as e:
            pass # Stille Unterdrückung bei ungültigen Kerzen

    df = pd.DataFrame(rows)
    if not df.empty:
        df["ts_event"] = df["ts_event"].astype("uint64")
        df["ts_init"] = df["ts_init"].astype("uint64")
        df = df.sort_values(by="ts_event", ascending=True).reset_index(drop=True)
    return df


async def process_instrument(
    session: aiohttp.ClientSession, 
    etoro_id: int, 
    symbol: str, 
    target_start_date: datetime, 
    api_key: str, 
    user_key: str, 
    interval: str,
    output_dir: str
):
    """Lade historische Kerzen für ein einzelnes Instrument herunter (inkl. Delta-Updates)."""
    out_dir = Path(output_dir) / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    
    existing_files = list(out_dir.glob("*.parquet"))
    existing_df = pd.DataFrame()
    
    # Delta-Update Logik: Lese existierende Daten, um den letzten Zeitstempel zu finden
    if existing_files:
        logger.info(f"[{symbol}] Found {len(existing_files)} existing parquet file(s). Reading to find latest timestamp.")
        dfs = []
        for f in existing_files:
            try:
                dfs.append(pd.read_parquet(f))
            except Exception as e:
                logger.warning(f"[{symbol}] Failed to read {f}: {e}")
                
        if dfs:
            existing_df = pd.concat(dfs, ignore_index=True)
            if not existing_df.empty and "ts_event" in existing_df.columns:
                max_ts_ns = existing_df["ts_event"].max()
                latest_dt = datetime.fromtimestamp(max_ts_ns / 1e9, tz=timezone.utc)
                
                # Passe das Startdatum für den Download an
                if latest_dt > target_start_date:
                    target_start_date = latest_dt
                    logger.info(f"[{symbol}] Delta update: Fetching only records newer than {target_start_date}")

    logger.info(f"=== Starting API download for {symbol} (ID: {etoro_id}) ===")
    
    current_end_time = datetime.now(timezone.utc)
    all_candles = []
    last_fetched_oldest_timestamp = None

    while current_end_time > target_start_date:
        chunk = await fetch_candles_chunk(
            session, etoro_id, current_end_time, api_key, user_key, interval
        )
        
        if not chunk:
            logger.info(f"[{symbol}] No more candles returned by the API.")
            break
            
        chunk_df = pd.DataFrame(chunk)
        
        possible_date_cols = ['fromdate', 'startdate', 'date', 'timestamp', 'time', 'start']
        date_col = next((col for col in chunk_df.columns if col.lower() in possible_date_cols), None)
        
        if not date_col:
            logger.error(f"[{symbol}] API response format changed. Date column not found.")
            break
            
        # Parse Dates & handle timezones
        chunk_df['parsed_date'] = pd.to_datetime(chunk_df[date_col])
        if chunk_df['parsed_date'].dt.tz is None:
             chunk_df['parsed_date'] = chunk_df['parsed_date'].dt.tz_localize('UTC')
        
        oldest_candle_time = chunk_df['parsed_date'].min()
        
        if last_fetched_oldest_timestamp == oldest_candle_time:
            logger.warning(f"[{symbol}] API returned identical historical bounds. Breaking loop.")
            break

        all_candles.extend(chunk)
        last_fetched_oldest_timestamp = oldest_candle_time
        
        current_end_time = oldest_candle_time - timedelta(seconds=1)
        
        logger.info(f"[{symbol}] Chunk fetched. Oldest record: {oldest_candle_time}. Total accumulated: {len(all_candles)}")
        
        # Wichtig: Rate-Limit Pause nach JEDEM Request
        await asyncio.sleep(RATE_LIMIT_DELAY)

    # Verarbeite neu heruntergeladene Daten
    new_df = pd.DataFrame()
    if all_candles:
        new_df = create_quote_ticks_dataframe(all_candles)
        if not new_df.empty:
            new_df = new_df[new_df["timestamp_dt"] >= target_start_date]
            new_df = new_df.drop(columns=["timestamp_dt"])

    if new_df.empty and existing_df.empty:
        logger.warning(f"[{symbol}] No data available (neither existing nor new). Moving to next.")
        return

    # Führe existierende und neue Daten zusammen
    if not new_df.empty:
        combined_df = pd.concat([existing_df, new_df], ignore_index=True) if not existing_df.empty else new_df
    else:
        combined_df = existing_df

    # Duplikate entfernen und chronologisch sortieren
    combined_df = combined_df.drop_duplicates(subset=["ts_event"]).sort_values(by="ts_event", ascending=True).reset_index(drop=True)

    if combined_df.empty:
        logger.warning(f"[{symbol}] Dataframe is empty after parsing and combining. Moving to next.")
        return

    # Generiere Nautilus-konformen Dateinamen basierend auf min und max Timestamp
    min_ts = combined_df["ts_event"].min()
    max_ts = combined_df["ts_event"].max()
    
    file_name = f"{format_nautilus_timestamp(min_ts)}_{format_nautilus_timestamp(max_ts)}.parquet"
    out_file = out_dir / file_name

    # Vermeide unnötige Schreibvorgänge, wenn keine neuen Daten vorhanden sind und die Zieldatei schon existiert
    if new_df.empty and out_file.exists():
        logger.info(f"[{symbol}] No new data fetched. Existing file {file_name} is already up to date.")
        return

    # Lösche alte Parquet Dateien (inkl. "data.parquet" oder veraltete Timestamp-Dateien)
    for f in existing_files:
        try:
            f.unlink()
            logger.debug(f"[{symbol}] Deleted old file {f.name}")
        except Exception as e:
            logger.warning(f"[{symbol}] Could not delete old file {f}: {e}")

    # Speichere die zusammengeführte Gesamtdatei
    combined_df.to_parquet(out_file)
    logger.info(f"[{symbol}] Success: Wrote {len(combined_df)} total rows to {out_file.name}")


async def main():
    parser = argparse.ArgumentParser(description="Fetch historical data for eToro instruments (with delta updates).")
    parser.add_argument("--symbol", type=str, default=None, help="Optional: Nautilus symbol to fetch (e.g. NATGAS.ETORO). If omitted, fetches ALL from instrument_map.py.")
    parser.add_argument("--months", type=int, default=6, help="Number of months to fetch (initial load depth)")
    parser.add_argument("--interval", type=str, default="OneDay", help="Candle interval (e.g. OneMinute, FiveMinutes, OneHour, OneDay)")
    parser.add_argument("--output-dir", type=str, default="data/nautilus/data/quote_tick", help="Output directory base path")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("ETORO_API_KEY")
    user_key = os.getenv("ETORO_USER_KEY")

    if not api_key or not user_key:
        logger.error("Missing ETORO_API_KEY or ETORO_USER_KEY in environment.")
        sys.exit(1)

    # Filtern der Instrumente, falls ein spezifisches Symbol angegeben wurde
    if args.symbol:
        instruments_to_fetch = {k: v for k, v in ETORO_INSTRUMENTS.items() if v == args.symbol}
        if not instruments_to_fetch:
            logger.error(f"Symbol {args.symbol} not found in instrument_map.py!")
            sys.exit(1)
    else:
        instruments_to_fetch = ETORO_INSTRUMENTS
        
    logger.info(f"Starting bulk download/update for {len(instruments_to_fetch)} instruments...")

    # target_start_date definiert, wie weit wir beim "Initial Load" zurückgehen
    target_start_date = datetime.now(timezone.utc) - timedelta(days=30 * args.months)

    timeout = aiohttp.ClientTimeout(total=20.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for etoro_id_str, symbol in instruments_to_fetch.items():
            try:
                etoro_id = int(etoro_id_str)
                await process_instrument(
                    session=session,
                    etoro_id=etoro_id,
                    symbol=symbol,
                    target_start_date=target_start_date,
                    api_key=api_key,
                    user_key=user_key,
                    interval=args.interval,
                    output_dir=args.output_dir
                )
            except Exception as e:
                logger.error(f"Unexpected error processing {symbol}: {e}")
                
            # Kleine Pause zwischen den Instrumenten zur Sicherheit
            await asyncio.sleep(RATE_LIMIT_DELAY)

    logger.info("=== Bulk update process completed ===")

if __name__ == "__main__":
    asyncio.run(main())