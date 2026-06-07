"""
tests/test_log_manager.py
==========================
Tests für automation/log_manager.py — abgedeckte Architektur-Regeln aus AGENTS.md §5.9:
  - JSON-Events im [JSON_EVENT]-Format (LLM-parsbar)
  - Log-Cleanup löscht Dateien älter als LOG_RETENTION_D Tage
  - setup_bot_logging erzeugt RotatingFileHandler + StreamHandler
  - emit_order_event erzeugt strukturierten ORDER_<status>-Event
"""
import json
import logging
import time
from pathlib import Path

import pytest

from automation.log_manager import (
    cleanup_old_logs,
    emit_execution_event,
    emit_order_event,
    setup_bot_logging,
)


class _CapturingHandler(logging.Handler):
    """Collects all log records for assertion."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_emit_execution_event_json_format(tmp_path):
    """[JSON_EVENT] prefix and valid JSON payload (AGENTS.md §5.9)."""
    logger = setup_bot_logging("test_exec", log_dir=tmp_path)
    handler = _CapturingHandler()
    logger.addHandler(handler)

    emit_execution_event(logger, "ORDER_SUBMITTED", {"symbol": "AAPL.ETORO", "amount": 100.0})

    json_records = [r for r in handler.records if "[JSON_EVENT]" in r.getMessage()]
    assert len(json_records) == 1

    msg = json_records[0].getMessage()
    assert msg.startswith("[JSON_EVENT] ")
    payload = json.loads(msg[len("[JSON_EVENT] "):])
    assert payload["event_type"] == "ORDER_SUBMITTED"
    assert payload["symbol"] == "AAPL.ETORO"
    assert "timestamp_utc" in payload


def test_emit_order_event_contains_status(tmp_path):
    """emit_order_event produces ORDER_<status> event (AGENTS.md §5.9)."""
    logger = setup_bot_logging("test_order", log_dir=tmp_path)
    handler = _CapturingHandler()
    logger.addHandler(handler)

    emit_order_event(logger, "TSLA.ETORO", "BUY", 150.0, price=230.5, status="FILLED")

    json_records = [r for r in handler.records if "[JSON_EVENT]" in r.getMessage()]
    assert len(json_records) == 1
    payload = json.loads(json_records[0].getMessage()[len("[JSON_EVENT] "):])
    assert payload["event_type"] == "ORDER_FILLED"
    assert payload["side"] == "BUY"
    assert payload["amount_usd"] == 150.0


def test_cleanup_old_logs_deletes_stale_files(tmp_path):
    """cleanup_old_logs removes files older than max_age_days (AGENTS.md §5.9)."""
    stale = tmp_path / "bot_20200101.log"
    stale.write_text("old log")
    # Set mtime to 10 days in the past
    old_ts = time.time() - 10 * 86400
    import os
    os.utime(stale, (old_ts, old_ts))

    fresh = tmp_path / "bot_today.log"
    fresh.write_text("fresh log")

    deleted = cleanup_old_logs(tmp_path, max_age_days=7)

    assert deleted == 1
    assert not stale.exists()
    assert fresh.exists()


def test_cleanup_old_logs_missing_dir():
    """cleanup_old_logs returns 0 if directory does not exist."""
    deleted = cleanup_old_logs(Path("/nonexistent/path/xyz"))
    assert deleted == 0


def test_setup_bot_logging_creates_file(tmp_path):
    """setup_bot_logging creates a .log file in log_dir (AGENTS.md §5.9)."""
    logger = setup_bot_logging("mybot", log_dir=tmp_path)
    log_files = list(tmp_path.glob("mybot_*.log"))
    assert len(log_files) == 1


def test_setup_bot_logging_has_rotating_file_handler(tmp_path):
    """setup_bot_logging attaches a RotatingFileHandler (AGENTS.md §5.9)."""
    import logging.handlers
    logger = setup_bot_logging("rottest", log_dir=tmp_path)
    handler_types = [type(h) for h in logger.handlers]
    assert logging.handlers.RotatingFileHandler in handler_types
