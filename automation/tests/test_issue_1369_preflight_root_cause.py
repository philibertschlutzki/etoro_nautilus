"""Issue #1369 (GH #1266, P2) — ein Wurzelbefund, ein Satz: eine Totalabweisung im Preflight erzeugte eine
FAIL-Kaskade (``check_fail_fast_inconclusive_budget`` FAIL, 9 blockierende + 15 weitere INCONCLUSIVE),
„Vollständig gerechnet (0/0 Symbole)“, „Gesamtlaufzeit: 0.00 h“ bei 14 s, „Kein Kandidat hat … bestanden“
bei null Kandidaten und ein ``COST_MODEL_REALISM_FROM_CALIBRATION``-Event über null Studies.

Fix: terminaler Status ``aborted_preflight_all_symbols_rejected`` (bzw. ``waiting_for_data``, #1363),
``SUPPRESSED_UPSTREAM_NO_SYMBOLS`` für abhängige Invarianten, Trichter-Zähler ``symbols_requested``/
``symbols_rejected_preflight``/``symbols_planned``/``symbols_completed``, Laufzeit in s/min, Kostenmodell-
Quelle ``config_nonzero``, Abschnitt 1 „Kein Kandidat evaluiert — Ursache: …“.

Akzeptanz (Nachbau des Referenzlaufs ``c2e5fa6b``): ein Wurzelbefund, ``symbols_requested = 3``, kein
Kalibrierungs-Event, Laufzeit „14 s“.
"""
from __future__ import annotations

import json

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import summary_de as sde
from automation.optimizer import sweep

_SYMS = ["TSLA.ETORO", "NVDA.ETORO", "GOOGL.ETORO"]
_REJECTED = [{"symbol": s, "reason": "REJECT_INSUFFICIENT_SPAN",
              "detail": "effective_span_days=92.0 < required_span_days=444"} for s in _SYMS]


def _reference_stream() -> list[dict]:
    """Der Strom des Referenzlaufs: der Wurzelbefund je Symbol (FAIL, blocking) plus die
    sweep-seitigen Stubs, die mangels Symbol kein Verdikt fällen konnten."""
    root = [{"name": "check_catalog_resolution_homogeneity", "check": "check_catalog_resolution_homogeneity",
             "passed": False, "severity": "blocking", "scope": s, "source": "sweep",
             "expected": "effective_span_days >= required_span_days",
             "actual": {"effective_span_days": 92.0, "required_span_days": 444},
             "detail": "effective_span_days=92.0 < required_span_days=444"} for s in _SYMS]
    stubs = [{"name": n, "check": n, "passed": None, "severity": "blocking", "scope": "global",
              "source": "sweep", "expected": "…", "actual": None, "detail": "nicht auswertbar"}
             for n in ("check_bar_quality", "check_tick_population")]
    # Run-weite Preflight-Checks, die der Referenzlauf trug (bestanden). Seit #1367 meldet
    # check_promotion_confidence_reachability bei 60 Holdout-Tagen ein ehrliches FAIL — ein ZWEITER,
    # unabhängiger Befund (Konfiguration), keine Folge der Totalabweisung; hier wie im Referenzlauf PASS.
    run_level = [
        {"name": n, "check": n, "passed": True, "severity": "blocking", "scope": "global", "source": "sweep",
         "expected": "…", "actual": None, "detail": "OK"}
        for n in ("check_required_config_keys", "check_instrument_metadata_coherence",
                  "check_promotion_confidence_reachability",
                  "check_history_floor_coherence")           # Issue #1376: run-weiter Preflight im Strom
    ] + [
        {"name": "check_wallclock_budget", "check": "check_wallclock_budget", "passed": True,
         "severity": "high", "scope": "global", "source": "sweep", "expected": "…", "actual": None,
         "detail": "Laufzeit-Budget nicht überschritten."},
        {"name": "check_any_arm_reachability", "check": "check_any_arm_reachability", "passed": True,
         "severity": "medium", "scope": "global", "source": "sweep", "expected": "…", "actual": None,
         "detail": "Alle eligible_requires_any-Klauseln erreichbar."},
    ]
    return root + stubs + run_level


@pytest.fixture
def reference_report(tmp_path, monkeypatch):
    monkeypatch.setattr(rpt, "_read_external_invariant_results", _reference_stream)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(rpt, "emit_execution_event", lambda _log, et, payload, **_k: events.append((et, payload)))
    out = rpt.generate_sweep_report(
        [], run_id="c2e5fa6b_test", reports_dir=tmp_path / "reports", wallclock_s=14.0,
        run_status="aborted_preflight_all_symbols_rejected", symbols_completed=0, symbols_planned=0,
        symbols_requested=3, symbols_rejected_preflight=3, symbols_rejected=_REJECTED)
    return json.loads(out.read_text("utf-8")), events


