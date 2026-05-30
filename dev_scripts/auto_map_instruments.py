import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Constants
ETORO_METADATA_URL = "https://api.etorostatic.com/sapi/instrumentsmetadata/V1.1/instruments"
CACHE_FILE = Path("data/universe/etoro_metadata_cache.json")
CACHE_TTL_HOURS = 24

def _fallback_precisions(asset_class: str) -> dict:
    """Mock of automation.utils._fallback_precisions for fallback."""
    if asset_class.lower() == "stocks" or asset_class.lower() == "equities":
        return {"price_precision": 2, "size_precision": 2}
    elif asset_class.lower() == "crypto":
        return {"price_precision": 4, "size_precision": 8}
    else:
        return {"price_precision": 4, "size_precision": 2}

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-file", default="automation/config/instrument_map.json")
    parser.add_argument("--universe-file", default="data/universe/momentum_ls.json")
    args = parser.parse_args()

    map_path = Path(args.map_file)
    universe_path = Path(args.universe_file)

    # Load existing map
    if not map_path.exists():
        logger.error(f"Instrument map not found at {map_path}")
        sys.exit(1)

    with open(map_path, "r", encoding="utf-8") as f:
        instrument_map_data = json.load(f)

    if "instruments" not in instrument_map_data:
        instrument_map_data["instruments"] = {}

    existing_map = instrument_map_data["instruments"]

    # Identify unknown symbols from universe file if it exists
    unknown_ids = set()
    if universe_path.exists():
        with open(universe_path, "r", encoding="utf-8") as f:
            universe_data = json.load(f)

        for pos in universe_data.get("universe", []):
            eid = pos.get("etoro_id")
            if eid and eid not in existing_map:
                unknown_ids.add(eid)

    if not unknown_ids:
        logger.info("No unknown instrument IDs found in universe. Exiting cleanly.")
        sys.exit(0)

    logger.info(f"Found {len(unknown_ids)} unknown IDs to map.")

    metadata = get_etoro_metadata()
    if not metadata:
        logger.error("Could not retrieve metadata. Cannot map symbols.")
        sys.exit(1)

    # Build lookup dictionary from metadata
    instruments_list = metadata.get("InstrumentDisplayDatas", [])
    meta_lookup = {str(item.get("InstrumentID")): item for item in instruments_list}

    newly_mapped = 0
    still_unknown = 0

    for uid in unknown_ids:
        if uid in meta_lookup:
            item = meta_lookup[uid]
            symbol = item.get("SymbolFull")
            asset_class = item.get("AssetClass", "Unknown")
            precisions = _fallback_precisions(asset_class)

            existing_map[uid] = {
                "symbol": symbol,
                "asset_class": asset_class,
                "price_precision": precisions["price_precision"],
                "size_precision": precisions["size_precision"]
            }
            newly_mapped += 1
            logger.info(f"Mapped {uid} -> {symbol} ({asset_class})")
        else:
            still_unknown += 1
            logger.warning(f"ID {uid} not found in eToro metadata.")

    if newly_mapped > 0:
        logger.info(f"Saving {newly_mapped} new mappings to {map_path}")
        # Re-save the updated map preserving the schema
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(instrument_map_data, f, indent=2, ensure_ascii=False)

    if still_unknown > 0:
        logger.error(f"{still_unknown} instrument IDs could not be mapped.")
        sys.exit(1)

    logger.info("All instrument IDs successfully mapped.")
    sys.exit(0)

if __name__ == "__main__":
    main()
