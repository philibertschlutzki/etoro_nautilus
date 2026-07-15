"""Issue #553 — Deflated-Sortino-Selektion (Multiple-Testing-Korrektur).

Wählt man den besten von ``N`` getesteten Konfigurationen, ist die erwartete beste Performance
auch unter H0 (kein echter Edge) positiv und wächst mit ``N``. Ohne Korrektur ist die
False-Positive-Rate der Winner-Selektion hoch: bei genügend Trials überschreitet irgendeine
Parametrisierung das Gate rein zufällig (Bailey & López de Prado, *Deflated Sharpe Ratio*).

Dieses Modul liefert eine Schwelle, die das kontrolliert. Rein & deterministisch (kein I/O,
kein globaler State) — separat Monte-Carlo-getestet (test_issue_553).
"""
import math
from statistics import NormalDist

_ND = NormalDist()
# Euler–Mascheroni-Konstante (für die Erwartungswert-Approximation des Maximums).
_EULER_MASCHERONI = 0.5772156649015329


def probabilistic_sharpe_ratio(sr, n_periods, *, skew: float = 0.0, kurtosis: float = 3.0,
                               sr_star: float = 0.0):
    """Issue #614 — Probabilistic Sharpe/Sortino Ratio (Bailey & López de Prado).

    ``PSR(SR*) = Φ[ (ŜR − SR*)·√(T−1) / √(1 − γ₃·ŜR + ((γ₄−1)/4)·ŜR²) ]``

    ``ŜR`` = **per-Perioden**-Ratio (Sharpe ODER Sortino — NICHT annualisiert), ``T`` = Anzahl
    Perioden (OOS-Bars), ``γ₃`` = Schiefe, ``γ₄`` = (NICHT-Exzess-)Kurtosis der Periodenrenditen
    (Normal ⇒ 3.0). Skalenfrei und in ``[0, 1]`` beschränkt — die asinh-Sättigung (#559) wird damit
    überflüssig. **Annualisierungs-invariant**: ``ŜR`` ist per-Periode, die Reskalierung mit √A (die
    Punktschätzer UND Standardfehler gleich multipliziert, #614) fällt vollständig heraus.

    ``None`` bei undefinierter Eingabe (``sr is None``, ``T < 2``) oder nicht-positivem Varianz-Term
    (numerisch degeneriert). Referenz (#614/#618): ``ŜR=0.11386, T=202, γ₃=0, γ₄=3, SR*=0 ⇒ 0.9463``.
    """
    if sr is None or n_periods is None or n_periods < 2:
        return None
    sr = float(sr)
    denom_var = 1.0 - float(skew) * sr + ((float(kurtosis) - 1.0) / 4.0) * (sr * sr)
    if denom_var <= 0.0:
        return None
    z = (sr - float(sr_star)) * math.sqrt(float(n_periods) - 1.0) / math.sqrt(denom_var)
    return float(_ND.cdf(z))


def sample_skew_kurtosis(returns) -> tuple[float, float]:
    """(Schiefe γ₃, NICHT-Exzess-Kurtosis γ₄) einer Renditesequenz. Bei < 2 Werten oder Nullvarianz
    ⇒ (0.0, 3.0) (Normal-Annahme, neutral für die PSR-Korrektur). Population-Momente (kein Bessel)."""
    import numpy as _np
    a = _np.asarray(list(returns), dtype=float)
    if a.size < 2:
        return 0.0, 3.0
    m = a.mean()
    s = a.std()  # Population-Std
    if not (s > 0.0):
        return 0.0, 3.0
    z = (a - m) / s
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean())
    return skew, kurt


def expected_max_standard_normal(n_trials: int) -> float:
    """E[max von ``n_trials`` i.i.d. N(0, 1)] — Bailey/López-de-Prado-Approximation.

    ``E[max_N] ≈ (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e))`` mit γ = Euler–Mascheroni. Der
    ERWARTUNGSWERT des Maximums (nicht das Quantil) — nützlich für Telemetrie/Intuition.
    """
    if n_trials <= 1:
        return 0.0
    return ((1.0 - _EULER_MASCHERONI) * _ND.inv_cdf(1.0 - 1.0 / n_trials)
            + _EULER_MASCHERONI * _ND.inv_cdf(1.0 - 1.0 / (n_trials * math.e)))


