import pytest
import os
import time
import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from automation.log_manager import emit_execution_event, cleanup_old_logs

class TestLogManager:
    def test_emit_execution_event_json_format(self):
        logger = Mock()
        payload = {"symbol": "AAPL", "amount": 100.0}

        emit_execution_event(logger, "ORDER_SUBMITTED", payload)

        assert logger.log.called
        args, kwargs = logger.log.call_args

        # log.log(level, msg)
        msg = args[1]
        assert "[JSON_EVENT]" in msg

        # Extract the JSON payload
        json_str = msg.split("[JSON_EVENT] ")[1]

        # Check it is valid JSON
        parsed_json = json.loads(json_str)
        assert parsed_json["event_type"] == "ORDER_SUBMITTED"
        assert parsed_json["symbol"] == "AAPL"
        assert parsed_json["amount"] == 100.0
        assert "timestamp_utc" in parsed_json

    def test_cleanup_old_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create a mock old log file
            old_log = log_dir / "old_test.log"
            old_log.touch()

            # Create a mock new log file
            new_log = log_dir / "new_test.log"
            new_log.touch()

            # Manipulate mtime
            current_time = time.time()
            # Old log = 8 days old
            os.utime(old_log, (current_time - 8 * 86400, current_time - 8 * 86400))
            # New log = 1 day old
            os.utime(new_log, (current_time - 1 * 86400, current_time - 1 * 86400))

            deleted = cleanup_old_logs(log_dir, max_age_days=7)

            assert deleted == 1
            assert not old_log.exists()
            assert new_log.exists()
