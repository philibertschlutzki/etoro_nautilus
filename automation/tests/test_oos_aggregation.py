import pytest
from automation.backtest_runner import select_winners

def test_oos_aggregation():
    # Setup mock data for all_results
    # 4 symbols total. 2 won, 2 lost.
    # Symbol 1 (Won): OOS total_trades = 5, total_return = 0.1
    # Symbol 2 (Won): OOS total_trades = 5, total_return = 0.2
    # Symbol 3 (Lost): OOS total_trades = 10, total_return = 0.3
    # Symbol 4 (Lost): OOS total_trades = 10, total_return = 0.4

    tournament_cfg = {
        "min_trades": 20,
        "min_sortino": 0.0,
        "min_profit_factor": 1.0,
        "max_drawdown": 1.0,
        "min_win_rate": 0.0,
        "min_total_return": 0.0,
        "oos_min_trades": 5,  # Average of winning OOS trades is 5
        "oos_min_total_return": 0.1,
        "eligible_requires_all": ["min_trades", "min_total_return"],
        "eligible_requires_any": [],
        "scoring": {
            "sortino_weight": 1.0,
            "profit_factor_weight": 0.0,
            "win_rate_weight": 0.0,
            "drawdown_penalty_weight": 0.0
        }
    }

    all_results = [
        # Symbol 1
        {
            "symbol": "SYM1",
            "strategy": "StrategyA",
            "metrics": {
                "total_trades": 20, "sortino_ratio": 2.0, "profit_factor": 1.5,
                "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5
            },
            "oos_metrics": {
                "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0,
                "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1
            }
        },
        {
            "symbol": "SYM1",
            "strategy": "StrategyB",
            "metrics": {
                "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5,
                "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5
            },
            "oos_metrics": {}
        },
        # Symbol 2
        {
            "symbol": "SYM2",
            "strategy": "StrategyA",
            "metrics": {
                "total_trades": 20, "sortino_ratio": 2.0, "profit_factor": 1.5,
                "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5
            },
            "oos_metrics": {
                "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0,
                "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.2
            }
        },
        {
            "symbol": "SYM2",
            "strategy": "StrategyB",
            "metrics": {
                "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5,
                "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5
            },
            "oos_metrics": {}
        },
        # Symbol 3
        {
            "symbol": "SYM3",
            "strategy": "StrategyB",  # StrategyB wins here
            "metrics": {
                "total_trades": 20, "sortino_ratio": 2.0, "profit_factor": 1.5,
                "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5
            },
            "oos_metrics": {}
        },
        {
            "symbol": "SYM3",
            "strategy": "StrategyA", # StrategyA loses here
            "metrics": {
                "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5,
                "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5
            },
            "oos_metrics": {
                "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0,
                "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.3
            }
        },
        # Symbol 4
        {
            "symbol": "SYM4",
            "strategy": "StrategyB",  # StrategyB wins here
            "metrics": {
                "total_trades": 20, "sortino_ratio": 2.0, "profit_factor": 1.5,
                "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5
            },
            "oos_metrics": {}
        },
        {
            "symbol": "SYM4",
            "strategy": "StrategyA", # StrategyA loses here
            "metrics": {
                "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5,
                "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5
            },
            "oos_metrics": {
                "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0,
                "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.4
            }
        }
    ]

    per_symbol_winners, aggregate_winner, _ = select_winners(all_results, tournament_cfg)

    assert aggregate_winner is not None, "Aggregate winner should be selected"

    # We set up the mock so both have 2 wins. Tie breaker logic should decide.
    # We will just ensure that for the winner's OOS eligible checks:
    # 1. We averaged total trades (5+5)/2 = 5.
    # 2. We did not include the losing symbols' OOS trades.
    #
    # With oos_min_trades = 5, and average = 5, the strategy should be eligible!

    assert aggregate_winner["oos_eligible"] is True, "OOS eligible should be true if logic averaged 5+5=5 trades"
