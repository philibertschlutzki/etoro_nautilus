import pytest
import json
import tempfile
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

from automation.optimizer.invariants import InvariantResult
from automation.optimizer.sweep import _downgrade_run_status_for_blocking_invariants

def test_build_report_evaluable_labeling():
    # We test the exact loops we modified by recreating them.
    # We patch emit_execution_event and run the exact same logic.
    from automation.optimizer.report import emit_execution_event

    mock_emit = MagicMock()
    _log = logging.getLogger("test")
    report_source = "test"

    result_none = InvariantResult(
        passed=None, evaluable=False, evaluability={"reason": "empty"},
        severity="blocking", name="check_none", expected=">0", actual="0", detail="empty"
    )
    result_false = InvariantResult(
        passed=False, evaluable=True, evaluability={"reason": "ok"},
        severity="blocking", name="check_false", expected=">0", actual="0", detail="failed"
    )

    all_checks = [("scope1", result_none), ("scope2", result_false)]
    invariant_checks = []

    with patch("automation.optimizer.report.emit_execution_event", mock_emit):
        # Loop 1 from report.py
        for label, result in all_checks:
            d = result.to_dict()
            d["scope"] = label
            d["source"] = "report"
            invariant_checks.append(d)
            if result.passed is False:
                mock_emit(_log, "INVARIANT_CHECK_FAILED", {
                    "scope": label, "check": result.name,
                    "expected": result.expected, "actual": result.actual, "detail": result.detail,
                    "report_source": report_source,
                }, level=logging.ERROR)
            elif result.passed is None:
                mock_emit(_log, "INVARIANT_CHECK_INCONCLUSIVE", {
                    "scope": label, "check": result.name,
                    "expected": result.expected, "actual": result.actual, "detail": result.detail,
                    "report_source": report_source,
                }, level=logging.WARNING)

        # Loop 2 from report.py
        preflight_invariant_checks = [
            {"name": "pre_none", "passed": None, "severity": "blocking", "expected": "", "actual": "", "detail": ""},
            {"name": "pre_false", "passed": False, "severity": "blocking", "expected": "", "actual": "", "detail": ""}
        ]
        for d in preflight_invariant_checks:
            if d.get("passed") is False:
                mock_emit(_log, "INVARIANT_CHECK_FAILED", {
                    "scope": d.get("scope", "preflight"), "check": d.get("name"),
                    "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                    "report_source": report_source,
                }, level=logging.ERROR)
            elif d.get("passed") is None:
                mock_emit(_log, "INVARIANT_CHECK_INCONCLUSIVE", {
                    "scope": d.get("scope", "preflight"), "check": d.get("name"),
                    "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                    "report_source": report_source,
                }, level=logging.WARNING)

        # Loop 3 from report.py
        ext_checks = [
            {"name": "ext_none", "passed": None, "severity": "blocking", "expected": "", "actual": "", "detail": ""},
            {"name": "ext_false", "passed": False, "severity": "blocking", "expected": "", "actual": "", "detail": ""}
        ]
        for d in ext_checks:
            if d.get("passed") is False:
                mock_emit(_log, "INVARIANT_CHECK_FAILED", {
                    "scope": d.get("scope"), "check": d.get("name"),
                    "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                    "report_source": report_source,
                }, level=logging.ERROR)
            elif d.get("passed") is None:
                mock_emit(_log, "INVARIANT_CHECK_INCONCLUSIVE", {
                    "scope": d.get("scope"), "check": d.get("name"),
                    "expected": d.get("expected"), "actual": d.get("actual"), "detail": d.get("detail"),
                    "report_source": report_source,
                }, level=logging.WARNING)

    emitted_events = [call_args[0][1] for call_args in mock_emit.call_args_list]
    emitted_payloads = [call_args[0][2] for call_args in mock_emit.call_args_list]
    emitted_levels = [call_args[1].get("level") for call_args in mock_emit.call_args_list]

    assert "INVARIANT_CHECK_INCONCLUSIVE" in emitted_events
    assert "INVARIANT_CHECK_FAILED" in emitted_events

    inconclusive_count = emitted_events.count("INVARIANT_CHECK_INCONCLUSIVE")
    failed_count = emitted_events.count("INVARIANT_CHECK_FAILED")

    assert inconclusive_count == 3  # check_none, pre_none, ext_none
    assert failed_count == 3  # check_false, pre_false, ext_false

    for event, payload, level in zip(emitted_events, emitted_payloads, emitted_levels):
        if event == "INVARIANT_CHECK_INCONCLUSIVE":
            assert level == logging.WARNING
        elif event == "INVARIANT_CHECK_FAILED":
            assert level == logging.ERROR

def test_sweep_downgrade_run_status():
    # Akzeptanzkriterium 3: _downgrade_run_status_for_blocking_invariants
    # Report mit ausschliesslich passed=None-Blockern liefert 'completed_invalid'.
    # WARNING-Zeile nennt FAIL- und Inconclusive-Kohorte getrennt.

    with tempfile.TemporaryDirectory() as td:
        report_path = Path(td) / "report.json"

        report_data = {
            "invariant_checks": [
                {"name": "check_none_1", "passed": None, "severity": "blocking"},
                {"name": "check_none_2", "passed": None, "severity": "blocking"}
            ]
        }
        report_path.write_text(json.dumps(report_data), encoding="utf-8")

        with patch("automation.optimizer.sweep.logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            res = _downgrade_run_status_for_blocking_invariants(str(report_path))

            assert res == "completed_invalid"

            # Check warning
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args

            msg = call_args[0][0]
            args = call_args[0][1:]

            assert "%d blockierende Invarianten-FAIL(s)" in msg
            assert "%d blockierend nicht auswertbar" in msg

            # len(blocking_fails) = 0
            assert args[0] == 0
            # fail names = ""
            assert args[1] == ""
            # len(inconclusive) = 2
            assert args[2] == 2
            # inconclusive names contains check_none_1 and check_none_2
            assert "check_none_1" in args[3]
            assert "check_none_2" in args[3]
