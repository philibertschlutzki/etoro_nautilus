from automation.backtest_runner import _calculate_stats

def test_expectancy_arithmetic_mean():
    # Mock data
    pnl_list = [10.0, 5.0, -2.0, 15.0]
    starting_capital = 1000.0
    hold_list = []

    # Per-trade returns: [0.01, 0.005, -0.002, 0.015]
    # Sum = 0.028
    # Mean = 0.007

    metrics = _calculate_stats(pnl_list, hold_list, starting_capital)

    assert "expectancy" in metrics
    assert abs(metrics["expectancy"] - 0.007) < 1e-9, f"Expected 0.007, got {metrics['expectancy']}"
