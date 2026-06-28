import pandas as pd
import numpy as np
from automation.backtest_runner import _calculate_stats

def test_issue_465_mtm_drawdown():
    # Test-Case A: Synthetischer Trade mit massivem Unrealized Loss (Intra-Trade),
    # der vor dem Exit teilweise recovert.
    pnl_list = [10.0]
    hold_list = [(3600*1e9, 1.0)]
    starting_capital = 100.0

    # Realized returns = [0.1]
    # Fallback Max DD would be 0.0 because it only checks realized curve (1.0 -> 1.1, no drop)

    # MtM Series:
    # Day 1: 100
    # Day 2: 50 (Massive drop! -50%)
    # Day 3: 110 (Recovers to close with +10)
    timestamps = pd.date_range("2026-01-01", periods=3)
    mtm_series = pd.Series([100.0, 50.0, 110.0], index=timestamps)

    metrics = _calculate_stats(
        pnl_list,
        hold_list,
        starting_capital,
        mtm_series=mtm_series
    )

    assert metrics["max_drawdown"] == 0.5, f"Expected 0.5, got {metrics['max_drawdown']}"
    import math
    assert math.isclose(metrics["total_return"], 0.1, abs_tol=1e-9)

    # Test-Case B: Mehrere zeitgleich offene, überlappende Trades. Drawdown invariant gegenüber Exit-Sortierung.
    # We test that the mtm_series correctly represents the drawdown regardless of exit order
    # (Because the mtm_series represents the sum of all overlapping positions)

    # MtM Series: 100 -> 90 -> 80 -> 120
    timestamps2 = pd.date_range("2026-01-01", periods=4)
    mtm_series2 = pd.Series([100.0, 90.0, 80.0, 120.0], index=timestamps2)
    metrics2 = _calculate_stats(
        [5.0, 15.0],
        [(3600*1e9, 1.0), (3600*1e9, 1.0)],
        starting_capital,
        mtm_series=mtm_series2
    )

    # Max DD from 100 to 80 is 20%
    assert metrics2["max_drawdown"] == 0.2

    # Compare with another exit sorting which doesn't affect MtM at all!
    metrics3 = _calculate_stats(
        [15.0, 5.0],
        [(3600*1e9, 1.0), (3600*1e9, 1.0)],
        starting_capital,
        mtm_series=mtm_series2
    )
    assert metrics3["max_drawdown"] == 0.2
