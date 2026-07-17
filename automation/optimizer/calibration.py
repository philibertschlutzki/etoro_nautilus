"""Issue #667 — Null-Kalibrierlauf für ``tournament.json['promotion_correction_mode']``.

DSR, PBO und Bootstrap-CI sind unterschiedliche, teils widersprüchliche Multiple-Testing-/Overfit-
Korrekturen. Ihre KONJUNKTIVE Verknüpfung (``promotion_correction_mode='conjunction'``, Default) auf
einem kurzen Universe-1-Holdout ist potenziell über-konservativ: die 95%-DSR-Schwelle ALLEIN ist
bereits streng; jede weitere UND-Bedingung kompoundiert die Type-II-Fehlerrate (reale Edges werden
strukturell abgelehnt), ohne notwendig einen Type-I-Gewinn zu liefern. #659 stellt den opt-in Modus
``'dsr_or_robust_pair'`` bereits bereit — aber NUR ein Kalibrierlauf (nicht Raten) rechtfertigt den
Wechsel des PRODUKTIVEN Defaults.

Dieses Modul liefert die WIEDERVERWENDBARE Monte-Carlo-Infrastruktur dafür: unter der Nullhypothese
H0 (kein echter Edge — ``n_configs`` i.i.d. Rausch-Konfigurationen, wahres SR=0) wird gemessen, wie
oft JEDER Modus dennoch einen "Gewinner" promotet (False-Positive-Winner-Rate). Ein Modus, dessen
FP-Rate klar über dem nominellen Niveau (``1 − confidence``) liegt, ist zu lax; ein Modus mit
FP-Rate deutlich UNTER dem Niveau des anderen bei ansonsten identischer Konstruktion kompoundiert
Type-II ohne zusätzlichen Type-I-Nutzen.

Rein & deterministisch (geseedeter RNG), KEIN I/O, KEIN Netzwerk — ein Referenz-Kalibrierlauf auf
SYNTHETISCHEN H0-Daten (analog den bereits etablierten deklarativen Kalibrier-Fixtures,
``reward._CALIBRATION_FIXTURE_*``, #631/#633), reproduzierbar und separat unit-testbar
(test_issue_667). Ein Kalibrierlauf auf der REALEN Universe-1-Holdout-Serie (Label-Permutation
oder Bar-Shuffle der tatsächlichen OOS-Renditen) bleibt die verbindliche Vorstufe für eine
PRODUKTIVE Aktivierung von ``'dsr_or_robust_pair'`` — diese Funktion liefert dafür die
wiederverwendbare, bereits gegen die echte Selektionslogik (DSR/PBO/Bootstrap-CI, dieselben
Funktionen wie ``confirm.py``) geprüfte Infrastruktur, keinen Ersatz für den Lauf auf echten Daten.
"""
from __future__ import annotations

import numpy as np

from automation.optimizer.bootstrap import bootstrap_ci, ci_lower_bound_passes, sortino_statistic
from automation.optimizer.cpcv import cpcv_group_boundaries, cpcv_paths, probability_of_backtest_overfitting
from automation.optimizer.deflation import (
    deflated_sharpe_ratio, sample_skew_kurtosis, sr0_multiple_testing_robust,
)


def _simulate_h0_cohort(rng: np.random.Generator, n_configs: int, n_periods: int,
                        period_std: float) -> list[np.ndarray]:
    """``n_configs`` i.i.d. N(0, period_std²)-Renditepfade — die Nullhypothese "kein echter Edge"."""
    return [rng.normal(0.0, period_std, n_periods) for _ in range(n_configs)]


