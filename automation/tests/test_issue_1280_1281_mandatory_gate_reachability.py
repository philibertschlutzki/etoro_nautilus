"""Issue #1280/#1281 (GH #1153/#1154, Katalog #1272-1297, P0) — check_mandatory_gate_reachability_
live in den Invarianten-Strom verdrahten + falschen Namen/Schwere/Text korrigieren.

Symptom. check_invariant_coverage FAILte in 4/4 Läufen: check_mandatory_gate_reachability_live
erschien weder im Strom noch auf der Allowlist. Parallel meldete check_any_arm_reachability_live
(severity 'medium') in 55/56 Studies: "OR-Arm-Klausel(n) strukturell unerreichbar:
oos_min_alpha_tstat" — obwohl oos_min_alpha_tstat Mitglied von eligible_requires_all ist, nicht
von eligible_requires_any (leer).

Fix.
1. Zweiter, getrennter Emit-Aufruf (run_optimization._emit_mandatory_gate_reachability_result).
2. Eigener Meldungstext, severity 'high' statt 'medium'.
3. Globaler blockierender Eintrag bei >= 80% betroffenen Studies
   (invariants.check_mandatory_gate_reachability_global).
"""
import inspect

from automation.optimizer import invariants as inv, report as rpt, run_optimization as ro


# ---------------------------------------------------------------------------------------------
# run_optimization._emit_mandatory_gate_reachability_result
# ---------------------------------------------------------------------------------------------

def test_emit_mandatory_gate_reachability_result_pass(caplog):
    import logging as _logging
    log = _logging.getLogger("optimizer")
    events = []

    def _capture(logger, event_type, payload, level=_logging.INFO):
        events.append(payload)

    import automation.optimizer.run_optimization as ro_mod
    orig = ro_mod.emit_execution_event
    ro_mod.emit_execution_event = _capture
    try:
        ro._emit_mandatory_gate_reachability_result(log, [], scope="study_X")
    finally:
        ro_mod.emit_execution_event = orig
    assert len(events) == 1
    assert events[0]["name"] == "check_mandatory_gate_reachability_live"
    assert events[0]["passed"] is True
    assert events[0]["severity"] == "high"


def test_emit_mandatory_gate_reachability_result_fail_names_the_clause_and_the_consequence():
    events = []

    def _capture(logger, event_type, payload, level=None):
        events.append(payload)

    import automation.optimizer.run_optimization as ro_mod
    import logging as _logging
    orig = ro_mod.emit_execution_event
    ro_mod.emit_execution_event = _capture
    try:
        ro._emit_mandatory_gate_reachability_result(
            _logging.getLogger("optimizer"), ["oos_min_alpha_tstat"], scope="study_X")
    finally:
        ro_mod.emit_execution_event = orig
    assert len(events) == 1
    payload = events[0]
    assert payload["passed"] is False
    assert payload["severity"] == "high"
    assert "MANDATORY-Gate" in payload["detail"]
    assert "jeder Trial wird unabhängig von jeder anderen Kennzahl abgelehnt" in payload["detail"]
    # Nicht die requires_any-Formulierung ("kollabiert ... auf die übrigen Arme").
    assert "kollabiert" not in payload["detail"]


def test_mandatory_gate_result_is_no_longer_merged_into_any_arm_live_unreachable():
    """Strukturbeweis: der #1093/#1241-Aufruf von check_mandatory_gate_reachability_live speist
    NICHT MEHR any_arm_live_unreachable (Root-Cause #1280/#1281)."""
    source = inspect.getsource(ro)
    idx_call = source.index("mandatory_gate_live_unreachable = check_mandatory_gate_reachability_live(")
    # Im unmittelbaren Merge-Ausdruck (die naechsten ~120 Zeichen nach dem Aufruf) darf
    # any_arm_live_unreachable NICHT als Zielvariable dieser Zuweisung erscheinen.
    snippet_before = source[max(0, idx_call - 80):idx_call]
    assert "any_arm_live_unreachable = list(any_arm_live_unreachable) +" not in snippet_before


def test_two_separate_emit_call_sites_in_the_live_reachability_block():
    source = inspect.getsource(ro)
    assert source.count("_emit_any_arm_reachability_result(") >= 3  # 2 Config-Load-Stellen + hier
    assert "_emit_mandatory_gate_reachability_result(" in source


# ---------------------------------------------------------------------------------------------
# invariants.check_mandatory_gate_reachability_global
# ---------------------------------------------------------------------------------------------

def test_no_results_is_inconclusive():
    r = inv.check_mandatory_gate_reachability_global([])
    assert r.passed is True
    assert r.inconclusive is True


def test_below_threshold_passes():
    results = [{"passed": False}] * 3 + [{"passed": True}] * 7  # 30% -> passt (< 80%)
    r = inv.check_mandatory_gate_reachability_global(results)
    assert r.passed is True


def test_reference_symptom_55_of_56_fails_blocking():
    results = [{"passed": False}] * 55 + [{"passed": True}] * 1
    r = inv.check_mandatory_gate_reachability_global(results)
    assert r.passed is False
    assert r.severity == "blocking"
    assert r.actual == round(55 / 56, 4)


def test_exactly_at_threshold_fails_since_the_issue_text_says_gte_80_percent():
    results = [{"passed": False}] * 8 + [{"passed": True}] * 2  # exakt 80%
    r = inv.check_mandatory_gate_reachability_global(results)
    assert r.passed is False  # ">= 80%" (Issue-Text) faellt, nicht nur "> 80%"


def test_just_below_threshold_passes():
    results = [{"passed": False}] * 79 + [{"passed": True}] * 21  # 79%
    r = inv.check_mandatory_gate_reachability_global(results)
    assert r.passed is True


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_check_mandatory_gate_reachability_global_appears_in_stream(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1280-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_mandatory_gate_reachability_global" in names
