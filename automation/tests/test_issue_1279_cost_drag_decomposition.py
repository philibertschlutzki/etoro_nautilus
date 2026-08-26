"""Issue #1279 (GH #1152, Katalog #1272-1297, P1) — Brutto/Netto-Kostendrag als Erstklass-Groesse
ausweisen.

Symptom. Die oekonomisch entscheidende Zahl des Batches (der Kostendrag zwischen
``holdout_total_return_gross`` und ``_net``, 0,68 bis 23,88 pp) existierte in keinem Feld und
keiner Report-Sektion.

Fix.
1. Neue Study-Felder: ``holdout_cost_drag_pct``, ``holdout_cost_drag_bps_per_round_trip``,
   ``holdout_cost_drag_component_round_trip_bps``/``_slippage_bps``/``_financing_bps``.
2. ``invariants.check_cost_drag_decomposition`` — die drei Komponenten summieren sich (Toleranz
   5 %).
3. Neue Report-Sektion 2.5 "Kostendrag je Study".
"""
from automation.optimizer import invariants as inv, report as rpt


def _record(**overrides):
    base = {
        "strategy": "AdxAtr", "symbol": "TSLA.ETORO",
        "holdout_total_return_gross": 0.554, "holdout_total_return_net": -12.479,
        "holdout_total_trades": 62, "round_trip_cost_bps": 3.0,
        "applied_slippage_bps": 110.81, "applied_financing_bps_per_day": 0.0,
        "median_bars_held": 5.0, "symbol_bar_quality": {"median_delta_t_s": 3600.0},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------------------------
# report._stamp_cost_drag_decomposition
# ---------------------------------------------------------------------------------------------

def test_reproduces_the_catalog_reference_example():
    """AdxAtr/TSLA 3e792e68: gross +0.554%, net -12.479% -> Drag 13.03 pp."""
    records = [_record()]
    rpt._stamp_cost_drag_decomposition(records)
    r = records[0]
    assert r["holdout_cost_drag_pct"] == round(0.554 - (-12.479), 4)
    assert r["holdout_cost_drag_pct"] == pytest_approx(13.033)
    assert r["holdout_cost_drag_component_round_trip_bps"] == 3.0
    assert r["holdout_cost_drag_component_slippage_bps"] == 110.81
    assert r["holdout_cost_drag_component_financing_bps"] == 0.0


def test_bps_per_round_trip_scales_by_trade_count():
    records = [_record(holdout_total_trades=100)]
    rpt._stamp_cost_drag_decomposition(records)
    r = records[0]
    # 13.033 pp * 100 / 100 trades = 13.033 bps/round-trip.
    assert r["holdout_cost_drag_bps_per_round_trip"] == pytest_approx(13.033)


def test_none_trades_leaves_bps_per_round_trip_none():
    records = [_record(holdout_total_trades=None)]
    rpt._stamp_cost_drag_decomposition(records)
    assert records[0]["holdout_cost_drag_bps_per_round_trip"] is None


def test_missing_gross_or_net_leaves_drag_pct_none():
    records = [_record(holdout_total_return_gross=None)]
    rpt._stamp_cost_drag_decomposition(records)
    assert records[0]["holdout_cost_drag_pct"] is None


def test_financing_component_scales_with_holding_days():
    records = [_record(applied_financing_bps_per_day=2.0, median_bars_held=24.0,
                       symbol_bar_quality={"median_delta_t_s": 3600.0})]
    rpt._stamp_cost_drag_decomposition(records)
    # 24 Bars * 3600s / 86400 = 1.0 Tag -> 2.0 bps/Tag * 1.0 Tag = 2.0 bps.
    assert records[0]["holdout_cost_drag_component_financing_bps"] == 2.0


# ---------------------------------------------------------------------------------------------
# invariants.check_cost_drag_decomposition
# ---------------------------------------------------------------------------------------------

def _decomposed(measured, rt, slip, fin):
    return {
        "strategy": "A", "symbol": "X.ETORO",
        "holdout_cost_drag_bps_per_round_trip": measured,
        "holdout_cost_drag_component_round_trip_bps": rt,
        "holdout_cost_drag_component_slippage_bps": slip,
        "holdout_cost_drag_component_financing_bps": fin,
    }


def test_no_telemetry_is_inconclusive():
    r = inv.check_cost_drag_decomposition([{"strategy": "A", "symbol": "X"}])
    assert r.passed is True
    assert r.inconclusive is True


def test_matching_sum_passes():
    r = inv.check_cost_drag_decomposition([_decomposed(113.81, 3.0, 110.81, 0.0)])
    assert r.passed is True


def test_within_tolerance_passes():
    # 4% off -> within the 5% default tolerance.
    r = inv.check_cost_drag_decomposition([_decomposed(100.0, 3.0, 93.0, 0.0)])
    assert r.passed is True


def test_beyond_tolerance_fails():
    r = inv.check_cost_drag_decomposition([_decomposed(100.0, 3.0, 50.0, 0.0)])
    assert r.passed is False
    assert r.severity == "high"
    assert "A/X.ETORO" in r.actual


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_check_cost_drag_decomposition_appears_in_stream(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1279-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_cost_drag_decomposition" in names


def pytest_approx(x):
    import pytest as _pytest
    return _pytest.approx(x, abs=1e-3)
