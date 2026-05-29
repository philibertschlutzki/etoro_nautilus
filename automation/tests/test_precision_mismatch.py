from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.instruments import Cfd
from nautilus_trader.model.enums import AssetClass, OmsType, AccountType
from nautilus_trader.model.currencies import USD
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.identifiers import Venue
import time
import os
from nautilus_trader.model.objects import Money
from pathlib import Path
from automation.backtest_runner import run_single_backtest_worker, _normalize_size_precision, create_mock_instrument

def test_single_worker_precision_mismatch():
    instrument_id = InstrumentId.from_str("AAPL.ETORO")
    tick = QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price(100.0, 2),
        ask_price=Price(100.1, 2),
        bid_size=Quantity(1.0, 0),
        ask_size=Quantity(1.0, 0),
        ts_event=time.time_ns(),
        ts_init=time.time_ns(),
    )

    original_catalog = "/tmp/test_catalog_worker_mismatch"
    os.makedirs(original_catalog, exist_ok=True)
    catalog = ParquetDataCatalog(original_catalog)
    catalog.write_data([tick])

    strat = {
        "strategy_class": "DynamicBreakoutStrategy",
        "strategy_module": "automation.strategies.dynamic_breakout",
        "config_class": "DynamicBreakoutConfig",
        "params": {}
    }

    import sys
    sys.path.append(str(Path(".").absolute()))

    res = run_single_backtest_worker(
        inst_id_str="AAPL.ETORO",
        bar_type="AAPL.ETORO-1-MINUTE-MID-INTERNAL",
        strat=strat,
        catalog_path=original_catalog,
        start_ns=None,
        end_ns=None,
        start_capital=1000.0,
        generate_html_report=False,
        reports_dir="/tmp/reports",
        worker_log_file="/tmp/worker.log"
    )

    # Assert that metrics are returned and it did not crash returning {}
    assert res != {}, "Worker crashed and returned {}"
    assert "symbol" in res
    assert res["symbol"] == "AAPL.ETORO"
