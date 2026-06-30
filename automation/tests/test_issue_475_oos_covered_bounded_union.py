import pytest
import json
from pathlib import Path
from automation.backtest_runner import extract_metrics, write_tournament_json
from automation.optimizer.parsing import parse_tournament
from unittest.mock import MagicMock
import pandas as pd
import datetime

# Mock engine to simulate fills
class MockEngine:
    def __init__(self, df):
        self.trader = MagicMock()
        self.trader.generate_fills_report.return_value = df
        self.trader.generate_order_fills_report.return_value = pd.DataFrame()

def generate_df_from_fills(fills):
    rows = []
    for qty, price, side, ts in fills:
        rows.append({
            "instrument_id": "TEST-USD",
            "last_qty": qty,
            "last_px": price,
            "order_side": side,
            "ts_event": ts
        })
    return pd.DataFrame(rows)

WF_DICT = {
    "is_window_days": 10,
    "oos_window_days": 5,
    "splits": 2,
    "embargo_period_days": 2,
    "holdout_days": 0
}

START_NS = 1_000_000_000_000_000_000 # initial TS
DAY_NS = 86400 * 1_000_000_000

import automation.backtest_runner
def dummy_fill_ts_ns(f):
    return f.ts_event
automation.backtest_runner._fill_ts_ns = dummy_fill_ts_ns

def test_oos_covered_after_last_oos():
    """Test A: All fills after last OOS end -> oos_covered=False"""
    fill_ts = START_NS + 25 * DAY_NS
    fills = [(1.0, 100.0, "BUY", fill_ts), (1.0, 110.0, "SELL", fill_ts + 1000)]
    engine = MockEngine(generate_df_from_fills(fills))
    result = extract_metrics(engine, 10000.0, walk_forward_dict=WF_DICT, start_ns=START_NS)
    assert result["_oos_covered"] is False

def test_oos_covered_in_embargo():
    """Test B: All fills in embargo (between IS end and OOS start) -> oos_covered=False"""
    fill_ts = START_NS + 11 * DAY_NS
    fills = [(1.0, 100.0, "BUY", fill_ts), (1.0, 110.0, "SELL", fill_ts + 1000)]
    engine = MockEngine(generate_df_from_fills(fills))
    result = extract_metrics(engine, 10000.0, walk_forward_dict=WF_DICT, start_ns=START_NS)
    assert result["_oos_covered"] is False

def test_oos_covered_inside_valid_fold():
    """Test C: At least one fill inside valid fold [oos_start_k, oos_end_k) -> oos_covered=True"""
    fill_ts = START_NS + 13 * DAY_NS
    fills = [(1.0, 100.0, "BUY", fill_ts), (1.0, 110.0, "SELL", fill_ts + 1000)]
    engine = MockEngine(generate_df_from_fills(fills))
    result = extract_metrics(engine, 10000.0, walk_forward_dict=WF_DICT, start_ns=START_NS)
    assert result["_oos_covered"] is True

def test_anchor_divergence_warning_not_triggered_on_valid(tmp_path, caplog):
    """Test D: Verification that Anchor Divergence Warning is not triggered on correct OOS trades"""
    import logging
    test_json = tmp_path / "tournament.json"
    data = {
        "aggregate_winner": {
            "oos_metrics": {
                "total_trades": 5,
                "sortino_ratio": 1.5
            }
        },
        "data_window": {
            "fill_ts_max": START_NS + 15 * DAY_NS,
            "oos_window_start_ns": START_NS + 12 * DAY_NS,
            "oos_covered": True
        }
    }
    with open(test_json, "w") as f:
        json.dump(data, f)
    with caplog.at_level(logging.WARNING):
        metrics = parse_tournament(test_json)
    assert metrics.oos_anchor_divergence is False
    assert "OOS Anchor Divergence detected" not in caplog.text
