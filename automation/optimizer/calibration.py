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

import math

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
    pbo_n_groups: int = 12, seed: int = 42, n_boot: int = 1000,
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
            var_sr, n_configs, min_cohort=min_cohort, n_periods=n_periods)
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


def calibrate_t_adaptive_confidence(
    *, n_configs: int, n_periods: int, period_std: float = 0.01,
    confidence_grid: tuple[float, ...] = (0.99, 0.975, 0.95, 0.925, 0.90, 0.85, 0.80),
    target_fp_rate: float = 0.05, n_reps: int = 200, min_cohort: int = 10,
    seed: int = 42,
) -> dict:
    """Issue #678 — T-ADAPTIVE ``deflation_confidence``: statt eines fixen 0.95-Konstante-Niveaus
    (das bei kleinem T, z. B. ~36 Holdout-Perioden, die konjunktive Promotion-Bestätigung praktisch
    unerreichbar macht, siehe confirm.py-Docstring/#659) wird die Schwelle EMPIRISCH aus einem
    Null-Kalibrierlauf für die TATSÄCHLICHE (n_configs, n_periods)-Grössenordnung abgeleitet — die
    Schwelle, bei der die 'conjunction'-False-Positive-Winner-Rate am nächsten am nominellen Ziel
    (``target_fp_rate``, Default 5 %) liegt. ``NICHT geraten`` (siehe tournament.json-Schema).

    WICHTIG (Root-Cause-Präzisierung, #678): Der im Issue beschriebene "Chicken-Egg-Deadlock"
    (Kalibrierung erfordert eine Live-Promotion, aber ohne Promotion keine Kalibrierbasis) existiert
    in DIESEM Code NICHT — ``calibrate_promotion_correction_mode`` (#667) arbeitet bereits auf
    SYNTHETISCHEN H0-Daten und benötigt keine einzige reale Promotion. Diese Funktion erweitert
    dieselbe synthetische Kalibrier-Infrastruktur um eine zweite Dimension (T-adaptive Konfidenz statt
    nur Moduswahl) für exakt dieselbe kleine T-Grössenordnung, die #678 als Problem benennt.

    Rückgabe: ``{'n_configs', 'n_periods', 'target_fp_rate', 'grid': [{'confidence',
    'fp_rate_conjunction'}, ...], 'selected_confidence'}`` — ``selected_confidence`` ist der
    Grid-Punkt mit der ABSOLUT kleinsten Abweichung von ``target_fp_rate`` (conjunction-Modus, der
    empirisch bessere Type-I-Kontrolle behält, siehe #667-Kalibrierlauf-Fund). Deterministisch bei
    festem ``seed`` (jede Grid-Stufe nutzt denselben Seed — bewusst KEINE Rauschreduktion durch
    Cross-Grid-Kohorten-Wiederverwendung, um die Funktion einfach und auditierbar zu halten; für
    produktive Kalibrierläufe ``n_reps`` erhöhen)."""
    grid = []
    best = None
    for conf in confidence_grid:
        res = calibrate_promotion_correction_mode(
            n_configs=n_configs, n_periods=n_periods, period_std=period_std,
            n_reps=n_reps, confidence=conf, min_cohort=min_cohort, seed=seed,
        )
        row = {"confidence": conf, "fp_rate_conjunction": res["fp_rate_conjunction"]}
        grid.append(row)
        dist = abs(res["fp_rate_conjunction"] - target_fp_rate)
        if best is None or dist < best[0]:
            best = (dist, conf)

    return {
        "n_configs": n_configs, "n_periods": n_periods, "target_fp_rate": target_fp_rate,
        "grid": grid, "selected_confidence": best[1] if best else None,
    }


def calibrate_gate_consolidation_false_positive_rate(
    *, n_configs: int = 12, n_periods: int = 200, period_std: float = 0.01,
    n_reps: int = 2000, confidence: float = 0.95, min_cohort: int = 10,
    expectancy_psr_correlation: float = 0.96, seed: int = 42,
) -> dict:
    """Issue #697 — Null-Kalibrierlauf: belegt, dass das Entfernen des mit ``oos_min_psr``
    ~96%-kollinearen ``oos_min_expectancy``-Gates aus ``eligible_requires_all`` die empirische
    False-Positive-Winner-Rate unter H0 (kein echter Edge) NICHT erhöht.

    Je Replikation wird — wie in ``confirm.confirm_per_symbol_promotion`` — der DSR-Gewinner
    (argmax des per-Perioden-Sortino über ``n_configs`` i.i.d. Rausch-Konfigurationen) ermittelt
    und die DSR exakt wie im Promotion-Pfad berechnet (dieselben Funktionen aus ``deflation.py``).
    Zusätzlich wird ein SYNTHETISCHER Expectancy-Proxy erzeugt, der mit dem standardisierten
    Sortino-Rang eine Pearson-Korrelation von ``expectancy_psr_correlation`` trägt (Default 0.96 —
    dieselbe Grössenordnung wie das reale #667-Symptom, ``|ρ(oos_min_expectancy, oos_min_psr)|
    =0.961``), via Standard-Bivariate-Konstruktion (``Y = ρ·X + √(1−ρ²)·Z``, ``X``/``Z`` unabhängig
    standardnormal) — ohne die reale Expectancy-Berechnung zu duplizieren, die Korrelationsstruktur
    ist der Test-Gegenstand, nicht die Formel selbst. ``expectancy_ok`` ⟺ ``expectancy_proxy > 0``
    (die im Issue-Kontext ökonomisch analoge "netto positiv"-Bedingung).

    ``'conjunction_with_expectancy'`` = DSR UND expectancy_ok (Pre-#697-Konjunktion);
    ``'psr_only'`` = NUR DSR (Post-#697). Rückgabe: ``{'fp_rate_conjunction_with_expectancy',
    'fp_rate_psr_only', 'nominal_fp_rate', ...}``. Akzeptanzkriterium (#697): ``fp_rate_psr_only``
    darf ``fp_rate_conjunction_with_expectancy`` NICHT MATERIELL übersteigen — die entfernte Klausel
    trug wegen der hohen Kollinearität ~keine unabhängige Type-I-Kontrolle bei; beide Raten bleiben
    nahe dem nominellen Niveau ``1 − confidence``. Deterministisch bei festem ``seed``."""
    rng = np.random.default_rng(seed)
    fp_conjunction = 0
    fp_psr_only = 0

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
            var_sr, n_configs, min_cohort=min_cohort, n_periods=n_periods)
        dsr = deflated_sharpe_ratio(sr_period, n_periods, sr0=sr0, skew=skew, kurtosis=kurt)
        dsr_ok = dsr is not None and dsr >= confidence

        std_sr = math.sqrt(var_sr) if var_sr > 0.0 else 1.0
        standardized_sr = sr_period / std_sr
        rho = float(expectancy_psr_correlation)
        expectancy_proxy = rho * standardized_sr + math.sqrt(max(0.0, 1.0 - rho ** 2)) * rng.normal(0.0, 1.0)
        expectancy_ok = expectancy_proxy > 0.0

        if dsr_ok:
            fp_psr_only += 1
        if dsr_ok and expectancy_ok:
            fp_conjunction += 1

    return {
        "n_reps": n_reps, "n_configs": n_configs, "n_periods": n_periods,
        "confidence": confidence, "nominal_fp_rate": 1.0 - confidence,
        "expectancy_psr_correlation": expectancy_psr_correlation,
        "fp_rate_conjunction_with_expectancy": fp_conjunction / n_reps,
        "fp_rate_psr_only": fp_psr_only / n_reps,
    }
