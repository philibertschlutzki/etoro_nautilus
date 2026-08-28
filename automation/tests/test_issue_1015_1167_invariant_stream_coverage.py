"""Issue #1015/#1167 (Katalog #1170) — 9 von 91 ``check_*``-Funktionen erscheinen nie im
Report-Invariantenstrom.

Symptom: HEAD definierte 91 ``check_*``-Funktionen; die Läufe emittierten 82. Fehlend:
``check_any_arm_reachability``, ``check_any_arm_reachability_live``, ``check_bar_quality``,
``check_budget``, ``check_data_span``, ``check_deployment_gate_completeness``,
``check_live_exposure_budget``, ``check_study_coherence_violation_rate``,
``check_wallclock_budget``.

Einordnung: acht davon LAUFEN — nur ausserhalb von ``_build_report`` (Sweep-Hauptschleife,
``run_optimization.py``, Worker-Prozess, Phase 5 des Orchestrators). Genau eine
(``check_live_exposure_budget``) war bereits als ``_DELIBERATELY_UNWIRED_INVARIANT_CHECKS``
dokumentiert.

Fix:
1. Sechs der acht laufen im SELBEN Prozess wie ``report.py`` (Sweep-Hauptschleife/
   ``run_optimization.py``, kein ``ProcessPoolExecutor`` in beiden Modulen) — sie emittieren jetzt
   symmetrisch (PASS UND FAIL) ein ``INVARIANT_STREAM_RESULT``-Event in den bereits von ``report.py``
   gelesenen ``"optimizer"``-Sidecar (``check_bar_quality``, ``check_wallclock_budget``,
   ``disk_guard.check_budget``, ``check_study_coherence_violation_rate``,
   ``check_any_arm_reachability``, ``check_any_arm_reachability_live``).
2. Zwei (``check_data_span`` — isolierter Worker-Prozess, ``"backtest_worker"``-Sidecar, analog
   #999/#1151; ``check_deployment_gate_completeness`` — Phase-5-Whitelist ohne 1:1-Bezug zu einem
   einzelnen Sweep-``run_id``) emittieren das Event ebenfalls, landen aber strukturell NICHT im
   Report-Sidecar und sind daher — wie ``check_live_exposure_budget`` zuvor — mit Begründung in
   ``_DELIBERATELY_UNWIRED_INVARIANT_CHECKS`` gelistet.
3. Neue Meta-Invariante ``invariants.check_invariant_coverage``: FAIL, wenn eine definierte
   ``check_*``-Funktion weder im Strom noch auf der Allowlist steht
   (Akzeptanzkriterium: ``n_defined - n_in_stream - n_allowlisted == 0``).
"""
import json
import logging

import optuna
import pytest

from automation import log_manager
from automation.optimizer import disk_guard, invariants as inv, report as rpt
from automation.optimizer import run_optimization as ro


@pytest.fixture(autouse=True)
def _reset_disk_guard():
    """Issue #1167 Regressionswaechter — ``test_disk_budget_callback_emits_failed_invariant_
    result_on_status_exceeded`` (unten) treibt ``disk_guard.sweep_abort_requested`` (Modul-
    globaler ``Event``) absichtlich auf ``set()``, um das FAIL-Event zu reproduzieren. Ohne
    Reset bleibt das Flag fuer JEDEN spaeter in DERSELBEN pytest-Session laufenden Test gesetzt
    (kontaminierte ``test_issue_807_symbol_degeneracy.py``-Laeufe, die dasselbe Flag lesen)."""
    disk_guard.reset_for_tests()
    yield
    disk_guard.reset_for_tests()


# ── invariants.check_invariant_coverage — reine Funktion ─────────────────────────────────────────

def test_passes_when_every_defined_name_is_in_stream():
    result = inv.check_invariant_coverage(["check_a", "check_b"], ["check_a", "check_b"])
    assert result.passed is True
    assert result.actual is None


def test_passes_when_missing_names_are_allowlisted():
    result = inv.check_invariant_coverage(
        ["check_a", "check_b"], ["check_a"], allowlisted_check_names=["check_b"])
    assert result.passed is True


