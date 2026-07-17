"""Issue #678 — T-adaptive deflation_confidence-Kalibrierung + Deadlock-Praezisierung.

1. ``calibrate_t_adaptive_confidence`` liefert eine deterministische Grid-Suche nach der
   ``deflation_confidence``, deren empirische False-Positive-Winner-Rate (conjunction-Modus) am
   naechsten am nominellen Ziel liegt — fuer eine gegebene (n_configs, n_periods)-Groessenordnung.
2. Der im Issue behauptete "Kalibrier-Deadlock" existiert NICHT: die Kalibrierung (#667, #678) laeuft
   auf synthetischen H0-Daten, ohne jede reale Promotion vorauszusetzen.
3. Der Default ``promotion_correction_mode='conjunction'`` bleibt GEGEN das exakte Universe-1-
   Holdout-T (~36 Perioden) empirisch bestaetigt (dsr_or_robust_pair hat eine deutlich hoehere
   False-Positive-Rate, nicht nur bei T=200/137 wie in #667, sondern auch bei T=36).
"""
from automation.optimizer.calibration import (
    calibrate_promotion_correction_mode,
    calibrate_t_adaptive_confidence,
)


def test_t_adaptive_calibration_runs_without_any_live_promotion():
    """Die Kalibrierung benoetigt AUSSCHLIESSLICH synthetische H0-Kohorten — kein Live-Backtest,
    keine Study, keine Promotion. Das widerlegt den im Issue behaupteten Deadlock direkt."""
    result = calibrate_t_adaptive_confidence(
        n_configs=8, n_periods=36, n_reps=20, seed=1,
        confidence_grid=(0.95, 0.85, 0.70),
    )
    assert result["selected_confidence"] in (0.95, 0.85, 0.70)
    assert len(result["grid"]) == 3
    for row in result["grid"]:
        assert 0.0 <= row["fp_rate_conjunction"] <= 1.0


def test_t_adaptive_calibration_is_deterministic_given_seed():
    r1 = calibrate_t_adaptive_confidence(n_configs=8, n_periods=36, n_reps=20, seed=7,
                                         confidence_grid=(0.95, 0.80))
    r2 = calibrate_t_adaptive_confidence(n_configs=8, n_periods=36, n_reps=20, seed=7,
                                         confidence_grid=(0.95, 0.80))
    assert r1 == r2


def test_lower_confidence_grid_points_yield_higher_or_equal_fp_rate():
    """Ein monoton sinkendes Konfidenzniveau darf die empirische False-Positive-Rate der
    conjunction-Konjunktion nicht sinken lassen (weniger strenge DSR-Schwelle ⇒ mind. so viele
    Rausch-Gewinner passieren)."""
    result = calibrate_t_adaptive_confidence(
        n_configs=10, n_periods=50, n_reps=60, seed=3,
        confidence_grid=(0.99, 0.95, 0.90, 0.80),
    )
    rates = [row["fp_rate_conjunction"] for row in result["grid"]]
    # Fallend sortierte Konfidenz ⇒ die FP-Rate darf nicht fallen (schwaechere Schwelle ⇒ laxer).
    for i in range(len(rates) - 1):
        assert rates[i] <= rates[i + 1] + 1e-9


def test_small_t_scale_reconfirms_conjunction_over_dsr_or_robust_pair():
    """Akzeptanzkriterium (praezisiert): bei der TATSAECHLICHEN Universe-1-Holdout-Groessenordnung
    (T~36) bleibt 'conjunction' die besser kalibrierte Wahl — 'dsr_or_robust_pair' kompoundiert die
    False-Positive-Rate auch hier deutlich (nicht nur bei den #667-Referenzgroessen T=200/137)."""
    res = calibrate_promotion_correction_mode(n_configs=8, n_periods=36, n_reps=150, seed=42)
    assert res["fp_rate_conjunction"] < res["fp_rate_dsr_or_robust_pair"]
    # conjunction bleibt naeher am nominellen 5%-Ziel als dsr_or_robust_pair (nicht identisch mit
    # nominal, aber deutlich naeher).
    nominal = res["nominal_fp_rate"]
    assert abs(res["fp_rate_conjunction"] - nominal) < abs(res["fp_rate_dsr_or_robust_pair"] - nominal)
