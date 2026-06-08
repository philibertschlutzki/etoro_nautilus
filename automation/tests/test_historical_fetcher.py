import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from automation.historical_fetcher import (
    _load_inception_bounds,
    _save_inception_bound,
    is_backtest_range_covered,
    INCEPTION_CACHE_PATH,
)

@pytest.fixture(autouse=True)
def clean_cache(tmp_path):
    """Ensure the cache file doesn't persist across tests."""
    with patch("automation.historical_fetcher.INCEPTION_CACHE_PATH", tmp_path / "inception_bounds.json"):
        yield tmp_path

def test_load_and_save_inception_bounds(clean_cache):
    """Test atomic reading and writing to the inception bounds cache."""
    # Ensure cache starts empty
    assert _load_inception_bounds() == {}

    # Save a bound
    _save_inception_bound("TSLA.ETORO", 123456789)
    bounds = _load_inception_bounds()
    assert bounds == {"TSLA.ETORO": 123456789}

    # Save another bound and overwrite existing
    _save_inception_bound("TSLA.ETORO", 999999999)
    _save_inception_bound("AAPL.ETORO", 111111111)

    bounds2 = _load_inception_bounds()
    assert bounds2 == {"TSLA.ETORO": 999999999, "AAPL.ETORO": 111111111}

def test_is_backtest_range_covered_with_cache(clean_cache, tmp_path):
    """Test that is_backtest_range_covered bypasses start_ns check if old enough compared to cache."""
    symbol = "YOUNG.ETORO"

    # Setup a mock catalog structure
    catalog_path = tmp_path / "nautilus"
    parquet_dir = catalog_path / "data" / "quote_tick" / symbol
    parquet_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = parquet_dir / "data.parquet"

    # Create a small parquet file with youngest ts = 1000
    ts_list = [1000, 2000, 3000]
    table = pa.Table.from_arrays([pa.array(ts_list, type=pa.int64())], names=["ts_event"])
    pq.write_table(table, str(parquet_file))

    # Test case 1: Cache is empty, requested start is earlier (500). Should be False.
    assert is_backtest_range_covered(symbol, 500, catalog_path) is False

    # Test case 2: Cache is populated, oldest bar (1000) <= bounds (1000), requested start is much older (0).
    _save_inception_bound(symbol, 1000)
    assert is_backtest_range_covered(symbol, 0, catalog_path) is True

    # Test case 3: Cache is populated, oldest bar (1000) > bounds (500), requested start is older (0).
    # This simulates a case where we have not fetched up to the known inception bound.
    _save_inception_bound(symbol, 500)
    assert is_backtest_range_covered(symbol, 0, catalog_path) is False

    # Test case 4: Cache is populated, oldest bar (1000) <= bounds (1500), requested start is older (0).
    # This shouldn't normally happen (we wouldn't record a bound higher than we fetched), but just checking logic.
    _save_inception_bound(symbol, 1500)
    assert is_backtest_range_covered(symbol, 0, catalog_path) is True
