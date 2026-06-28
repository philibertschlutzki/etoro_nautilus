import pytest
from automation.optimizer.reward import _shortfall_distance, _constraint_distance_penalty
from collections import namedtuple

TournamentMetrics = namedtuple("TournamentMetrics", [
    "oos_total_trades",
    "oos_total_return",
    "oos_win_rate",
    "oos_max_drawdown",
    "oos_sortino",
    "oos_profit_factor"
])

def test_shortfall_distance_scaling():
    # Ohne Scale
    d1 = _shortfall_distance(0.001, 0.005) # gap 0.004 / 0.005 = 0.8 -> 0.64
    assert abs(d1 - 0.64) < 1e-6

    # Mit Scale
    d2 = _shortfall_distance(0.001, 0.005, scale=0.1) # gap 0.004 / 0.1 = 0.04 -> 0.0016
    assert abs(d2 - 0.0016) < 1e-6

    # Actual > Target
    d3 = _shortfall_distance(0.01, 0.005, scale=0.1)
    assert d3 == 0.0

def test_constraint_distance_penalty_isolation():
    weights = {
        "constraint_distance_penalty_weight": 1.0,
        "unevaluable_shaping_span": 0.25
    }
    tournament_cfg = {
        "oos_min_trades": 10,
        "min_trades": 50,
        "oos_min_total_return": 0.05,
        "min_total_return": 0.10,
        "oos_min_expectancy": 0.001,
        "min_expectancy": 0.005,
        "oos_min_win_rate": 0.4,
        "min_win_rate": 0.5,
        "return_penalty_scale": 0.1
    }

    m = TournamentMetrics(
        oos_total_trades=10, # erfüllt (10 >= 10)
        oos_total_return=0.04, # verfehlt (0.04 < 0.05). Gap = 0.01. Scale = 0.1. penalty = (0.01/0.1)^2 = 0.01
        oos_win_rate=0.4, # erfüllt (0.4 >= 0.4)
        oos_max_drawdown=0.1, # cap ist 0.2 (erfüllt)
        oos_sortino=None,
        oos_profit_factor=None
    )

    penalty = _constraint_distance_penalty(m, weights, risk_dd_cap=0.2, tournament_cfg=tournament_cfg)

    assert abs(penalty - 0.01) < 1e-6