def sr0_multiple_testing(var_sr_trials: float, n_trials: int) -> float:
    """Issue #618 — die Multiple-Testing-Nullhypothesen-Ratio SR₀ (Bailey/López de Prado, DSR).

    ``SR₀ = √V[ŜR_trials] · E[max_N standard normal]`` mit ``E[max_N] = expected_max_standard_normal(N)``.
    Der erwartete Bestwert von N getesteten Konfigurationen unter H0 (kein Edge) wächst mit N — genau
    diese Hürde muss der Gewinner schlagen. ``V[ŜR_trials]`` = Streuung der Ratios ÜBER die N Trials
    (die Multiple-Testing-Varianz), NICHT der Standardfehler des Gewinners (der steckt in der PSR/DSR).
    Degeneriert auf 0.0 bei fehlender Streuung oder N ≤ 1 (kein Multiple-Testing)."""
    if var_sr_trials is None or var_sr_trials <= 0.0 or n_trials <= 1:
        return 0.0
    return math.sqrt(float(var_sr_trials)) * expected_max_standard_normal(n_trials)


def deflated_sharpe_ratio(sr, n_periods, *, var_sr_trials: float, n_trials: int,
                          skew: float = 0.0, kurtosis: float = 3.0):
    """Issue #618 — vollständige Deflated Sharpe/Sortino Ratio: die PSR relativ zur Multiple-Testing-
    Schwelle ``SR₀`` (statt zu 0). ``DSR = Φ[(ŜR − SR₀)·√(T−1)/√(1 − γ₃·ŜR + ((γ₄−1)/4)·ŜR²)]``.

    Alle Grössen PER-PERIODE (nicht annualisiert, #614). ``promote ⟺ DSR ≥ deflation_confidence``.
    Referenz (VwapExhaustion): ``ŜR=0.11386, T=202, N=100, V[ŜR_trials]=1.803e-3`` ⇒
    ``SR₀=0.1075, DSR=0.5364`` ⇒ HOLD (< 0.95). ``None`` bei undefinierter Eingabe."""
    sr0 = sr0_multiple_testing(var_sr_trials, n_trials)
    return probabilistic_sharpe_ratio(sr, n_periods, skew=skew, kurtosis=kurtosis, sr_star=sr0)


def deflated_threshold(n_trials: int, dispersion: float, *, confidence: float = 0.95,
                       baseline: float = 0.0) -> float:
    """``confidence``-Quantil des MAXIMUMS von ``n_trials`` i.i.d. N(``baseline``, ``dispersion``²).

    Unter H0 (jede Konfiguration hat den wahren Wert ``baseline`` bei Cross-Trial-Streuung
    ``dispersion``) gilt exakt ``P(max_N > threshold) = 1 − confidence``. Ein Winner MUSS diese
    Schwelle schlagen, damit die False-Positive-Winner-Rate auf das nominelle Niveau ``1 − confidence``
    sinkt (nicht nur die statische Gate-Schwelle).

    Herleitung: Für ``X_i ~ N(μ, σ²)`` i.i.d. hat ``max_i X_i`` die CDF ``Φ((x−μ)/σ)^N``. Das
    ``q``-Quantil ist ``x = μ + σ·Φ⁻¹(q^{1/N})``; für die Winner-Kontrolle ``q = confidence``.

    Degeneriert sauf ``baseline``, wenn keine Streuung (``dispersion <= 0``) oder nur ein Trial
    vorliegt (kein Multiple-Testing).
    """
    if dispersion <= 0.0 or n_trials <= 1:
        return baseline
    q = confidence ** (1.0 / n_trials)
    q = min(max(q, 1e-12), 1.0 - 1e-12)
    return baseline + dispersion * _ND.inv_cdf(q)


def deflated_reward_threshold(rewards, *, confidence: float = 0.95):
    """Issue #592 — Deflations-Schwelle auf der REWARD-Skala (dem tatsächlichen Selektionskriterium
    ``argmax(reward)`` über N Trials), NICHT auf einer geklemmten Teil-Kennzahl (Sortino).

    Zwei Fehler behob #592: (1) der 50.0-Sentinel-Filter war eine hartcodierte Zahl, die seit
    einer Clip-Änderung (RATIO_CAP 50 → 15 → entfernt, #588) ins Leere griff und ±Clip-Artefakte
    ungefiltert in die Dispersion liess; (2) die Dispersion wurde auf der GEKLEMMTEN Sortino-Skala
    geschätzt, die ``deflated_threshold`` (Maximum von N i.i.d. UNBESCHRÄNKTEN Normalen) modelliert.
    Auf der Reward-Skala entfällt beides. ``baseline = median(rewards)`` (die Reward-Skala ist NICHT
    nullzentriert — ``baseline=0.0`` wäre auf einer bei −6.8 zentrierten Skala sinnlos).

    Rückgabe ``(threshold, n, sigma, baseline)``; ``(None, n, None, None)`` bei < 2 Werten
    (kein Multiple-Testing)."""
    import statistics
    vals = [float(r) for r in rewards if r is not None]
    n = len(vals)
    if n < 2:
        return None, n, None, None
    sigma = statistics.pstdev(vals)
    baseline = statistics.median(vals)
    thr = deflated_threshold(n, sigma, confidence=confidence, baseline=baseline)
    return thr, n, sigma, baseline