def test_one_root_finding_and_the_funnel_counters(reference_report):
    report, events = reference_report
    assert (report["symbols_requested"], report["symbols_rejected_preflight"], report["symbols_planned"],
            report["symbols_completed"]) == (3, 3, 0, 0)
    assert report["all_symbols_rejected_preflight"] is True
    assert report["work_completed"] is False and report["work_aborted"] is True
    checks = report["invariant_checks"]
    blocking_fail_names = {c["name"] for c in checks if c.get("severity") == "blocking" and c.get("passed") is False}
    assert blocking_fail_names == {"check_catalog_resolution_homogeneity"}            # EIN Wurzelbefund
    unsuppressed_blocking_none = [
        c["name"] for c in checks
        if c.get("severity") == "blocking" and c.get("passed") is None and not inv.is_suppressed_upstream(c)]
    assert unsuppressed_blocking_none == []
    budget = next(c for c in checks if c["name"] == "check_fail_fast_inconclusive_budget")
    assert budget["passed"] is True
    assert budget["actual"]["reason"] == inv.SUPPRESSED_UPSTREAM_NO_SYMBOLS
    stub = next(c for c in checks if c["name"] == "check_tick_population")
    assert stub["evaluability"]["inconclusive_reason"] == inv.SUPPRESSED_UPSTREAM_NO_SYMBOLS
    # Kein Kalibrierungs-Event über null Studies; die Quelle ist die Konfiguration.
    assert not any(et == "COST_MODEL_REALISM_FROM_CALIBRATION" for et, _ in events)
    assert report["cross_study"]["cost_model_realism_source"] in ("config_zero", "config_nonzero")


def test_meta_checks_pass_on_the_total_rejection_run(reference_report):
    report, _ = reference_report
    by_name = {c["name"]: c for c in report["invariant_checks"]}
    assert by_name["check_fail_fast_invariants_are_blocking"]["passed"] is True
    assert by_name["check_invariant_coverage"]["passed"] is True, by_name["check_invariant_coverage"]["actual"]


def test_summary_states_one_root_cause_in_section_1_and_the_runtime_in_seconds(reference_report):
    report, _ = reference_report
    text = sde.generate_german_summary(report)
    section_1 = text.split("## 2.")[0]
    assert "0 von 3 Symbolen gerechnet (3 im Preflight abgewiesen: TSLA.ETORO (REJECT_INSUFFICIENT_SPAN" in section_1
    assert "Kein Kandidat evaluiert — Ursache:" in section_1
    assert "Vollständig gerechnet" not in section_1 and "kein Kandidat hat sowohl" not in section_1
    assert "Zusätzlich nicht auswertbar (blockierend)" not in section_1
    assert "check_catalog_resolution_homogeneity (3 Study/Studies)" in section_1
    assert "- Gesamtlaufzeit: 14 s" in text
    assert "- Symbole: 0 von 3 Symbolen gerechnet" in text
    assert "SUPPRESSED_UPSTREAM_NO_SYMBOLS" in text


@pytest.mark.parametrize("seconds,expected", [
    (14, "14 s"), (59.4, "59 s"), (125, "2 min 05 s"), (3599, "59 min 59 s"), (7200, "2.00 h"), (None, "k. A."),
])
def test_runtime_format(seconds, expected):
    assert sde._fmt_hours(seconds) == expected


def test_terminal_status_and_waiting_for_data_precedence(monkeypatch):
    monkeypatch.setattr(sweep, "_LAST_DATA_DEPTH_ETA", None)
    assert sweep._preflight_terminal_status("complete", 3, 0, run_id="r") == "aborted_preflight_all_symbols_rejected"
    assert sweep._preflight_terminal_status("complete", 3, 1, run_id="r") == "complete"
    assert sweep._preflight_terminal_status("complete", 0, 0, run_id="r") == "complete"
    assert sweep._preflight_terminal_status("aborted_signal", 3, 0, run_id="r") == "aborted_signal"
    monkeypatch.setattr(sweep, "_LAST_DATA_DEPTH_ETA", {"waiting": True, "run_id": "r"})
    assert sweep._preflight_terminal_status("complete", 3, 0, run_id="r") == "complete"   # ⇒ waiting_for_data
    assert sweep._preflight_terminal_status("complete", 3, 0, run_id="other") == "aborted_preflight_all_symbols_rejected"
    assert sweep._sweep_completion_event("aborted_preflight_all_symbols_rejected")[0] == "SWEEP_ABORTED"
    assert "aborted_preflight_all_symbols_rejected" in sde._RUN_STATUS_LABELS_DE


