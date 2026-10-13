import logging

import pytest

from automation import fatal_exit


def test_hard_exit_writes_reason_caller_and_exception_before_exiting(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(fatal_exit, "FATAL_LOG", tmp_path / "errors" / "bot_fatal.log")
    monkeypatch.setattr(fatal_exit.time, "sleep", lambda s: None)
    exits = []
    monkeypatch.setattr(fatal_exit.os, "_exit", lambda code: exits.append(code))
    with caplog.at_level(logging.CRITICAL):
        try:
            raise ConnectionResetError("ws weg")
        except ConnectionResetError:
            fatal_exit.hard_exit("WebSocket geschlossen")
    assert exits == [1]
    text = (tmp_path / "errors" / "bot_fatal.log").read_text()
    assert "WebSocket geschlossen" in text and "ConnectionResetError: ws weg" in text
    assert "test_fatal_exit.py" in text
    assert any("HARD_EXIT" in r.getMessage() for r in caplog.records)
