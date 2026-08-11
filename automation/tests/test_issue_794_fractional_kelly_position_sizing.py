from automation.fractional_trading import calculate_fractional_kelly_size


def test_issue_794_fractional_kelly_negative_expectancy():
    # Win rate 30%, avg win $100, avg loss $100 -> negative expectancy -> f* = 0.0
    f_size = calculate_fractional_kelly_size(
        win_rate=0.30,
        avg_win_chf=100.0,
        avg_loss_chf=100.0,
        total_capital_chf=10000.0
    )
    assert f_size == 0.0


def test_issue_794_fractional_kelly_positive_expectancy_and_cap():
    # High win rate 70%, avg win $200, avg loss $100 -> f* high -> capped at max_exposure_fraction (0.15)
    f_size = calculate_fractional_kelly_size(
        win_rate=0.70,
        avg_win_chf=200.0,
        avg_loss_chf=100.0,
        total_capital_chf=10000.0,
        max_exposure_fraction=0.15
    )
    assert f_size == 0.15


def test_issue_794_fractional_kelly_drawdown_dampener():
    # With drawdown, psi_dd dampens position sizing
    f_no_dd = calculate_fractional_kelly_size(
        win_rate=0.52,
        avg_win_chf=110.0,
        avg_loss_chf=100.0,
        total_capital_chf=10000.0,
        fractional_multiplier=0.01,
        max_exposure_fraction=0.50,
        current_drawdown_frac=0.0
    )
    
    f_with_dd = calculate_fractional_kelly_size(
        win_rate=0.52,
        avg_win_chf=110.0,
        avg_loss_chf=100.0,
        total_capital_chf=10000.0,
        fractional_multiplier=0.01,
        max_exposure_fraction=0.50,
        current_drawdown_frac=0.10,
        max_drawdown_limit=0.20
    )
    
    assert f_with_dd < f_no_dd