def test_fails_and_names_the_gap_when_neither_stream_nor_allowlist_cover_it():
    result = inv.check_invariant_coverage(
        ["check_a", "check_b", "check_c"], ["check_a"], allowlisted_check_names=["check_b"])
    assert result.passed is False
    assert result.actual["missing"] == ["check_c"]
    assert result.actual["n_defined"] == 3
    assert "check_c" in result.detail


def test_reproduces_the_1167_reference_symptom_91_defined_82_in_stream():
    """9 fehlende Checks, davon 1 bereits allowlisted (check_live_exposure_budget) -- das
    urspruengliche #1167-Symptom vor diesem Fix."""
    defined = [f"check_{i}" for i in range(91)]
    stream = defined[:82]
    missing_9 = defined[82:]
    result_before_fix = inv.check_invariant_coverage(defined, stream)
    assert result_before_fix.passed is False
    assert len(result_before_fix.actual["missing"]) == 9

    result_after_fix = inv.check_invariant_coverage(
        defined, stream + missing_9[:-1], allowlisted_check_names=[missing_9[-1]])
    assert result_after_fix.passed is True


def test_a_name_present_in_both_stream_and_allowlist_does_not_break_the_arithmetic():
    """Set-Differenz statt naiver Subtraktion: ein Name in BEIDEN Mengen darf n_defined -
    n_in_stream - n_allowlisted nicht negativ/verfaelscht werden lassen."""
    result = inv.check_invariant_coverage(
        ["check_a"], ["check_a"], allowlisted_check_names=["check_a"])
    assert result.passed is True


def test_severity_is_high():
    result = inv.check_invariant_coverage(["check_a"], [])
    assert result.severity == "high"


# ── report._all_defined_check_names — Text-Scan über das gesamte automation-Paket ────────────────

def test_all_nine_1167_functions_are_discovered():
    names = rpt._all_defined_check_names()
    for name in (
        "check_any_arm_reachability", "check_any_arm_reachability_live", "check_bar_quality",
        "check_budget", "check_data_span", "check_deployment_gate_completeness",
        "check_live_exposure_budget", "check_study_coherence_violation_rate",
        "check_wallclock_budget",
    ):
        assert name in names, name


def test_invariants_py_checks_are_also_discovered():
    names = rpt._all_defined_check_names()
    assert "check_invariant_registry_wired" in names
    assert "check_invariant_coverage" in names


def test_no_import_of_backtest_runner_is_required():
    """``backtest_runner.py`` haengt an ``nautilus_trader`` (in dieser Sandbox nicht installiert);
    der Scan MUSS über reinen Text laufen, sonst crasht diese Introspektion selbst in Umgebungen
    ohne die Laufzeit-Abhängigkeit."""
    import sys
    assert "nautilus_trader" not in sys.modules or True  # kein Vorab-Zustand vorausgesetzt
    names = rpt._all_defined_check_names()  # darf nicht importieren -> darf nicht crashen
    assert "check_data_span" in names


# ── report._read_external_invariant_results — liest den "optimizer"-Sidecar zurück ───────────────

def _register_optimizer_sidecar(monkeypatch, tmp_path, events):
    path = tmp_path / "optimizer.events.jsonl"
    lines = "\n".join(json.dumps(ev) for ev in events)
    path.write_text(lines + ("\n" if events else ""), "utf-8")
    monkeypatch.setitem(log_manager._JSONL_SIDECAR_PATHS, "optimizer", path)
    return path


def test_returns_empty_list_without_a_registered_sidecar(monkeypatch):
    monkeypatch.delitem(log_manager._JSONL_SIDECAR_PATHS, "optimizer", raising=False)
    assert rpt._read_external_invariant_results() == []


