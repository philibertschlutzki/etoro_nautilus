"""Issue #701 (P3) — `deflation_var_floor` Dead-Key entfernen (nach `n_periods`-Unconditional-
Verifikation).

Verifikation (siehe deflation.py-Docstring/confirm.py-Kommentare für die vollständige Herleitung):
``backtest_runner._calculate_stats`` setzt ``sortino_period`` NUR innerhalb des Zweigs, in dem
``n_periods = len(period_rets)`` bereits > 0 ist (beide Felder stammen aus DEMSELBEN
Berechnungsblock) — ``oos_sortino_period is not None`` impliziert daher an JEDER Produktions-
Call-Site (confirm.py, run_optimization.py) ein truthy ``oos_n_periods``. Der var_floor-Fallback
(wirksam nur ohne n_periods) war auf jedem Entscheidungs-Pfad bereits tot. Fix: der Key
(tournament.json) und der Fallback-Zweig (deflation.sr0_multiple_testing_robust) sind entfernt;
n_periods ist jetzt ein Pflicht-Parameter, ein fehlender Wert bricht fail-loud ab statt still auf
eine geratene 0.0018-Konstante zurückzufallen.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import calibration
from automation.optimizer.deflation import sr0_multiple_testing_robust


TCFG_PATH = Path("automation/config/tournament.json")
TCFG = json.loads(TCFG_PATH.read_text("utf-8"))


# ── Config: der Key ist vollständig weg ──────────────────────────────────────────────────────────
def test_deflation_var_floor_absent_from_tournament_json():
    assert "deflation_var_floor" not in TCFG


def test_deflation_var_floor_absent_from_schema_fields():
    assert "deflation_var_floor" not in TCFG["_schema"]["fields"]


def test_tournament_json_still_valid_json_after_removal():
    # Re-Parse belegt: keine trailing comma/Syntax-Bruch durch die Entfernung.
    assert isinstance(json.loads(TCFG_PATH.read_text("utf-8")), dict)


# ── deflation.sr0_multiple_testing_robust: n_periods ist Pflicht, kein var_floor mehr ────────────
def test_var_floor_kwarg_no_longer_accepted():
    with pytest.raises(TypeError):
        sr0_multiple_testing_robust(1e-6, 20, var_floor=0.0018, n_periods=200)


def test_n_periods_none_raises_value_error():
    with pytest.raises(ValueError):
        sr0_multiple_testing_robust(1e-6, 20, n_periods=None)


def test_n_periods_le_1_raises_value_error():
    with pytest.raises(ValueError):
        sr0_multiple_testing_robust(1e-6, 20, n_periods=1)


def test_valid_n_periods_computes_lo2002_sr0():
    sr0, floor_dominant, lam, source = sr0_multiple_testing_robust(1e-6, 20, n_periods=200)
    assert source == "lo2002"
    assert sr0 > 0.0


# ── calibration.py: keine var_floor-Parameter mehr (n_periods war dort bereits Pflicht) ──────────
def test_calibrate_promotion_correction_mode_has_no_var_floor_param():
    import inspect
    sig = inspect.signature(calibration.calibrate_promotion_correction_mode)
    assert "var_floor" not in sig.parameters
    assert "n_periods" in sig.parameters


def test_calibrate_t_adaptive_confidence_has_no_var_floor_param():
    import inspect
    sig = inspect.signature(calibration.calibrate_t_adaptive_confidence)
    assert "var_floor" not in sig.parameters


def test_calibrate_gate_consolidation_has_no_var_floor_param():
    import inspect
    sig = inspect.signature(calibration.calibrate_gate_consolidation_false_positive_rate)
    assert "var_floor" not in sig.parameters


def test_calibration_functions_still_run_end_to_end():
    """Kein var_floor mehr nötig — die Kalibrierläufe bleiben voll funktionsfähig (n_periods war
    an diesen Call-Sites immer schon ein Pflicht-/immer-übergebener Parameter)."""
    res = calibration.calibrate_promotion_correction_mode(n_configs=8, n_periods=100, n_reps=20)
    assert 0.0 <= res["fp_rate_conjunction"] <= 1.0

    res2 = calibration.calibrate_t_adaptive_confidence(
        n_configs=8, n_periods=100, n_reps=20, confidence_grid=(0.95, 0.9))
    assert res2["selected_confidence"] in (0.95, 0.9)

    res3 = calibration.calibrate_gate_consolidation_false_positive_rate(
        n_configs=8, n_periods=100, n_reps=20)
    assert 0.0 <= res3["fp_rate_psr_only"] <= 1.0