def test_preflight_funnel_counts_every_unplanned_requested_symbol():
    funnel = sweep._preflight_funnel(
        _SYMS + ["AAPL.ETORO"], {"AAPL.ETORO": [("S", "AAPL.ETORO", {})]},
        [_REJECTED[0], _REJECTED[1], {"symbol": "ZZZ.ETORO", "reason": "X", "detail": None}],
        gate1_rejected_symbols={"GOOGL.ETORO"})
    assert funnel["symbols_requested"] == 4 and funnel["symbols_rejected_preflight"] == 3
    assert [r["symbol"] for r in funnel["symbols_rejected"]] == _SYMS
    assert funnel["symbols_rejected"][2]["reason"] == "GATE1_REJECTED"


def test_funnel_reaches_checkpoint_events_and_report():
    src = open(sweep.__file__, encoding="utf-8").read()
    assert "**_preflight_funnel(_requested_syms, pairs_by_symbol, _symbols_rejected, _gate1_rejected_symbols)" in src
    finished = src[src.index("_sweep_event_payload = {"):src.index("if _sweep_event_type == \"SWEEP_FINISHED\":")]
    assert '"symbols_requested": symbols_requested' in finished
    assert '"symbols_rejected_preflight": symbols_rejected_preflight' in finished
    assert src.count("symbols_requested=symbols_requested,") == 2           # beide Report-Pfade


def test_downgrade_ignores_suppressed_blocking_inconclusive(tmp_path):
    path = tmp_path / "run.json"
    suppressed = inv.suppress_inconclusive_for_no_symbols(
        [{"name": "check_holding_time_cap", "severity": "blocking", "passed": None, "scope": "global"}])
    path.write_text(json.dumps({"symbols_planned": 0, "invariant_checks": suppressed}), "utf-8")
    assert sweep._downgrade_run_status_for_blocking_invariants(path) == "complete"
    assert rpt._compute_decision_admissible(suppressed) is True
    # Ohne Unterdrückung bleibt ein blockierendes INCONCLUSIVE ein Blocker.
    path.write_text(json.dumps({"symbols_planned": 0, "invariant_checks": [
        {"name": "check_holding_time_cap", "severity": "blocking", "passed": None, "scope": "global"}]}), "utf-8")
    assert sweep._downgrade_run_status_for_blocking_invariants(path) == "completed_invalid"


def test_suppression_never_touches_a_verdict():
    checks = [{"name": "a", "passed": False}, {"name": "b", "passed": True}, {"name": "c", "passed": None}]
    inv.suppress_inconclusive_for_no_symbols(checks)
    assert [inv.is_suppressed_upstream(c) for c in checks] == [False, False, True]
    assert checks[0]["passed"] is False and checks[1]["passed"] is True
    assert inv.all_requested_symbols_rejected(3, 0) and not inv.all_requested_symbols_rejected(None, 0)
    assert not inv.all_requested_symbols_rejected(3, 2)


# ─── Kostenmodell-Quelle ohne Studies ────────────────────────────────────────────────

def test_cost_model_fallback_is_config_nonzero_not_calibrated_cache(tmp_path):
    (tmp_path / "backtest.json").write_text(json.dumps({
        "overnight_financing_bps_per_day_by_asset_class": {"DEFAULT": 0.5},
        "slippage_bps_by_asset_class": {"DEFAULT": 2.0},
    }), "utf-8")
    zero, source, symbols = rpt._cost_model_realism_from_applied([], tmp_path)
    assert (zero, source, symbols) == (False, "config_nonzero", [])
    assert inv.check_cost_model_realism_admissible("config_nonzero").passed is True


def test_no_calibration_event_without_a_study(monkeypatch):
    events = []
    monkeypatch.setattr(rpt, "emit_execution_event", lambda _log, et, payload, **_k: events.append(et))
    rpt._emit_cost_model_realism_event("calibrated_cache", [])
    rpt._emit_cost_model_realism_event("config_nonzero", [])
    assert events == []
    rpt._emit_cost_model_realism_event("calibrated_cache", [{"applied_slippage_bps": 4.2}])
    assert events == ["COST_MODEL_REALISM_FROM_CALIBRATION"]
