import os
import math
import pytest
import pyarrow as pa
import pyarrow.parquet as pq
import concurrent.futures
from pathlib import Path
import sys
import multiprocessing

from automation._serde import encode_price_fsb16, encode_qty_fsb16
from automation.backtest_runner import run_single_backtest_worker

def run_isolated_worker(*args, **kwargs):
    """
    Kapselt den Backtest-Lauf in einen separaten Prozess,
    um Rust-Core-Panics im Hauptprozess zu verhindern.
    """
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as executor:
        future = executor.submit(run_single_backtest_worker, *args, **kwargs)
        return future.result()

def test_keltner_atr_period_effect(tmp_path):
    catalog_path = tmp_path / "nautilus"
    tick_dir = catalog_path / "data" / "quote_tick" / "AAPL.ETORO"
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

    import time
    base_ts = int(time.time() * 1e9) - 100 * 3600 * 1_000_000_000

    # Generate ~100 oscillating points so an SMA/Keltner crosses over multiple times
    for i in range(100):
        # Oscillate around 100.0 with a sin wave amplitude 5
        price_val = 100.0 + 5.0 * math.sin(i * 0.5)
        # Adding a bit of trend to cause trades
        price_val += i * 0.1
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
        b"instrument_id":   b"AAPL.ETORO",
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

    strat_20 = {
        "strategy_class": "MeanReversionStrategy",
        "strategy_module": "automation.strategies.mean_reversion",
        "config_class": "MeanReversionConfig",
        "params": {
            "keltner_period": 20,
            "keltner_atr_period": 20,
            "keltner_multiplier": 2.0
        }
    }

    res_20 = run_isolated_worker(
        inst_id_str="AAPL.ETORO",
        bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        strat=strat_20,
        catalog_path=str(catalog_path),
        start_ns=None,
        end_ns=None,
        start_capital=1000.0,
        generate_html_report=False,
        reports_dir=str(tmp_path / "reports"),
        worker_log_file=str(tmp_path / "worker.log"),
    )

    strat_5 = {
        "strategy_class": "MeanReversionStrategy",
        "strategy_module": "automation.strategies.mean_reversion",
        "config_class": "MeanReversionConfig",
        "params": {
            "keltner_period": 20,
            "keltner_atr_period": 5,
            "keltner_multiplier": 2.0
        }
    }

    res_5 = run_isolated_worker(
        inst_id_str="AAPL.ETORO",
        bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        strat=strat_5,
        catalog_path=str(catalog_path),
        start_ns=None,
        end_ns=None,
        start_capital=1000.0,
        generate_html_report=False,
        reports_dir=str(tmp_path / "reports_5"),
        worker_log_file=str(tmp_path / "worker_5.log"),
    )

    assert res_20 != {}, "Worker crashed and returned empty dict for atr_period=20"
    assert res_5 != {}, "Worker crashed and returned empty dict for atr_period=5"

    metrics_20 = res_20.get("metrics", {})
    metrics_5 = res_5.get("metrics", {})

    trades_20 = metrics_20.get("total_trades", 0)
    trades_5 = metrics_5.get("total_trades", 0)

    print(f"Total trades with atr_period=20: {trades_20}")
    print(f"Total trades with atr_period=5: {trades_5}")

    # Der Test schlägt fehl (oder sollte es zumindest vor dem Fix tun, wenn der Parameter gar keine Wirkung hätte)
    # Mit dem Fix muss es eine Wirkung geben, auch wenn es theoretisch durch Zufall gleich sein könnte, ist es sehr unwahrscheinlich.
    assert trades_20 != trades_5, "Änderung von keltner_atr_period hatte keinen Einfluss auf die Metriken"