import argparse
import asyncio
import logging
import os
import sys
import time
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


def _make_headers(api_key: str, user_key: str) -> dict[str, str]:
    import uuid
    return {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


async def fetch_candles_for_month(
    session: aiohttp.ClientSession,
    instrument_id: int,
    start_date: datetime,
    end_date: datetime,
    api_key: str,
    user_key: str
) -> list[dict]:
    url = "https://public-api.etoro.com/api/v1/market-data/candles"

    # Format eToro style
    params = {
        "instrumentId": instrument_id,
        "period": "OneMinute",
        "clientType": "Real",
        "fromDate": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "toDate": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    logger.info(f"Fetching {instrument_id} from {params['fromDate']} to {params['toDate']}")

    headers = _make_headers(api_key, user_key)
    try:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("candles", [])
            else:
                body = await resp.text()
                logger.warning(f"Failed to fetch {instrument_id} candles: HTTP {resp.status} - {body[:300]}")
                return []
    except Exception as e:
        logger.warning(f"Error fetching candles: {e}")
        return []


def create_quote_ticks_dataframe(candles: list[dict]) -> pd.DataFrame:
    rows = []

    for c in candles:
        try:
            # Parse ISO date
            # eToro usually returns e.g. "2023-01-01T12:00:00Z"
            ts_dt = pd.to_datetime(c["fromdate"])

            # ts_event / ts_init are unix nanoseconds
            ts_ns = int(ts_dt.timestamp() * 1e9)

            # Using low for bid, high for ask
            # QuoteTick bids/asks must be strings/bytes in typical parquet format
            low = str(c["low"]).encode("utf-8")
            high = str(c["high"]).encode("utf-8")
            one_sz = b"1"

            rows.append({
                "bid_price": low,
                "ask_price": high,
                "bid_size": one_sz,
                "ask_size": one_sz,
                "ts_event": pd.Series(ts_ns, dtype='uint64').iloc[0],
                "ts_init": pd.Series(ts_ns, dtype='uint64').iloc[0]
            })
        except Exception as e:
            logger.warning(f"Failed to process candle {c}: {e}")

    df = pd.DataFrame(rows)
    # Ensure correct types based on previous check
    if not df.empty:
        df["ts_event"] = df["ts_event"].astype("uint64")
        df["ts_init"] = df["ts_init"].astype("uint64")
    return df


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--etoro-id", type=int, required=True, help="eToro instrument ID")
    parser.add_argument("--symbol", type=str, required=True, help="Nautilus symbol (e.g., ADA.ETORO)")
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
    start_date = end_date - timedelta(days=30 * args.months)

    all_candles = []

    timeout = aiohttp.ClientTimeout(total=15.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        current_end = end_date
        while current_end > start_date:
            current_start = max(current_end - timedelta(days=30), start_date)

            candles = await fetch_candles_for_month(
                session, args.etoro_id, current_start, current_end, api_key, user_key
            )
            all_candles.extend(candles)

            current_end = current_start

            # sleep 1s between requests to avoid rate limits
            await asyncio.sleep(1.0)

    if not all_candles:
        logger.warning(f"No candles fetched for {args.symbol}.")
        sys.exit(0)

    df = create_quote_ticks_dataframe(all_candles)

    if df.empty:
        logger.warning("Dataframe is empty after parsing.")
        sys.exit(0)

    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_file)

    # Read back to validate
    read_df = pd.read_parquet(out_file)
    if len(read_df) == 0:
        logger.error(f"Validation failed: Parquet file {out_file} is empty.")
        sys.exit(1)

    logger.info(f"Summary: fetched {len(all_candles)} candles, wrote {len(df)} rows to {out_file}")
    logger.info(f"Date range covered: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
