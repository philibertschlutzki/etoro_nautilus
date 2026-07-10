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
        "oos_min_trades": 4,
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

    # Synthetic trades for Strategy A
    # We create overlapping losses to ensure max_drawdown > individual max_drawdowns
    # Symbol 1 (OOS): Trade 1: TS=100, PnL=-6000 (starting cap=100k, return = -0.06)
    #                 Trade 2: TS=120, PnL= 1000
    #                 Trade 3: TS=140, PnL=-5000 (return = -0.05)
    #                 Trade 4: TS=160, PnL=12000
    # Symbol 2 (OOS): Trade 1: TS=110, PnL=-5000 (return = -0.05)
    #                 Trade 2: TS=130, PnL=-4000 (return = -0.04)
    #                 Trade 3: TS=150, PnL=-2000 (return = -0.02)
    #                 Trade 4: TS=170, PnL=20000

    # With 100_000 initial equity:
    # Cum Equity changes:
    # TS=100 (S1): Eq=94,000  (peak=100,000, DD = 6%)
    # TS=110 (S2): Eq=89,300  (peak=100,000, DD = 10.7%)
    # TS=120 (S1): Eq=90,193  (peak=100,000)
    # TS=130 (S2): Eq=86,585  (peak=100,000, DD = 13.4%)
    # TS=140 (S1): Eq=82,256  (peak=100,000, DD = 17.7%)
    # TS=150 (S2): Eq=80,611  (peak=100,000, DD = 19.38%) -> MAX DD
    # TS=160 (S1): Eq=90,284
    # TS=170 (S2): Eq=108,341

    records_s1 = [
        (-6000.0, 100, 1000, 1.0, 10000.0),
        ( 1000.0, 120, 1000, 1.0, 10000.0),
        (-5000.0, 140, 1000, 1.0, 10000.0),
        (12000.0, 160, 1000, 1.0, 10000.0)
    ]
    records_s2 = [
        (-5000.0, 110, 1000, 1.0, 10000.0),
        (-4000.0, 130, 1000, 1.0, 10000.0),
        (-2000.0, 150, 1000, 1.0, 10000.0),
        (20000.0, 170, 1000, 1.0, 10000.0)
    ]

    all_results = [
        {
            "symbol": "SYM1",
            "strategy": "StrategyA",
            "start_capital": 100000.0,
            "metrics": { "total_trades": 20, "sortino_ratio": 50.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5, "median_position_notional": 50.0 },
            "oos_metrics": {
                "total_trades": 4, "sortino_ratio": 1.0, "profit_factor": 1.1, "max_drawdown": 0.1, "win_rate": 0.5, "total_return": 0.1, "median_position_notional": 50.0,
                "_oos_trade_records": records_s1
            }
        },
        {
            "symbol": "SYM1",
            "strategy": "StrategyB",
            "start_capital": 100000.0,
            "metrics": { "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5, "median_position_notional": 50.0 },
            "oos_metrics": { "total_trades": 4, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1, "median_position_notional": 50.0, "_oos_trade_records": [] }
        },
        {
            "symbol": "SYM2",
            "strategy": "StrategyA",
            "start_capital": 100000.0,
            "metrics": { "total_trades": 20, "sortino_ratio": 50.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5, "median_position_notional": 50.0 },
            "oos_metrics": {
                "total_trades": 4, "sortino_ratio": 1.0, "profit_factor": 1.8, "max_drawdown": 0.09, "win_rate": 0.25, "total_return": 0.2, "median_position_notional": 50.0,
                "_oos_trade_records": records_s2
            }
        },
        {
            "symbol": "SYM2",
            "strategy": "StrategyB",
            "start_capital": 100000.0,
            "metrics": { "total_trades": 20, "sortino_ratio": 1.0, "profit_factor": 1.5, "max_drawdown": 0.1, "win_rate": 0.6, "total_return": 0.5, "median_position_notional": 50.0 },
            "oos_metrics": { "total_trades": 4, "sortino_ratio": 1.0, "profit_factor": 1.0, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.2, "median_position_notional": 50.0, "_oos_trade_records": [] }
        }
    ]

    per_symbol_winners, aggregate_winner, warnings, _, _ = select_winners(all_results, tournament_cfg)
    assert aggregate_winner is not None, "Aggregate winner should be selected"
    assert aggregate_winner["strategy"] == "StrategyA", "StrategyA must be the winner"

    # In Strategy A, neither individual symbol has DD >= 0.11 (they are 0.1 and 0.09)
    # But chronologically combined, they suffer compounding losses dropping equity to ~80.6k.
    # Therefore portfolio DD is > 0.18.

    portfolio_dd = aggregate_winner["oos_metrics"]["max_drawdown"]
    # Da der sequentielle Fallback in Issue #474 entfernt wurde und select_winners noch keine mtm_series
    # aufbaut, fällt max_drawdown korrekterweise auf 0.0 zurück, um PnL-Aggregations-Artefakte zu verhindern.
    assert portfolio_dd == 0.0, f"Portfolio DD should fall back to 0.0 without mtm_series, got {portfolio_dd}"

    assert aggregate_winner["oos_metrics"]["total_trades"] == 8, "Total trades should be the portfolio sum"
    assert aggregate_winner["oos_metrics"]["win_rate"] == 0.375, "Win rate should be reconstructed correctly (3/8)"
    assert aggregate_winner["oos_metrics"]["aggregation_basis"] == "portfolio_equity_curve"

    # Ensure memory bloat is removed
    assert "_oos_trade_records" not in aggregate_winner["oos_metrics"]
    for val in per_symbol_winners.values():
        assert "_oos_trade_records" not in val["oos_metrics"]


def test_issue_574_pooled_frequency_metrics():
    """Asserts that Win Rate, Profit Factor, and Expectancy are pooled, NOT fold-medians."""
    from automation.backtest_runner import apply_fold_aggregation, _evaluate_oos_eligibility

    # Two folds with sparse winners
    # Fold 1: 0 winners, 5 losers -> wr=0.0, pf=0.0
    # Fold 2: 0 winners, 5 losers -> wr=0.0, pf=0.0
    # Fold 3: 20 winners, 0 losers -> wr=1.0, pf=inf
    # Median of wr: median([0.0, 0.0, 1.0]) = 0.0
    # Pooled wr: 20 winners / 30 trades = 0.66

    per_fold = [
        {"total_return": -0.05, "sortino_ratio": -1.0, "win_rate": 0.0, "expectancy": -0.01, "profit_factor": 0.0, "total_trades": 5},
        {"total_return": -0.05, "sortino_ratio": -1.0, "win_rate": 0.0, "expectancy": -0.01, "profit_factor": 0.0, "total_trades": 5},
        {"total_return": 0.20, "sortino_ratio": 5.0, "win_rate": 1.0, "expectancy": 0.01, "profit_factor": 99.0, "total_trades": 20}
    ]

    # Original pooled metrics calculated prior to apply_fold_aggregation
    oos_metrics = {
        "sortino_ratio": 1.0, # Will be overwritten by median(-1, -1, 5) = -1.0
        "win_rate": 0.66,
        "expectancy": 0.005,
        "profit_factor": 2.5,
        "total_return": 0.1,
        "total_trades": 30,
        "median_position_notional": 1000.0,
    }

    apply_fold_aggregation(oos_metrics, per_fold)

    assert oos_metrics["sortino_ratio"] == -1.0
    assert oos_metrics["win_rate"] == 0.66, "Win rate should be pooled, not median of 0.0"
    assert oos_metrics["profit_factor"] == 2.5, "Profit factor should be pooled"
    assert oos_metrics["expectancy"] == 0.005, "Expectancy should be pooled"

    # Test holdout parity logic (1 split)
    per_fold_single = [
        {"total_return": 0.1, "sortino_ratio": 1.5, "win_rate": 0.5, "expectancy": 0.02, "profit_factor": 1.5, "total_trades": 10}
    ]
    oos_metrics_single = {
        "sortino_ratio": 1.5,
        "win_rate": 0.5,
        "expectancy": 0.02,
        "profit_factor": 1.5,
        "total_return": 0.1,
        "total_trades": 10
    }

    apply_fold_aggregation(oos_metrics_single, per_fold_single)
    # Ensure they are perfectly bit-identical
    assert oos_metrics_single["sortino_ratio"] == 1.5
    assert oos_metrics_single["win_rate"] == 0.5
    assert oos_metrics_single["profit_factor"] == 1.5

    # Check Eligibility
    cfg = {
        "oos_min_trades": 20,
        "oos_min_win_rate": 0.4,
        "oos_min_profit_factor": 1.2,
        "oos_min_sortino": -2.0,
        "eligible_requires_all": ["min_trades", "min_win_rate", "min_profit_factor"]
    }

    ev = _evaluate_oos_eligibility(oos_metrics, cfg)
    assert ev["oos_eligible"] is True, f"Strategy should pass eligibility, got {ev['oos_rejection_reasons']}"
