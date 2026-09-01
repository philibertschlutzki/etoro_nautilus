"""Issue #1344 (GH #1238) — ein einzelnes abgewiesenes Symbol beendet nicht mehr den gesamten
Lauf: der Preflight-Loop arbeitet die volle Symbolliste ab, sammelt jede Ablehnung namentlich
(``symbols_rejected``), und ein per-Symbol-Preflight-FAIL (``check_tick_population``/
``check_bar_quality``) darf den GESAMTEN Lauf nur dann auf ``completed_invalid`` herabstufen, wenn
am Ende KEIN Symbol übrig geblieben ist.
"""
import json
import logging

from automation.optimizer import sweep


def test_one_degenerate_symbol_does_not_block_the_surviving_symbol(tmp_path, monkeypatch):
    """Akzeptanzkriterium #1344: ein Lauf mit mehreren Symbolen, von denen eines degeneriert ist,
    liefert weiterhin einen dispatchten Trial fuer das gesunde Symbol (der Preflight-Loop bricht
    NICHT beim ersten abgewiesenen Symbol ab, sondern fährt mit den übrigen fort)."""
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)

    events = []

    def _capture(logger, event_type, payload, level=logging.INFO):
        events.append((event_type, payload, level))

    monkeypatch.setattr(sweep, "emit_execution_event", _capture)
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda: ["GOOD.ETORO", "BAD.ETORO"])
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {"walk_forward": {}})
    monkeypatch.setattr(sweep, "count_available_bars", lambda syms, **kw: {})

    def _fake_bar_quality(symbol):
        if symbol == "BAD.ETORO":
            return {
                "highs": [100.0] * 20, "lows": [100.0] * 20, "closes": [100.0] * 20,
                "bar_coverage_ratio": 1.0, "median_delta_t_s": 3600.0,
                "ticks_per_bar_median": 4.0, "ticks_per_bar_p05": 4.0,
                "frac_bars_single_tick": 0.0,
            }
        return {
            "highs": [100.0 + i * 0.3 for i in range(200)],
            "lows": [99.0 + i * 0.3 for i in range(200)],
            "closes": [99.5 + i * 0.31 for i in range(200)],
            "bar_coverage_ratio": 1.0, "median_delta_t_s": 3600.0,
            "ticks_per_bar_median": 4.0, "ticks_per_bar_p05": 4.0,
            "frac_bars_single_tick": 0.0,
        }

    optimize_calls = []

    def _fake_optimize(pair):
        optimize_calls.append(pair)
        return None

    try:
        sweep.run_per_symbol_sweep(
            ["SmaCrossoverStrategy"], ["GOOD.ETORO", "BAD.ETORO"],
            optimize_symbol=_fake_optimize, confirm=lambda *a, **kw: None,
            bar_quality_fn=_fake_bar_quality,
            run_id="test-1344-multi-symbol",
        )
    except Exception:
        pass

    reject_events = [p for (etype, p, _lvl) in events if etype == "REJECT_DATA_DEGENERATE"]
    assert [e["symbol"] for e in reject_events] == ["BAD.ETORO"]

    # GOOD.ETORO ist trotz der BAD.ETORO-Ablehnung NICHT ebenfalls im Bar-Qualitaets-Preflight
    # uebersprungen worden — der Loop verarbeitet beide Symbole, nicht nur das erste abgewiesene.
    bar_quality_stream = [
        p for (etype, p, _lvl) in events
        if etype == "INVARIANT_STREAM_RESULT" and p.get("name") == "check_bar_quality"
    ]
    by_scope = {p["scope"]: p["passed"] for p in bar_quality_stream}
    assert by_scope == {"GOOD.ETORO": True, "BAD.ETORO": False}

    completed_events = [p for (etype, p, _lvl) in events if etype == "sweep_completed"]
    assert completed_events, "kein sweep_completed-Ereignis emittiert"
    rejected = completed_events[0]["symbols_rejected"]
    assert [r["symbol"] for r in rejected] == ["BAD.ETORO"]
    assert rejected[0]["reason"] == "REJECT_DATA_DEGENERATE"


def test_all_symbols_rejected_still_yields_empty_optimize_calls(tmp_path, monkeypatch):
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)

    events = []
    monkeypatch.setattr(
        sweep, "emit_execution_event",
        lambda logger, event_type, payload, level=logging.INFO: events.append((event_type, payload)))
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda: ["ONLY.ETORO"])
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {"walk_forward": {}})
    monkeypatch.setattr(sweep, "count_available_bars", lambda syms, **kw: {})

    optimize_calls = []
    try:
        sweep.run_per_symbol_sweep(
            ["SmaCrossoverStrategy"], ["ONLY.ETORO"],
            optimize_symbol=lambda pair: optimize_calls.append(pair),
            confirm=lambda *a, **kw: None,
            tick_population_fn=lambda symbol: {"n_ticks_raw": 0, "n_ticks_after_session_filter": 0},
            bar_quality_fn=lambda symbol: None,
            run_id="test-1344-all-rejected",
        )
    except Exception:
        pass

    assert optimize_calls == []
    completed_events = [p for (etype, p) in events if etype == "sweep_completed"]
    if completed_events:
        rejected = completed_events[0]["symbols_rejected"]
        assert [r["symbol"] for r in rejected] == ["ONLY.ETORO"]


def test_downgrade_excludes_scoped_preflight_rejection_when_symbols_survived(tmp_path):
    """Kern des Fixes: ein scope-gebundener blockierender check_bar_quality-FAIL fuer EIN Symbol
    darf den Lauf NICHT auf 'completed_invalid' herabstufen, wenn symbols_planned > 0 zeigt, dass
    andere Symbole ueberlebt haben."""
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "run_status": "complete",
        "symbols_planned": 1,
        "symbols_completed": 1,
        "invariant_checks": [
            {"name": "check_bar_quality", "scope": "BAD.ETORO", "severity": "blocking",
             "passed": False},
            {"name": "check_some_other_thing", "scope": None, "severity": "high", "passed": False},
        ],
    }), encoding="utf-8")

    result = sweep._downgrade_run_status_for_blocking_invariants(report_path)
    assert result == "complete"


def test_downgrade_still_invalidates_run_when_no_symbol_survived(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "run_status": "complete",
        "symbols_planned": 0,
        "symbols_completed": 0,
        "invariant_checks": [
            {"name": "check_bar_quality", "scope": "ONLY.ETORO", "severity": "blocking",
             "passed": False},
        ],
    }), encoding="utf-8")

    result = sweep._downgrade_run_status_for_blocking_invariants(report_path)
    assert result == "completed_invalid"


def test_downgrade_still_invalidates_run_for_unscoped_blocking_fail(tmp_path):
    """Ein run-weiter (nicht symbol-gebundener) blockierender FAIL bleibt weiterhin ein
    Herabstufungsgrund, unabhaengig von symbols_planned."""
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "run_status": "complete",
        "symbols_planned": 3,
        "symbols_completed": 3,
        "invariant_checks": [
            {"name": "check_some_run_wide_thing", "scope": None, "severity": "blocking",
             "passed": False},
        ],
    }), encoding="utf-8")

    result = sweep._downgrade_run_status_for_blocking_invariants(report_path)
    assert result == "completed_invalid"
