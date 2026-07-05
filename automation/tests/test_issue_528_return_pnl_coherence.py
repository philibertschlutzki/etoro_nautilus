import pytest
import pandas as pd
from automation.backtest_runner import _calculate_stats

def test_coherence_negative_pnl_flat_position():
    """Test A (Flache Endposition, Sigma PnL < 0): Assert total_return < 0"""
    pnl_list = [-100.0, -50.0]
    hold_list = [(1000, 1.0), (1000, 1.0)]
    starting_capital = 100_000.0

    # Synthetic MtM series showing a drop in equity
    mtm_series = pd.Series([100_000.0, 99_900.0, 99_850.0])

    stats = _calculate_stats(pnl_list, hold_list, starting_capital, mtm_series=mtm_series)
    assert stats["total_return"] < 0, "total_return must be negative when sum(PnL) is negative"

def test_coherence_positive_pnl_flat_position():
    """Test B (Flache Endposition, Sigma PnL > 0): Assert total_return > 0"""
    pnl_list = [100.0, 50.0]
    hold_list = [(1000, 1.0), (1000, 1.0)]
    starting_capital = 100_000.0

    # Synthetic MtM series showing a rise in equity
    mtm_series = pd.Series([100_000.0, 100_100.0, 100_150.0])

    stats = _calculate_stats(pnl_list, hold_list, starting_capital, mtm_series=mtm_series)
    assert stats["total_return"] > 0, "total_return must be positive when sum(PnL) is positive"

@pytest.mark.skip(reason="Open positions can logically diverge floating MtM and realized sum, explicitly excluded from coherence check.")
def test_coherence_open_end_position():
    """Test C (Offene Endposition): Test explizit ausklammern/skippen"""
    pass
