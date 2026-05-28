import pytest
from archive.dev_scripts.momentum_ls_tournament import compute_metrics

def test_metrics_no_downside():
    # All returns positive, downside_deviation should be 0, Sortino should be 0
    trades = [
        {"side": "BUY", "entry_price": 100.0, "exit_price": 110.0, "quantity": 1, "entry_ts": 1000000000000, "exit_ts": 2000000000000},
        {"side": "BUY", "entry_price": 100.0, "exit_price": 110.0, "quantity": 1, "entry_ts": 3000000000000, "exit_ts": 4000000000000},
    ]
    metrics = compute_metrics(trades)
    assert metrics["sortino_ratio"] == 0.0
    assert metrics["profit_factor"] == 999.0

def test_metrics_no_gross_loss():
    # Similar to above, but checks Profit Factor explicitly
    trades = [
        {"side": "SELL", "entry_price": 100.0, "exit_price": 90.0, "quantity": 1, "entry_ts": 1000000000000, "exit_ts": 2000000000000},
    ]
    metrics = compute_metrics(trades)
    assert metrics["profit_factor"] == 999.0

def test_metrics_mixed():
    trades = [
        {"side": "BUY", "entry_price": 100.0, "exit_price": 110.0, "quantity": 1, "entry_ts": 1000000000000, "exit_ts": 2000000000000}, # 10%
        {"side": "SELL", "entry_price": 100.0, "exit_price": 105.0, "quantity": 1, "entry_ts": 3000000000000, "exit_ts": 4000000000000}, # -5%
    ]
    metrics = compute_metrics(trades)
    assert metrics["profit_factor"] == 2.0  # 10% / 5% = 2.0
    assert metrics["win_rate"] == 0.5
    assert metrics["sortino_ratio"] > 0.0
