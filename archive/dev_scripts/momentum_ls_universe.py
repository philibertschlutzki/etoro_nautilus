import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

# Ensure adapters is in the path
sys.path.append(str(Path(__file__).resolve().parent.parent))
from archive.adapters.instrument_map import ETORO_INSTRUMENTS

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


async def fetch_universe(username: str, api_key: str, user_key: str) -> dict[str, Any] | None:
    url = f"https://public-api.etoro.com/api/v1/user-info/people/{username}/portfolio/live"
    headers = _make_headers(api_key, user_key)
    timeout = aiohttp.ClientTimeout(total=10.0)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status in (401, 403):
                    logger.error(f"Authentication failed: HTTP {resp.status}. Check ETORO_API_KEY and ETORO_USER_KEY.")
                    sys.exit(1)
                elif resp.status != 200:
                    body = await resp.text()
                    logger.error(f"HTTP {resp.status} - {body[:300]}")
                    sys.exit(1)

                data = await resp.json()
                return data

    except asyncio.TimeoutError:
        logger.error("Request to eToro API timed out.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


def parse_and_save(data: dict[str, Any], username: str, output_path: str) -> None:
    positions = data.get("clientPortfolio", data).get("positions", [])

    universe = []
    known_count = 0
    unknown_count = 0

    for pos in positions:
        pos_lower = {k.lower(): v for k, v in pos.items()}
        instrument_id = str(pos_lower.get("instrumentid", ""))
        raw_name = pos_lower.get("instrumentname", "Unknown")

        symbol = ETORO_INSTRUMENTS.get(instrument_id)

        if symbol is None:
            logger.warning(f"Unknown instrument ID: {instrument_id} ({raw_name})")
            unknown_count += 1
        else:
            known_count += 1

        universe.append({
            "etoro_id": instrument_id,
            "symbol": symbol,
            "raw_name": raw_name
        })

    output_data = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "username": username,
        "universe": universe
    }

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = out_file.with_suffix('.tmp')

    with open(tmp_file, "w") as f:
        json.dump(output_data, f, indent=2)

    os.replace(tmp_file, out_file)

    logger.info(f"Total positions: {len(universe)}")
    logger.info(f"Known symbols: {known_count}")
    logger.info(f"Unknown symbols: {unknown_count}")
    logger.info(f"Saved to {output_path}")

async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/universe/momentum_ls.json")
    args = parser.parse_args()

    load_dotenv()

    api_key = os.getenv("ETORO_API_KEY")
    user_key = os.getenv("ETORO_USER_KEY")
    username = os.getenv("MOMENTUM_LS_USERNAME")

    missing = []
    if not api_key: missing.append("ETORO_API_KEY")
    if not user_key: missing.append("ETORO_USER_KEY")
    if not username: missing.append("MOMENTUM_LS_USERNAME")

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    data = await fetch_universe(username, api_key, user_key)
    if data:
        parse_and_save(data, username, args.output)

if __name__ == "__main__":
    asyncio.run(main())
