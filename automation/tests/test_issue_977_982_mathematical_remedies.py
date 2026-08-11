import math
from automation.optimizer.deflation import (
    calculate_downside_shrunk_psr,
    calculate_risk_adjusted_expectancy,
    calculate_effective_horizon_n_periods,
)
from automation.optimizer.gate import evaluate_continuous_feasibility_distance


def test_issue_977_downside_shrunk_psr_small_sample():
    # N = 10 (< 30) should apply shrinkage towards normal skew/kurt (0.0, 3.0)
    psr_val = calculate_downside_shrunk_psr(
        sr_hat=1.5,
        sr_star=0.0,
        n_trades=10,
        raw_skew=1.2,
        raw_kurt=5.0
    )
    assert 0.0 < psr_val < 1.0
    assert isinstance(psr_val, float)


def test_issue_977_downside_shrunk_psr_insufficient_trades():
    # N < 5 -> returns 0.0 fallback safely
    psr_val = calculate_downside_shrunk_psr(
        sr_hat=1.5,
        sr_star=0.0,
        n_trades=3,
        raw_skew=0.0,
        raw_kurt=3.0
    )
    assert psr_val == 0.0


def test_issue_978_risk_adjusted_expectancy_zero_loss():
    # Zero downside std (0.0) should be clamped to volatility_floor (0.0002)
    rae = calculate_risk_adjusted_expectancy(
        mean_return=0.015,
        downside_std=0.0,
        volatility_floor=0.0002,
        n_trades=15,
        target_trades=30
    )
    assert rae > 0.0
    assert math.isfinite(rae)


def test_issue_981_effective_horizon_n_periods():
    # 450 days * 24 hours = 10800 hours. Median holding = 12 hours -> N_eff = 900
    n_eff = calculate_effective_horizon_n_periods(
        total_oos_hours=10800.0,
        median_holding_hours=12.0
    )
    assert n_eff == 900.0


def test_issue_980_continuous_feasibility_distance():
    # Negative gate deltas should produce a smooth negative penalty in [-50.0, 0.0)
    deltas = {
        "oos_min_trades": -15.0,
        "oos_min_sortino": -0.5,
        "oos_max_drawdown": 0.02  # positive delta (passed)
    }
    penalty = evaluate_continuous_feasibility_distance(deltas, max_penalty=50.0)
    assert -50.0 <= penalty < 0.0
