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


def expected_max_standard_normal(n_trials: int) -> float:
    """E[max von ``n_trials`` i.i.d. N(0, 1)] — Bailey/López-de-Prado-Approximation.

    ``E[max_N] ≈ (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e))`` mit γ = Euler–Mascheroni. Der
    ERWARTUNGSWERT des Maximums (nicht das Quantil) — nützlich für Telemetrie/Intuition.
    """
    if n_trials <= 1:
        return 0.0
    return ((1.0 - _EULER_MASCHERONI) * _ND.inv_cdf(1.0 - 1.0 / n_trials)
            + _EULER_MASCHERONI * _ND.inv_cdf(1.0 - 1.0 / (n_trials * math.e)))


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
