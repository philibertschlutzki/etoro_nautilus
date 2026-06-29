import pytest
from automation.backtest_runner import compute_fold_boundaries, extract_metrics
import pandas as pd
from unittest.mock import MagicMock, patch

def test_single_pass_contiguous_oos_boundaries():
    """
    Testet die Geometrie der Folds gemaess Issue #490:
    - Ein fixes IS-Fenster.
    - OOS-Sub-Folds sind kontiguierlich und schliessen nach dem Embargo an.
    """
    start_ns = 1000 * 86400 * 1_000_000_000  # Start at some arbitrary day

    walk_forward_dict = {
        "is_window_days": 90,
        "oos_window_days": 30,
        "splits": 3,
        "embargo_period_days": 7
    }

    boundaries = compute_fold_boundaries(start_ns, walk_forward_dict)

    assert len(boundaries) == 3

    # Assert IS window is identical for all folds
    is_window_ns = 90 * 86400 * 1_000_000_000
    expected_is_start = start_ns
    expected_purge_end = expected_is_start + is_window_ns + 7 * 86400 * 1_000_000_000
    oos_window_ns = 30 * 86400 * 1_000_000_000

    for k, (is_start, oos_start, oos_end) in enumerate(boundaries):
        assert is_start == expected_is_start

        expected_oos_start = expected_purge_end + k * oos_window_ns
        expected_oos_end = expected_oos_start + oos_window_ns

        assert oos_start == expected_oos_start
        assert oos_end == expected_oos_end

        # Check contiguity
        if k > 0:
            prev_oos_end = boundaries[k-1][2]
            assert oos_start == prev_oos_end

def test_extract_metrics_no_is_overlap():
    """
    Testet, dass extract_metrics die Trades im IS-Fenster nicht mehrfach einbezieht (Kein Overlap-Averaging).
    """
    # Create engine mock
    engine = MagicMock()
    df = pd.DataFrame([
        {'instrument_id': 'BTC/USD', 'price': 50000.0, 'quantity': 1.0, 'ts_event': 1000 * 86400 * 1_000_000_000 + 1000, 'side': 'BUY', 'commission': 0},
        {'instrument_id': 'BTC/USD', 'price': 51000.0, 'quantity': 1.0, 'ts_event': 1000 * 86400 * 1_000_000_000 + 2000, 'side': 'SELL', 'commission': 0},
        {'instrument_id': 'BTC/USD', 'price': 52000.0, 'quantity': 1.0, 'ts_event': 1000 * 86400 * 1_000_000_000 + 3000, 'side': 'BUY', 'commission': 0},
        {'instrument_id': 'BTC/USD', 'price': 53000.0, 'quantity': 1.0, 'ts_event': 1000 * 86400 * 1_000_000_000 + 4000, 'side': 'SELL', 'commission': 0}
    ])
    engine.trader.generate_fills_report.return_value = df

    walk_forward_dict = {
        "is_window_days": 90,
        "oos_window_days": 30,
        "splits": 3,
        "embargo_period_days": 7
    }
    start_ns = 1000 * 86400 * 1_000_000_000

    with patch('automation.backtest_runner._calculate_stats') as mock_stats:
        mock_stats.return_value = {"total_trades": 2, "mock": "value"}
        res = extract_metrics(engine, 10000.0, None, walk_forward_dict, start_ns)

        # We need to make sure that _calculate_stats is called with 2 trades for IS, not 6
        # The mock might be called multiple times (for each fold and for the aggregate)
        # But the IS call should have length 2
        is_stats_calls = [c for c in mock_stats.call_args_list if c[0][0] == [1000.0, 1000.0]]

        # It's called once with 2 pnls
        assert len(is_stats_calls) == 1
        assert len(is_stats_calls[0][0][0]) == 2
