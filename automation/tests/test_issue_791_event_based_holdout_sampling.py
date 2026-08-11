import pytest
from automation.backtest_runner import generate_event_based_holdout_split


def test_issue_791_event_based_holdout_split_basic():
    trades = [{"id": i} for i in range(400)]
    is_trades, oos_trades = generate_event_based_holdout_split(trades, min_oos_trades=100, oos_fraction=0.30)
    
    # 400 * 0.30 = 120 >= 100 -> n_oos = 120, split_idx = 280
    assert len(is_trades) == 280
    assert len(oos_trades) == 120
    assert len(oos_trades) >= 100
    assert is_trades[-1]["id"] == 279
    assert oos_trades[0]["id"] == 280


def test_issue_791_event_based_holdout_split_min_floor():
    trades = [{"id": i} for i in range(334)]
    is_trades, oos_trades = generate_event_based_holdout_split(trades, min_oos_trades=100, oos_fraction=0.30)
    
    # 334 * 0.30 = 100.2 -> ceil is 101 -> n_oos = 101, split_idx = 233
    assert len(oos_trades) >= 100
    assert len(is_trades) + len(oos_trades) == 334


def test_issue_791_event_based_holdout_split_insufficient_trades():
    trades = [{"id": i} for i in range(50)]
    with pytest.raises(ValueError, match="Insuffiziente Trade-Anzahl"):
        generate_event_based_holdout_split(trades, min_oos_trades=100, oos_fraction=0.30)
