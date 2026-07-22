"""Issue #667 — Vier Kern-Gates ~68-70% kollinear + Confirm-Stack in Konjunktion: kompoundierter
Type-II-Fehler für Universe-1.

Fix: (1) ein Null-Kalibrierlauf (Monte-Carlo unter H0) misst die empirische False-Positive-Winner-
Rate von 'conjunction' vs 'dsr_or_robust_pair' — die produktive Modus-Wahl ist damit belegt, nicht
geraten; (2) eine Rang-Korrelationsmatrix der vier eligible-Gates (expectancy/profitable_folds/
any_condition/psr) wird telemetriert, mit einem Regressions-Wächter, der bei |ρ|>0.95 fail-loud-
artig warnt.
"""
import logging

import pytest

from automation.optimizer.reward import (
    _spearman_rank_correlation,
    _any_condition_delta_from_gate_deltas,
    gate_rank_correlation_matrix,
    assert_gate_collinearity_guard,
)
from automation.optimizer.calibration import calibrate_promotion_correction_mode

# Issue #760 — gate_rank_correlation_matrix/assert_gate_collinearity_guard leiten die geprüften
# Keys jetzt zur Laufzeit aus tournament_cfg ab (keine eingefrorene Code-Konstante mehr); dieses
# Fixture reproduziert exakt den #667-4-Key-Satz (oos_min_expectancy, oos_min_profitable_folds_frac,
# any_condition, oos_min_psr).
_TCFG = {
    "eligible_requires_all": ["oos_min_expectancy", "oos_min_profitable_folds_frac", "oos_min_psr"],
    "eligible_requires_any": ["min_profit_factor", "min_win_rate"],
}


