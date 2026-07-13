import pandas as pd
import pytest
import math
from unittest.mock import patch
from automation.backtest_runner import _get_annualization_factor

def test_annualization_rth_factor():
    """Test that a synthetic RTH series evaluates to ~1638 bars/year.
    RTH Series: 6.5 hours/day (e.g., 9:30 AM to 4:00 PM), 5 days/week.
    """
    # Create a synthetic series representing 1h candles during RTH
    # E.g. TSLA: 6.5 hours/day * 252 days/year = 1638
    
    rows = []
    day = pd.Timestamp("2025-01-01")
    n = 0
    # Simulate approximately 1 year of trading data
    while n < 252:
        if day.weekday() < 5: # Monday to Friday
            # 7 hourly bars to cover 6.5h session
            # e.g., 9:30 - 10:00 (1 bar), 10:00 - 11:00 (1 bar) ... up to 15:00 - 16:00
            # Let's just simulate 7 bars per day for simplicity to roughly hit 1764 or 1638.
            # Wait, 1638 bars / 252 days = 6.5. Let's do 7 bars some days, 6 bars some days, or just 6.5 average.
            # For simplicity, 1 bar per hour for 7 bars per day = 1764. The requirement says ~1638 ± 5%.
            # Let's do exactly 1638 bars over 252 days. 1638/252 = 6.5.
            # So 6 bars one day, 7 bars another day.
            bars_today = 6 if n % 2 == 0 else 7
            rows.extend([day + pd.Timedelta(hours=h) for h in range(9, 9 + bars_today)])
            n += 1
        day += pd.Timedelta(days=1)

    s_rth = pd.Series(range(len(rows)), index=pd.DatetimeIndex(rows))
    
    with patch("automation.backtest_runner._read_annualization_periods", return_value=None):
        factor_rth = _get_annualization_factor(s_rth)

    # The requirement is ~1638 ± 5%
    # Acceptance Criteria: [1500, 1800]
    assert 1500 <= factor_rth <= 1800
    assert math.isclose(factor_rth, 1638.0, rel_tol=0.05)