def test_reads_back_invariant_result_events_with_their_source(monkeypatch, tmp_path):
    _register_optimizer_sidecar(monkeypatch, tmp_path, [
        {"event_type": "INVARIANT_STREAM_RESULT", "name": "check_bar_quality", "check": "check_bar_quality",
         "passed": True, "source": "sweep", "scope": "AAPL.ETORO", "expected": "e", "actual": None,
         "detail": "d", "severity": "high", "timestamp_utc": "x", "run_id": "r1"},
        {"event_type": "INVARIANT_STREAM_RESULT", "name": "check_wallclock_budget",
         "check": "check_wallclock_budget", "passed": False, "source": "sweep", "scope": "global",
         "expected": "e2", "actual": {"elapsed_s": 1.0}, "detail": "over budget", "severity": "high"},
        {"event_type": "BAR_QUALITY_PROFILE", "symbol": "AAPL.ETORO"},  # anderer event_type: ignoriert
    ])
    results = rpt._read_external_invariant_results()
    assert len(results) == 2
    by_name = {r["name"]: r for r in results}
    assert by_name["check_bar_quality"]["passed"] is True
    assert by_name["check_bar_quality"]["source"] == "sweep"
    assert by_name["check_wallclock_budget"]["passed"] is False
    assert by_name["check_wallclock_budget"]["actual"] == {"elapsed_s": 1.0}
    # Huellenfelder (event_type/timestamp_utc/run_id) sind entfernt.
    assert "event_type" not in by_name["check_bar_quality"]
    assert "run_id" not in by_name["check_bar_quality"]


def test_end_to_end_emission_via_emit_execution_event_is_readable(monkeypatch, tmp_path):
    """Kein Event-Fixture von Hand gebaut: ruft ``emit_execution_event`` GENAU so auf, wie die
    Call-Sites es tun, und liest über den echten Sidecar-Mechanismus zurück."""
    _register_optimizer_sidecar(monkeypatch, tmp_path, [])
    log = logging.getLogger("optimizer")
    log_manager.emit_execution_event(log, "INVARIANT_STREAM_RESULT", {
        "name": "check_budget", "check": "check_budget", "passed": True, "source": "sweep",
        "scope": "study_X", "expected": "e", "actual": None, "detail": "OK", "severity": "medium",
    })
    results = rpt._read_external_invariant_results()
    assert len(results) == 1
    assert results[0]["check"] == "check_budget"
    assert results[0]["passed"] is True


# ── report._DELIBERATELY_UNWIRED_INVARIANT_CHECKS — neue, begründete Einträge ───────────────────

def test_the_three_structurally_disjoint_checks_are_allowlisted():
    for name in ("check_data_span", "check_deployment_gate_completeness",
                "check_invariant_coverage"):
        assert name in rpt._DELIBERATELY_UNWIRED_INVARIANT_CHECKS, name


def test_the_five_actually_wired_checks_are_not_on_the_allowlist():
    # Issue #1311 (GH #1188, P2) — ``check_budget`` wurde HIER entfernt: es HAT zwar eine
    # Aufrufstelle (``disk_guard.check_budget``, aufgerufen von ``run_optimization.disk_budget_
    # callback``), diese feuert aber nur alle ``disk_check_interval_trials`` (Default 200)
    # abgeschlossene Trials EINER Study — "hat eine Aufrufstelle" (die urspruengliche #1015/#1167-
    # Definition von "wired") ist NICHT dasselbe wie "erscheint verlaesslich im Strom eines
    # tatsaechlichen Laufs" (B-14: 0/2 committete Referenzlaeufe enthalten einen check_budget-
    # Eintrag). ``check_budget`` steht seither bewusst in ``_DELIBERATELY_UNWIRED_INVARIANT_
    # CHECKS`` (siehe dortiger Kommentar) statt hier.
    for name in ("check_bar_quality", "check_wallclock_budget",
                "check_study_coherence_violation_rate", "check_any_arm_reachability",
                "check_any_arm_reachability_live"):
        assert name not in rpt._DELIBERATELY_UNWIRED_INVARIANT_CHECKS, name


def test_the_pre_existing_allowlist_entries_survive():
    for name in ("check_live_exposure_budget", "check_cost_model_resolution",
                "check_cost_model_floor"):
        assert name in rpt._DELIBERATELY_UNWIRED_INVARIANT_CHECKS, name


# ── report._build_report — Verdrahtung (Textsicherung, siehe test_issue_1014-Konvention) ─────────

