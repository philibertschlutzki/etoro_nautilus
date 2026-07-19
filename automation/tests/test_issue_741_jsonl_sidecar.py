"""Issue #741 — echtes JSONL-Sidecar für strukturierte Events.

``emit_execution_event`` schrieb bislang nur ``[JSON_EVENT] {...}`` als Substring INNERHALB einer
formatierten ``StructuredFormatter``-Zeile — trotz sweep.py-Kommentars "rotierende JSONL" ist das
KEIN valides JSONL (``json.loads(line)`` scheitert ohne vorherige Regex-Extraktion des Präfixes).

Fix: additiv zur bestehenden Prosa-Logzeile landet dasselbe Event-Dict als EINE valide,
dekorationsfreie JSON-Zeile in ``logs/{log_name}_{run_id}.events.jsonl``.
"""
import json
import logging

import pytest

from automation.log_manager import setup_bot_logging, emit_execution_event


def _teardown_logger(name: str):
    lg = logging.getLogger(name)
    for h in list(lg.handlers):
        lg.removeHandler(h)


def test_jsonl_sidecar_has_exactly_n_valid_json_lines(tmp_path):
    run_id = "commit_20260719T000000"
    try:
        logger = setup_bot_logging("optimizer", log_dir=tmp_path, run_id=run_id)
        n = 12
        for i in range(n):
            emit_execution_event(logger, "optimizer_trial_completed", {"trial": i, "symbol": "A.ETORO"})

        sidecar = tmp_path / f"optimizer_{run_id}.events.jsonl"
        assert sidecar.exists(), "JSONL-Sidecar-Datei wurde nicht erzeugt"

        lines = sidecar.read_text("utf-8").splitlines()
        assert len(lines) == n

        for i, line in enumerate(lines):
            payload = json.loads(line)  # muss OHNE Praefix-Strip parsebar sein
            assert payload["event_type"] == "optimizer_trial_completed"
            assert payload["trial"] == i
            assert "timestamp_utc" in payload
    finally:
        _teardown_logger("optimizer")


def test_jsonl_sidecar_path_follows_run_id_convention(tmp_path):
    run_id = "deadbeef_20260719T120000"
    try:
        logger = setup_bot_logging("optimizer", log_dir=tmp_path, run_id=run_id)
        emit_execution_event(logger, "sweep_completed", {"pairs": 3})
        assert (tmp_path / f"optimizer_{run_id}.events.jsonl").exists()
        # Die Prosa-Logzeile bleibt fuer Menschen/Terminal unveraendert (additive, nicht ersetzende
        # Aenderung) — [JSON_EVENT]-Praefix weiterhin in der .log-Datei vorhanden.
        log_content = (tmp_path / f"optimizer_{run_id}.log").read_text("utf-8")
        assert "[JSON_EVENT]" in log_content
    finally:
        _teardown_logger("optimizer")


def test_jsonl_sidecar_without_run_id_uses_date_based_name(tmp_path):
    """Loggers ohne run_id (Dauerbetrieb) bekommen ebenfalls einen Sidecar — tages-basiert,
    analog zum rotierenden Prosa-Log."""
    try:
        logger = setup_bot_logging("catalog_service_test741", log_dir=tmp_path)
        emit_execution_event(logger, "CATALOG_REFRESH", {"symbols": 5})
        sidecars = list(tmp_path.glob("catalog_service_test741_*.events.jsonl"))
        assert len(sidecars) == 1
        payload = json.loads(sidecars[0].read_text("utf-8").splitlines()[0])
        assert payload["event_type"] == "CATALOG_REFRESH"
    finally:
        _teardown_logger("catalog_service_test741")


def test_emit_execution_event_without_prior_setup_is_a_silent_noop(tmp_path):
    """Ein Logger, der NIE via setup_bot_logging initialisiert wurde, hat keinen registrierten
    Sidecar-Pfad — emit_execution_event darf dabei nicht crashen (No-Op)."""
    logger = logging.getLogger("never_set_up_logger_741")
    emit_execution_event(logger, "SOME_EVENT", {"x": 1})  # muss klaglos durchlaufen
