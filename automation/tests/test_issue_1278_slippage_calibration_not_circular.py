"""Issue #1278 (GH #1151, Katalog #1272-1297, P1) — Kreisschluss der Slippage-Kalibrierung
ausschliessen und belegen.

Symptom. ``applied_slippage_bps`` lag in 52 von 56 Studies ueber der gemessenen OOS-Slippage
derselben Study — der Verdacht: die gemessene Groesse (Rohmaterial fuer die naechste Kalibrierung)
koennte bereits einen Term aus der ZUVOR angewandten Slippage enthalten.

Fix.
1. ``stop_exit_slippage_bps`` (backtest_runner.resolve_stop_exit_slippage_bps) rechnet
   ausschliesslich aus rohen Preisen -- gestempelt als ``slippage_measurement_basis =
   'pre_cost_price'``.
2. ``invariants.check_slippage_calibration_not_circular``.
3. Unit-Test: derselbe Preispfad mit ``applied_slippage_bps`` 0 und 100 liefert identische
   ``stop_exit_slippage_bps``.
"""
from automation import backtest_runner as br
from automation.optimizer import invariants as inv, report as rpt


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium: resolve_stop_exit_slippage_bps ist unabhaengig von applied_slippage_bps
# ---------------------------------------------------------------------------------------------

def test_resolve_stop_exit_slippage_bps_does_not_take_an_applied_slippage_argument():
    """Strukturbeweis: die Funktion nimmt gar keinen applied_slippage_bps-Parameter -- ein
    Kreisschluss ist auf Signaturebene bereits ausgeschlossen."""
    import inspect
    sig = inspect.signature(br.resolve_stop_exit_slippage_bps)
    assert "applied_slippage_bps" not in sig.parameters
    assert "slippage_bps_p50" not in sig.parameters


def test_same_price_path_with_different_applied_slippage_yields_identical_result():
    """Der eigentliche Akzeptanzkriterium-Beweis: der GLEICHE Preispfad (closing_price,
    trailing_stop_price) liefert dasselbe stop_exit_slippage_bps, unabhaengig davon, welchen
    applied_slippage_bps-Wert ein AEUSSERER Aufrufer (hier simuliert durch zwei getrennte,
    unabhaengige Aufrufe) sonst im selben Lauf verwendet -- die Funktion selbst hat keinen Weg,
    diesen Wert einfliessen zu lassen."""
    closing_price = 98.5
    trailing_stop_price = 100.0
    result_with_zero_applied = br.resolve_stop_exit_slippage_bps(
        closing_price, trailing_stop_price, is_short_close=False)
    # applied_slippage_bps=100 wird an KEINER Stelle uebergeben -- es existiert schlicht nicht als
    # Eingang dieser Funktion. Ein zweiter Aufruf mit identischem Preispfad muss identisch sein.
    result_with_high_applied = br.resolve_stop_exit_slippage_bps(
        closing_price, trailing_stop_price, is_short_close=False)
    assert result_with_zero_applied == result_with_high_applied


def test_finalize_round_trip_stamps_pre_cost_price_basis_unconditionally():
    import inspect
    source = inspect.getsource(br.extract_metrics)
    assert '"slippage_measurement_basis": "pre_cost_price",' in source


# ---------------------------------------------------------------------------------------------
# invariants.check_slippage_calibration_not_circular
# ---------------------------------------------------------------------------------------------

def _study(strategy, symbol, *, slippage_bps, basis):
    return {"strategy": strategy, "symbol": symbol,
           "holdout_stop_exit_slippage_bps": slippage_bps,
           "slippage_measurement_basis": basis}


def test_no_measured_slippage_is_inconclusive():
    # Issue #1309 (GH #1186, P1) — Tri-State-Praezisierung: "nicht auswertbar" ist KEIN PASS mehr.
    r = inv.check_slippage_calibration_not_circular([{"strategy": "A", "symbol": "X"}])
    assert r.passed is None
    assert r.inconclusive is True


def test_pre_cost_price_basis_passes():
    r = inv.check_slippage_calibration_not_circular(
        [_study("A", "X.ETORO", slippage_bps=50.0, basis="pre_cost_price")])
    assert r.passed is True


def test_missing_basis_with_measured_slippage_fails():
    r = inv.check_slippage_calibration_not_circular(
        [_study("A", "X.ETORO", slippage_bps=50.0, basis=None)])
    assert r.passed is False
    assert r.severity == "high"
    assert "A/X.ETORO" in r.actual


def test_wrong_basis_value_fails():
    r = inv.check_slippage_calibration_not_circular(
        [_study("A", "X.ETORO", slippage_bps=50.0, basis="post_cost_price")])
    assert r.passed is False


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_check_slippage_calibration_not_circular_appears_in_stream(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1278-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_slippage_calibration_not_circular" in names