def test_check_invariant_coverage_is_wired_into_build_report():
    source = __import__("pathlib").Path(rpt.__file__).read_text("utf-8")
    assert "_inv.check_invariant_coverage(" in source
    assert "_read_external_invariant_results()" in source
    assert "_all_defined_check_names()" in source


def test_every_invariant_checks_entry_gets_a_source_field():
    source = __import__("pathlib").Path(rpt.__file__).read_text("utf-8")
    assert 'd["source"] = "report"' in source
    assert 'd.setdefault("source", "sweep")' in source


# ── run_optimization.disk_budget_callback — symmetrisches INVARIANT_STREAM_RESULT ───────────────────────

class _FakeStudyForDisk:
    study_name = "study_TestStrategy_AAA.ETORO"

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeTrialForDisk:
    def __init__(self, number):
        self.number = number


def _json_events(caplog):
    return [json.loads(r.message.split("[JSON_EVENT]", 1)[1].strip())
           for r in caplog.records if "[JSON_EVENT]" in r.message]


def test_disk_budget_callback_emits_passed_invariant_result_on_status_ok(monkeypatch, caplog):
    monkeypatch.setattr(ro.disk_guard, "check_budget", lambda *a, **k: disk_guard.STATUS_OK)
    study = _FakeStudyForDisk()
    with caplog.at_level(logging.INFO, logger="optimizer"):
        ro.disk_budget_callback(study, _FakeTrialForDisk(199),
                                opt_data={"disk_check_interval_trials": 200})
    results = [e for e in _json_events(caplog) if e.get("event_type") == "INVARIANT_STREAM_RESULT"]
    assert len(results) == 1
    assert results[0]["check"] == "check_budget"
    assert results[0]["passed"] is True
    assert results[0]["source"] == "sweep"


def test_disk_budget_callback_emits_failed_invariant_result_on_status_exceeded(monkeypatch, caplog):
    monkeypatch.setattr(ro.disk_guard, "check_budget", lambda *a, **k: disk_guard.STATUS_EXCEEDED)
    study = _FakeStudyForDisk()
    with caplog.at_level(logging.INFO, logger="optimizer"):
        ro.disk_budget_callback(study, _FakeTrialForDisk(399),
                                opt_data={"disk_check_interval_trials": 200})
    results = [e for e in _json_events(caplog) if e.get("event_type") == "INVARIANT_STREAM_RESULT"]
    assert len(results) == 1
    assert results[0]["passed"] is False
    assert results[0]["severity"] == "high"


# ── run_optimization.check_study_coherence_violation_rate — jetzt auch bei PASS ein Event ────────

def _study_with_trials(n, n_violations, evaluated=True):
    study = optuna.create_study(direction="maximize")
    for i in range(n):
        t = study.ask()
        t.set_user_attr("oos_evaluated", evaluated)
        t.set_user_attr("oos_coherence_violation", i < n_violations)
        study.tell(t, 1.0)
    return study


def test_coherence_rate_check_now_emits_invariant_result_on_pass(caplog):
    """Vor diesem Fix: PASS war spurlos (nur die Rueckgabe False). Reproduziert das #1167-Symptom
    UND den Fix in einem Test."""
    study = _study_with_trials(64, n_violations=1)
    with caplog.at_level(logging.INFO, logger="optimizer"):
        result = ro.check_study_coherence_violation_rate(
            study, {"max_coherence_violation_rate": 0.10})
    assert result is False
    results = [e for e in _json_events(caplog) if e.get("event_type") == "INVARIANT_STREAM_RESULT"
              and e.get("check") == "check_study_coherence_violation_rate"]
    assert len(results) == 1
    assert results[0]["passed"] is True
    assert results[0]["source"] == "sweep"


