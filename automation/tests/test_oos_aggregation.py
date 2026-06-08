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

    # median_position_notional >= 10 required by Issue #254 gatekeeper in _is_eligible

    # Needs valid trade records for the calculation to produce total_trades > 0
    # For SYM1
    p1_records = [(-100.0, 1000, 10, 1.0, 1000.0)] * 5 + [(100.0, 2000, 10, 1.0, 1000.0)] * 5
    # For SYM2
    p2_records = [(-100.0, 3000, 10, 1.0, 1000.0)] * 5 + [(200.0, 4000, 10, 1.0, 1000.0)] * 5

    all_results = [
        {
            "symbol": "SYM1",
            "strategy": "StrategyA",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 20, "sortino_ratio": 50.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5, "median_position_notional": 50.0 },
            "oos_metrics": { "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1, "median_position_notional": 50.0, "_oos_trade_records": p1_records }
        },
        {
            "symbol": "SYM1",
            "strategy": "StrategyB",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5, "median_position_notional": 50.0 },
            "oos_metrics": { "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1, "median_position_notional": 50.0, "_oos_trade_records": p1_records }
        },
        {
            "symbol": "SYM2",
            "strategy": "StrategyA",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 20, "sortino_ratio": 50.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5, "median_position_notional": 50.0 },
            "oos_metrics": { "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.2, "median_position_notional": 50.0, "_oos_trade_records": p2_records }
        },
        {
            "symbol": "SYM2",
            "strategy": "StrategyB",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5, "median_position_notional": 50.0 },
            "oos_metrics": { "total_trades": 10, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.2, "median_position_notional": 50.0, "_oos_trade_records": p2_records }
        }
    ]

    per_symbol_winners, aggregate_winner, warnings, _, _ = select_winners(all_results, tournament_cfg)
    assert aggregate_winner is not None, "Aggregate winner should be selected"
    assert aggregate_winner["strategy"] == "StrategyA", "StrategyA must be the winner"
    assert aggregate_winner["oos_eligible"] is True, f"OOS eligible should be true since average is 10, got {aggregate_winner['oos_rejection_reasons']}"
    assert aggregate_winner["oos_metrics"]["total_trades"] == 20, "Total trades should be the portfolio sum"
    assert aggregate_winner["oos_metrics"]["win_rate"] == 0.5, "Win rate should be reconstructed correctly"
    # total return depends on compounding in _calculate_stats now
    assert aggregate_winner["oos_metrics"]["total_return"] > 0.0, "Total return should be positive"
    assert aggregate_winner["oos_metrics"]["aggregation_basis"] == "portfolio_equity_curve_from_chronologically_merged_trades"

    # Verifiziere die Konsistenz der Count-Ratio (Anzahl Wins passt mathematisch zur aggregierten Win-Rate)
    total_trades = aggregate_winner["oos_metrics"]["total_trades"]
    win_rate = aggregate_winner["oos_metrics"]["win_rate"]
    # Berechne die erwarteten absoluten Gewinne basierend auf den Rohdaten (5 Wins aus SYM1 + 5 Wins aus SYM2 = 10)
    expected_total_wins = 10
    assert round(win_rate * total_trades) == expected_total_wins


def test_oos_chronological_drawdown_aggregation():
    tournament_cfg = {
        "min_trades": 5,
        "min_sortino": 0.0,
        "min_profit_factor": 0.0,
        "max_drawdown": 1.0,
        "min_win_rate": 0.0,
        "min_total_return": -1.0,
        "oos_min_trades": 2,
        "oos_min_total_return": -1.0,
        "require_oos": True,
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": [],
        "scoring": {
            "sortino_weight": 1.0,
            "profit_factor_weight": 0.0,
            "win_rate_weight": 0.0,
            "drawdown_penalty_weight": 0.0
        }
    }

    # Simulate disjoint drawdown periods for 3 pairs
    # Pair 1: trades at time 1 and 2
    # Pair 2: trades at time 3 and 4
    # Pair 3: trades at time 5 and 6
    # Each starts with 1000 starting_capital and loses 100, which is exactly a 10% drawdown

    # Starting capital is 1000.
    # Pair 1 trade 1 (t=1): PnL -100
    # Pair 1 trade 2 (t=2): PnL +100

    p1_records = [(-100.0, 1000, 10, 1.0, 1000.0), (100.0, 2000, 10, 1.0, 1000.0)]
    p2_records = [(-100.0, 3000, 10, 1.0, 1000.0), (100.0, 4000, 10, 1.0, 1000.0)]
    p3_records = [(-100.0, 5000, 10, 1.0, 1000.0), (100.0, 6000, 10, 1.0, 1000.0)]

    all_results = [
        {
            "symbol": "SYM1",
            "strategy": "StrategyDrawdown",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 10, "sortino_ratio": 50.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0 },
            "oos_metrics": {
                "total_trades": 2, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0,
                "_oos_trade_records": p1_records
            }
        },
        {
            "symbol": "SYM2",
            "strategy": "StrategyDrawdown",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 10, "sortino_ratio": 50.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0 },
            "oos_metrics": {
                "total_trades": 2, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0,
                "_oos_trade_records": p2_records
            }
        },
        {
            "symbol": "SYM3",
            "strategy": "StrategyDrawdown",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 10, "sortino_ratio": 50.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0 },
            "oos_metrics": {
                "total_trades": 2, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0,
                "_oos_trade_records": p3_records
            }
        }
    ]

    per_symbol_winners, aggregate_winner, warnings, _, _ = select_winners(all_results, tournament_cfg)
    assert aggregate_winner is not None, "Aggregate winner should be selected"
    assert aggregate_winner["strategy"] == "StrategyDrawdown", "StrategyDrawdown must be the winner"

    agg_oos_metrics = aggregate_winner["oos_metrics"]
    assert agg_oos_metrics["total_trades"] == 6, "Total portfolio trades should be 6"
    assert agg_oos_metrics["aggregation_basis"] == "portfolio_equity_curve_from_chronologically_merged_trades"

    # Portfolio equity curve starting with 1000:
    # 1. -100 (equity 900)
    # 2. +100 (equity 1000)
    # 3. -100 (equity 900)
    # 4. +100 (equity 1000)
    # 5. -100 (equity 900)
    # 6. +100 (equity 1000)
    # The max drawdown here is exactly 10%.
    assert agg_oos_metrics["max_drawdown"] >= 0.1, f"Expected aggregate drawdown >= 0.1, got {agg_oos_metrics['max_drawdown']}"


