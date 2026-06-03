import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
from automation.utils import _fallback_precisions


import aiohttp
from dotenv import load_dotenv

# Search .env in automation/ first, then in PROJECT_ROOT (fallback)
_THIS_DIR = Path(__file__).resolve().parent
ENV_FILE = _THIS_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE = _THIS_DIR.parent / ".env"
load_dotenv(str(ENV_FILE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# Constants for Metadata
ETORO_METADATA_URL = "https://api.etorostatic.com/sapi/instrumentsmetadata/V1.1/instruments"
CACHE_FILE = Path("data/universe/etoro_metadata_cache.json")
CACHE_TTL_HOURS = 24

def get_etoro_metadata():
    """Fetch eToro metadata with caching and retries."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    if CACHE_FILE.exists():
        mtime = os.path.getmtime(CACHE_FILE)
        age_hours = (time.time() - mtime) / 3600
        if age_hours < CACHE_TTL_HOURS:
            logger.info(f"Using cached metadata (age: {age_hours:.1f} hours).")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    logger.info("Fetching fresh metadata from eToro API...")
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[ 500, 502, 503, 504 ])
    session.mount('https://', HTTPAdapter(max_retries=retries))

    try:
        response = session.get(ETORO_METADATA_URL, timeout=15)
        response.raise_for_status()
        data = response.json()

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        return data
    except Exception as e:
        logger.error(f"Failed to fetch metadata: {e}")
        if CACHE_FILE.exists():
            logger.info("Falling back to stale cache.")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

def _make_headers(api_key: str, user_key: str) -> dict[str, str]:
    import uuid
    return {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

def load_instrument_map(path: Path) -> dict[str, str]:
    """Lädt {etoro_id: symbol} aus instrument_map.json."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for etoro_id, info in data.get("instruments", {}).items():
        result[etoro_id] = info.get("symbol")
    return result

def is_universe_stale(universe_path: Path, max_age_hours: float = 24.0) -> bool:
    """Prüft ob die Universe-Datei älter als max_age_hours ist."""
    if not universe_path.exists():
        return True
    try:
        with open(universe_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        fetched_at_str = data.get("fetched_at")
        if not fetched_at_str:
            return True
        fetched_at = datetime.fromisoformat(fetched_at_str)
        now = datetime.now(timezone.utc)
        age = (now - fetched_at).total_seconds() / 3600.0
        return age > max_age_hours
    except Exception as e:
        logger.warning(f"Error checking universe file: {e}")
        return True

async def run_fetch(
    api_key: str,
    user_key: str,
    output_path: Path,
    instrument_map_path: Path,
) -> bool:
    username = os.getenv("MOMENTUM_LS_USERNAME")
    if not username:
        logger.error("Missing required environment variable: MOMENTUM_LS_USERNAME")
        sys.exit(1)
    """Fetcht das Universe und speichert es. Gibt True bei Erfolg zurück."""
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
    except asyncio.TimeoutError:
        logger.error("Request to eToro API timed out.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

    instrument_map = load_instrument_map(instrument_map_path)
    positions = data.get("clientPortfolio", data).get("positions", [])

    universe = []
    known_count = 0
    unknown_count = 0
    unknown_instruments = {}

    for pos in positions:
        pos_lower = {k.lower(): v for k, v in pos.items()}
        instrument_id = str(pos_lower.get("instrumentid", ""))
        raw_name = pos_lower.get("instrumentname", "Unknown")

        symbol = instrument_map.get(instrument_id)

        if symbol is None:
            if instrument_id not in unknown_instruments:
                unknown_instruments[instrument_id] = {"name": raw_name, "count": 0}
            unknown_instruments[instrument_id]["count"] += 1
            unknown_count += 1
        else:
            known_count += 1

        universe.append({
            "etoro_id": instrument_id,
            "symbol": symbol,
            "raw_name": raw_name
        })

    if unknown_instruments:
        logger.info(f"Found {len(unknown_instruments)} unknown instrument IDs. Attempting to resolve...")
        metadata = get_etoro_metadata()
        if metadata:
            instruments_list = metadata.get("InstrumentDisplayDatas", [])
            meta_lookup = {str(item.get("InstrumentID")): item for item in instruments_list}

            with open(instrument_map_path, "r", encoding="utf-8") as f:
                instrument_map_data = json.load(f)

            if "instruments" not in instrument_map_data:
                instrument_map_data["instruments"] = {}

            existing_map = instrument_map_data["instruments"]
            newly_mapped = 0

            for uid, info in unknown_instruments.items():
                if uid in meta_lookup:
                    item = meta_lookup[uid]
                    symbol_full = item.get("SymbolFull")
                    symbol = f"{symbol_full}.ETORO" if symbol_full else None
                    asset_class = item.get("AssetClass", "Unknown")
                    precisions = _fallback_precisions(symbol if symbol else asset_class)

                    existing_map[uid] = {
                        "symbol": symbol,
                        "asset_class": asset_class,
                        "price_precision": precisions[0],
                        "size_precision": precisions[1]
                    }
                    newly_mapped += 1
                    logger.info(f"Resolved {uid} -> {symbol} ({asset_class})")

                    # Update universe entry on-the-fly
                    for u in universe:
                        if u["etoro_id"] == uid:
                            u["symbol"] = symbol
                else:
                    logger.warning(f"Unknown instrument ID: {uid} ({info['name']}) - could not resolve in metadata")

            if newly_mapped > 0:
                with open(instrument_map_path, "w", encoding="utf-8") as f:
                    json.dump(instrument_map_data, f, indent=2, ensure_ascii=False)
                logger.info(f"Saved {newly_mapped} new mappings to {instrument_map_path}")
                known_count += newly_mapped
        else:
            logger.error("Could not fetch eToro metadata to resolve unknown instruments.")
            for uid, info in unknown_instruments.items():
                logger.warning(f"Unknown instrument ID: {uid} ({info['name']}) - occurred {info['count']} times")

    output_data = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "username": username,
        "universe": universe
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = output_path.with_suffix('.tmp')

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    os.replace(tmp_file, output_path)

    logger.info(f"Total positions: {len(universe)}")
    logger.info(f"Known symbols: {known_count}")
    logger.info(f"Unknown symbols: {len(unknown_instruments)} unique, {unknown_count} total")
    logger.info(f"Saved to {output_path}")

    return True

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/universe/momentum_ls.json")
    args = parser.parse_args()

    api_key = os.getenv("ETORO_API_KEY")
    user_key = os.getenv("ETORO_USER_KEY")

    missing = []
    if not api_key: missing.append("ETORO_API_KEY")
    if not user_key: missing.append("ETORO_USER_KEY")
    if not os.getenv("MOMENTUM_LS_USERNAME"): missing.append("MOMENTUM_LS_USERNAME")

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    instrument_map_path = _THIS_DIR / "config" / "instrument_map.json"

    asyncio.run(run_fetch(
        api_key=api_key,
        user_key=user_key,
        output_path=Path(args.output),
        instrument_map_path=instrument_map_path
    ))

if __name__ == "__main__":
    main()
