"""Issue #1292 (GH #1165, Katalog #1272-1297, P2) — check_cost_stress_distinctness erwartet ein
Delta aus der falschen Slippage-Ebene.

Symptom. Einziger FAIL in 3e792e68: OpeningRange/TSLA mit actual_delta 0,002325 gegen
min_expected_delta 0,002553, hergeleitet aus slippage_p50_bps_calibrated = 78,4052 (asset-class-
weit). Die tatsaechlich auf diese Study angewandte Slippage betrug applied_slippage_bps = 23,25 —
Faktor 3,4 niedriger.

Fix.
1. Erwartung aus ``applied_slippage_bps`` DERSELBEN Study bilden, ``slippage_p50_bps_calibrated``
   nur als Ruckfall (mit ``expectation_basis``-Telemetrie).
2. ``holdout_trailing_stop_exit_share`` gegen 1 gedeckelt.
"""
from automation.optimizer import invariants as inv


def _study(*, actual_delta, applied_slippage=None, slippage_p50=None,
          n_ts_exits=65, n_total=100, min_delta_coefficient=0.5):
    base_expectancy = 1.0
    return {
        "strategy": "OpeningRange", "symbol": "TSLA.ETORO",
        "holdout_total_trades": n_total,
        "holdout_expectancy_capital_weighted": base_expectancy,
        "holdout_expectancy_cost_stress_full_realism": base_expectancy - actual_delta,
        "applied_slippage_bps": applied_slippage,
        "slippage_p50_bps_calibrated": slippage_p50,
        "holdout_n_trailing_stop_exits": n_ts_exits,
    }


def test_reference_symptom_no_longer_fails_using_applied_slippage_bps():
    """Reproduziert 3e792e68/OpeningRange-TSLA: mit applied_slippage_bps=23,25 statt des
    asset-class-weiten p50=78,4052 verschwindet der FAIL."""
    record = _study(actual_delta=0.002325, applied_slippage=23.25, slippage_p50=78.4052,
                    n_ts_exits=65.12, n_total=100)
    result = inv.check_cost_stress_distinctness([record])
    assert result.passed is True


def test_falls_back_to_slippage_p50_when_applied_slippage_missing():
    record = _study(actual_delta=0.01, applied_slippage=None, slippage_p50=10.0,
                    n_ts_exits=50, n_total=100)
    result = inv.check_cost_stress_distinctness([record])
    # min_expected = 0.5 * (10/10000) * 0.5 = 0.00025 <= 0.01 -> passes, using the fallback basis.
    assert result.passed is True


def test_expectation_basis_recorded_as_applied_slippage_when_offending():
    record = _study(actual_delta=0.0001, applied_slippage=1000.0, slippage_p50=None,
                    n_ts_exits=90, n_total=100)
    result = inv.check_cost_stress_distinctness([record])
    assert result.passed is False
    offender = list(result.provenance["delta_offenders"].values())[0]
    assert offender["expectation_basis"] == "applied_slippage_bps"


def test_expectation_basis_recorded_as_slippage_p50_when_used_as_fallback():
    record = _study(actual_delta=0.0001, applied_slippage=None, slippage_p50=1000.0,
                    n_ts_exits=90, n_total=100)
    result = inv.check_cost_stress_distinctness([record])
    assert result.passed is False
    offender = list(result.provenance["delta_offenders"].values())[0]
    assert offender["expectation_basis"] == "slippage_p50_bps_calibrated"


def test_trailing_stop_exit_share_is_capped_at_one():
    # n_ts_exits > n_total -- ein zaehlbasisbedingtes Artefakt, das den Anteil nicht ueber 1
    # treiben darf.
    record = _study(actual_delta=0.02, applied_slippage=100.0, n_ts_exits=150, n_total=100)
    result = inv.check_cost_stress_distinctness([record])
    # min_expected = 0.5 * 0.01 * 1.0 (gedeckelt) = 0.005 <= 0.02 -> passes.
    assert result.passed is True


def test_calibration_availability_counts_either_source():
    record = _study(actual_delta=0.5, applied_slippage=5.0, slippage_p50=None,
                    n_ts_exits=10, n_total=100)
    result = inv.check_cost_stress_distinctness([record])
    assert result.evaluability["n_studies_with_calibration"] == 1
