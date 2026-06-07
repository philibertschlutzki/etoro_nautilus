import pytest
from automation.backtest_runner import select_winners

def test_oos_aggregation():
    tournament_cfg = {
        "min_trades": 20,
        "min_sortino": 0.0,
        "min_profit_factor": 1.0,
        "max_drawdown": 1.0,
        "min_win_rate": 0.0,
        "min_total_return": 0.0,
        "oos_min_trades": 5,
        "oos_min_total_return": 0.1,
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": [],
        "scoring": {
            "sortino_weight": 1.0,
            "profit_factor_weight": 0.0,
            "win_rate_weight": 0.0,
            "drawdown_penalty_weight": 0.0
        }
    }

    all_results = [
        {
            "symbol": "SYM1",
            "strategy": "StrategyA",
            "metrics": { "total_trades": 20, "sortino_ratio": 50.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5 },
            "oos_metrics": { "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1 }
        },
        {
            "symbol": "SYM1",
            "strategy": "StrategyB",
            "metrics": { "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5 },
            "oos_metrics": { "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1 }
        },
        {
            "symbol": "SYM2",
            "strategy": "StrategyA",
            "metrics": { "total_trades": 20, "sortino_ratio": 50.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5 },
            "oos_metrics": { "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.2 }
        },
        {
            "symbol": "SYM2",
            "strategy": "StrategyB",
            "metrics": { "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5 },
            "oos_metrics": { "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.2 }
        }
    ]

    per_symbol_winners, aggregate_winner, warnings = select_winners(all_results, tournament_cfg)
    assert aggregate_winner is not None, "Aggregate winner should be selected"
    assert aggregate_winner["strategy"] == "StrategyA", "StrategyA must be the winner"
    assert aggregate_winner["oos_eligible"] is True, "OOS eligible should be true since average is 10"
    assert aggregate_winner["oos_metrics"]["total_trades"] == 20, "Total trades should be the portfolio sum"
    assert aggregate_winner["oos_metrics"]["win_rate"] == 0.5, "Win rate should be reconstructed correctly"
    assert aggregate_winner["oos_metrics"]["total_return"] == 0.15, "Total return should be trade-weighted"
    assert aggregate_winner["oos_metrics"]["aggregation_basis"] == "portfolio_sum_for_trades_and_trade_weighted_mean_for_return_and_median_for_ratios"
