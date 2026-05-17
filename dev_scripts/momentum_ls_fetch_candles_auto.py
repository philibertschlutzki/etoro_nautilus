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
            pass

    df = pd.DataFrame(rows)
    if not df.empty:
        df["ts_event"] = df["ts_event"].astype("uint64")
        df["ts_init"] = df["ts_init"].astype("uint64")
        df = df.sort_values(by="ts_event", ascending=True).reset_index(drop=True)
    return df


def get_date_range_str(df: pd.DataFrame) -> str:
    """Hilfsfunktion für lesbare Datumsbereiche im Report"""
    if df.empty or "ts_event" not in df.columns:
        return "N/A"
    min_dt = datetime.fromtimestamp(df["ts_event"].min() / 1e9, tz=timezone.utc)
    max_dt = datetime.fromtimestamp(df["ts_event"].max() / 1e9, tz=timezone.utc)
    return f"{min_dt.strftime('%Y-%m-%d')} to {max_dt.strftime('%Y-%m-%d')}"


async def process_instrument(
    session: aiohttp.ClientSession, 
    etoro_id: int, 
    symbol: str, 
    target_start_date: datetime, 
    api_key: str, 
    user_key: str, 
    intervals_cascade: list[str],
    output_dir: str
) -> dict:
    """Lade historische Kerzen herunter und retourniere einen Summary-Eintrag."""
    out_dir = Path(output_dir) / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    
    existing_files = list(out_dir.glob("*.parquet"))
    existing_df = pd.DataFrame()
    
    # Delta-Update Logik
    if existing_files:
        logger.info(f"[{symbol}] Found existing data. Reading to find latest timestamp.")
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
                
                if latest_dt > target_start_date:
                    target_start_date = latest_dt
                    logger.info(f"[{symbol}] Delta update: Fetching only records newer than {target_start_date}")

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

    # Verarbeitung
    new_df = pd.DataFrame()
    if all_candles:
        new_df = create_quote_ticks_dataframe(all_candles)
        if not new_df.empty:
            new_df = new_df[new_df["timestamp_dt"] >= target_start_date]
            new_df = new_df.drop(columns=["timestamp_dt"])

    if new_df.empty and existing_df.empty:
        logger.warning(f"[{symbol}] No data available (neither existing nor new). Moving to next.")
        return {"symbol": symbol, "status": "No Data", "intervals": "-", "rows": 0, "range": "N/A"}

    # Kombinieren
    if not new_df.empty:
        combined_df = pd.concat([existing_df, new_df], ignore_index=True) if not existing_df.empty else new_df
    else:
        combined_df = existing_df

    combined_df = combined_df.drop_duplicates(subset=["ts_event"]).sort_values(by="ts_event", ascending=True).reset_index(drop=True)

    if combined_df.empty:
        return {"symbol": symbol, "status": "Error (Empty after merge)", "intervals": "-", "rows": 0, "range": "N/A"}

    min_ts = combined_df["ts_event"].min()
    max_ts = combined_df["ts_event"].max()
    
    file_name = f"{format_nautilus_timestamp(min_ts)}_{format_nautilus_timestamp(max_ts)}.parquet"
    out_file = out_dir / file_name

    # Check ob up to date
    if new_df.empty and out_file.exists():
        logger.info(f"[{symbol}] No new data fetched. Existing file is already up to date.")
        return {
            "symbol": symbol, 
            "status": "Up-to-date", 
            "intervals": "-", 
            "rows": len(combined_df), 
            "range": get_date_range_str(combined_df)
        }

    # Aufräumen & Speichern
    for f in existing_files:
        try:
            f.unlink()
        except Exception:
            pass

    combined_df.to_parquet(out_file)
    logger.info(f"[{symbol}] Success: Wrote {len(combined_df)} total rows.")
    
    return {
        "symbol": symbol, 
        "status": "Updated/New", 
        "intervals": ", ".join(intervals_used) if intervals_used else "-", 
        "rows": len(combined_df), 
        "range": get_date_range_str(combined_df)
    }


async def main():
    parser = argparse.ArgumentParser(description="Fetch historical data for eToro instruments.")
    parser.add_argument("--symbol", type=str, default=None, help="Optional: Nautilus symbol to fetch.")
    parser.add_argument("--months", type=int, default=12, help="Number of months to fetch (initial load depth)")
    parser.add_argument("--intervals", type=str, default="OneHour,OneDay,OneWeek", help="Comma-separated intervals")
    parser.add_argument("--output-dir", type=str, default="data/nautilus/data/quote_tick", help="Output directory")
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
    
    # Sammelt die Resultate für den Report
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
                    output_dir=args.output_dir
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
        # Erstelle eine formattierte Text-Tabelle
        report_lines = []
        report_lines.append("=== DOWNLOAD SUMMARY REPORT ===")
        report_lines.append(f"{'Symbol':<15} | {'Status':<12} | {'Intervals Used':<25} | {'Total Rows':<10} | {'Date Range'}")
        report_lines.append("-" * 105)
        
        for res in summary_results:
            report_lines.append(f"{res['symbol']:<15} | {res['status']:<12} | {res['intervals']:<25} | {res['rows']:<10} | {res['range']}")
            
        report_text = "\n".join(report_lines)
        
        # Gebe die Tabelle in der Konsole aus
        print(f"\n{report_text}\n")
        
        # Speichere die Tabelle zusätzlich als .txt Datei, damit nichts verloren geht
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_path = Path(args.output_dir) / f"download_summary_{timestamp_str}.txt"
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)
            
        logger.info(f"Summary report was successfully saved to: {report_path}")

if __name__ == "__main__":
    asyncio.run(main())