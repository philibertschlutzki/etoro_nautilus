"""Issue #653 — Varianz-Floor `0.0018` als harte Konstante + `N_min = 10`-Diskontinuität.

Vor #653 ersetzte `sr0_multiple_testing_robust` die Kohorten-Varianz unterhalb `deflation_min_cohort`
durch eine HARTE Konstante (`max(observed, 0.0018)`) und darüber durch die rohe Stichproben-Varianz —
ein diskontinuierlicher Cutover: zwei fast identische Kohorten (N=9 vs. N=10) erhielten SR₀ um Faktor
~3,5 auseinander, weil die Kohorten-Varianz selbst ein Selektions-Artefakt ist (das Gate censoriert
die Verteilung und behält nur nahezu identische Passierer).

Fix: ein STETIGES Shrinkage-Gewicht (`_cohort_shrinkage_weight`) blendet die empirische Varianz
gegen eine theoretisch begründete Referenz (Lo 2002, `lo2002_sharpe_variance`, T-bewusst) — kein
harter Cutover mehr, SR₀(N) ist über jeden N-Übergang stetig.
"""
import math

import pytest

from automation.optimizer.deflation import (
    lo2002_sharpe_variance,
    _cohort_shrinkage_weight,
    sr0_multiple_testing_robust,
    sr0_multiple_testing,
)


# ── Lo (2002) Referenzrechnung ────────────────────────────────────────────────────────────────────
def test_lo2002_reference_calculation_sr_zero():
    """Var[ŜR] = (1 + ŜR²/2)/T bei ŜR=0 (Nullhypothese) reduziert sich auf 1/T."""
    assert lo2002_sharpe_variance(0.0, 200) == pytest.approx(1.0 / 200.0)
    assert lo2002_sharpe_variance(0.0, 50) == pytest.approx(0.02)


def test_lo2002_reference_calculation_nonzero_sr():
    """Ein bekannter ŜR/T-Fall: ŜR=0.5, T=100 ⇒ (1 + 0.125)/100 = 0.01125."""
    assert lo2002_sharpe_variance(0.5, 100) == pytest.approx((1.0 + 0.125) / 100.0)
    assert lo2002_sharpe_variance(0.5, 100) == pytest.approx(0.01125)


def test_lo2002_increases_with_sr_magnitude():
    """Var[ŜR] steigt mit |ŜR| (der ŜR²/2-Term) — ein grösserer geschätzter Edge ist unsicherer."""
    lo = lo2002_sharpe_variance(0.0, 100)
    hi = lo2002_sharpe_variance(1.0, 100)
    assert hi > lo


def test_lo2002_decreases_with_more_periods():
    """Var[ŜR] fällt mit T — mehr Bars ⇒ präzisere Schätzung (die Floor-Skalierungs-Invariante)."""
    short_t = lo2002_sharpe_variance(0.0, 50)
    long_t = lo2002_sharpe_variance(0.0, 500)
    assert short_t > long_t


def test_lo2002_rejects_degenerate_t():
    with pytest.raises(ValueError):
        lo2002_sharpe_variance(0.0, 1)
    with pytest.raises(ValueError):
        lo2002_sharpe_variance(0.0, None)


# ── Shrinkage-Gewicht: stetig, monoton fallend in N ──────────────────────────────────────────────
def test_shrinkage_weight_bounds_and_midpoint():
    assert _cohort_shrinkage_weight(0, 10) == pytest.approx(1.0)
    assert _cohort_shrinkage_weight(10, 10) == pytest.approx(0.5)
    assert _cohort_shrinkage_weight(1000, 10) < 0.02


def test_shrinkage_weight_monotone_decreasing_in_n():
    ws = [_cohort_shrinkage_weight(n, 10) for n in range(0, 50)]
    assert all(ws[i] > ws[i + 1] for i in range(len(ws) - 1))


def test_shrinkage_weight_no_jump_at_min_cohort():
    """Der alte Fehler: λ sprang bei N=min_cohort von 'voller Floor' auf 'kein Floor'. Das neue
    Gewicht ändert sich zwischen N=9 und N=10 nur um einen kleinen, stetigen Schritt."""
    w9 = _cohort_shrinkage_weight(9, 10)
    w10 = _cohort_shrinkage_weight(10, 10)
    assert abs(w9 - w10) < 0.05


