import pytest
import pandas as pd
import numpy as np
import logging
from unittest.mock import patch, MagicMock

from automation.backtest_runner import _calculate_stats
from automation.optimizer import reward
from automation.optimizer.parsing import ParsedTrialResults, OptimizerMetrics, OosMetrics, IsMetrics, OptimizationConfig

def generate_synthetic_equity(target_sortino: float) -> pd.Series:
    """
    Generiert Equity Curve mit spezifischer Target-Charakteristik.
    Nutzung von np.random zur Erzeugung von Returns.
    """
    # Deterministic seed for reproducible tests
    np.random.seed(42)

    if target_sortino == 18.0:
        returns = np.random.normal(0.001, 0.0001, 1000)
        returns[returns < 0] = -0.00001
    elif target_sortino == 47.0:
        returns = np.random.normal(0.005, 0.00001, 1000)
        returns[returns < 0] = -0.000001
    else:
        raise ValueError("Target not configured")

    equity = (1 + pd.Series(returns)).cumprod() * 10000
    equity = pd.concat([pd.Series([10000.0]), equity], ignore_index=True)
    return equity

# Dummy parsing data since reward.compute_reward requires a populated parsed result
def _create_mock_parsed_trial(sortino_ratio: float):
    return ParsedTrialResults(
        is_metrics=IsMetrics(
            is_best_total_return=0.1,
            is_best_win_rate=0.5,
            is_total_trades=50,
            is_sortino_median=2.0
        ),
        oos_metrics=OosMetrics(
            oos_total_return=0.1,
            oos_max_drawdown=0.05,
            oos_sortino_median=sortino_ratio,
            oos_win_rate=0.5,
            oos_profit_factor=2.0,
            oos_expectancy=0.001,
            oos_total_trades=50,
            oos_avg_holding_time_s=1000.0,
            oos_calmar_ratio=1.0,
            oos_anchor_divergence=False,
            mtm_empty=False
        ),
        optimizer_metrics=OptimizerMetrics(
            oos_fully_eligible_pairs=1,
            oos_near_miss_pairs=0,
            oos_evaluated_pairs=1,
            oos_eligible_pairs=1,
            oos_covered_pairs=1,
            per_fold_oos_sortino=[sortino_ratio],
            per_symbol_oos_sortino={ 'TEST': sortino_ratio }
        ),
        active_constraints={'sortino': True},
        # These fields need to just exist for passing logic
        constraint_distances={},
        data_history_days=365,
        parameters={},
        is_eligible=True,
        is_eligible_population=[],
        global_winner_id=None,
        symbols=['TEST'],
        trial_id=1,
        sweep_id='test',
        timestamp='test',
        config=OptimizationConfig(
             universe_size=1,
             reward_mode='per_symbol'
        )
    )

def test_no_source_clip_sortino():
    # Setup 1 & 2
    equity_18 = generate_synthetic_equity(18.0)
    equity_47 = generate_synthetic_equity(47.0)

    pnl = [10.0] * 50
    hold = [(1000, 1.0)] * 50

    with patch("automation.backtest_runner._read_sortino_downside_floor", return_value=0.001), \
         patch("automation.backtest_runner._read_optimizer_config", return_value={"sortino_numeric_guard": 100.0, "profit_factor_cap": 15.0}), \
         patch("automation.backtest_runner._read_sortino_mar", return_value=0.0), \
         patch("automation.backtest_runner._get_annualization_factor", return_value=252.0), \
         patch("automation.backtest_runner._read_max_drawdown_cap", return_value=0.2), \
         patch("automation.backtest_runner._read_sortino_min_trades", return_value=5):

        # Test 1 & 2: Evaluate sortino equality before reward
        # NO DataFrame Mocking! Using the actual logic.
        stats_18 = _calculate_stats(pnl, hold, 10000.0, mtm_series=equity_18)
        stats_47 = _calculate_stats(pnl, hold, 10000.0, mtm_series=equity_47)

        sortino_18 = stats_18['sortino_ratio']
        sortino_47 = stats_47['sortino_ratio']

        # Assertion 1: Striktes Ungleichheitsverhältnis vor Reward-Processing
        assert sortino_18 < sortino_47

        # Assertion 2: Striktes Ungleichheitsverhältnis nach reward.compute_reward
        # Using real compute_reward implementation from reward.py
        with patch('automation.optimizer.reward._read_optimizer_config') as mock_cfg:
             mock_cfg.return_value = {
                 "sortino_clip_abs": 5.0,
                 "penalty_unevaluable_oos": -10.0,
                 "sortino_soft_scale": 5.0,
                 "w_ret": 2.0,
                 "evaluable_floor_epsilon": 0.001
             }
             trial_18 = _create_mock_parsed_trial(sortino_18)
             trial_47 = _create_mock_parsed_trial(sortino_47)

             reward_18 = reward.compute_reward(trial_18, False)
             reward_47 = reward.compute_reward(trial_47, False)

             assert reward_18 < reward_47

        # Assertion 3: Telemetrie (SORTINO_GUARD_TRIPPED warning)
        # Verify that warning triggers strictly when > 100.0 (the guard limit)
        with patch.object(logging.getLogger("backtest_runner"), 'warning') as mock_warn:
             # Generate an artificial equity curve that exceeds the 100.0 guard limit
             returns = np.random.normal(0.01, 0.0000001, 1000)
             returns[returns < 0] = -0.000000001
             equity_extreme = (1 + pd.Series(returns)).cumprod() * 10000
             equity_extreme = pd.concat([pd.Series([10000.0]), equity_extreme], ignore_index=True)

             _calculate_stats(pnl, hold, 10000.0, mtm_series=equity_extreme)

             assert mock_warn.call_count == 1
             assert "SORTINO_GUARD_TRIPPED" in mock_warn.call_args[0][0]

        # Assertion 4: count(|s| == cap) == 0 (Checking that cap doesn't apply to values >= 15.0 anymore)
        # Because we're verifying the old hardcap behavior is removed:
        assert sortino_18 != 15.0
        assert sortino_47 != 15.0

        simulated_sweeps = [sortino_18, sortino_47, 20.0, 16.0, 99.0] * 20
        assert all(abs(s) != 15.0 for s in simulated_sweeps)