def test_oos_chronological_drawdown_compounding():
    tournament_cfg = {
        "min_trades": 5,
        "min_sortino": 0.0,
        "min_profit_factor": 0.0,
        "max_drawdown": 1.0,
        "min_win_rate": 0.0,
        "min_total_return": -1.0,
        "oos_min_trades": 2,
        "oos_min_total_return": -1.0,
        "require_oos": True,
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": [],
        "scoring": {
            "sortino_weight": 1.0,
            "profit_factor_weight": 0.0,
            "win_rate_weight": 0.0,
            "drawdown_penalty_weight": 0.0
        }
    }

    # Simulate compounding disjoint drawdown periods for 3 pairs
    # Starting capital is 1000.
    # Pair 1: t=1: -100, t=2: -100
    # Pair 2: t=3: -100, t=4: -100
    # Pair 3: t=5: -100, t=6: +600

    # Individual maximum drawdowns:
    # Pair 1: -200 / 1000 = 20%
    # Pair 2: -200 / 1000 = 20%
    # Pair 3: -100 / 1000 = 10%
    # Median individual drawdown = 20%

    # Combined portfolio:
    # t=1: -100
    # t=2: -100
    # t=3: -100
    # t=4: -100
    # t=5: -100
    # t=6: +600
    # Total combined max drawdown = 500 / 1000 = 50%

    p1_records = [(-100.0, 1000, 10, 1.0, 1000.0), (-100.0, 2000, 10, 1.0, 1000.0)]
    p2_records = [(-100.0, 3000, 10, 1.0, 1000.0), (-100.0, 4000, 10, 1.0, 1000.0)]
    p3_records = [(-100.0, 5000, 10, 1.0, 1000.0), (600.0, 6000, 10, 1.0, 1000.0)]

    all_results = [
        {
            "symbol": "SYM1",
            "strategy": "StrategyCompound",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 10, "sortino_ratio": 50.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0 },
            "oos_metrics": {
                "total_trades": 2, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.2, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0,
                "_oos_trade_records": p1_records
            }
        },
        {
            "symbol": "SYM2",
            "strategy": "StrategyCompound",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 10, "sortino_ratio": 50.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0 },
            "oos_metrics": {
                "total_trades": 2, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.2, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0,
                "_oos_trade_records": p2_records
            }
        },
        {
            "symbol": "SYM3",
            "strategy": "StrategyCompound",
            "strat_params": {"starting_capital": 1000.0},
            "metrics": { "total_trades": 10, "sortino_ratio": 50.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0 },
            "oos_metrics": {
                "total_trades": 2, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.0, "median_position_notional": 50.0,
                "_oos_trade_records": p3_records
            }
        }
    ]

    per_symbol_winners, aggregate_winner, warnings, _, _ = select_winners(all_results, tournament_cfg)
    assert aggregate_winner is not None, "Aggregate winner should be selected"
    assert aggregate_winner["strategy"] == "StrategyCompound", "StrategyCompound must be the winner"

    agg_oos_metrics = aggregate_winner["oos_metrics"]

    # 5 trades of -10% sequentially, max drawdown is ~ 0.5 (compounding exactly 1 - (0.9^5) = 1 - 0.59049 = 0.40951 or similar, but the _calculate_stats compounds them.
    # We just want to check it's definitely >= the median of 0.2, showing it's a portfolio curve.
    assert agg_oos_metrics["max_drawdown"] > 0.2, f"Expected aggregate drawdown to be > 0.2 (compounded), got {agg_oos_metrics['max_drawdown']}"
