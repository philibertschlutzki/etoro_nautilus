"""Issue #651 — DSR-Drop-Entscheidung ignoriert den #636-Varianz-Floor.

Bei Small-Cohorts nutzte die promotion-entscheidende `deflated_dsr` ein ANDERES SR₀ als die
Telemetrie `deflation_dsr_z`/`deflated_sr0`: `deflated_sharpe_ratio` berechnete SR₀ intern aus
`var_sr_trials`/`n_trials` (UNGEFLOORT), während `deflation_dsr_z` (via `psr_z`) und `deflated_sr0`
bereits das GEFLOORTE SR₀ (`sr0_multiple_testing_robust`) nutzten — Inflationsfaktor bis ~3.5× bei
N=9 (Hourly-Referenzfall).

Fix: `deflated_sharpe_ratio` nimmt `sr0` jetzt als Parameter — der Aufrufer berechnet SR₀ EINMAL
(gefloort, falls N < deflation_min_cohort) und übergibt es an BEIDE Konsumenten.
"""
import math
from statistics import NormalDist

import pytest

from automation.optimizer.deflation import (
    deflated_sharpe_ratio, psr_z, probabilistic_sharpe_ratio,
    sr0_multiple_testing, sr0_multiple_testing_robust,
)

_ND = NormalDist()


def test_deflated_dsr_equals_phi_of_dsr_z_for_the_same_sr0():
    """Akzeptanzkriterium (#651): Φ(deflation_dsr_z) mit sr_star=deflation_sr0 == deflated_dsr
    (bis auf Float-Toleranz) — für EIN beliebiges SR0, das beide Konsumenten identisch speist."""
    sr, n_periods, skew, kurt = 0.11386, 202, 0.0, 3.0
    sr0 = sr0_multiple_testing(1.803e-3, 100)

    dsr = deflated_sharpe_ratio(sr, n_periods, sr0=sr0, skew=skew, kurtosis=kurt)
    dsr_z = psr_z(sr, n_periods, skew=skew, kurtosis=kurt, sr_star=sr0)

    assert dsr == pytest.approx(_ND.cdf(dsr_z), abs=1e-9)


def test_deflated_dsr_equals_phi_of_dsr_z_with_active_floor():
    """Dieselbe Identität gilt auch, wenn SR0 aus dem GEFLOORTEN Small-Cohort-Pfad stammt (N < 10) —
    genau der #651-Regressionsfall (Entscheidung vs. Telemetrie divergierten hier vorher)."""
    var_sr_trials, n_trials = 1.483e-4, 9   # der im Issue zitierte Hourly-Referenzfall
    sr, n_periods = 0.08, 200

    sr0, used_floor, *_ = sr0_multiple_testing_robust(var_sr_trials, n_trials,
                                                       min_cohort=10, n_periods=n_periods)
    assert used_floor is True   # N=9 < min_cohort=10 ⇒ der Floor MUSS greifen

    dsr = deflated_sharpe_ratio(sr, n_periods, sr0=sr0)
    dsr_z = psr_z(sr, n_periods, sr_star=sr0)
    assert dsr == pytest.approx(_ND.cdf(dsr_z), abs=1e-9)


def test_hourly_n9_regression_fixture_reproduces_a_single_sr0_value():
    """Regressions-Fixture (Hourly N=9, #651-Referenzwerte): das gefloorte SR₀ (0,0018-Anker) MUSS
    das EINZIGE SR₀ sein, das sowohl in die Entscheidung als auch die Telemetrie einfliesst — vor
    #651 divergierten hier zwei verschiedene SR₀-Werte um den Faktor √(0,0018/1,483e-4) ≈ 3,48×."""
    deflation_var, deflation_n = 1.483e-4, 9
    deflation_sr0, used_floor, *_ = sr0_multiple_testing_robust(
        deflation_var, deflation_n, min_cohort=10, n_periods=200)
    assert used_floor is True

    # Der ungefloorte Wert (die alte, fehlerhafte interne Berechnung von deflated_sharpe_ratio vor
    # #651) wäre deutlich kleiner gewesen — beweist, dass der Floor tatsächlich wirkt (die genaue
    # Shrinkage-Gewichtung stammt aus #653; #651 betrifft nur, dass EIN SR0 beide Konsumenten speist).
    unfloored_sr0 = sr0_multiple_testing(deflation_var, deflation_n)
    assert deflation_sr0 > unfloored_sr0

    # Beide Konsumenten (Entscheidung + Telemetrie) erhalten JETZT exakt denselben Wert.
    sr, n_periods = 0.03, 200
    dsr = deflated_sharpe_ratio(sr, n_periods, sr0=deflation_sr0)
    dsr_z = psr_z(sr, n_periods, sr_star=deflation_sr0)
    assert dsr == pytest.approx(probabilistic_sharpe_ratio(sr, n_periods, sr_star=deflation_sr0))
    assert dsr == pytest.approx(_ND.cdf(dsr_z), abs=1e-9)


def test_deflated_sharpe_ratio_no_longer_accepts_var_sr_trials_kwarg():
    """Signatur-Migration (#651): deflated_sharpe_ratio nimmt sr0 direkt, nicht mehr
    var_sr_trials/n_trials (die interne, ungefloorte Rekonstruktion ist entfallen)."""
    with pytest.raises(TypeError):
        deflated_sharpe_ratio(0.1, 200, var_sr_trials=1e-3, n_trials=50)
