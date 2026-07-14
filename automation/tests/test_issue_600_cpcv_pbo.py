"""Issue #600 — CPCV + PBO.

Combinatorial Purged Cross-Validation erzeugt aus denselben Daten C(N,k) Pfade (statt eines
Einzelpfads) und liefert die Probability of Backtest Overfitting: PBO > 0.5 ⇒ der In-Sample-Gewinner
ist out-of-sample schlechter als der Median.
"""
import math

import numpy as np

from automation.optimizer.cpcv import (
    cpcv_group_boundaries, cpcv_paths, purged_train_groups,
    probability_of_backtest_overfitting,
)


def test_cpcv_group_boundaries_partition_covers_all():
    bounds = cpcv_group_boundaries(100, 6)
    assert len(bounds) == 6
    assert bounds[0][0] == 0 and bounds[-1][1] == 100
    # lückenlos & disjunkt (halb-offen).
    for (s0, e0), (s1, e1) in zip(bounds, bounds[1:]):
        assert e0 == s1


def test_cpcv_paths_count_and_partition():
    paths = cpcv_paths(6, 2)
    assert len(paths) == math.comb(6, 2) == 15
    for train, test in paths:
        assert len(test) == 2
        assert set(train).isdisjoint(test)
        assert set(train) | set(test) == set(range(6))


def test_cpcv_paths_validates_k():
    import pytest
    with pytest.raises(ValueError):
        cpcv_paths(4, 0)
    with pytest.raises(ValueError):
        cpcv_paths(4, 4)


def test_purged_train_groups_removes_adjacent_and_embargo():
    train = (0, 1, 2, 4, 5)     # test = (3,)
    purged = purged_train_groups(train, (3,), embargo_groups=1)
    # Gruppe 2 (angrenzend davor, Purge) und Gruppe 4 (Embargo danach) fallen weg.
    assert 2 not in purged and 4 not in purged
    assert set(purged) == {0, 1, 5}


def test_pbo_high_when_is_winner_is_oos_worst():
    """In jedem Pfad ist der IS-Beste (Strategie 0) OOS der Schlechteste ⇒ PBO ≈ 1 (systematisches
    Overfitting)."""
    n_paths, n_strat = 10, 5
    is_perf = np.zeros((n_paths, n_strat))
    oos_perf = np.zeros((n_paths, n_strat))
    for p in range(n_paths):
        is_perf[p] = [5.0, 1.0, 2.0, 3.0, 4.0]     # Strategie 0 IS-bester
        oos_perf[p] = [-5.0, 4.0, 3.0, 2.0, 1.0]   # Strategie 0 OOS-schlechtester
    pbo = probability_of_backtest_overfitting(is_perf, oos_perf)
    assert pbo == 1.0


def test_pbo_low_when_is_winner_is_oos_best():
    """Der IS-Beste ist auch OOS der Beste ⇒ PBO ≈ 0 (kein Overfitting)."""
    n_paths, n_strat = 10, 5
    is_perf = np.tile([5.0, 1.0, 2.0, 3.0, 4.0], (n_paths, 1))
    oos_perf = np.tile([5.0, 1.0, 2.0, 3.0, 4.0], (n_paths, 1))
    pbo = probability_of_backtest_overfitting(is_perf, oos_perf)
    assert pbo == 0.0


def test_pbo_around_half_for_random_selection():
    """Zufällige (entkoppelte) IS/OOS-Performance ⇒ PBO nahe 0.5."""
    rng = np.random.default_rng(0)
    is_perf = rng.normal(0, 1, (200, 8))
    oos_perf = rng.normal(0, 1, (200, 8))   # unabhängig von IS
    pbo = probability_of_backtest_overfitting(is_perf, oos_perf)
    assert 0.35 < pbo < 0.65


def test_pbo_nan_for_degenerate_input():
    assert math.isnan(probability_of_backtest_overfitting(np.zeros((0, 3)), np.zeros((0, 3))))
    assert math.isnan(probability_of_backtest_overfitting(np.zeros((5, 1)), np.zeros((5, 1))))
