"""Issue #599 — Stationary-Bootstrap-Konfidenzintervalle statt Punktschätzern.

Das Gate soll die UNTERGRENZE des CI prüfen (nicht den Punktschätzer) — die statistisch saubere
Version dessen, was deflated_selection heuristisch approximiert.
"""
import numpy as np

from automation.optimizer.bootstrap import (
    lag1_autocorrelation, optimal_block_length, stationary_bootstrap_indices,
    bootstrap_ci, sortino_statistic, ci_lower_bound_passes,
)


def test_lag1_autocorrelation_known_values():
    assert abs(lag1_autocorrelation([1.0, 1.0, 1.0])) == 0.0     # Nullvarianz ⇒ 0
    # Stark positiv autokorrelierte (monoton steigende) Sequenz.
    assert lag1_autocorrelation(list(range(20))) > 0.7
    assert lag1_autocorrelation([1.0]) == 0.0


def test_optimal_block_length_grows_with_autocorrelation():
    rng = np.random.default_rng(0)
    iid = rng.normal(0, 1, 300)
    # AR(1) mit hoher Persistenz.
    ar = np.zeros(300)
    for i in range(1, 300):
        ar[i] = 0.9 * ar[i - 1] + rng.normal(0, 1)
    b_iid = optimal_block_length(iid)
    b_ar = optimal_block_length(ar)
    assert 1 <= b_iid <= 150 and 1 <= b_ar <= 150
    assert b_ar > b_iid, "autokorrelierte Serie ⇒ längere Blöcke"


def test_stationary_bootstrap_indices_valid_and_deterministic():
    rng1 = np.random.default_rng(7)
    rng2 = np.random.default_rng(7)
    idx1 = stationary_bootstrap_indices(50, 5.0, rng1)
    idx2 = stationary_bootstrap_indices(50, 5.0, rng2)
    assert idx1.shape == (50,)
    assert idx1.min() >= 0 and idx1.max() < 50
    assert np.array_equal(idx1, idx2)   # deterministisch bei gleichem Seed


def test_bootstrap_ci_brackets_point_estimate():
    rng = np.random.default_rng(1)
    x = rng.normal(0.01, 0.02, 200)     # positiver Edge
    point, lo, hi = bootstrap_ci(x, np.mean, n_boot=500, confidence=0.95, seed=3)
    assert lo <= point <= hi
    assert lo < hi


def test_gate_on_ci_lower_bound_separates_edge_from_noise():
    """Ein klarer Edge passiert das CI-Untergrenzen-Gate; reines Rauschen (Mittel 0) i. d. R. nicht."""
    rng = np.random.default_rng(11)
    strong = rng.normal(0.02, 0.01, 300)   # Mittel deutlich > 0 bei kleiner Streuung
    noise = rng.normal(0.0, 0.02, 300)     # Mittel 0
    _, lo_strong, _ = bootstrap_ci(strong, np.mean, n_boot=500, seed=5)
    _, lo_noise, _ = bootstrap_ci(noise, np.mean, n_boot=500, seed=5)
    assert ci_lower_bound_passes(lo_strong, 0.0) is True
    assert ci_lower_bound_passes(lo_noise, 0.0) is False
    # NaN-Untergrenze (undefinierte Statistik) ⇒ fail-safe False.
    assert ci_lower_bound_passes(float("nan"), 0.0) is False


def test_sortino_statistic_matches_manual():
    rets = np.array([0.01, -0.02, 0.03, -0.01, 0.02])
    downside = np.clip(rets, a_min=None, a_max=0.0)
    dd = float(np.sqrt((downside ** 2).mean()))
    expected = float(rets.mean() / dd)
    assert abs(sortino_statistic(rets) - expected) < 1e-12
