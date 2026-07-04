import pytest
from unittest.mock import MagicMock
import pandas as pd
from automation.backtest_runner import extract_metrics

def test_extract_metrics_scale_out_aggregation():
    """
    Tests that partial fills (scale-out) are aggregated into a single trade
    representing the round trip (position open to flat).
    """
    engine_mock = MagicMock()

    # Simulate a Scale-Out strategy
    # 1. Buy 100 at 10.0
    # 2. Sell 30 at 11.0 (profit = 30)
    # 3. Sell 30 at 12.0 (profit = 60)
    # 4. Sell 40 at 13.0 (profit = 120)
    # Total PnL = 30 + 60 + 120 = 210

    records = [
        {
            "instrument_id": "AAPL.NASDAQ",
            "last_qty": 100.0,
            "last_px": 10.0,
            "order_side": "BUY",
            "ts_event": 1000,
        },
        {
            "instrument_id": "AAPL.NASDAQ",
            "last_qty": 30.0,
            "last_px": 11.0,
            "order_side": "SELL",
            "ts_event": 2000,
        },
        {
            "instrument_id": "AAPL.NASDAQ",
            "last_qty": 30.0,
            "last_px": 12.0,
            "order_side": "SELL",
            "ts_event": 3000,
        },
        {
            "instrument_id": "AAPL.NASDAQ",
            "last_qty": 40.0,
            "last_px": 13.0,
            "order_side": "SELL",
            "ts_event": 4000,
        }
    ]

    df_fills = pd.DataFrame.from_records(records)
    engine_mock.trader.generate_fills_report.return_value = df_fills

    metrics_result = extract_metrics(engine_mock, starting_capital=10000.0)
    m = metrics_result["metrics"] if "metrics" in metrics_result else metrics_result

    assert m.get("total_trades", 0) > 0, "No trades extracted."

    # Assert total round trips (positions) is 1, not 3.
    assert m["total_trades"] == 1, f"Expected 1 position, got {m['total_trades']}"

    # Return of 210 on 10000 capital -> 0.021
    assert m["expectancy"] == pytest.approx(0.021)

    # Verify that the dual schema contains 'fill_matches'
    assert "fill_matches" in m, "fill_matches missing from output"
    assert m["fill_matches"]["total_trades"] == 3, f"Expected 3 fill matches, got {m['fill_matches']['total_trades']}"