def _cohort_pbo(cohort: list[np.ndarray], n_groups: int) -> float | None:
    """PBO der Kohorte auf ``n_groups`` kontiguierlichen Gruppen (dieselbe Konstruktion wie
    ``confirm._study_pbo`` seit #663 — Gruppen-Mittelwert als Split-Metrik)."""
    n_obs = min(len(c) for c in cohort)
    n_groups_eff = min(n_groups, n_obs)
    if n_groups_eff < 8:
        return None
    boundaries = cpcv_group_boundaries(n_obs, n_groups_eff)
    mat = np.array([[float(c[s:e].mean()) for s, e in boundaries] for c in cohort])
    k_test = max(1, n_groups_eff // 2)
    if not (0 < k_test < n_groups_eff):
        return None
    is_rows, oos_rows = [], []
    for train, test in cpcv_paths(n_groups_eff, k_test):
        is_rows.append(mat[:, list(train)].mean(axis=1))
        oos_rows.append(mat[:, list(test)].mean(axis=1))
    return probability_of_backtest_overfitting(np.array(is_rows), np.array(oos_rows))


def calibrate_promotion_correction_mode(
    *, n_configs: int = 12, n_periods: int = 200, period_std: float = 0.01,
    n_reps: int = 200, confidence: float = 0.95, min_cohort: int = 10,
    var_floor: float = 0.0018, pbo_n_groups: int = 12, seed: int = 42, n_boot: int = 1000,
) -> dict:
    """Issue #667 — Monte-Carlo-Kalibrierlauf: empirische False-Positive-Winner-Rate von
    ``'conjunction'`` (DSR UND Bootstrap-CI UND PBO) vs. ``'dsr_or_robust_pair'`` (DSR ODER
    (PBO UND Bootstrap-CI)) unter H0 (``n_reps`` unabhängige Kohorten, je ``n_configs`` i.i.d.
    Rausch-Konfigurationen mit ``n_periods`` Perioden).

    Je Replikation wird — wie in ``confirm.confirm_per_symbol_promotion`` — die Config mit dem
    höchsten mittleren Perioden-Return als "IS-Gewinner" selektiert (argmax(reward)-Analogon; unter
    H0 ist das bereits ein reines Rausch-Maximum) und DSR/Bootstrap-CI/PBO exakt wie im
    Promotion-Pfad berechnet (dieselben Funktionen aus ``deflation.py``/``bootstrap.py``/
    ``cpcv.py``). Jede Promotion unter H0 ist per Konstruktion ein FALSE POSITIVE.

    Rückgabe: ``{'n_reps', 'n_configs', 'n_periods', 'confidence', 'nominal_fp_rate',
    'fp_rate_conjunction', 'fp_rate_dsr_or_robust_pair'}``. Deterministisch bei festem ``seed``."""
    rng = np.random.default_rng(seed)
    fp_conjunction = 0
    fp_robust_pair = 0

    for _ in range(n_reps):
        cohort = _simulate_h0_cohort(rng, n_configs, n_periods, period_std)
        cohort_sr = [sortino_statistic(c, mar=0.0, annualization=1.0) for c in cohort]
        cohort_sr = [float(s) if s == s else 0.0 for s in cohort_sr]  # NaN (dd<=0) ⇒ 0.0

        winner_idx = int(np.argmax(cohort_sr))
        winner = cohort[winner_idx]
        sr_period = cohort_sr[winner_idx]
        skew, kurt = sample_skew_kurtosis(winner)

        var_sr = float(np.var(cohort_sr, ddof=0))
        sr0, *_ = sr0_multiple_testing_robust(
            var_sr, n_configs, min_cohort=min_cohort, var_floor=var_floor, n_periods=n_periods)
        dsr = deflated_sharpe_ratio(sr_period, n_periods, sr0=sr0, skew=skew, kurtosis=kurt)
        dsr_ok = dsr is not None and dsr >= confidence

        _, ci_lo, _ = bootstrap_ci(
            winner.tolist(), lambda a: sortino_statistic(a, mar=0.0, annualization=1.0),
            confidence=confidence, seed=seed, n_boot=n_boot)
        ci_ok = ci_lower_bound_passes(ci_lo, 0.0)

        pbo = _cohort_pbo(cohort, pbo_n_groups)
        pbo_ok = pbo is not None and pbo <= 0.5

        if dsr_ok and ci_ok and pbo_ok:
            fp_conjunction += 1
        if dsr_ok or (pbo_ok and ci_ok):
            fp_robust_pair += 1

    return {
        "n_reps": n_reps, "n_configs": n_configs, "n_periods": n_periods,
        "confidence": confidence, "nominal_fp_rate": 1.0 - confidence,
        "fp_rate_conjunction": fp_conjunction / n_reps,
        "fp_rate_dsr_or_robust_pair": fp_robust_pair / n_reps,
    }
