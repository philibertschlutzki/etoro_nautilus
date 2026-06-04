import os
import math
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path
from automation._serde import encode_price_fsb16, encode_qty_fsb16
from automation.backtest_runner import run_single_backtest_worker
import json
import pytest
import concurrent.futures

def run_isolated_worker(*args, **kwargs):
    """
    Kapselt den Backtest-Lauf in einen separaten Prozess,
    um Rust-Core-Panics im Hauptprozess zu verhindern.
    """
    import multiprocessing
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as executor:
        future = executor.submit(run_single_backtest_worker, *args, **kwargs)
        return future.result()


def test_no_identical_strategies(tmp_path):
    catalog_path = tmp_path / "nautilus"
    tick_dir = catalog_path / "data" / "quote_tick" / "TEST.ETORO"
    tick_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = tick_dir / "data.parquet"

    price_prec = 2
    size_prec = 2
    bid_prices, ask_prices, bid_sizes, ask_sizes, ts_events, ts_inits = [], [], [], [], [], []
    import time
    base_ts = int(time.time() * 1e9) - 1000 * 3600 * 1_000_000_000

    # Generate 1000 hours of somewhat volatile data
    for i in range(1000):
        # Sine wave + trend + some noise
        noise = (i % 3) - 1
        price_val = 100.0 + 10.0 * math.sin(i * 0.1) + (i * 0.05) + noise
        qty_val = 1.0

        bid_prices.append(encode_price_fsb16(price_val, price_prec))
        ask_prices.append(encode_price_fsb16(price_val + 0.02, price_prec))
        bid_sizes.append(encode_qty_fsb16(qty_val, size_prec))
        ask_sizes.append(encode_qty_fsb16(qty_val, size_prec))

        ts = base_ts + i * 3600 * 1_000_000_000
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
        b"instrument_id":   b"TEST.ETORO",
    }
    table = pa.table({
        "bid_price": pa.array(bid_prices, type=_FSB16),
        "ask_price": pa.array(ask_prices, type=_FSB16),
        "bid_size":  pa.array(bid_sizes,  type=_FSB16),
        "ask_size":  pa.array(ask_sizes,  type=_FSB16),
        "ts_event":  pa.array(ts_events,  type=pa.uint64()),
        "ts_init":   pa.array(ts_inits,   type=pa.uint64()),
    }, schema=schema).replace_schema_metadata(meta)
    pq.write_table(table, str(parquet_file))

    # Read strategies.json
    with open("automation/config/strategies.json", "r") as f:
        strategies = json.load(f)["strategies"]

    active_strategies = [s for s in strategies if s.get("active", True)]

    results = {}

    for strat in active_strategies:
        res = run_isolated_worker(
            inst_id_str="TEST.ETORO",
            bar_type="TEST.ETORO-1-HOUR-MID-INTERNAL",
            strat=strat,
            catalog_path=str(catalog_path),
            start_ns=None,
            end_ns=None,
            start_capital=10000.0,
            generate_html_report=False,
            reports_dir=str(tmp_path / "reports"),
            worker_log_file=str(tmp_path / "worker.log")
        )
        if "metrics" in res:
            m = res["metrics"]
            trades = m.get("total_trades", 0)
            if trades > 0:
                pf = m.get("profit_factor")
                pf = pf if pf is not None else 0.0
                sig = (
                    trades,
                    round(m.get("win_rate", 0), 4),
                    round(pf, 4),
                    round(m.get("total_return", 0), 4)
                )
                if sig in results:
                    pytest.fail(f"Strategies '{strat['strategy_class']}' and '{results[sig]}' produced identical metrics {sig}. Duplicate logic!")
                results[sig] = strat["strategy_class"]
