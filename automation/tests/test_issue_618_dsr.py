"""Issue #611/#618 — Deflated Sortino Ratio (DSR) statt Reward-Skalen-Deflation.

#611: die alte Kohorte (ALLE oos_evaluated-Trials) war eine Zwei-Punkt-Mischung; ihr σ war die
BERNOULLI-Standardabweichung der Gate-Passrate (anti-monoton: je besser die Strategie, desto höher die
Hürde). #618: die Deflation nutzte das 95 %-Quantil des Maximums (k=3.283) statt E[max] (2.531) und
kannte KEIN T. Fix: vollständige DSR auf der per-Perioden-Sortino-Skala über die ELIGIBLE Kohorte.
"""
import math
import statistics

from automation.optimizer.deflation import (
    probabilistic_sharpe_ratio, sr0_multiple_testing, deflated_sharpe_ratio,
    expected_max_standard_normal)


# ── #618 — expected_max_standard_normal hat jetzt eine Produktions-Call-Site (kein toter Code) ────
def test_expected_max_is_used_in_sr0():
    assert sr0_multiple_testing(1.0, 100) == math.sqrt(1.0) * expected_max_standard_normal(100)
    # E[max_100] ≈ 2.531 (NICHT das 95%-Quantil 3.283).
    assert abs(expected_max_standard_normal(100) - 2.531) < 0.02


# ── #618 — Referenzrechnung (VwapExhaustion-Gewinner), Toleranz 1e-3 ─────────────────────────────
def test_dsr_reference_calculation():
    sr0 = sr0_multiple_testing(1.803e-3, 100)
    assert abs(sr0 - 0.1075) < 1e-3, sr0
    dsr = deflated_sharpe_ratio(0.11386, 202, var_sr_trials=1.803e-3, n_trials=100,
                                skew=0.0, kurtosis=3.0)
    assert abs(dsr - 0.5364) < 1e-3, dsr
    assert dsr < 0.95   # DSR = 0.536 < 0.95 ⇒ HOLD (reproduzierbar)


# ── #618 — die DSR bezieht T ein (der zentrale Bruch mit deflated_threshold, das kein T kannte) ───
def test_dsr_depends_on_T():
    a = deflated_sharpe_ratio(0.2, 50, var_sr_trials=1e-3, n_trials=100)
    b = deflated_sharpe_ratio(0.2, 500, var_sr_trials=1e-3, n_trials=100)
    assert b > a, "mehr Bars (T) ⇒ höhere Konfidenz ⇒ höhere DSR"


def test_dsr_monotone_in_edge():
    lo = deflated_sharpe_ratio(0.10, 202, var_sr_trials=1.8e-3, n_trials=100)
    hi = deflated_sharpe_ratio(0.20, 202, var_sr_trials=1.8e-3, n_trials=100)
    assert hi > lo


# ── #611 — SR₀ hängt von der ELIGIBLE-Sortino-Streuung ab, NICHT von der Passrate (kein Bernoulli) ─
def test_sr0_not_anti_monotone_in_pass_rate():
    """Die alte Reward-σ = gap·√(p(1−p)) war maximal bei p=0.5. Die neue SR₀ nutzt die Streuung der
    eligiblen Sortinos: eine ENGERE eligible-Verteilung ⇒ KLEINERE SR₀, egal wie viele Failure-Trials
    (die Passrate) daneben liegen. So kann eine höhere Passrate die Hürde nicht mehr aufblähen."""
    sr0_tight = sr0_multiple_testing(1e-4, 100)
    sr0_wide = sr0_multiple_testing(1e-2, 100)
    assert sr0_wide > sr0_tight   # SR₀ folgt der ECHTEN Streuung, nicht der Passrate


def test_two_point_mixture_no_threshold_above_eligible_max():
    """Akzeptanz #611: eine Zwei-Punkt-Verteilung {−13.3 (×86), +2.0 (×14)} liefert KEINE Schwelle
    oberhalb des Maximums der eligiblen Teilmenge. Die DSR-Kohorte = NUR die eligiblen (+2.0-er, hier
    als konstante per-Perioden-Sortinos) ⇒ V=0 ⇒ SR₀=0 ⇒ keine Multiple-Testing-Hürde über dem Edge."""
    eligible_sr = [2.0] * 14
    var = statistics.pvariance(eligible_sr)
    sr0 = sr0_multiple_testing(var, len(eligible_sr))
    assert sr0 == 0.0
    # ⇒ der Gewinner (ŜR = 2.0 > SR₀ = 0) wird NICHT unter seinen eigenen Edge gedrückt.
    dsr = deflated_sharpe_ratio(2.0, 202, var_sr_trials=var, n_trials=14)
    assert dsr > 0.95   # ein realer, konsistenter Edge über T=202 ⇒ PASS (nicht künstlich verworfen)


# ── #618 — SR₀ nutzt E[max] (2.531), nicht das 95%-Quantil (3.283) ⇒ ~30 % weniger konservativ ───
def test_sr0_less_conservative_than_old_quantile():
    from automation.optimizer.deflation import deflated_threshold
    var = 4.0e-3
    n = 100
    sr0 = sr0_multiple_testing(var, n)                       # √V · E[max_N]
    old = deflated_threshold(n, math.sqrt(var), confidence=0.95, baseline=0.0)  # √V · Φ⁻¹(0.95^(1/N))
    assert sr0 < old    # E[max] (2.531) < 95%-Quantil des Maximums (3.283)
