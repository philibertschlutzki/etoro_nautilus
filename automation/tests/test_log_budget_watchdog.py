import os
import time

from automation import bot_watchdog
from automation.log_budget import enforce_log_budget, is_protected


def _mk(path, size, age_s, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (now - age_s, now - age_s))


def test_budget_deletes_oldest_and_keeps_errors(tmp_path):
    now = time.time()
    logs = tmp_path / "logs"
    _mk(logs / "old.log", 400, 7200 * 5, now)
    _mk(logs / "mid.log", 400, 7200 * 3, now)
    _mk(logs / "errors" / "bot_errors.log", 400, 7200 * 9, now)
    _mk(logs / "paper_report_alerts.log", 400, 7200 * 8, now)
    _mk(logs / "executor.sh", 400, 7200 * 8, now)
    _mk(logs / "active.log", 400, 10, now)
    res = enforce_log_budget(logs, max_bytes=1900, now=now)
    assert not (logs / "old.log").exists() and not (logs / "mid.log").exists()
    for kept in ("errors/bot_errors.log", "paper_report_alerts.log", "executor.sh", "active.log"):
        assert (logs / kept).exists()
    assert not res["over_budget"]
    text = (logs / "errors" / "log_budget_errors.log").read_text()
    assert "LOG_BUDGET_EXCEEDED" in text and "LOG_BUDGET_CLEANED" in text


def test_budget_noop_under_limit_and_reports_unreachable(tmp_path):
    now = time.time()
    logs = tmp_path / "logs"
    _mk(logs / "a.log", 100, 9999, now)
    assert enforce_log_budget(logs, max_bytes=1000, now=now)["deleted"] == []
    _mk(logs / "errors" / "e.log", 5000, 9999, now)
    res = enforce_log_budget(logs, max_bytes=1000, now=now)
    assert res["over_budget"] and (logs / "errors" / "e.log").exists()
    assert "LOG_BUDGET_NOT_REACHED" in (logs / "errors" / "log_budget_errors.log").read_text()


def test_is_protected(tmp_path):
    logs = tmp_path
    assert is_protected(logs / "x_ERROR.log", logs)
    assert is_protected(logs / "paper_reports" / "a.md", logs)
    assert not is_protected(logs / "optimizer_1.log", logs)


def test_classify():
    c = bot_watchdog.classify
    assert c(lock_held=True, orchestrator=False, log_age_s=10) == "UP"
    assert c(lock_held=True, orchestrator=False, log_age_s=bot_watchdog.HUNG_AFTER_S + 1) == "HUNG"
    assert c(lock_held=False, orchestrator=True, log_age_s=None) == "STARTING"
    assert c(lock_held=False, orchestrator=False, log_age_s=None) == "DOWN"


def test_watchdog_alerts_only_on_state_change(tmp_path):
    logs = tmp_path / "logs"
    state = tmp_path / "state.json"
    lock = tmp_path / "missing.lock"
    for _ in range(3):
        assert bot_watchdog.run(logs, state, lock, orchestrator=False) == "DOWN"
    lines = (logs / "errors" / "bot_watchdog_errors.log").read_text().splitlines()
    assert len(lines) == 1 and "BOT_DOWN" in lines[0]
    bot_watchdog.run(logs, state, lock, orchestrator=True)
    assert "BOT_STARTING" in (logs / "bot_watchdog_status.log").read_text()
