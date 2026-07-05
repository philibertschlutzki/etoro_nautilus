import pandas as pd
import numpy as np
import math
from unittest.mock import patch
from automation.backtest_runner import _calculate_stats

def test_annualization_path_parity():
    # Test-Case: Isolated execution of the MtM-Path with config injected and fallback path
    # Issue #510 requirement: path parity test asserting absolute identity between paths.
    # Note: "Fallback-Pfad" refers to using Trading-Time frequency derivation when config key is missing.

    starting_capital = 1000.0

    # 1. Synthetische Zeitreihe mit Gaps (Wochenende/Overnight)
    # 5 Tage Woche, Lücken am Wochenende
    dates = pd.bdate_range("2026-01-01", periods=10) # 10 business days

    # Random equity curve for diversity, ensuring small returns so we don't hit the RATIO_CAP (50.0)
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.2, 10) # much higher variance to reduce sortino raw
    equity = starting_capital * np.cumprod(1 + returns)
    mtm_series = pd.Series(equity, index=dates)

    pnl_list = [10.0, -5.0, 15.0]
    hold_list = [(3600*1e9, 1.0)] * 3

    with patch("automation.backtest_runner._read_annualization_periods", return_value=111.0):
        metrics_with_config = _calculate_stats(
            pnl_list, hold_list, starting_capital, mtm_series=mtm_series, min_trades_for_sortino=2
        )

    sortino_config = metrics_with_config["sortino_ratio"]

    with patch("automation.backtest_runner._read_annualization_periods", return_value=None):
        metrics_without_config = _calculate_stats(
            pnl_list, hold_list, starting_capital, mtm_series=mtm_series, min_trades_for_sortino=2
        )

    sortino_fallback = metrics_without_config["sortino_ratio"]

    assert sortino_config is not None
    assert sortino_fallback is not None

    period_rets = mtm_series.pct_change().dropna()
    downside_rets = period_rets[period_rets < 0]
    dd_dev = downside_rets.std()
    mean_ret = period_rets.mean()

    RATIO_CAP = 50.0
    expected_config_sortino = min((mean_ret / dd_dev) * math.sqrt(111.0), RATIO_CAP)
    expected_fallback_sortino = min((mean_ret / dd_dev) * math.sqrt(31557600.0 / 86400), RATIO_CAP)

    assert math.isclose(sortino_config, expected_config_sortino, rel_tol=1e-9)
    assert math.isclose(sortino_fallback, expected_fallback_sortino, rel_tol=1e-9)

def test_annualization_path_parity_strict():
    # If we mock the config to be the exact same as the median derived factor, they must match exactly.
    starting_capital = 1000.0
    dates = pd.bdate_range("2026-01-01", periods=10) # 10 business days
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.2, 10)
    equity = starting_capital * np.cumprod(1 + returns)
    mtm_series = pd.Series(equity, index=dates)

    pnl_list = [10.0, -5.0, 15.0]
    hold_list = [(3600*1e9, 1.0)] * 3

    median_dt_seconds = mtm_series.index.to_series().diff().median().total_seconds()
    derived_factor = 31557600.0 / median_dt_seconds

    with patch("automation.backtest_runner._read_annualization_periods", return_value=derived_factor):
        metrics_with_config = _calculate_stats(
            pnl_list, hold_list, starting_capital, mtm_series=mtm_series, min_trades_for_sortino=2
        )

    with patch("automation.backtest_runner._read_annualization_periods", return_value=None):
        metrics_without_config = _calculate_stats(
            pnl_list, hold_list, starting_capital, mtm_series=mtm_series, min_trades_for_sortino=2
        )

    sortino_config = metrics_with_config["sortino_ratio"]
    sortino_fallback = metrics_without_config["sortino_ratio"]

    assert math.isclose(sortino_config, sortino_fallback, rel_tol=1e-9)
