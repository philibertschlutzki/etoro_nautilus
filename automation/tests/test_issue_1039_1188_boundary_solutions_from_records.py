"""Issue #1039/#1188 (P2) — "Randlösungen mit Bounds-Vorschlag: 0" bei drei Studies mit
Bounds-Befund.

Symptom. Abschnitt 5.3 meldete 0; ``cross_study.boundary_solutions = []``. Drei Studies trugen
``winner_outside_default_bounds`` (OpeningRangeBreakout, SqueezeBreakout, TrendPullback), neun
trugen ``boundary_hit_fraction > 0``.

Root-Cause. ``boundary_solutions`` wurde AUSSCHLIESSLICH aus dem #761-Diagnose-Cache gespeist
(nicht-leer nur bei ``diagnostic_writeback_enabled=True``, seit #1090 nicht der Default) — ein
komplett getrennter Pfad von den Study-Feldern desselben Reports.

Fix. ``boundary_solutions`` wird primär aus den Study-Records selbst abgeleitet
(``winner_outside_default_bounds_after_override`` ODER ``boundary_hit_fraction > 0.3``); der
Diagnose-Cache bleibt als zusätzliche Anreicherung (proposed_bounds/widen_applications) erhalten.
Neue Invariante ``check_boundary_solutions_matches_study_records`` bewacht die Konsistenz
dauerhaft.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report


def _study(strategy, symbol, *, override=None, fraction=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "winner_outside_default_bounds_after_override": override,
        "boundary_hit_fraction": fraction,
        "boundary_parameter": next(iter(override), None) if override else None,
        "boundary_side": "low",
        "boundary_veto_evidence": {"p": {"direction": "low"}} if override else None,
    }


# ── report._boundary_solutions_section: primär aus Study-Records ────────────────────────────────
def test_derives_from_strict_override_even_with_an_empty_diagnostic_cache(monkeypatch):
    monkeypatch.setattr(report, "_diagnosed_pairs_all", lambda: [])
    studies = [
        _study("OpeningRangeBreakoutStrategy", "TSLA.ETORO",
               override={"or_bars": [1, [1, 8]]}, fraction=0.1),
        _study("SqueezeBreakoutStrategy", "NVDA.ETORO",
               override={"max_bars_in_trade": [6, [12, 24]]}, fraction=0.111),
        _study("TrendPullbackStrategy", "AAPL.ETORO",
               override={"ema_period": [18, [50, 300]]}, fraction=0.286),
        _study("SmaCrossoverStrategy", "MSFT.ETORO", fraction=0.05),  # kein Bounds-Befund
    ]
    result = report._boundary_solutions_section(studies)
    names = {(r["strategy"], r["symbol"]) for r in result}
    assert names == {
        ("OpeningRangeBreakoutStrategy", "TSLA.ETORO"),
        ("SqueezeBreakoutStrategy", "NVDA.ETORO"),
        ("TrendPullbackStrategy", "AAPL.ETORO"),
    }


def test_derives_from_high_boundary_fraction_even_without_a_strict_override(monkeypatch):
    monkeypatch.setattr(report, "_diagnosed_pairs_all", lambda: [])
    studies = [_study("SmaCrossoverStrategy", "A.ETORO", fraction=0.5)]
    result = report._boundary_solutions_section(studies)
    assert len(result) == 1
    assert result[0]["fraction"] == 0.5


def test_enriches_with_diagnostic_cache_proposed_bounds_when_available(monkeypatch):
    monkeypatch.setattr(report, "_diagnosed_pairs_all", lambda: [{
        "strategy": "TrendPullbackStrategy", "symbol": "AAPL.ETORO",
        "binding_cause": "boundary_solution",
        "proposed_bounds": {"ema_period": [5, 25]},
        "boundary_params": {"ema_period": 18},
        "widen_applications": {"ema_period": 1},
    }])
    studies = [_study("TrendPullbackStrategy", "AAPL.ETORO",
                      override={"ema_period": [18, [50, 300]]}, fraction=0.286)]
    result = report._boundary_solutions_section(studies)
    assert result[0]["proposed_bounds"] == {"ema_period": [5, 25]}
    assert result[0]["params"] == {"ema_period": 18}
    assert result[0]["widen_applications"] == {"ema_period": 1}


def test_legacy_none_studies_out_falls_back_to_diagnostic_cache_only(monkeypatch):
    """Rueckwaertskompat: ``studies_out=None`` bleibt bit-identisch zum Pre-#1039-Verhalten (reiner
    Diagnose-Cache-Pfad, fuer bestehende Direktaufrufer ohne Study-Kontext)."""
    monkeypatch.setattr(report, "_diagnosed_pairs_all", lambda: [{
        "strategy": "S", "symbol": "A.ETORO", "binding_cause": "boundary_solution",
        "boundary_hit_fraction": 0.4,
    }])
    result = report._boundary_solutions_section(None)
    assert len(result) == 1
    assert result[0]["strategy"] == "S"


# ── invariants.check_boundary_solutions_matches_study_records ───────────────────────────────────
def test_check_fails_when_solutions_empty_but_a_study_carries_override():
    result = inv.check_boundary_solutions_matches_study_records(
        [], [_study("TrendPullbackStrategy", "AAPL.ETORO", override={"ema_period": [18, [50, 300]]})])
    assert result.passed is False
    assert result.severity == "medium"
    assert "TrendPullbackStrategy/AAPL.ETORO" in result.detail


def test_check_passes_when_no_study_carries_an_override():
    result = inv.check_boundary_solutions_matches_study_records(
        [], [_study("S", "A.ETORO", fraction=0.05)])
    assert result.passed is True


def test_check_passes_when_solutions_nonempty_and_coherent():
    result = inv.check_boundary_solutions_matches_study_records(
        [{"strategy": "T", "symbol": "A.ETORO"}],
        [_study("T", "A.ETORO", override={"x": [1, [2, 3]]})])
    assert result.passed is True
