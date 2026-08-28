"""Issue #1305 (GH #1182, P1) — Diagnose-Rückschrieb läuft nicht mehr trotz
decision_admissible=false.

Symptom. Ein Lauf war als nicht entscheidungsfähig gestempelt (``run_status=
'completed_invalid'``, ``decision_admissible=false``, blockierende Invarianten FAILen) — und schrieb
trotzdem 14 Suchraum-Empfehlungen in den Diagnose-Cache.

Fix.
1. ``report.diagnosis_writeback_admissible(run_report) -> (bool, str)`` — zentraler Wächter.
2. ``sweep_diagnostics.record_diagnosed_pair`` erhält ``decision_admissible`` (Default ``None``
   ⇒ bit-identisches Alt-Verhalten) und unterdrückt bei ``False`` jeden Schreibvorgang, emittiert
   ``DIAGNOSIS_WRITEBACK_SUPPRESSED``.
3. ``invariants.check_diagnosis_writeback_admissible`` — Regressions-Wächter.
"""
import logging

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import report
from automation.optimizer import sweep_diagnostics as sd


# ---------------------------------------------------------------------------------------------
# report.diagnosis_writeback_admissible — reine Entscheidungsfunktion
# ---------------------------------------------------------------------------------------------

def test_admissible_when_decision_admissible_true_and_no_blocking_fails():
    admissible, reason = report.diagnosis_writeback_admissible({
        "decision_admissible": True,
        "invariant_checks": [{"severity": "blocking", "passed": True}],
    })
    assert admissible is True
    assert reason == "admissible"


def test_inadmissible_when_decision_admissible_false():
    admissible, reason = report.diagnosis_writeback_admissible({
        "decision_admissible": False, "invariant_checks": [],
    })
    assert admissible is False
    assert reason == "decision_admissible_false"


def test_inadmissible_when_a_blocking_check_failed():
    admissible, reason = report.diagnosis_writeback_admissible({
        "decision_admissible": True,  # inkonsistenter Eingang -> die Checks selbst entscheiden.
        "invariant_checks": [{"severity": "blocking", "passed": False}],
    })
    assert admissible is False
    assert reason == "blocking_invariant_failed"


def test_inadmissible_when_a_blocking_check_is_inconclusive():
    admissible, reason = report.diagnosis_writeback_admissible({
        "decision_admissible": True,
        "invariant_checks": [{"severity": "blocking", "passed": None}],
    })
    assert admissible is False
    assert reason == "blocking_invariant_inconclusive"


def test_non_blocking_failure_does_not_suppress_writeback():
    admissible, reason = report.diagnosis_writeback_admissible({
        "decision_admissible": True,
        "invariant_checks": [{"severity": "high", "passed": False}],
    })
    assert admissible is True
    assert reason == "admissible"


# ---------------------------------------------------------------------------------------------
# sweep_diagnostics.record_diagnosed_pair — decision_admissible=False unterdrückt den Schreibvorgang.
# ---------------------------------------------------------------------------------------------

def test_record_diagnosed_pair_suppressed_when_decision_admissible_false(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "_read_diagnostic_writeback_enabled", lambda: True)
    events = []

    def _capture(logger, event_type, payload, level=logging.INFO):
        events.append((event_type, payload, level))

    monkeypatch.setattr("automation.log_manager.emit_execution_event", _capture)

    sd.record_diagnosed_pair(
        {"strategy": "S", "symbol": "X.ETORO", "action": "search_space_override",
         "binding_cause": "signal_sparse"},
        work_dir=tmp_path, decision_admissible=False,
    )
    cache = sd.load_diagnosed_pairs_cache(tmp_path)
    assert cache == {}  # nichts geschrieben.
    suppressed = [p for (etype, p, _lvl) in events if etype == "DIAGNOSIS_WRITEBACK_SUPPRESSED"]
    assert suppressed
    assert suppressed[0]["reason"] == "decision_admissible_false"
    assert suppressed[0]["strategy"] == "S"


def test_record_diagnosed_pair_unaffected_when_decision_admissible_true(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "_read_diagnostic_writeback_enabled", lambda: True)
    sd.record_diagnosed_pair(
        {"strategy": "S", "symbol": "X.ETORO", "action": "search_space_override",
         "binding_cause": "signal_sparse"},
        work_dir=tmp_path, decision_admissible=True,
    )
    cache = sd.load_diagnosed_pairs_cache(tmp_path)
    assert ("S", "X.ETORO") in cache


def test_record_diagnosed_pair_default_none_is_backward_compatible(tmp_path, monkeypatch):
    """Kein decision_admissible-Argument (Default None) -> bit-identisches Alt-Verhalten (schreibt
    wie vor #1305)."""
    monkeypatch.setattr(sd, "_read_diagnostic_writeback_enabled", lambda: True)
    sd.record_diagnosed_pair(
        {"strategy": "S", "symbol": "X.ETORO", "action": "search_space_override",
         "binding_cause": "signal_sparse"},
        work_dir=tmp_path,
    )
    cache = sd.load_diagnosed_pairs_cache(tmp_path)
    assert ("S", "X.ETORO") in cache


# ---------------------------------------------------------------------------------------------
# invariants.check_diagnosis_writeback_admissible
# ---------------------------------------------------------------------------------------------

def test_check_diagnosis_writeback_admissible_not_applicable_when_admissible():
    result = inv.check_diagnosis_writeback_admissible(True, False)
    assert result.passed is True
    assert "nicht anwendbar" in result.detail


def test_check_diagnosis_writeback_admissible_passes_when_correctly_suppressed():
    result = inv.check_diagnosis_writeback_admissible(False, False)
    assert result.passed is True


def test_check_diagnosis_writeback_admissible_fails_when_writeback_attempted_anyway():
    result = inv.check_diagnosis_writeback_admissible(False, True)
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual == {"decision_admissible": False, "writeback_attempted": True}


# ---------------------------------------------------------------------------------------------
# End-to-end: _writeback_search_stagnation_diagnoses/_writeback_gate_unreachable_diagnoses
# respektieren decision_admissible.
# ---------------------------------------------------------------------------------------------

def test_writeback_search_stagnation_diagnoses_suppressed_when_inadmissible(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "_read_diagnostic_writeback_enabled", lambda: True)
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)

    recs = report._writeback_search_stagnation_diagnoses(
        {"S/X.ETORO": 0.1}, None, run_id="run-1305", work_dir=tmp_path,
        decision_admissible=False,
    )
    # recommend_diagnosis_action wird weiterhin aufgerufen (Report-Sichtbarkeit der Empfehlung
    # selbst bleibt erhalten), aber record_diagnosed_pair schreibt NICHTS in den Cache.
    assert recs  # die Funktion liefert weiterhin die Empfehlung fuer Telemetrie/Tests.
    cache = sd.load_diagnosed_pairs_cache(tmp_path)
    assert cache == {}


def test_writeback_search_stagnation_diagnoses_writes_when_admissible(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "_read_diagnostic_writeback_enabled", lambda: True)
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)

    report._writeback_search_stagnation_diagnoses(
        {"S/X.ETORO": 0.1}, None, run_id="run-1305", work_dir=tmp_path,
        decision_admissible=True,
    )
    cache = sd.load_diagnosed_pairs_cache(tmp_path)
    assert ("S", "X.ETORO") in cache
