"""Issue #1276 (GH #1149, Katalog #1272-1297, P0) — slippage_p50_bps_calibrated auf der Ebene
stempeln, die auch angewandt wurde.

Symptom. ``report.py`` stempelte ``slippage_p50_bps_calibrated`` ausschliesslich asset-class-weit
— identisch in allen 14 Studies eines Laufs — waehrend ``applied_slippage_bps`` je Study um Faktor
11,5 streute (23,25 bis 267,11) und ``slippage_calibration_scope`` je Study 'strategy_symbol'
meldete.

Fix.
1. ``report._resolve_slippage_p50_calibrated`` — dieselbe Fallback-Kette wie
   ``backtest_runner.resolve_slippage_bps`` (by_strategy_symbol -> by_symbol -> asset_class).
2. ``slippage_p50_calibration_scope`` je Study gestempelt.
3. ``invariants.check_slippage_scope_agreement``.
"""
from automation.optimizer import invariants as inv, report as rpt


def _entry(*, asset_p50=78.4052, by_symbol=None, by_strategy_symbol=None):
    return {"p50": asset_p50, "n_observations": 500,
           "by_symbol": by_symbol or {}, "by_strategy_symbol": by_strategy_symbol or {}}


# ---------------------------------------------------------------------------------------------
# report._resolve_slippage_p50_calibrated
# ---------------------------------------------------------------------------------------------

def test_falls_back_to_asset_class_when_no_finer_data():
    p50, scope = rpt._resolve_slippage_p50_calibrated(_entry(), "TSLA.ETORO", "OpeningRange")
    assert p50 == 78.4052
    assert scope == "asset_class"


def test_prefers_symbol_level_when_sufficient_observations():
    entry = _entry(by_symbol={"TSLA.ETORO": {"p50": 23.25, "n_observations": 50}})
    p50, scope = rpt._resolve_slippage_p50_calibrated(entry, "TSLA.ETORO", "OpeningRange")
    assert p50 == 23.25
    assert scope == "symbol"


def test_prefers_strategy_symbol_level_above_symbol_level():
    entry = _entry(
        by_symbol={"TSLA.ETORO": {"p50": 23.25, "n_observations": 50}},
        by_strategy_symbol={"OpeningRange|TSLA.ETORO": {"p50": 267.11, "n_observations": 40}},
    )
    p50, scope = rpt._resolve_slippage_p50_calibrated(entry, "TSLA.ETORO", "OpeningRange")
    assert p50 == 267.11
    assert scope == "strategy_symbol"


def test_insufficient_observations_falls_through_to_coarser_level():
    entry = _entry(
        by_symbol={"TSLA.ETORO": {"p50": 23.25, "n_observations": 5}},  # < 30, ignoriert
        by_strategy_symbol={"OpeningRange|TSLA.ETORO": {"p50": 267.11, "n_observations": 2}},
    )
    p50, scope = rpt._resolve_slippage_p50_calibrated(entry, "TSLA.ETORO", "OpeningRange")
    assert p50 == 78.4052
    assert scope == "asset_class"


def test_none_entry_returns_none_and_asset_class_scope():
    p50, scope = rpt._resolve_slippage_p50_calibrated(None, "TSLA.ETORO", "OpeningRange")
    assert p50 is None
    assert scope == "asset_class"


def test_varies_per_study_within_a_run():
    """Reproduziert das Katalog-Symptom: unterschiedliche Studies desselben Laufs muessen
    unterschiedliche p50 erhalten koennen, wenn ihre Symbole/Strategien feinere Kalibrierung
    tragen."""
    entry = _entry(
        by_symbol={"TSLA.ETORO": {"p50": 23.25, "n_observations": 50}},
        by_strategy_symbol={"Vwap|TSLA.ETORO": {"p50": 267.11, "n_observations": 40}},
    )
    p50_opening_range, _ = rpt._resolve_slippage_p50_calibrated(entry, "TSLA.ETORO", "OpeningRange")
    p50_vwap, _ = rpt._resolve_slippage_p50_calibrated(entry, "TSLA.ETORO", "Vwap")
    assert p50_opening_range != p50_vwap


# ---------------------------------------------------------------------------------------------
# invariants.check_slippage_scope_agreement
# ---------------------------------------------------------------------------------------------

def _study(strategy, symbol, p50_scope, applied_scope):
    return {"strategy": strategy, "symbol": symbol,
           "slippage_p50_calibration_scope": p50_scope,
           "slippage_calibration_scope": applied_scope}


def test_no_studies_with_both_fields_is_inconclusive():
    r = inv.check_slippage_scope_agreement([{"strategy": "A", "symbol": "X"}])
    assert r.passed is True
    assert r.inconclusive is True


def test_matching_scopes_pass():
    r = inv.check_slippage_scope_agreement(
        [_study("Vwap", "TSLA.ETORO", "strategy_symbol", "strategy_symbol")])
    assert r.passed is True


def test_mismatched_scopes_fail():
    r = inv.check_slippage_scope_agreement(
        [_study("Vwap", "TSLA.ETORO", "asset_class", "strategy_symbol")])
    assert r.passed is False
    assert r.severity == "high"
    assert "Vwap/TSLA.ETORO" in r.actual


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_check_slippage_scope_agreement_appears_in_stream(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1276-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_slippage_scope_agreement" in names
