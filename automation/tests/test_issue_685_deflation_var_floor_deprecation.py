"""Issue #685 — deflation_var_floor war nach der #653-Lo-2002-Migration ein irreführender Legacy-Key.

Historie: #685 markierte den Key als DEPRECATED (nur noch ein Legacy-Fallback für Aufrufer ohne
n_periods) und verlangte VOR einer Entfernung die Verifikation, dass n_periods an JEDER
Produktions-Call-Site unconditional verfügbar ist. Issue #701 hat diese Verifikation durchgeführt
(``oos_sortino_period is not None`` impliziert an jeder Call-Site ``oos_n_periods >= 1``, da beide
aus demselben ``backtest_runner``-Berechnungsblock stammen) und den Key + den Fallback-Zweig
ERSATZLOS ENTFERNT — dieser Test verifiziert, dass die Entfernung vollständig ist.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.deflation import sr0_multiple_testing_robust

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_deflation_var_floor_key_is_fully_removed_from_config():
    assert "deflation_var_floor" not in TCFG
    assert "deflation_var_floor" not in TCFG["_schema"]["fields"]


def test_deflation_min_cohort_schema_documents_the_701_removal():
    doc = TCFG["_schema"]["fields"]["deflation_min_cohort"]
    assert "#701" in doc
    assert "ENTFERNT" in doc or "entfernt" in doc


def test_sr0_multiple_testing_robust_no_longer_accepts_var_floor_kwarg():
    with pytest.raises(TypeError):
        sr0_multiple_testing_robust(1e-6, 20, min_cohort=10, var_floor=0.0018, n_periods=200)


def test_n_periods_is_a_required_keyword_argument():
    with pytest.raises(TypeError):
        sr0_multiple_testing_robust(1e-6, 20, min_cohort=10)


def test_missing_n_periods_value_fails_loud():
    with pytest.raises(ValueError, match="n_periods"):
        sr0_multiple_testing_robust(1e-6, 20, min_cohort=10, n_periods=None)


def test_theoretical_var_source_is_always_lo2002_now():
    _, _, _, source = sr0_multiple_testing_robust(1e-6, 20, min_cohort=10, n_periods=200)
    assert source == "lo2002"
