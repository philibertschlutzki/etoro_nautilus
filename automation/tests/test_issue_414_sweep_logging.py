"""Issue #414 — Sweep-Entrypoint ohne Logging-Init verwirft die `[JSON_EVENT]`-Telemetrie.

`getLogger("optimizer")` hat im Standalone-Sweep keinen Handler; `emit_execution_event` loggt auf
INFO → Pythons `lastResort` gibt nur WARNING+ aus ⇒ alle #404-Per-Trial-Events fallen weg. Fix:
`sweep.main()` ruft als ERSTE Anweisung `setup_bot_logging("optimizer")` (File-Handler DEBUG +
Stream-Handler INFO, `propagate=False`). Optunas eigener Logger bleibt unangetastet (Pitfall #74).
"""
import logging

import optuna

from automation.log_manager import setup_bot_logging, emit_execution_event
from automation.optimizer import sweep


def _teardown_optimizer_logger():
    lg = logging.getLogger("optimizer")
    for h in list(lg.handlers):
        lg.removeHandler(h)


def test_optimizer_logger_has_file_and_stream_handler_after_setup(tmp_path):
    try:
        setup_bot_logging("optimizer", log_dir=tmp_path)
        lg = logging.getLogger("optimizer")
        assert lg.handlers, "Logger MUSS nach setup_bot_logging Handler haben"
        has_file = any(isinstance(h, logging.handlers.RotatingFileHandler) for h in lg.handlers)
        has_stream = any(isinstance(h, logging.StreamHandler)
                         and not isinstance(h, logging.handlers.RotatingFileHandler)
                         for h in lg.handlers)
        assert has_file, "File-Handler fehlt"
        assert has_stream, "Stream-Handler fehlt"
    finally:
        _teardown_optimizer_logger()


def test_json_event_reaches_file(tmp_path):
    """Nach setup landet ein emit_execution_event als `[JSON_EVENT]`-Zeile in der Logdatei."""
    import logging.handlers  # noqa: F401 (sichert die handlers-Namespaces fuer den Vorgaenger-Test)
    try:
        logger = setup_bot_logging("optimizer", log_dir=tmp_path)
        emit_execution_event(logger, "optimizer_trial_completed",
                             {"symbol": "WDAY.ETORO", "oos_evaluated": False})
        for h in logger.handlers:
            h.flush()
        log_files = list(tmp_path.glob("optimizer_*.log"))
        assert log_files, "keine Logdatei erzeugt"
        content = log_files[0].read_text("utf-8")
        assert "[JSON_EVENT]" in content
        assert "optimizer_trial_completed" in content
        assert "WDAY.ETORO" in content
    finally:
        _teardown_optimizer_logger()


def test_sweep_main_initializes_logging_first(monkeypatch, tmp_path):
    """sweep.main([...]) ruft setup_bot_logging('optimizer') auf (mit gemocktem Sweep-Lauf)."""
    calls = []
    monkeypatch.setattr(sweep, "setup_bot_logging", lambda name, *a, **k: calls.append(name))
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: [])
    monkeypatch.setattr(sweep, "_resolve_strategies", lambda arg: ["S"])
    monkeypatch.setattr(sweep, "log_active_config", lambda *a, **k: None)

    out = sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--tier", "all"])
    assert out == []
    assert calls == ["optimizer"], "setup_bot_logging('optimizer') MUSS aufgerufen werden"


def test_optuna_logger_untouched(tmp_path):
    """setup_bot_logging('optimizer') darf Optunas Logger/Verbosity NICHT veraendern (Pitfall #74)."""
    before = optuna.logging.get_verbosity()
    try:
        setup_bot_logging("optimizer", log_dir=tmp_path)
        assert optuna.logging.get_verbosity() == before
        assert optuna.logging.get_verbosity() != optuna.logging.ERROR
    finally:
        _teardown_optimizer_logger()
