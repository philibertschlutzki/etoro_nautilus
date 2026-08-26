"""Issue #1277 (GH #1150, Katalog #1272-1297, P0) — Kalibrierte Slippage nur dann in den
Selektionspfad, wenn die Bar-Achse sie tragen kann.

Symptom. Seit #1078/#1226 wird die gemessene Fill-Slippage AN DER QUELLE vom Trial abgezogen,
unbedingt (kein Bar-Achsen-Vorbehalt). Bei einer Bar-Achse ohne Intrabar-Information (#1272) ist
diese "gemessene Ausfuehrungsgroesse" ein Bar-Achsen-Artefakt.

Fix.
1. ``backtest_runner._bar_axis_supports_stop_verdict_from_exit_meta`` — dieselbe Vorbedingung wie
   ``invariants._bar_axis_supports_stop_verdict`` (#1274), auf rt_exit_meta EINER Study.
2. Fail-closed auf ``selection_cost_basis='round_trip_only'`` mit
   ``selection_cost_basis_downgrade_reason='BAR_AXIS_DEGENERATE'``.
3. ``invariants.check_selection_cost_basis_admissible``.
"""
import inspect

from automation import backtest_runner
from automation.optimizer import invariants as inv, report as rpt


def _meta(*, population=100, zero_frac=0.1):
    return {"bar_range_population_n": population, "zero_range_bar_fraction": zero_frac}


# ---------------------------------------------------------------------------------------------
# backtest_runner._bar_axis_supports_stop_verdict_from_exit_meta
# ---------------------------------------------------------------------------------------------

def test_healthy_axis_supports_selection_cost():
    assert backtest_runner._bar_axis_supports_stop_verdict_from_exit_meta(
        [_meta(), _meta()]) is True


def test_confirmed_zero_population_does_not_support():
    assert backtest_runner._bar_axis_supports_stop_verdict_from_exit_meta(
        [_meta(population=0, zero_frac=1.0)]) is False


def test_high_zero_range_fraction_does_not_support():
    assert backtest_runner._bar_axis_supports_stop_verdict_from_exit_meta(
        [_meta(zero_frac=0.9), _meta(zero_frac=0.8)]) is False


def test_no_telemetry_is_fail_closed():
    assert backtest_runner._bar_axis_supports_stop_verdict_from_exit_meta([{}]) is False


def test_matches_the_invariants_report_side_criterion_exactly():
    """Dieselbe Logik wie invariants._bar_axis_supports_stop_verdict, nur auf rt_exit_meta statt
    study_records -- beide muessen fuer AEQUIVALENTE Eingaben dasselbe Urteil liefern."""
    records = [{"bar_range_population_n": 0, "zero_range_bar_fraction": 1.0}]
    meta = [_meta(population=0, zero_frac=1.0)]
    assert (inv._bar_axis_supports_stop_verdict(records)
           == backtest_runner._bar_axis_supports_stop_verdict_from_exit_meta(meta))


# ---------------------------------------------------------------------------------------------
# extract_metrics wiring — Strukturbeweis (analog test_issue_1078_1226s Konvention)
# ---------------------------------------------------------------------------------------------

def test_extract_metrics_gates_deduction_behind_bar_axis_check_too():
    source = inspect.getsource(backtest_runner.extract_metrics)
    assert "_bar_axis_supports_stop_verdict_from_exit_meta(" in source
    idx_gate = source.index(
        "if _read_apply_calibrated_slippage_in_selection() and "
        "_bar_axis_supports_selection_cost:")
    idx_call = source.index(
        "rt_pnls_with_ts, n_slippage_adjusted_round_trips = _apply_calibrated_slippage_deduction(")
    assert idx_gate < idx_call


def test_extract_metrics_stamps_downgrade_reason_on_both_levels():
    source = inspect.getsource(backtest_runner.extract_metrics)
    assert 'is_metrics["selection_cost_basis_downgrade_reason"] = selection_cost_basis_downgrade_reason' in source
    assert 'oos_metrics["selection_cost_basis_downgrade_reason"] = selection_cost_basis_downgrade_reason' in source


def test_mtm_series_adjustment_shares_the_same_bar_axis_gate():
    source = inspect.getsource(backtest_runner.extract_metrics)
    assert ("if _read_apply_calibrated_slippage_in_selection() and "
           "_bar_axis_supports_selection_cost:\n            mtm_series, "
           "n_slippage_adjusted_mtm_events") in source


# ---------------------------------------------------------------------------------------------
# invariants.check_selection_cost_basis_admissible
# ---------------------------------------------------------------------------------------------

def _study(strategy, symbol, *, cost_basis, population=100, zero_frac=0.1):
    return {"strategy": strategy, "symbol": symbol, "selection_cost_basis": cost_basis,
           "bar_range_population_n": population, "zero_range_bar_fraction": zero_frac}


def test_passes_when_bar_axis_healthy_regardless_of_cost_basis():
    r = inv.check_selection_cost_basis_admissible(
        [_study("A", "X.ETORO", cost_basis="round_trip_plus_calibrated_slippage")])
    assert r.passed is True


def test_fails_when_degenerate_axis_and_calibrated_slippage_used():
    r = inv.check_selection_cost_basis_admissible([
        _study("A", "X.ETORO", cost_basis="round_trip_plus_calibrated_slippage",
              population=0, zero_frac=1.0),
    ])
    assert r.passed is False
    assert r.severity == "blocking"
    assert "A/X.ETORO" in r.actual


def test_passes_when_degenerate_axis_but_round_trip_only():
    r = inv.check_selection_cost_basis_admissible([
        _study("A", "X.ETORO", cost_basis="round_trip_only", population=0, zero_frac=1.0),
    ])
    assert r.passed is True


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_check_selection_cost_basis_admissible_appears_in_stream(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1277-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_selection_cost_basis_admissible" in names
