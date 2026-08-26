"""Issue #1274 (GH #1147, Katalog #1272-1297, P0) — Stop-Invarianten bei degenerierter Bar-Achse
unterdruecken statt urteilen lassen.

Symptom. Vier Pruefungen sprachen ein eigenes Urteil ueber die Stop-MECHANIK, obwohl ihre
Eingangsgroesse aus einer Bar-Achse ohne Intrabar-Information stammte: check_effective_stop_distance
(blocking), check_trailing_stop_loss_share (blocking), check_trailing_stop_risk_calibration_
acceptance (high), check_stop_distance_model_fidelity (high).

Fix.
1. ``invariants._bar_axis_supports_stop_verdict(study_records) -> bool``.
2. ``invariants.suppress_stop_verdict_if_bar_axis_degenerate`` — passed=None mit
   detail='SUPPRESSED_UPSTREAM_BAR_AXIS (#1272/#1274)' statt eines urspruenglichen FAIL/PASS.
3. 'check_bar_quality' zu optimizer.json['fail_fast_invariants'] ergaenzt.
"""
import json

from automation.optimizer import invariants as inv


def _study(strategy="A", symbol="X.ETORO", *, population=100, zero_frac=0.1):
    return {"strategy": strategy, "symbol": symbol,
           "bar_range_population_n": population, "zero_range_bar_fraction": zero_frac}


# ---------------------------------------------------------------------------------------------
# _bar_axis_supports_stop_verdict
# ---------------------------------------------------------------------------------------------

def test_healthy_bar_axis_supports_verdict():
    assert inv._bar_axis_supports_stop_verdict([_study()]) is True


def test_confirmed_zero_population_does_not_support_verdict():
    assert inv._bar_axis_supports_stop_verdict(
        [_study(population=0, zero_frac=1.0)]) is False


def test_high_zero_range_fraction_median_does_not_support_verdict():
    records = [_study(zero_frac=0.9), _study(symbol="Y.ETORO", zero_frac=0.8)]
    assert inv._bar_axis_supports_stop_verdict(records) is False


def test_no_telemetry_at_all_is_fail_closed():
    assert inv._bar_axis_supports_stop_verdict([{"strategy": "A", "symbol": "X"}]) is False


def test_mixed_studies_median_governs():
    # Median(0.1, 0.2, 0.9) = 0.2 <= 0.5 -> unterstuetzt trotz eines einzelnen hohen Ausreissers.
    records = [_study(zero_frac=0.1), _study(symbol="Y.ETORO", zero_frac=0.2),
              _study(symbol="Z.ETORO", zero_frac=0.9)]
    assert inv._bar_axis_supports_stop_verdict(records) is True


# ---------------------------------------------------------------------------------------------
# suppress_stop_verdict_if_bar_axis_degenerate
# ---------------------------------------------------------------------------------------------

def test_suppresses_a_fail_under_degenerate_bar_axis():
    original = inv.InvariantResult(
        name="check_effective_stop_distance", passed=False, expected="x", actual={"a": 1},
        severity="blocking", detail="original FAIL detail")
    records = [_study(population=0, zero_frac=1.0)]
    result = inv.suppress_stop_verdict_if_bar_axis_degenerate(original, records)
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False
    assert result.name == "check_effective_stop_distance"
    assert result.severity == "blocking"
    assert "SUPPRESSED_UPSTREAM_BAR_AXIS" in result.detail


def test_passes_through_unchanged_when_bar_axis_is_healthy():
    original = inv.InvariantResult(
        name="check_trailing_stop_loss_share", passed=False, expected="x", actual={"a": 1},
        severity="blocking", detail="a real, non-suppressed FAIL")
    records = [_study()]
    result = inv.suppress_stop_verdict_if_bar_axis_degenerate(original, records)
    assert result is original


def test_a_real_pass_is_also_left_unchanged():
    original = inv.InvariantResult(
        name="check_stop_distance_model_fidelity", passed=True, expected="x", actual=None,
        severity="high", detail="OK")
    result = inv.suppress_stop_verdict_if_bar_axis_degenerate(original, [_study()])
    assert result is original


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_report_suppresses_all_four_checks_under_degenerate_bar_axis(tmp_path, monkeypatch):
    from automation.optimizer import report as rpt

    proposal = {
        "strategy": "AdxAtr", "symbol": "TSLA.ETORO", "status": "REJECT_HOLDOUT_GATE",
        "reward": 1.0, "proposed_instrument_override": {}, "R_symbol": 1.0, "R_global": 1.0,
        "promotion_margin": 0.0, "n_trials": 0, "n_eligible": 0,
    }
    report = rpt._build_report(
        [proposal], run_id="run-1274-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    by_name = {c.get("check") or c.get("name"): c for c in report["invariant_checks"]}
    for name in ("check_effective_stop_distance", "check_trailing_stop_loss_share",
                "check_trailing_stop_risk_calibration_acceptance",
                "check_stop_distance_model_fidelity"):
        assert name in by_name, f"{name} fehlt im Strom"
        # Ohne jede Study-Telemetrie ist die Bar-Achse fail-closed nicht unterstuetzend ->
        # entweder bereits INCONCLUSIVE aus dem Original-Check selbst, oder SUPPRESSED.
        assert by_name[name]["passed"] is not False


def test_fail_fast_invariants_config_lists_check_bar_quality():
    cfg = json.loads(
        __import__("pathlib").Path("automation/config/optimizer.json").read_text("utf-8"))
    assert "check_bar_quality" in cfg["fail_fast_invariants"]
    assert "check_effective_stop_distance" in cfg["fail_fast_invariants"]


def test_first_failing_fail_fast_invariant_picks_up_bar_quality_fail():
    from automation.optimizer.sweep import _first_failing_fail_fast_invariant
    invariant_checks = [
        {"name": "check_bar_quality", "passed": False},
        {"name": "check_effective_stop_distance", "passed": None},  # SUPPRESSED, not a trigger
    ]
    fail_fast_invariants = ["check_holding_time_cap", "check_effective_stop_distance",
                            "check_bar_quality"]
    triggered = _first_failing_fail_fast_invariant(invariant_checks, fail_fast_invariants)
    assert triggered == "check_bar_quality"
