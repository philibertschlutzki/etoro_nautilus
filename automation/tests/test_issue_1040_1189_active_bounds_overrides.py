"""Issue #1040/#1189 (P2) — Aktive Bounds-Overrides sind im Report nicht inventarisiert.

Symptom. ``boundary_veto_evidence`` belegt aktive Overrides indirekt (TrendPullback ``ema_period``
mit ``active_bounds [5, 25]`` gegen ``default_bounds [50, 300]``), aber der Report meldet
gleichzeitig ``diagnosed_pairs: []``/"Automatisch denylistete Paare: 0" -- ein Leser kann nicht
erkennen, dass ein Suchraum ueberhaupt geweitet wurde.

Fix. ``bounds.active_bounds_overrides()`` inventarisiert BEIDE Quellen (kuratiert:
``search_space_overrides.json``; automatisch: der #761-Diagnose-Cache), je (Strategie, Symbol,
Parameter) mit ``{active_bounds, default_bounds, source, set_in_run_id, rationale}``. ``report.py``
exportiert das als ``cross_study.active_bounds_overrides``; ``summary_de.py`` zeigt es als eigenen
Abschnitt 5.4, nur wenn nicht leer.
"""
from automation.optimizer import bounds
from automation.optimizer import summary_de


# ── bounds.active_bounds_overrides: kuratierte Quelle (echte search_space_overrides.json) ────────
def test_curated_override_lists_trendpullback_ema_period():
    """Akzeptanzkriterium #1040/1 -- TrendPullbackStrategy.ema_period muss erscheinen (echte,
    ausgelieferte search_space_overrides.json traegt genau diesen Eintrag fuer TSLA.ETORO)."""
    overrides = bounds.active_bounds_overrides()
    matches = [o for o in overrides
              if o["strategy"] == "TrendPullbackStrategy" and o["parameter"] == "ema_period"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["symbol"] == "TSLA.ETORO"
    assert entry["active_bounds"] == [5, 25]
    assert entry["default_bounds"] == [50, 300]
    assert entry["source"] == "curated"
    # Akzeptanzkriterium #1040/2 -- Herkunft (Datei) ist aufloesbar.
    assert entry["set_in_run_id"] is None
    assert entry["rationale"] is not None and len(entry["rationale"]) > 0


def test_curated_overrides_cover_every_documented_symbol_pair():
    """Alle sieben in search_space_overrides.json dokumentierten Strategien tragen mindestens
    einen Eintrag."""
    overrides = bounds.active_bounds_overrides()
    curated_strategies = {o["strategy"] for o in overrides if o["source"] == "curated"}
    assert curated_strategies == {
        "VwapExhaustionStrategy", "HourlyMeanReversionStrategy", "FlashCrashReversalStrategy",
        "TrendPullbackStrategy", "AdxAtrMomentumStrategy", "SqueezeBreakoutStrategy",
        "OpeningRangeBreakoutStrategy",
    }


# ── bounds.active_bounds_overrides: automatische Quelle (#761-Diagnose-Cache) ────────────────────
def test_auto_proposed_override_carries_run_id_and_rationale(monkeypatch):
    from automation.optimizer import sweep_diagnostics as sd
    monkeypatch.setattr(sd, "load_diagnosed_pairs_cache", lambda: {
        ("SqueezeBreakoutStrategy", "NVDA.ETORO"): {
            "action": "search_space_override", "binding_cause": "boundary_solution",
            "proposed_bounds": {"max_bars_in_trade": [4, 18]},
            "first_seen_run_id": "run-abc123",
        },
    })
    overrides = bounds.active_bounds_overrides()
    matches = [o for o in overrides
              if o["strategy"] == "SqueezeBreakoutStrategy" and o["parameter"] == "max_bars_in_trade"
              and o["source"] == "auto_proposed"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["symbol"] == "NVDA.ETORO"
    assert entry["active_bounds"] == [4, 18]
    assert entry["set_in_run_id"] == "run-abc123"
    assert entry["rationale"] == "boundary_solution"


def test_empty_when_neither_source_contributes(monkeypatch):
    from automation.optimizer import spaces as sp
    from automation.optimizer import sweep_diagnostics as sd
    monkeypatch.setattr(sp, "_load_search_space_overrides", lambda: {})
    monkeypatch.setattr(sd, "load_diagnosed_pairs_cache", lambda: {})
    assert bounds.active_bounds_overrides() == []


# ── report.py wiring ──────────────────────────────────────────────────────────────────────────
def test_report_exports_active_bounds_overrides_in_cross_study(tmp_path, monkeypatch):
    from automation.optimizer import report
    out_path = report.generate_sweep_report(
        [], run_id="activebounds1", reports_dir=tmp_path / "reports",
    )
    import json
    data = json.loads(out_path.read_text("utf-8"))
    assert "active_bounds_overrides" in data["cross_study"]
    names = {(o["strategy"], o["parameter"]) for o in data["cross_study"]["active_bounds_overrides"]}
    assert ("TrendPullbackStrategy", "ema_period") in names


# ── summary_de.py: eigener Abschnitt, nur wenn nicht leer ────────────────────────────────────────
def _minimal_report(**overrides):
    base = {
        "run_id": "run-1", "run_status": "complete",
        "started_at_utc": "2026-08-19T00:00:00Z", "wallclock_s": 10.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "symbols_completed": 1, "symbols_planned": 1,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


def test_section_5_4_shown_when_overrides_present():
    report = _minimal_report(cross_study={
        "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
        "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        "active_bounds_overrides": [{
            "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO", "parameter": "ema_period",
            "active_bounds": [5, 25], "default_bounds": [50, 300], "source": "curated",
            "set_in_run_id": None, "rationale": "Kalibrierlauf X",
        }],
    })
    text = summary_de.generate_german_summary(report)
    assert "### 5.4 Aktive Suchraum-Overrides (1)" in text
    assert "TrendPullbackStrategy" in text
    assert "ema_period" in text


def test_section_5_4_omitted_when_empty():
    report = _minimal_report()
    text = summary_de.generate_german_summary(report)
    assert "5.4 Aktive Suchraum-Overrides" not in text
