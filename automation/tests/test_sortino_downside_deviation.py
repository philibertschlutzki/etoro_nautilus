import pytest
import math
import numpy as np
import pandas as pd
from unittest.mock import patch

from automation.backtest_runner import _calculate_stats, _read_sortino_numeric_guard

@pytest.fixture
def mock_sortino_config():
    with patch("automation.backtest_runner._read_sortino_mar", return_value=0.0), \
         patch("automation.backtest_runner._read_sortino_min_trades", return_value=2):
        yield

def test_sortino_target_downside_deviation_calculation(mock_sortino_config):
    # Test 1: Constant loss series ([-0.001]*k) + minimal win series yields a finite Sortino (< 10), not RATIO_CAP.
    # Wir uebergeben eine mtm_series, die genau diese returns erzeugt (mtm startet bei 1.0)
    rets1 = [-0.001] * 10 + [0.001]
    mtm_vals1 = [1.0]
    for r in rets1:
        mtm_vals1.append(mtm_vals1[-1] * (1 + r))
    mtm_series1 = pd.Series(mtm_vals1)

    # Um _calculate_stats richtig aufzurufen, benoetigen wir passende Argumente.
    # min_trades_for_sortino = 2
    # Wir muessen pnl_list fuellen, da sonst 'n = len(pnl_list) == 0' greift (early exit in _calculate_stats).
    # Ausserdem muss losses_count >= 2 sein, um nicht early exited zu werden fuer profit_factor.
    stats1 = _calculate_stats(
        pnl_list=[-1.0]*10 + [1.0],
        hold_list=[(1, 1.0)]*11,
        starting_capital=1.0,
        mtm_series=mtm_series1,
        min_trades_for_sortino=2
    )

    sortino1 = stats1.get("sortino_ratio")
    assert sortino1 is not None, "Sortino should not be None"
    assert sortino1 > -15.0, "Sortino should not be clipped to exactly -RATIO_CAP if it's finite"
    assert abs(sortino1) < 15.0, "Sortino should be finite and within cap bounds"

    # Test 2: Reference validation against numpy
    # numpy formula for RMS target downside deviation with MAR=0.0
    period_rets1 = mtm_series1.pct_change().dropna()
    downside_diff = period_rets1.clip(upper=0.0)
    dd_dev_ref = float(np.sqrt((downside_diff ** 2).mean()))
    mean_ret = period_rets1.mean()
    annualization_factor = 252 # Da freq nicht ableitbar ist, faellt es in der Regel auf 252 zurueck, falls nicht gesetzt. Wir ueberschreiben es intern.

    # We must patch _get_annualization_factor to ensure a deterministic factor for comparison
    with patch("automation.backtest_runner._get_annualization_factor", return_value=252):
        stats1_fixed_ann = _calculate_stats(
            pnl_list=[-1.0]*10 + [1.0],
            hold_list=[(1, 1.0)]*11,
            starting_capital=1.0,
            mtm_series=mtm_series1,
            min_trades_for_sortino=2
        )
        sortino1_fixed = stats1_fixed_ann.get("sortino_ratio")
        dd_dev_ref = max(dd_dev_ref, 1e-6)
        expected_sortino = max(-15.0, min((mean_ret / dd_dev_ref) * math.sqrt(252), 15.0))
        assert np.isclose(sortino1_fixed, expected_sortino, atol=1e-12)

    # Test 3: Loss-frequency sensitivity
    # Identical mean_ret but varying loss frequencies must yield distinct Sortinos
    # rets2 has fewer losses but same mean return. We need to construct rets2 carefully.
    rets2 = [-0.001] * 5 + [0.0] * 5 + [0.001] # fewer actual losses, same mean as rets1? No, mean is different.

    # Let's create two series with exactly the same mean_ret but different loss frequencies
    # Series A: 2 losses of -0.01, 2 gains of 0.01 -> mean = 0.0
    # Series B: 4 losses of -0.005, 4 gains of 0.005 -> mean = 0.0
    retsA = [-0.01, -0.01, 0.01, 0.01]
    retsB = [-0.005, -0.005, -0.005, -0.005, 0.005, 0.005, 0.005, 0.005]

    # Let's adjust them so mean is not 0 (e.g. positive)
    retsA = [-0.01, -0.01, 0.02, 0.02] # mean = 0.005, 2 losses. rms = sqrt((-0.01^2)*2 / 4) = sqrt(0.0002 / 4) = sqrt(0.00005)
    # To get different Sortino but same mean, we need different RMS.
    # We want mean = 0.005. So sum = 0.005 * n.
    # Let's try retsC: [-0.01, 0.0, 0.01, 0.02] -> mean = 0.005, 1 loss.
    retsB = [-0.01, 0.0, 0.01, 0.02]

    mtm_valsA = [1.0]
    for r in retsA: mtm_valsA.append(mtm_valsA[-1] * (1 + r))
    mtm_seriesA = pd.Series(mtm_valsA)

    mtm_valsB = [1.0]
    for r in retsB: mtm_valsB.append(mtm_valsB[-1] * (1 + r))
    mtm_seriesB = pd.Series(mtm_valsB)

    with patch("automation.backtest_runner._get_annualization_factor", return_value=252):
        statsA = _calculate_stats(pnl_list=[-1.0]*2+[1.0]*2, hold_list=[(1,1.0)]*4, starting_capital=1.0, mtm_series=mtm_seriesA, min_trades_for_sortino=2)
        statsB = _calculate_stats(pnl_list=[-1.0]*1+[1.0]*3, hold_list=[(1,1.0)]*4, starting_capital=1.0, mtm_series=mtm_seriesB, min_trades_for_sortino=2)

    sortinoA = statsA.get("sortino_ratio")
    sortinoB = statsB.get("sortino_ratio")

    assert sortinoA is not None and sortinoB is not None
    assert sortinoA != sortinoB, "Loss frequency should affect the Sortino metric"

    # Test 4 (Issue #588): KEIN Hard-Clip mehr. Der frühere ±RATIO_CAP-Clip (±15) vernichtete die
    # Rangordnung oberhalb der Grenze. Ein extremer, aber UNTERHALB des reinen Numerik-Guards
    # liegender Sortino bleibt jetzt UNGEKLEMMT (trägt Ordnung); ein Wert JENSEITS des Guards ist ein
    # Datenfehler ⇒ None (SORTINO_GUARD_TRIPPED), NICHT still auf ±15 geklemmt.
    guard = _read_sortino_numeric_guard()

    # Extremely negative sortino (unterhalb des Guards) — endlich, ungeklemmt, |s| kann > 15 sein.
    rets_extreme_neg = [-0.1] * 10
    mtm_vals_neg = [1.0]
    for r in rets_extreme_neg: mtm_vals_neg.append(mtm_vals_neg[-1] * (1 + r))
    mtm_series_neg = pd.Series(mtm_vals_neg)

    # Extremely positive sortino (near-zero downside × riesiger Faktor ⇒ jenseits des Guards).
    rets_extreme_pos = [0.1] * 10 + [-0.000000001]
    mtm_vals_pos = [1.0]
    for r in rets_extreme_pos: mtm_vals_pos.append(mtm_vals_pos[-1] * (1 + r))
    mtm_series_pos = pd.Series(mtm_vals_pos)

    # Issue #614 — der Guard ist von 1e6 auf 25.0 gesenkt. Ein annualisierter Sortino ZWISCHEN der alten
    # Clip-Grenze (15) und dem Guard (25) bleibt ungeklemmt; hier Faktor 400 ⇒ sortino_period≈−1 ⇒ ann≈−20.
    with patch("automation.backtest_runner._get_annualization_factor", return_value=400.0):
        stats_neg = _calculate_stats(pnl_list=[-1.0]*10, hold_list=[(1,1.0)]*10, starting_capital=1.0, mtm_series=mtm_series_neg, min_trades_for_sortino=2)
    s_neg = stats_neg.get("sortino_ratio")
    assert s_neg is not None and math.isfinite(s_neg)
    assert s_neg < -15.0, "kein Hard-Clip mehr: ein Sortino unterhalb der alten Grenze (15) bleibt ungeklemmt"
    assert abs(s_neg) <= guard  # #614 — unterhalb des Datenfehler-Guards (25)

    with patch("automation.backtest_runner._get_annualization_factor", return_value=10_000_000_000):
        # Riesiger Faktor × near-zero Downside ⇒ |sortino_raw| jenseits des Guards ⇒ Datenfehler.
        stats_pos = _calculate_stats(pnl_list=[-1.0]+[1.0]*10, hold_list=[(1,1.0)]*11, starting_capital=1.0, mtm_series=mtm_series_pos, min_trades_for_sortino=2)
    assert stats_pos.get("sortino_ratio") is None, "Wert jenseits des Numerik-Guards ⇒ None (kein still-Clip)"

