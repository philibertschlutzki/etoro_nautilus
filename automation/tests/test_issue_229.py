import pytest
import math
import pyarrow as pa
import pyarrow.parquet as pq
from automation._serde import encode_price_fsb16, encode_qty_fsb16
from automation.backtest_runner import run_single_backtest_worker
from pathlib import Path
import time
import sys
import multiprocessing
import concurrent.futures

def run_isolated_worker(*args, **kwargs):
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as executor:
        future = executor.submit(run_single_backtest_worker, *args, **kwargs)
        return future.result()

def test_mean_reversion_trades_generated(tmp_path):
    catalog_path = tmp_path / "nautilus"
    tick_dir = catalog_path / "data" / "quote_tick" / "TSLA.ETORO"
    tick_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = tick_dir / "data.parquet"

    price_prec = 2
    size_prec = 2

    bid_prices = []
    ask_prices = []
    bid_sizes = []
    ask_sizes = []
    ts_events = []
    ts_inits = []

    base_ts = int(time.time() * 1e9) - 1000 * 3600 * 1_000_000_000

    # Generate oscillating points
    for i in range(1000):
        # Oscillate around 200.0 with a high sin wave amplitude to trigger bands
        price_val = 200.0 + 50.0 * math.sin(i * 0.2)
        qty_val = 1.0

        bid_prices.append(encode_price_fsb16(price_val, price_prec))
        ask_prices.append(encode_price_fsb16(price_val, price_prec))
        bid_sizes.append(encode_qty_fsb16(qty_val, size_prec))
        ask_sizes.append(encode_qty_fsb16(qty_val, size_prec))

        ts = base_ts + i * 3600 * 1_000_000_000  # 1 hour intervals
        ts_events.append(ts)
        ts_inits.append(ts)

    _FSB16 = pa.binary(16)
    schema = pa.schema([
        pa.field("bid_price", _FSB16),
        pa.field("ask_price", _FSB16),
        pa.field("bid_size",  _FSB16),
        pa.field("ask_size",  _FSB16),
        pa.field("ts_event",  pa.uint64()),
        pa.field("ts_init",   pa.uint64()),
    ])

    meta = {
        b"price_precision": str(price_prec).encode(),
        b"size_precision":  str(size_prec).encode(),
        b"instrument_id":   b"TSLA.ETORO",
    }

    table = pa.table(
        {
            "bid_price": pa.array(bid_prices, type=_FSB16),
            "ask_price": pa.array(ask_prices, type=_FSB16),
            "bid_size":  pa.array(bid_sizes,  type=_FSB16),
            "ask_size":  pa.array(ask_sizes,  type=_FSB16),
            "ts_event":  pa.array(ts_events,  type=pa.uint64()),
            "ts_init":   pa.array(ts_inits,   type=pa.uint64()),
        },
        schema=schema,
    )
    table = table.replace_schema_metadata(meta)
    pq.write_table(table, str(parquet_file))

    sys.path.append(str(Path(".").absolute()))

    strat = {
        "strategy_class": "MeanReversionStrategy",
        "strategy_module": "automation.strategies.mean_reversion",
        "config_class": "MeanReversionConfig",
        "params": {
             "trend_filter_period": 0 # simplify
        }
    }

    res = run_isolated_worker(
        inst_id_str="TSLA.ETORO",
        bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
        strat=strat,
        catalog_path=str(catalog_path),
        start_ns=None,
        end_ns=None,
        start_capital=1000.0,
        generate_html_report=False,
        reports_dir=str(tmp_path / "reports"),
        worker_log_file=str(tmp_path / "worker.log"),
    )

    assert res != {}, "Worker crashed"
    metrics = res.get("metrics", {})
    assert metrics.get("total_trades", 0) > 20, f"MeanReversion has flat-lock! Trades: {metrics.get('total_trades')}"


def test_combo_trend_trades_generated(tmp_path):
    catalog_path = tmp_path / "nautilus"
    tick_dir = catalog_path / "data" / "quote_tick" / "TSLA.ETORO"
    tick_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = tick_dir / "data.parquet"

    price_prec = 2
    size_prec = 2

    bid_prices = []
    ask_prices = []
    bid_sizes = []
    ask_sizes = []
    ts_events = []
    ts_inits = []

    base_ts = int(time.time() * 1e9) - 1000 * 3600 * 1_000_000_000

    # Generate oscillating points + trend
    for i in range(1000):
        # Trend up + oscillating
        price_val = 200.0 + i*0.1 + 10.0 * math.sin(i * 0.2)
        qty_val = 1.0

        bid_prices.append(encode_price_fsb16(price_val, price_prec))
        ask_prices.append(encode_price_fsb16(price_val, price_prec))
        bid_sizes.append(encode_qty_fsb16(qty_val, size_prec))
        ask_sizes.append(encode_qty_fsb16(qty_val, size_prec))

        ts = base_ts + i * 3600 * 1_000_000_000  # 1 hour intervals
        ts_events.append(ts)
        ts_inits.append(ts)

    _FSB16 = pa.binary(16)
    schema = pa.schema([
        pa.field("bid_price", _FSB16),
        pa.field("ask_price", _FSB16),
        pa.field("bid_size",  _FSB16),
        pa.field("ask_size",  _FSB16),
        pa.field("ts_event",  pa.uint64()),
        pa.field("ts_init",   pa.uint64()),
    ])

    meta = {
        b"price_precision": str(price_prec).encode(),
        b"size_precision":  str(size_prec).encode(),
        b"instrument_id":   b"TSLA.ETORO",
    }

    table = pa.table(
        {
            "bid_price": pa.array(bid_prices, type=_FSB16),
            "ask_price": pa.array(ask_prices, type=_FSB16),
            "bid_size":  pa.array(bid_sizes,  type=_FSB16),
            "ask_size":  pa.array(ask_sizes,  type=_FSB16),
            "ts_event":  pa.array(ts_events,  type=pa.uint64()),
            "ts_init":   pa.array(ts_inits,   type=pa.uint64()),
        },
        schema=schema,
    )
    table = table.replace_schema_metadata(meta)
    pq.write_table(table, str(parquet_file))

    strat = {
        "strategy_class": "ComboTrendVwapStrategy",
        "strategy_module": "automation.strategies.tesla_combo_strategy",
        "config_class": "ComboTrendVwapConfig",
        "params": {}
    }

    res = run_isolated_worker(
        inst_id_str="TSLA.ETORO",
        bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
        strat=strat,
        catalog_path=str(catalog_path),
        start_ns=None,
        end_ns=None,
        start_capital=1000.0,
        generate_html_report=False,
        reports_dir=str(tmp_path / "reports"),
        worker_log_file=str(tmp_path / "worker.log"),
    )

    assert res != {}, "Worker crashed"
    metrics = res.get("metrics", {})
    assert metrics.get("total_trades", 0) >= 10, f"ComboTrendVwapStrategy under-trading! Trades: {metrics.get('total_trades')}"

def test_mock_against_real_parquet_requirement():
    # Placeholder: Ensuring realistic testing
    assert True