def test_coherence_rate_check_emits_invariant_result_on_fail_alongside_existing_events(caplog):
    study = _study_with_trials(64, n_violations=35)
    with caplog.at_level(logging.INFO, logger="optimizer"):
        ro.check_study_coherence_violation_rate(study, {"max_coherence_violation_rate": 0.10})
    events = _json_events(caplog)
    results = [e for e in events if e.get("event_type") == "INVARIANT_STREAM_RESULT"
              and e.get("check") == "check_study_coherence_violation_rate"]
    assert len(results) == 1
    assert results[0]["passed"] is False
    # bestehendes #803-Verhalten (STUDY_ABORTED_ON_INVARIANT) bleibt unveraendert erhalten.
    assert any(e.get("event_type") == "STUDY_ABORTED_ON_INVARIANT" for e in events)


def test_coherence_rate_check_emits_passed_result_when_not_configured(caplog):
    study = _study_with_trials(10, n_violations=0)
    with caplog.at_level(logging.INFO, logger="optimizer"):
        result = ro.check_study_coherence_violation_rate(study, {})
    assert result is False
    results = [e for e in _json_events(caplog) if e.get("event_type") == "INVARIANT_STREAM_RESULT"
              and e.get("check") == "check_study_coherence_violation_rate"]
    assert len(results) == 1
    assert results[0]["passed"] is True


# ── run_optimization._emit_any_arm_reachability_result — beide Reachability-Checks ───────────────

def test_emit_any_arm_reachability_result_pass(caplog):
    with caplog.at_level(logging.INFO, logger="optimizer"):
        ro._emit_any_arm_reachability_result(
            logging.getLogger("optimizer"), [], check_name="check_any_arm_reachability",
            scope="TestStrategy")
    results = [e for e in _json_events(caplog) if e.get("event_type") == "INVARIANT_STREAM_RESULT"]
    assert len(results) == 1
    assert results[0]["passed"] is True
    assert results[0]["check"] == "check_any_arm_reachability"
    assert results[0]["source"] == "sweep"


def test_emit_any_arm_reachability_result_fail_names_the_clauses(caplog):
    with caplog.at_level(logging.INFO, logger="optimizer"):
        ro._emit_any_arm_reachability_result(
            logging.getLogger("optimizer"), ["min_win_rate"],
            check_name="check_any_arm_reachability_live", scope="study_X")
    results = [e for e in _json_events(caplog) if e.get("event_type") == "INVARIANT_STREAM_RESULT"]
    assert len(results) == 1
    assert results[0]["passed"] is False
    assert results[0]["actual"]["unreachable_clauses"] == ["min_win_rate"]


def test_any_arm_reachability_call_sites_are_wired_in_run_optimization():
    source = __import__("pathlib").Path(ro.__file__).read_text("utf-8")
    assert source.count("_emit_any_arm_reachability_result(") >= 3  # def + optimize() + optimize_symbol()


# ── sweep.py / backtest_runner.py / daily_orchestrator.py — Textsicherung der übrigen Call-Sites ─

def test_sweep_emits_invariant_result_for_bar_quality_and_wallclock_budget():
    from automation.optimizer import sweep
    source = __import__("pathlib").Path(sweep.__file__).read_text("utf-8")
    assert '"name": "check_bar_quality"' in source
    assert '"name": "check_wallclock_budget"' in source
    assert source.count('"source": "sweep"') >= 2


def test_backtest_runner_emits_invariant_result_for_data_span():
    # Kein Modul-Import: backtest_runner.py haengt zur Laufzeit an nautilus_trader (in dieser
    # Sandbox nicht installiert, siehe test_no_import_of_backtest_runner_is_required oben) —
    # reiner Text-Scan, analog _all_defined_check_names.
    import pathlib
    source = (pathlib.Path("automation") / "backtest_runner.py").read_text("utf-8")
    assert '"name": "check_data_span"' in source
    assert '"source": "worker"' in source


def test_daily_orchestrator_emits_invariant_result_for_deployment_gate_completeness():
    from automation import daily_orchestrator
    source = __import__("pathlib").Path(daily_orchestrator.__file__).read_text("utf-8")
    assert '"source": "orchestrator"' in source
    assert "INVARIANT_STREAM_RESULT" in source


# ── Regressionswächter: #984/#1138 bleibt grün trotz der neuen Funktion/Allowlist-Einträge ───────

def test_invariant_registry_wiring_check_still_passes():
    result = rpt._invariant_registry_wiring_check()
    assert result.passed is True
