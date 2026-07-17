"""Issue #685 — deflation_var_floor ist nach der #653-Lo-2002-Migration ein irreführender Legacy-Key.

1. Der Config-Key trägt einen unmissverständlichen Deprecation-Hinweis (nicht nur beiläufig in einem
   dichten Absatz erwähnt).
2. Funktional: bei vorhandenem n_periods hat ``var_floor`` NULL Einfluss auf SR₀ (die Referenz ist
   immer Lo-2002) — nur wenn n_periods fehlt, greift der Legacy-Fallback.
"""
import json
from pathlib import Path

from automation.optimizer.deflation import sr0_multiple_testing_robust

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_schema_carries_unmistakable_deprecation_marker():
    desc = TCFG["_schema"]["fields"]["deflation_var_floor"]
    assert "DEPRECATED" in desc
    assert desc.strip().startswith("⚠️ DEPRECATED") or desc.lstrip().startswith("⚠️ DEPRECATED"), (
        "Deprecation-Hinweis muss am ANFANG stehen, nicht in einem dichten Absatz vergraben sein."
    )
    assert "#653" in desc and "Lo-2002" in desc


def test_var_floor_has_no_effect_when_n_periods_present():
    """Kein Konsument darf var_floor als aktiven Floor interpretieren, solange n_periods bekannt
    ist — zwei stark unterschiedliche var_floor-Werte müssen identisches SR₀ liefern."""
    sr0_low_floor, *_ = sr0_multiple_testing_robust(
        1e-6, 20, min_cohort=10, var_floor=0.0018, n_periods=200)
    sr0_high_floor, *_ = sr0_multiple_testing_robust(
        1e-6, 20, min_cohort=10, var_floor=999.0, n_periods=200)
    assert sr0_low_floor == sr0_high_floor


def test_var_floor_only_active_as_legacy_fallback_without_n_periods():
    """Fehlt n_periods (Legacy-Aufrufer ohne T-Telemetrie), IST var_floor die theoretische
    Referenz — unterschiedliche Werte liefern unterschiedliches SR₀."""
    sr0_low_floor, *_ = sr0_multiple_testing_robust(
        1e-6, 20, min_cohort=10, var_floor=0.0018, n_periods=None)
    sr0_high_floor, *_ = sr0_multiple_testing_robust(
        1e-6, 20, min_cohort=10, var_floor=999.0, n_periods=None)
    assert sr0_low_floor != sr0_high_floor


def test_theoretical_var_source_confirms_lo2002_when_n_periods_present():
    _, _, _, source = sr0_multiple_testing_robust(
        1e-6, 20, min_cohort=10, var_floor=0.0018, n_periods=200)
    assert source == "lo2002"


def test_theoretical_var_source_confirms_var_floor_only_as_fallback():
    _, _, _, source = sr0_multiple_testing_robust(
        1e-6, 20, min_cohort=10, var_floor=0.0018, n_periods=None)
    assert source == "var_floor"
