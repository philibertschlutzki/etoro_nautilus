import pandas as pd
import numpy as np
import math
import pytest
from unittest.mock import patch
from automation.backtest_runner import _calculate_stats, _get_annualization_factor

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
    mar = 0.0
    downside_diff = (period_rets - mar).clip(upper=0.0)
    dd_dev = float(np.sqrt((downside_diff ** 2).mean()))
    mean_ret = period_rets.mean()

    RATIO_CAP = 50.0
    expected_config_sortino = max(-RATIO_CAP, min(((mean_ret - mar) / dd_dev) * math.sqrt(111.0), RATIO_CAP))
    expected_fallback_sortino = max(-RATIO_CAP, min(((mean_ret - mar) / dd_dev) * math.sqrt(pd.Timedelta(days=365.25).total_seconds() / 86400), RATIO_CAP))

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
    derived_factor = pd.Timedelta(days=365.25).total_seconds() / median_dt_seconds

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


YEAR_SECONDS = 31_557_600.0


def test_issue_532_empirical_supersedes_static_default():
    # Issue #532 — Ohne expliziten Config-Override wird der Faktor EMPIRISCH aus dem realen
    # mtm_series-Index abgeleitet (31_557_600 / median_dt_seconds), nicht mehr aus einem
    # statischen Default (252).

    # 1h-Kerzen (kontinuierlich): median_dt = 3600s ⇒ Faktor = 8766 (≫ 252).
    s_1h = pd.Series(range(500), index=pd.date_range("2025-01-01", periods=500, freq="1h"))
    with patch("automation.backtest_runner._read_annualization_periods", return_value=None):
        factor_1h = _get_annualization_factor(s_1h)
    assert factor_1h == pytest.approx(YEAR_SECONDS / 3600.0)  # 8766.0
    assert factor_1h > 252.0  # Akzeptanzkriterium: 1h-Bars ⇒ Faktor ≫ 252

    # Equity-Marktzeiten (7 Bars/Tag, Overnight-/Wochenend-Lücken): der Median trifft robust die
    # dominante Intra-Session-Frequenz (1h) ⇒ derselbe Faktor 8766 trotz Gaps.
    rows, day, n = [], pd.Timestamp("2025-01-01"), 0
    while n < 30:
        if day.weekday() < 5:
            rows += [day + pd.Timedelta(hours=h) for h in range(10, 17)]
            n += 1
        day += pd.Timedelta(days=1)
    s_gap = pd.Series(range(len(rows)), index=pd.DatetimeIndex(rows))
    with patch("automation.backtest_runner._read_annualization_periods", return_value=None):
        factor_gap = _get_annualization_factor(s_gap)
    assert factor_gap == pytest.approx(YEAR_SECONDS / 3600.0)
    assert factor_gap > 252.0


def test_issue_532_explicit_config_override_wins():
    # Ein non-null Config-Wert bleibt ein EXPLIZITER Override und überstimmt die Empirik bewusst.
    s_1h = pd.Series(range(100), index=pd.date_range("2025-01-01", periods=100, freq="1h"))
    with patch("automation.backtest_runner._read_annualization_periods", return_value=252.0):
        assert _get_annualization_factor(s_1h) == pytest.approx(252.0)
    # Ohne verwertbaren Index: expliziter Override, sonst neutral (1.0).
    with patch("automation.backtest_runner._read_annualization_periods", return_value=252.0):
        assert _get_annualization_factor(None) == pytest.approx(252.0)
    with patch("automation.backtest_runner._read_annualization_periods", return_value=None):
        assert _get_annualization_factor(None) == pytest.approx(1.0)