# ── Kernkriterium (#653): SR₀(N) ist über den N_min-Übergang STETIG (kein Faktor-3-Sprung) ───────
def test_sr0_continuous_across_min_cohort_transition():
    """Zwei fast identische Kohorten (N=9 vs. N=10, identische winzige empirische Varianz, gleiches
    T) dürfen KEINEN Faktor-3(+)-Sprung in SR₀ erzeugen — der #653-Kernfehler."""
    tiny_var = 1.483e-4  # der im Issue zitierte Hourly-Referenzwert
    n_periods = 200

    sr0_9, floor_dom_9, *_ = sr0_multiple_testing_robust(
        tiny_var, 9, min_cohort=10, n_periods=n_periods)
    sr0_10, floor_dom_10, *_ = sr0_multiple_testing_robust(
        tiny_var, 10, min_cohort=10, n_periods=n_periods)

    assert floor_dom_9 is True
    assert floor_dom_10 is True   # #653 — N=min_cohort zählt noch als floor-dominant (Gewicht=0.5)
    ratio = max(sr0_9, sr0_10) / min(sr0_9, sr0_10)
    assert ratio < 1.1, f"SR0(9)={sr0_9:.5f} vs SR0(10)={sr0_10:.5f} — Sprung Faktor {ratio:.2f}"


def test_sr0_continuous_in_transition_window_around_min_cohort():
    """SR₀(N) bei fixer (winziger) empirischer Varianz und festem T zeigt keine Sprünge in der
    UNMITTELBAREN Umgebung von deflation_min_cohort=10 (die Zone, in der vor #653 der harte Cutover
    lag). Ausserhalb dieses Fensters wächst ``expected_max_standard_normal(N)`` selbst mit N (mehr
    Trials ⇒ höheres erwartetes Rausch-Maximum unter H0) — das ist ERWÜNSCHTES, kein Sprung-Verhalten
    und daher nicht Gegenstand dieser Stetigkeits-Prüfung."""
    tiny_var = 1e-4
    n_periods = 200
    sr0s = [
        sr0_multiple_testing_robust(tiny_var, n, min_cohort=10, n_periods=n_periods)[0]
        for n in range(7, 15)
    ]
    # Aufeinanderfolgende Werte dürfen sich in dieser Übergangszone nur graduell ändern.
    for a, b in zip(sr0s, sr0s[1:]):
        if a > 0:
            assert abs(b - a) / a < 0.2, f"Sprung erkannt: {a:.5f} -> {b:.5f}"


def test_floor_scales_with_t_shorter_oos_more_conservative():
    """Akzeptanzkriterium (#653): kürzeres OOS-Fenster (kleineres T) ⇒ höhere Schätz-Unsicherheit ⇒
    konservativerer (grösserer) Floor ⇒ grösseres SR₀ bei identischer, winziger Kohorten-Varianz."""
    tiny_var = 1e-6
    sr0_short_t, *_ = sr0_multiple_testing_robust(
        tiny_var, 3, min_cohort=10, n_periods=30)
    sr0_long_t, *_ = sr0_multiple_testing_robust(
        tiny_var, 3, min_cohort=10, n_periods=500)
    assert sr0_short_t > sr0_long_t


def test_missing_n_periods_fails_loud_no_more_var_floor_constant():
    """Issue #701 — der var_floor-Legacy-Fallback (Rückwärtskompat ohne n_periods) wurde nach
    Verifikation, dass n_periods an jeder Produktions-Call-Site unconditional verfügbar ist,
    ERSATZLOS ENTFERNT: ein fehlendes n_periods ist jetzt ein Bug im Aufrufer (ValueError), kein
    stiller Rückfall mehr auf die alte 0.0018-Konstante."""
    with pytest.raises(ValueError, match="n_periods"):
        sr0_multiple_testing_robust(1e-9, 2, min_cohort=10, n_periods=None)


def test_large_cohort_trusts_observed_variance():
    """Bei grossem N (weit über min_cohort) dominiert die empirische Varianz nahezu vollständig —
    der Floor-Einfluss wird vernachlässigbar (λ(N) → 0)."""
    observed_var = 0.05
    sr0, floor_dominant, *_ = sr0_multiple_testing_robust(
        observed_var, 500, min_cohort=10, n_periods=200)
    assert floor_dominant is False
    # Nahezu identisch zur unrobusten Referenz bei vernachlässigbarem Floor-Gewicht.
    assert sr0 == pytest.approx(sr0_multiple_testing(observed_var, 500), rel=0.05)