def test_sortino_nan_poisoning(mock_sortino_config):
    # Test 5: Verify that if mean_ret somehow results in a NaN (which poisons sortino_raw),
    # the sortino ratio is gracefully set to None instead of -RATIO_CAP (-15.0).
    rets = [np.nan, np.nan, np.nan]
    mtm_vals = [1.0, 1.0, 1.0, 1.0] # constant mtm gives period_rets = 0.0 which means dd_dev = 0.0 -> None

    # We must explicitly force the NaN path by patching period_rets.mean() and avoiding the dd_dev=0 trap

    # Let's create a series that has dd_dev > 0 but the mean is somehow NaN?
    # Not possible normally in pandas, but we can simulate it with a mock.
    with patch("pandas.Series.mean", return_value=np.nan):
        rets_valid = [-0.1, -0.2, -0.3]
        mtm_vals_valid = [1.0]
        for r in rets_valid: mtm_vals_valid.append(mtm_vals_valid[-1] * (1 + r))
        mtm_series_valid = pd.Series(mtm_vals_valid)

        stats = _calculate_stats(
            pnl_list=[-1.0]*3,
            hold_list=[(1, 1.0)]*3,
            starting_capital=1.0,
            mtm_series=mtm_series_valid,
            min_trades_for_sortino=2
        )
        assert stats.get("sortino_ratio") is None, "NaN-poisoned raw Sortino must evaluate to None, not -RATIO_CAP"
