import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch
from automation.backtest_runner import _calculate_stats, _get_annualization_factor

def test_issue_464_sortino_dimension():
    # Test-Case: Zwei synthetische Return-Serien.
    # Identische Per-Trade-Return-Verteilung, jedoch Serie 2 mit exakt doppelter Trade-Frequenz.

    starting_capital = 1000.0

    # Serie 1: 1 Trade pro Woche, returns = 1%
    dates1 = pd.date_range("2026-01-01", periods=10, freq="7D")
    # MtM equity: just goes up 10 each time and sometimes down 5
    equity1 = [1000, 1002, 995, 1005, 1002, 1008, 1005, 1012, 1008, 1015]
    mtm_series1 = pd.Series(equity1, index=dates1)

    # Dummy pnls / holds just to bypass initial checks
    pnl_list1 = [10, -5, 10, -5, 10, -5, 10, -5, 10]
    hold_list1 = [(3600*1e9, 1.0)] * 9

    # Calculate for series 1
    # We'll mock the configuration to test it properly, but we can also just use the default.
    metrics1 = _calculate_stats(pnl_list1, hold_list1, starting_capital, mtm_series=mtm_series1, min_trades_for_sortino=2)
    sortino1 = metrics1["sortino_ratio"]

    # Serie 2: Doppelte Trade-Frequenz, halbe Periode (3.5D freq)
    dates2 = pd.date_range("2026-01-01", periods=19, freq="3.5D")
    # Duplicate the pattern to keep per-trade distribution the same
    equity2 = [1000, 1002, 995, 1005, 1002, 1008, 1005, 1012, 1008, 1015, 1012, 1018, 1015, 1022, 1018, 1025, 1022, 1028, 1025]
    mtm_series2 = pd.Series(equity2, index=dates2)

    pnl_list2 = pnl_list1 * 2
    hold_list2 = hold_list1 * 2
    metrics2 = _calculate_stats(pnl_list2, hold_list2, starting_capital, mtm_series=mtm_series2, min_trades_for_sortino=2)
    sortino2 = metrics2["sortino_ratio"]

    print(f"Sortino 1: {sortino1}, Sortino 2: {sortino2}")

    # Sortino should be different because standard deviation and mean returns change slightly,
    # but more importantly, it scales with frequency and is NOT hardcoded to 252 (if config is different).
    # Since we test the zero-hardcoding approach, let's inject a mock.


    # Assert sortinos are properly calculated
    assert sortino1 is not None
    assert sortino2 is not None
    # Skaliert nicht identisch wegen Frequenz
    assert sortino1 != sortino2

    # Issue #532 — der Annualisierungsfaktor wird EMPIRISCH aus der realen Bar-Frequenz abgeleitet
    # (kein statisches 252). Serie 2 hat exakt die halbe Periode (3.5D vs. 7D) ⇒ doppelte Frequenz
    # ⇒ exakt doppelter Faktor. Das ist die Dimensionskonsistenz-Invariante aus Issue #464/#510.
    with patch("automation.backtest_runner._read_annualization_periods", return_value=None):
        factor1 = _get_annualization_factor(mtm_series1)
        factor2 = _get_annualization_factor(mtm_series2)
    assert factor1 == pytest.approx(31_557_600.0 / (7 * 86400))
    assert factor2 == pytest.approx(2 * factor1, rel=1e-9)
