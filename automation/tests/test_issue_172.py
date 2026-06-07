import pytest
from automation.backtest_runner import select_winners

def test_select_winners_oos_structure():
    # Setup tournament config
    tournament_cfg = {
        "min_trades": 10,
        "min_sortino": 0.3,
        "min_profit_factor": 1.1,
        "max_drawdown": 0.30,
        "min_win_rate": 0.35,
        "min_total_return": 0.005,
        "min_expectancy": 0.0005,
        "oos_min_trades": 5,
        "oos_min_total_return": 0.005,
        "oos_min_expectancy": 0.0005,
        "eligible_requires_all": ["min_trades", "min_total_return", "min_win_rate", "max_drawdown", "min_expectancy"],
        "eligible_requires_any": ["min_sortino", "min_profit_factor"],
        "scoring": {
            "sortino_weight": 0.4,
            "profit_factor_weight": 0.3,
            "win_rate_weight": 0.2,
            "drawdown_penalty_weight": 0.1
        }
    }

    # Example results with valid IS metrics but failing OOS metrics
    all_results = [
        {
            "symbol": "BTCUSD",
            "strategy": "ComboTrendVwapStrategy",
            "metrics": {
                "total_trades": 20,
                "win_rate": 0.5,
                "profit_factor": 1.5,
                "sortino_ratio": 2.0,
                "max_drawdown": 0.1,
                "total_return": 0.05,
                "avg_holding_time_s": 3600,
                "median_holding_time_s": 3600
            },
            "oos_metrics": {
                "total_trades": 2, # Failing min trades
                "win_rate": 0.5,
                "profit_factor": 1.5,
                "sortino_ratio": 2.0,
                "max_drawdown": 0.1,
                "total_return": 0.001,
                "avg_holding_time_s": 3600,
                "median_holding_time_s": 3600,
                "oos_span_days": 60
            },
            "strat_params": {}
        }
    ]

    per_symbol_winners, aggregate_winner, warnings, _, _ = select_winners(all_results, tournament_cfg)

    # Check that aggregate_winner contains the required keys
    assert aggregate_winner is None or aggregate_winner.get('oos_eligible') is False









def test_oos_not_evaluated():
    tournament_cfg = {
        "min_trades": 10,
        "min_sortino": 0.3,
        "min_profit_factor": 1.1,
        "max_drawdown": 0.30,
        "min_win_rate": 0.35,
        "min_total_return": 0.005,
        "min_expectancy": 0.0005,
        "oos_min_trades": 5,
        "oos_min_total_return": 0.005,
        "oos_min_expectancy": 0.0005,
        "eligible_requires_all": ["min_trades", "min_total_return", "min_win_rate", "max_drawdown", "min_expectancy"],
        "eligible_requires_any": ["min_sortino", "min_profit_factor"],
        "scoring": {
            "sortino_weight": 0.4,
            "profit_factor_weight": 0.3,
            "win_rate_weight": 0.2,
            "drawdown_penalty_weight": 0.1
        }
    }

    # Missing OOS metrics entirely
    all_results = [
        {
            "symbol": "BTCUSD",
            "strategy": "ComboTrendVwapStrategy",
            "metrics": {
                "total_trades": 20,
                "win_rate": 0.5,
                "profit_factor": 1.5,
                "sortino_ratio": 2.0,
                "max_drawdown": 0.1,
                "total_return": 0.05,
                "avg_holding_time_s": 3600,
                "median_holding_time_s": 3600
            },
            "oos_metrics": None, # No trades
            "strat_params": {}
        }
    ]

    per_symbol_winners, aggregate_winner, warnings, _, _ = select_winners(all_results, tournament_cfg)
    assert aggregate_winner is None or aggregate_winner["oos_eligible"] is False
