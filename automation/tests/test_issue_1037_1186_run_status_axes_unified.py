"""Issue #1037/#1186 (P2) — Drei Formulierungen für einen Lauf-Zustand.

Symptom. ``run_status = "completed_invalid"``, ``fail_fast_triggered = "check_effective_stop_
distance"``, Report: "vollständig gerechnet, aber wegen blockierender Invarianten nicht
entscheidungsfähig (kein Arbeitsabbruch)". Ein Feld namens ``fail_fast_triggered`` ist gesetzt,
obwohl nichts abgebrochen wurde. Ein anderer Lauf desselben Tages meldet für denselben Sachverhalt
``complete_with_blocking_invariants`` mit ``fail_fast_triggered = None`` -- zwei Läufe, zwei
``run_status``-Werte, ein Zustand.

Root-Cause. ``fail_fast_triggered`` vermischte "diese Invariante steht auf der Fail-Fast-Liste und
ist gefailt" mit "der Lauf wurde deswegen abgebrochen". Zwei UNABHÄNGIGE Code-Pfade in sweep.py
(Symbol-Loop-Downgrade vs. Read-Back-Patch nach dem Report-Schreiben) markierten denselben
Faktenstand (volle Abdeckung, blockierende Invariante, kein Abbruch) mit zwei verschiedenen
run_status-Strings.

Fix.
1. ``fail_fast_triggered`` -> ``blocking_invariant_triggered`` umbenannt (kein "fail_fast" mehr im
   Namen, der faelschlich einen Abbruch suggerierte).
2. Neues, unabhaengiges ``work_aborted: bool``-Feld (``report._compute_work_aborted``), dieselbe
   Definition wie ``sweep._sweep_completion_event`` ("run_status beginnt mit 'aborted_'").
3. Beide Downgrade-Pfade in sweep.py schreiben seither denselben kanonischen String
   ('completed_invalid') fuer denselben Faktenstand.
4. Neue Invariante ``check_run_status_axes_coherence`` bewacht die Konsistenz dauerhaft.
"""
from automation.optimizer import invariants as inv
from automation.optimizer.report import _build_report, _compute_work_aborted
from automation.optimizer.sweep import _downgrade_run_status_for_blocking_invariants


# ── _compute_work_aborted (reine Funktion) ────────────────────────────────────────────────────
def test_work_aborted_true_for_every_aborted_prefixed_status():
    for status in ("aborted_invariant", "aborted_wallclock", "aborted_disk",
                   "aborted_signal", "aborted_error"):
        assert _compute_work_aborted(status) is True, status


def test_work_aborted_false_for_complete_and_its_informational_variants():
    for status in ("complete", "completed_with_quarantine", "completed_with_failures",
                   "resumed_complete", "completed_invalid"):
        assert _compute_work_aborted(status) is False, status


# ── check_run_status_axes_coherence ───────────────────────────────────────────────────────────
def test_axes_coherence_fails_when_work_completed_and_aborted_both_true():
    result = inv.check_run_status_axes_coherence({
        "work_completed": True, "work_aborted": True, "run_status": "aborted_invariant"})
    assert result.passed is False


def test_axes_coherence_fails_when_status_claims_abort_but_work_aborted_false():
    result = inv.check_run_status_axes_coherence({
        "work_completed": True, "work_aborted": False, "run_status": "aborted_invariant"})
    assert result.passed is False


def test_axes_coherence_fails_when_work_aborted_true_but_status_does_not_claim_abort():
    result = inv.check_run_status_axes_coherence({
        "work_completed": False, "work_aborted": True, "run_status": "complete"})
    assert result.passed is False


def test_axes_coherence_passes_for_a_consistent_completed_invalid_report():
    result = inv.check_run_status_axes_coherence({
        "work_completed": True, "work_aborted": False, "run_status": "completed_invalid"})
    assert result.passed is True


def test_axes_coherence_passes_for_a_consistent_genuine_abort():
    result = inv.check_run_status_axes_coherence({
        "work_completed": False, "work_aborted": True, "run_status": "aborted_disk"})
    assert result.passed is True


def test_axes_coherence_not_applicable_for_legacy_report_without_the_new_fields():
    result = inv.check_run_status_axes_coherence({"run_status": "aborted_invariant"})
    assert result.passed is True


# ── Akzeptanzkriterium 1: beide sweep.py-Downgrade-Pfade konvergieren auf denselben String ──────
def test_readback_downgrade_path_produces_the_canonical_completed_invalid_string(tmp_path):
    report_path = tmp_path / "report.json"
    import json
    report_path.write_text(json.dumps({
        "run_status": "complete",
        "invariant_checks": [
            {"name": "check_holding_time_cap", "passed": False, "severity": "blocking"},
        ],
    }), "utf-8")
    new_status = _downgrade_run_status_for_blocking_invariants(report_path)
    assert new_status == "completed_invalid"
    assert new_status != "complete_with_blocking_invariants"


# ── Akzeptanzkriterium 2: kein Feldname mit "fail_fast" ohne echten Abbruch ─────────────────────
def test_build_report_no_longer_carries_a_fail_fast_named_field(tmp_path):
    report = _build_report(
        [], run_id="run-x", started_at_utc="2026-08-19T00:00:00Z", wallclock_s=10.0,
        cli_args={}, run_status="complete", symbols_completed=1, symbols_planned=1,
        blocking_invariant_triggered=None, reports_dir=tmp_path,
    )
    assert not any("fail_fast" in k for k in report.keys())
    assert "blocking_invariant_triggered" in report
    assert "work_aborted" in report


def test_build_report_exposes_work_aborted_and_blocking_invariant_triggered_consistently(tmp_path):
    """Kernszenario aus #1037: die Fail-Fast-Probe feuerte (blocking_invariant_triggered gesetzt),
    aber der Lauf war NICHT abgebrochen (run_status='complete', alle Symbole fertig) -- work_aborted
    muss dennoch False bleiben, obwohl der Name des Feldes 'blocking_invariant_triggered' nahelegen
    KOENNTE (aber nicht mehr wie 'fail_fast_triggered' explizit behauptet), dass etwas abbrach."""
    report = _build_report(
        [], run_id="run-y", started_at_utc="2026-08-19T00:00:00Z", wallclock_s=10.0,
        cli_args={}, run_status="complete", symbols_completed=14, symbols_planned=14,
        blocking_invariant_triggered="check_effective_stop_distance", reports_dir=tmp_path,
    )
    assert report["work_aborted"] is False
    assert report["work_completed"] is True
    assert report["blocking_invariant_triggered"] == "check_effective_stop_distance"


def test_build_report_derives_work_aborted_true_for_a_genuine_abort(tmp_path):
    report = _build_report(
        [], run_id="run-z", started_at_utc="2026-08-19T00:00:00Z", wallclock_s=10.0,
        cli_args={}, run_status="aborted_disk", symbols_completed=2, symbols_planned=14,
        reports_dir=tmp_path,
    )
    assert report["work_aborted"] is True
    assert report["work_completed"] is False


def test_build_report_wires_the_new_axes_coherence_check_into_the_stream(tmp_path):
    report = _build_report(
        [], run_id="run-w", started_at_utc="2026-08-19T00:00:00Z", wallclock_s=10.0,
        cli_args={}, run_status="complete", symbols_completed=1, symbols_planned=1,
        reports_dir=tmp_path,
    )
    names = {c.get("name") for c in report["invariant_checks"]}
    assert "check_run_status_axes_coherence" in names
