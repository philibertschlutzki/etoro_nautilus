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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

RATE_LIMIT_DELAY = 1.1 

def _make_headers(api_key: str, user_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

async def fetch_candles_chunk(
    session: aiohttp.ClientSession,
    instrument_id: int,
    end_time: datetime,
    api_key: str,
    user_key: str,
    interval: str = "OneMinute" #hier kann der Zeitraum auf z.B. OneDay geändert werden.
) -> list[dict]:
    count = 1000
    url = f"https://public-api.etoro.com/api/v1/market-data/instruments/{instrument_id}/history/candles/desc/{interval}/{count}"

    params = {
        "endTime": end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    logger.info(f"Fetching {instrument_id} ({interval}) max {count} candles before {params['endTime']}...")

    headers = _make_headers(api_key, user_key)
    
    for attempt in range(3):
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    raw_data = await resp.json()
                    
                    # eToro API liefert eine doppelt verschachtelte Struktur: 
                    # {"candles": [{"instrumentId": 22, "candles": [{"fromDate": ...}]}]}
                    
                    # 1. Äußeres Dictionary entpacken
                    if isinstance(raw_data, dict):
                        inner = raw_data.get("candles") or raw_data.get("Candles") or raw_data.get("data")
                        if inner is not None:
                            raw_data = inner
                            
                    # 2. Inneres Array entpacken, falls das erste Element ein Wrapper ist
                    if isinstance(raw_data, list) and len(raw_data) > 0:
                        first = raw_data[0]
                        if isinstance(first, dict):
                            inner_candles = first.get("candles") or first.get("Candles")
                            if inner_candles is not None and isinstance(inner_candles, list):
                                return inner_candles
                                
                    return raw_data if isinstance(raw_data, list) else []
                
                elif resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limit exceeded (HTTP 429). Waiting for {retry_after} seconds before retry...")
                    await asyncio.sleep(retry_after)
                    continue
                    
                else:
                    body = await resp.text()
                    logger.warning(f"Failed to fetch {instrument_id} candles: HTTP {resp.status} - {body[:300]}")
                    return []
        except Exception as e:
            logger.warning(f"Network error fetching candles (Attempt {attempt+1}): {e}")
            await asyncio.sleep(5)

    return []

def _get_key_case_insensitive(row: dict, possible_keys: list) -> str:
    """Findet den tatsächlichen Key-Namen im Dictionary unabhängig von Groß-/Kleinschreibung."""
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
            logger.warning(f"Failed to process candle {c}: {e}")

    df = pd.DataFrame(rows)
    if not df.empty:
        df["ts_event"] = df["ts_event"].astype("uint64")
        df["ts_init"] = df["ts_init"].astype("uint64")
        df = df.sort_values(by="ts_event", ascending=True).reset_index(drop=True)
    return df


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--etoro-id", type=int, required=True, help="eToro instrument ID")
    parser.add_argument("--symbol", type=str, required=True, help="Nautilus symbol (e.g., NATGAS.ETORO)")
    parser.add_argument("--months", type=int, default=6, help="Number of months to fetch")
    parser.add_argument("--output-dir", type=str, default="data/nautilus/data/quote_tick", help="Output directory")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("ETORO_API_KEY")
    user_key = os.getenv("ETORO_USER_KEY")

    if not api_key or not user_key:
        logger.error("Missing ETORO_API_KEY or ETORO_USER_KEY in environment.")
        sys.exit(1)

    out_dir = Path(args.output_dir) / args.symbol
    out_file = out_dir / "data.parquet"

    if out_file.exists():
        logger.warning(f"Parquet file {out_file} already exists. Skipping.")
        sys.exit(0)

    end_date = datetime.now(timezone.utc)
    target_start_date = end_date - timedelta(days=30 * args.months)

    all_candles = []
    current_end_time = end_date
    last_fetched_oldest_timestamp = None

    timeout = aiohttp.ClientTimeout(total=20.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while current_end_time > target_start_date:
            
            chunk = await fetch_candles_chunk(
                session, args.etoro_id, current_end_time, api_key, user_key
            )
            
            if not chunk:
                logger.info("No more candles returned by the API.")
                break
                
            chunk_df = pd.DataFrame(chunk)
            
            possible_date_cols = ['fromdate', 'startdate', 'date', 'timestamp', 'time', 'start']
            date_col = next((col for col in chunk_df.columns if col.lower() in possible_date_cols), None)
            
            if not date_col:
                logger.error("API response format changed. Date column not found.")
                logger.error(f"Available columns: {chunk_df.columns.tolist()}")
                if len(chunk) > 0:
                    logger.error(f"First row sample data: {chunk[0]}")
                break
                
            chunk_df['parsed_date'] = pd.to_datetime(chunk_df[date_col])
            oldest_candle_time = chunk_df['parsed_date'].min()
            
            if last_fetched_oldest_timestamp == oldest_candle_time:
                logger.warning("API returned identical historical bounds. Breaking loop.")
                break

            all_candles.extend(chunk)
            last_fetched_oldest_timestamp = oldest_candle_time
            
            current_end_time = oldest_candle_time - timedelta(seconds=1)
            
            logger.info(f"Chunk fetched. Current oldest record: {oldest_candle_time}. Records accumulated: {len(all_candles)}")
            await asyncio.sleep(RATE_LIMIT_DELAY)

    if not all_candles:
        logger.warning(f"No candles fetched for {args.symbol}.")
        sys.exit(0)

    df = create_quote_ticks_dataframe(all_candles)

    if df.empty:
        logger.warning("Dataframe is empty after filtering. Columns might have been mismatched.")
        sys.exit(0)

    df = df.drop_duplicates(subset=["ts_event"]).reset_index(drop=True)
    df = df[df["timestamp_dt"] >= target_start_date]
    df = df.drop(columns=["timestamp_dt"])

    if df.empty:
        logger.warning("Dataframe is empty after filtering to requested date range.")
        sys.exit(0)

    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_file)

    read_df = pd.read_parquet(out_file)
    if len(read_df) == 0:
        logger.error(f"Validation failed: Parquet file {out_file} is empty.")
        sys.exit(1)

    logger.info(f"Summary: wrote {len(df)} rows to {out_file}")
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())