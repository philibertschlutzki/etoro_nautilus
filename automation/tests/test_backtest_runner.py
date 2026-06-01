import pytest
from automation.backtest_runner import create_mock_instrument

def test_create_mock_instrument_size_precision_0():
    inst = create_mock_instrument("AAPL.NASDAQ", size_precision=0)
    assert inst.size_precision == 8
    assert inst.size_increment.as_double() < 1.0

def test_create_mock_instrument_size_precision_none():
    inst = create_mock_instrument("AAPL.NASDAQ", size_precision=None)
    assert inst.size_precision == 8

def test_create_mock_instrument_size_precision_8():
    inst = create_mock_instrument("AAPL.NASDAQ", size_precision=8)
    assert inst.size_precision == 8

def test_create_mock_instrument_size_precision_5():
    inst = create_mock_instrument("EURUSD.FOREX", size_precision=5)
    assert inst.size_precision == 5

from unittest.mock import MagicMock
from automation.backtest_runner import extract_metrics
import pandas as pd

def test_extract_metrics_holding_time():
    engine_mock = MagicMock()
    # Create mock df_fills with specific timestamps
    # For a hold time of 1 hour = 3600 seconds = 3600 * 1e9 ns
    # ts_event expects ns. Let's make an entry, then exit 3600 * 1e9 ns later

    # Let's say entry is at ts_event = 1000 * 1e9
    # Exit is at ts_event = 4600 * 1e9

    records = [
        # Buy 1.0 @ 100.0
        {
            "instrument_id": "AAPL.NASDAQ",
            "last_qty": 1.0,
            "last_px": 100.0,
            "order_side": "BUY",
            "ts_event": 1000 * 10**9,
        },
        # Sell 1.0 @ 110.0 (Profit = 10.0, holding time = 3600s)
        {
            "instrument_id": "AAPL.NASDAQ",
            "last_qty": 1.0,
            "last_px": 110.0,
            "order_side": "SELL",
            "ts_event": 4600 * 10**9,
        }
    ]
    df_fills = pd.DataFrame.from_records(records)
    engine_mock.trader.generate_fills_report.return_value = df_fills

    metrics_result = extract_metrics(engine_mock, starting_capital=10000.0)

    assert metrics_result is not None
    assert "metrics" in metrics_result
    m = metrics_result["metrics"]

    # 1 trade closed
    assert m["total_trades"] == 1
    # Average holding time should be 3600.0 s
    assert m["avg_holding_time_s"] == 3600.0
    assert m["median_holding_time_s"] == 3600.0

def test_extract_metrics_holding_time_short():
    engine_mock = MagicMock()
    # Let's say entry is SELL at ts_event = 2000 * 1e9
    # Exit is BUY at ts_event = 2100 * 1e9 (Hold = 100s)

    records = [
        {
            "instrument_id": "TSLA.NASDAQ",
            "last_qty": 0.5,
            "last_px": 200.0,
            "order_side": "SELL",
            "ts_event": 2000 * 10**9,
        },
        {
            "instrument_id": "TSLA.NASDAQ",
            "last_qty": 0.5,
            "last_px": 190.0,
            "order_side": "BUY",
            "ts_event": 2100 * 10**9,
        }
    ]
    df_fills = pd.DataFrame.from_records(records)
    engine_mock.trader.generate_fills_report.return_value = df_fills

    metrics_result = extract_metrics(engine_mock, starting_capital=10000.0)
    m = metrics_result["metrics"]

    assert m["total_trades"] == 1
    assert m["avg_holding_time_s"] == 100.0
    assert m["median_holding_time_s"] == 100.0


def test_extract_metrics_weighted_holding_time():
    engine_mock = MagicMock()
    # Buy 10 units at ts=0
    # Sell 2 units at ts=10 (hold = 10s)
    # Sell 8 units at ts=20 (hold = 20s)

    # Expected weighted avg: (2*10 + 8*20) / 10 = (20 + 160) / 10 = 180 / 10 = 18.0 s

    records = [
        {
            "instrument_id": "MSFT.NASDAQ",
            "last_qty": 10.0,
            "last_px": 100.0,
            "order_side": "BUY",
            "ts_event": 0,
        },
        {
            "instrument_id": "MSFT.NASDAQ",
            "last_qty": 2.0,
            "last_px": 110.0,
            "order_side": "SELL",
            "ts_event": 10 * 10**9,
        },
        {
            "instrument_id": "MSFT.NASDAQ",
            "last_qty": 8.0,
            "last_px": 120.0,
            "order_side": "SELL",
            "ts_event": 20 * 10**9,
        }
    ]
    df_fills = pd.DataFrame.from_records(records)
    engine_mock.trader.generate_fills_report.return_value = df_fills

    metrics_result = extract_metrics(engine_mock, starting_capital=10000.0)
    m = metrics_result["metrics"]

    assert m["total_trades"] == 2
    assert m["avg_holding_time_s"] == 18.0
