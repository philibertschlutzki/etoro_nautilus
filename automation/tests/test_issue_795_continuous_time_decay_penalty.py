import math
from automation.optimizer.reward import calculate_continuous_time_decay_penalty


def test_issue_795_time_decay_before_soft():
    # Before t_soft (6h), penalty should be exactly 1.0
    p = calculate_continuous_time_decay_penalty(holding_time_hours=4.0, t_soft_hours=6.0)
    assert p == 1.0
    
    p6 = calculate_continuous_time_decay_penalty(holding_time_hours=6.0, t_soft_hours=6.0)
    assert p6 == 1.0


def test_issue_795_time_decay_at_max():
    # At t_max (24h), penalty should equal penalty_at_max (0.20)
    p24 = calculate_continuous_time_decay_penalty(
        holding_time_hours=24.0,
        t_soft_hours=6.0,
        t_max_hours=24.0,
        penalty_at_max=0.20
    )
    assert abs(p24 - 0.20) < 1e-4


def test_issue_795_time_decay_smooth_monotonic():
    # Decay should be strictly decreasing between 6h and 24h
    p10 = calculate_continuous_time_decay_penalty(holding_time_hours=10.0)
    p15 = calculate_continuous_time_decay_penalty(holding_time_hours=15.0)
    p20 = calculate_continuous_time_decay_penalty(holding_time_hours=20.0)
    
    assert 1.0 > p10 > p15 > p20 > 0.20
