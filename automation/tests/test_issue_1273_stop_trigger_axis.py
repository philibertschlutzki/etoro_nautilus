"""Issue #1273 (GH #1146, Katalog #1272-1297, P0) — Stop-/Target-Trigger-Achse explizit deklarieren
und invariant halten.

Fix.
1. ``optimizer.json['stop_trigger_axis'] ∈ {'intrabar', 'bar_close_only'}``, Default
   'bar_close_only'.
2. Neue Invariante ``check_stop_trigger_axis_coherence``: 'intrabar' bei
   ``zero_range_bar_fraction > 0.5`` (irgendeine Study) ⇒ FAIL 'blocking'.
3. ``stop_distance_bps_modelled`` bleibt als Alias, ``stop_trigger_threshold_bps`` ist der neue,
   semantisch praezise Name (mit Deprecation-Telemetrie)."""
import json

from automation.optimizer import invariants as inv


def _study(strategy, symbol, zero_range_bar_fraction):
    return {"strategy": strategy, "symbol": symbol,
           "zero_range_bar_fraction": zero_range_bar_fraction}


# ---------------------------------------------------------------------------------------------
# invariants.check_stop_trigger_axis_coherence
# ---------------------------------------------------------------------------------------------

def test_none_axis_is_inconclusive():
    r = inv.check_stop_trigger_axis_coherence(None, [])
    assert r.passed is True
    assert r.inconclusive is True
    assert r.severity == "blocking"


def test_bar_close_only_always_passes_regardless_of_measurement():
    r = inv.check_stop_trigger_axis_coherence(
        "bar_close_only", [_study("AdxAtr", "TSLA.ETORO", 1.0)])
    assert r.passed is True
    assert r.inconclusive is False


def test_intrabar_with_degenerate_bar_axis_fails_blocking():
    r = inv.check_stop_trigger_axis_coherence(
        "intrabar", [_study("AdxAtr", "TSLA.ETORO", 1.0)])
    assert r.passed is False
    assert r.severity == "blocking"
    assert "AdxAtr/TSLA.ETORO" in r.actual


def test_intrabar_with_healthy_bar_axis_passes():
    r = inv.check_stop_trigger_axis_coherence(
        "intrabar", [_study("AdxAtr", "TSLA.ETORO", 0.1)])
    assert r.passed is True


def test_single_offender_among_many_still_fails():
    records = [_study("A", "X.ETORO", 0.1), _study("B", "Y.ETORO", 0.6)]
    r = inv.check_stop_trigger_axis_coherence("intrabar", records)
    assert r.passed is False
    assert list(r.actual.keys()) == ["B/Y.ETORO"]


# ---------------------------------------------------------------------------------------------
# report.py wiring — stop_trigger_axis field + check in stream + study alias
# ---------------------------------------------------------------------------------------------

def test_default_config_declares_bar_close_only():
    cfg = json.loads((__import__("pathlib").Path("automation/config/optimizer.json"))
                     .read_text("utf-8"))
    assert cfg.get("stop_trigger_axis") == "bar_close_only"


def test_report_top_level_carries_stop_trigger_axis(tmp_path):
    from automation.optimizer import report as rpt
    report = rpt._build_report(
        [], run_id="run-1273-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    assert report["stop_trigger_axis"] == "bar_close_only"
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_stop_trigger_axis_coherence" in names


def test_study_record_stamps_stop_trigger_threshold_bps_alias():
    from automation.optimizer.report import _study_record

    class _T:
        def __init__(self):
            self.value = 1.0
            self.params = {}
            self.user_attrs = {"atr_median_bps": 10.0, "atr_trailing_multiplier_median": 2.0,
                               "oos_evaluated": True, "oos_eligible": True,
                               "oos_coherence_violation": False}

    class _S:
        def __init__(self):
            self.trials = [_T()]
            self.best_value = 1.0
            self.user_attrs = {}

    proposal = {"symbol": "TSLA.ETORO", "strategy": "AdxAtr"}
    record, _checks = _study_record(proposal, _S())
    # Der neue Name spiegelt IMMER exakt den Alt-Wert -- unabhaengig davon, welche konkrete
    # Zahl die interne ATR-Aggregation fuer diese Fixture liefert (Details dort nicht Gegenstand
    # dieses Tests, siehe stop_distance_bps_modelled-Berechnung).
    assert record["stop_trigger_threshold_bps"] == record["stop_distance_bps_modelled"]
    assert record["stop_distance_bps_modelled_deprecated_alias_of"] == "stop_trigger_threshold_bps"
