"""Issue #1067/#1217 Fix Punkt 3 — invariants.check_search_overhead_share.

FAIL bei (Σ tpe_fit_seconds + store_scan_seconds) / Σ study_wallclock_s > 0,5 — die Suche
verbringt dann mehr als die Haelfte ihrer Wanduhrzeit mit Sampler-/Store-Overhead statt mit
tatsaechlichen Backtests (Symptom B-12).
"""
from automation.optimizer import invariants as inv


def _study(wallclock, fit_seconds):
    return {"strategy": "A", "symbol": "X.ETORO", "study_wallclock_s": wallclock,
            "tpe_fit_seconds": fit_seconds}


def test_passes_when_overhead_is_a_small_fraction():
    result = inv.check_search_overhead_share([_study(3600.0, 100.0)], store_scan_seconds=50.0)
    assert result.passed is True
    assert result.actual["overhead_fraction"] < 0.5


def test_fails_when_overhead_exceeds_half_of_wallclock():
    """Reproduziert das B-12-Muster: TPE-Fit-Zeit dominiert die Study-Wallclock."""
    result = inv.check_search_overhead_share([_study(3600.0, 2000.0)], store_scan_seconds=0.0)
    assert result.passed is False
    assert result.actual["overhead_fraction"] > 0.5


def test_aggregates_across_multiple_studies():
    studies = [_study(1000.0, 100.0), _study(1000.0, 100.0), _study(1000.0, 100.0)]
    result = inv.check_search_overhead_share(studies, store_scan_seconds=300.0)
    # Σfit=300, store_scan=300, Σwallclock=3000 -> 600/3000=0.2
    assert result.actual["overhead_fraction"] == 0.2
    assert result.actual["total_tpe_fit_seconds"] == 300.0
    assert result.passed is True


def test_store_scan_seconds_contributes_to_the_numerator():
    result = inv.check_search_overhead_share([_study(1000.0, 100.0)], store_scan_seconds=600.0)
    # (100+600)/1000 = 0.7 > 0.5
    assert result.passed is False


def test_inconclusive_without_any_tpe_fit_telemetry():
    """Ein Pre-#1217-Report oder ein NSGAIISampler-Lauf ('pareto'-Modus) traegt keine tpe_fit_
    seconds -- INCONCLUSIVE, kein FAIL auf einer nicht gemessenen Groesse."""
    result = inv.check_search_overhead_share(
        [{"strategy": "A", "symbol": "X.ETORO", "study_wallclock_s": 1000.0}])
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


def test_inconclusive_when_total_wallclock_is_zero():
    result = inv.check_search_overhead_share([_study(0.0, 5.0)])
    assert result.passed is None
    assert result.inconclusive is True


def test_custom_threshold_is_respected():
    result = inv.check_search_overhead_share(
        [_study(1000.0, 250.0)], store_scan_seconds=0.0, max_overhead_fraction=0.2)
    assert result.passed is False  # 0.25 > 0.2