# ── Spearman-Rangkorrelation (rein) ──────────────────────────────────────────────────────────────
def test_spearman_perfect_positive_correlation():
    assert _spearman_rank_correlation([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)


def test_spearman_perfect_negative_correlation():
    assert _spearman_rank_correlation([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)


def test_spearman_none_for_too_few_points():
    assert _spearman_rank_correlation([1, 2], [3, 4]) is None


def test_spearman_none_for_zero_variance():
    assert _spearman_rank_correlation([1, 1, 1, 1], [1, 2, 3, 4]) is None


def test_spearman_handles_ties_via_average_rank():
    rho = _spearman_rank_correlation([1, 1, 2, 3], [1, 1, 2, 3])
    assert rho == pytest.approx(1.0)


# ── any_condition-Proxy (bester Arm) ─────────────────────────────────────────────────────────────
def test_any_condition_delta_picks_best_arm():
    deltas = {"oos_min_profit_factor": -0.5, "oos_min_win_rate": 0.2}
    assert _any_condition_delta_from_gate_deltas(deltas) == 0.2


def test_any_condition_delta_none_when_no_arms_present():
    assert _any_condition_delta_from_gate_deltas({"oos_min_trades": 5.0}) is None
    assert _any_condition_delta_from_gate_deltas(None) is None


# ── Gate-Rang-Korrelationsmatrix ─────────────────────────────────────────────────────────────────
def _collinear_cohort(n=30):
    """Synthetisches Fixture: alle vier Gate-Deltas leiten sich aus DERSELBEN latenten
    "net-of-cost-profitabel"-Achse ab (#667-Symptom) — perfekt kollinear (monotone Transformation)."""
    cohort = []
    for i in range(n):
        latent = (i - n / 2) / 10.0
        cohort.append({
            "oos_min_expectancy": latent,
            "oos_min_profitable_folds_frac": latent * 2.0,       # monotone Transformation
            "oos_min_profit_factor": latent * 0.5,
            "oos_min_win_rate": latent * 0.4,
            "oos_min_psr": latent + 1.0,                          # monotoner Shift
        })
    return cohort


def test_gate_rank_correlation_matrix_detects_perfect_collinearity():
    result = gate_rank_correlation_matrix(_collinear_cohort(), _TCFG)
    assert result["n_samples"] == 30
    for pair, rho in result["correlations"].items():
        assert rho is not None
        assert abs(rho) > 0.99, f"{pair}: erwartete perfekte Kollinearität, rho={rho}"


def test_gate_rank_correlation_matrix_skips_incomplete_rows():
    cohort = _collinear_cohort(10) + [{"oos_min_expectancy": 0.1}]  # unvollständige Zeile
    result = gate_rank_correlation_matrix(cohort, _TCFG)
    assert result["n_samples"] == 10  # die unvollständige Zeile trägt nicht bei


def test_gate_rank_correlation_matrix_empty_cohort():
    result = gate_rank_correlation_matrix([], _TCFG)
    assert result["n_samples"] == 0
    assert all(rho is None for rho in result["correlations"].values())


def test_gate_rank_correlation_matrix_uncorrelated_gates():
    import random
    rng = random.Random(3)
    cohort = []
    for _ in range(40):
        cohort.append({
            "oos_min_expectancy": rng.uniform(-1, 1),
            "oos_min_profitable_folds_frac": rng.uniform(-1, 1),
            "oos_min_profit_factor": rng.uniform(-1, 1),
            "oos_min_psr": rng.uniform(-1, 1),
        })
    result = gate_rank_correlation_matrix(cohort, _TCFG)
    for pair, rho in result["correlations"].items():
        assert rho is not None
        assert abs(rho) < 0.5, f"{pair}: unerwartet hohe Korrelation bei unabhängigen Zufallsgrössen"


# ── Regressions-Wächter (Akzeptanzkriterium #667) ────────────────────────────────────────────────
def test_collinearity_guard_warns_on_redundant_gates(caplog):
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        result = assert_gate_collinearity_guard(_collinear_cohort(), _TCFG, threshold=0.95)
    assert any("Gate-Kollinearität" in r.message for r in caplog.records)
    assert result["n_samples"] == 30


def test_collinearity_guard_silent_for_uncorrelated_gates(caplog):
    import random
    rng = random.Random(9)
    cohort = [{
        "oos_min_expectancy": rng.uniform(-1, 1),
        "oos_min_profitable_folds_frac": rng.uniform(-1, 1),
        "oos_min_profit_factor": rng.uniform(-1, 1),
        "oos_min_psr": rng.uniform(-1, 1),
    } for _ in range(40)]
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        assert_gate_collinearity_guard(cohort, _TCFG, threshold=0.95)
    assert not any("Gate-Kollinearität" in r.message for r in caplog.records)


# ── Null-Kalibrierlauf (Monte-Carlo unter H0) ────────────────────────────────────────────────────
def test_calibration_is_deterministic_given_seed():
    r1 = calibrate_promotion_correction_mode(n_configs=6, n_periods=60, n_reps=15, seed=1, n_boot=30)
    r2 = calibrate_promotion_correction_mode(n_configs=6, n_periods=60, n_reps=15, seed=1, n_boot=30)
    assert r1 == r2


def test_calibration_fp_rates_are_valid_probabilities():
    report = calibrate_promotion_correction_mode(n_configs=6, n_periods=60, n_reps=20, seed=2, n_boot=30)
    assert 0.0 <= report["fp_rate_conjunction"] <= 1.0
    assert 0.0 <= report["fp_rate_dsr_or_robust_pair"] <= 1.0
    assert report["n_reps"] == 20
    assert report["nominal_fp_rate"] == pytest.approx(0.05)


def test_calibration_conjunction_never_exceeds_robust_pair_fp_rate():
    """'dsr_or_robust_pair' ist eine STRIKTE Disjunktions-Lockerung von 'conjunction'
    (dsr_ok OR (pbo_ok AND ci_ok) ⊇ dsr_ok AND ci_ok AND pbo_ok) ⇒ es promotet unter H0 NIEMALS
    weniger oft als 'conjunction' (strukturelle Invariante, unabhängig vom konkreten Seed)."""
    report = calibrate_promotion_correction_mode(n_configs=8, n_periods=80, n_reps=25, seed=5, n_boot=30)
    assert report["fp_rate_dsr_or_robust_pair"] >= report["fp_rate_conjunction"]
