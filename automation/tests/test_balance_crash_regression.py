import math
import pyarrow as pa
import pyarrow.parquet as pq
from automation._serde import encode_price_fsb16, encode_qty_fsb16
from automation.backtest_runner import run_single_backtest_worker
import pytest
import concurrent.futures

def run_isolated_worker(*args, **kwargs):
    import multiprocessing
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as executor:
        future = executor.submit(run_single_backtest_worker, *args, **kwargs)
        return future.result()

def test_balance_crash_regression(tmp_path):
    """
    Testet explizit, ob _get_current_balance mit dem Typfehler
    bei 'account.balances' crasht. Erwartet, dass der Lauf durchgeht
    und Trades generiert werden.
    """
    catalog_path = tmp_path / "nautilus"
    tick_dir = catalog_path / "data" / "quote_tick" / "AAPL.ETORO"
    tick_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = tick_dir / "data.parquet"

    price_prec, size_prec = 2, 2
    bid_prices, ask_prices, bid_sizes, ask_sizes, ts_events, ts_inits = [], [], [], [], [], []
    import time
    base_ts = int(time.time() * 1e9) - 100 * 3600 * 1_000_000_000

    # Oszillierende Preisdaten, um Trades auszulösen
    for i in range(100):
        price_val = 100.0 + 5.0 * math.sin(i * 0.5)
        qty_val = 1.0

        bid_prices.append(encode_price_fsb16(price_val, price_prec))
        ask_prices.append(encode_price_fsb16(price_val + 0.05, price_prec))
        bid_sizes.append(encode_qty_fsb16(qty_val, size_prec))
        ask_sizes.append(encode_qty_fsb16(qty_val, size_prec))

        ts = base_ts + i * 3600 * 1_000_000_000
        ts_events.append(ts)
        ts_inits.append(ts)

    _FSB16 = pa.binary(16)
    schema = pa.schema([
        pa.field("bid_price", _FSB16), pa.field("ask_price", _FSB16),
        pa.field("bid_size",  _FSB16), pa.field("ask_size",  _FSB16),
        pa.field("ts_event",  pa.uint64()), pa.field("ts_init",   pa.uint64()),
    ])

    meta = {
        b"price_precision": str(price_prec).encode(),
        b"size_precision":  str(size_prec).encode(),
        b"instrument_id":   b"AAPL.ETORO",
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

    strat = {
        "strategy_class": "SmaCrossoverStrategy",
        "strategy_module": "automation.strategies.sma_crossover",
        "config_class": "SmaCrossoverConfig",
        "params": {
            "fast_sma": 2,
            "slow_sma": 10,
            "trade_amount_pct": 5.0 # Zwingt _compute_quantity in den % Pfad, der _get_current_balance ruft
        }
    }

    res = run_isolated_worker(
        inst_id_str="AAPL.ETORO",
        bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        strat=strat,
        catalog_path=str(catalog_path),
        start_ns=None,
        end_ns=None,
        start_capital=10000.0,
        generate_html_report=False,
        reports_dir=str(tmp_path / "reports"),
        worker_log_file=str(tmp_path / "worker.log"),
    )

    assert res != {}, "Worker ist abgestürzt (möglicherweise durch TypeError)"
    metrics = res.get("metrics", {})
    assert metrics.get("total_trades", 0) > 0, "Es sollten Trades erzeugt werden"
