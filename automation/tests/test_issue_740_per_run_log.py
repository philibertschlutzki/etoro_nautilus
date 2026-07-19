"""Issue #740 — Pro-Lauf-Log-Datei für den Optimizer statt geteiltem 1MB×5-Ringpuffer.

``setup_bot_logging("optimizer")`` hängte bislang einen ``RotatingFileHandler`` (max 1 MB, 5
Backups) an den EINEN, tages-gemeinsamen Pfad ``logs/optimizer_<date>.log``. Ein einzelner
``--tier all``-Sweep über hunderte Trials erzeugt weit mehr als 5 MB an Events, bevor er fertig
ist — der Ringpuffer überschreibt dann den LAUF-ANFANG (das dokumentierte "Tail-Fragment"-Muster).

Fix: bei explizit gesetztem ``run_id`` schreibt ``setup_bot_logging`` NICHT-rotierend nach
``logs/{log_name}_{run_id}.log`` — ein Lauf = eine vollständige Datei.
"""
import logging

from automation.log_manager import setup_bot_logging, emit_execution_event, default_run_id


def _teardown_logger(name: str):
    lg = logging.getLogger(name)
    for h in list(lg.handlers):
        lg.removeHandler(h)


def test_per_run_log_survives_more_than_5mb_of_events(tmp_path):
    """Über 5 MB an Events innerhalb EINES Laufs — auch die frühesten Zeilen bleiben erhalten."""
    run_id = "testcommit_20260719T000000"
    try:
        logger = setup_bot_logging("optimizer", log_dir=tmp_path, run_id=run_id)

        first_marker = "EARLIEST_EVENT_MUST_SURVIVE_0001"
        emit_execution_event(logger, "optimizer_trial_completed", {"marker": first_marker, "trial": 0})

        # > 5 MB an Folge-Events erzwingen (5x das alte RotatingFileHandler-Limit).
        padding = "x" * 2000
        total_bytes = 0
        trial = 1
        while total_bytes < 5 * 1024 * 1024:
            emit_execution_event(logger, "optimizer_trial_completed",
                                 {"trial": trial, "padding": padding})
            total_bytes += len(padding)
            trial += 1

        for h in logger.handlers:
            h.flush()

        log_files = list(tmp_path.glob(f"optimizer_{run_id}.log"))
        assert len(log_files) == 1, "genau EINE pro-Lauf-Datei erwartet, keine Rotation"
        content = log_files[0].read_text("utf-8")
        assert first_marker in content, (
            "die früheste Event-Zeile darf NICHT verloren gehen — genau das #740-Bug-Muster"
        )
        assert content.count("optimizer_trial_completed") == trial
    finally:
        _teardown_logger("optimizer")


def test_run_id_in_filename_and_no_rotating_handler(tmp_path):
    import logging.handlers
    run_id = "abc1234_20260719T010203"
    try:
        logger = setup_bot_logging("optimizer", log_dir=tmp_path, run_id=run_id)
        log_files = list(tmp_path.glob("optimizer_*.log"))
        assert len(log_files) == 1
        assert log_files[0].name == f"optimizer_{run_id}.log"
        handler_types = [type(h) for h in logger.handlers]
        assert logging.handlers.RotatingFileHandler not in handler_types
        assert logging.FileHandler in handler_types
    finally:
        _teardown_logger("optimizer")


def test_second_run_writes_to_a_different_file_no_overwrite(tmp_path):
    """Zwei aufeinanderfolgende Läufe (verschiedene run_id) dürfen sich NICHT überschreiben."""
    try:
        logger1 = setup_bot_logging("optimizer", log_dir=tmp_path, run_id="run_one")
        emit_execution_event(logger1, "optimizer_trial_completed", {"trial": 0, "run": "one"})
        for h in logger1.handlers:
            h.flush()

        logger2 = setup_bot_logging("optimizer", log_dir=tmp_path, run_id="run_two")
        emit_execution_event(logger2, "optimizer_trial_completed", {"trial": 0, "run": "two"})
        for h in logger2.handlers:
            h.flush()

        file_one = tmp_path / "optimizer_run_one.log"
        file_two = tmp_path / "optimizer_run_two.log"
        assert file_one.exists() and file_two.exists()
        assert '"run": "one"' in file_one.read_text("utf-8")
        assert '"run": "two"' in file_two.read_text("utf-8")
        assert '"run": "two"' not in file_one.read_text("utf-8")
    finally:
        _teardown_logger("optimizer")


def test_momentum_ls_run_logger_stays_on_rotation_without_run_id(tmp_path):
    """Regression: Dauerbetrieb-Logger OHNE run_id bleiben bit-identisch auf Rotation (#740 betrifft
    ausschliesslich den ``"optimizer"``-Logger, sweep.py ruft run_id explizit)."""
    import logging.handlers
    try:
        logger = setup_bot_logging("momentum_ls_run", log_dir=tmp_path)
        handler_types = [type(h) for h in logger.handlers]
        assert logging.handlers.RotatingFileHandler in handler_types
        log_files = list(tmp_path.glob("momentum_ls_run_*.log"))
        assert len(log_files) == 1
        import re
        assert re.fullmatch(r"momentum_ls_run_\d{8}\.log", log_files[0].name), log_files[0].name
    finally:
        _teardown_logger("momentum_ls_run")


def test_default_run_id_includes_git_commit_and_is_filesystem_safe():
    run_id = default_run_id()
    assert "_" in run_id
    for forbidden in ("/", "\\", ":", " "):
        assert forbidden not in run_id
