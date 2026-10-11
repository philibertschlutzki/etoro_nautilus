import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation import ops_guard as og

NOW = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)


def _make(path: Path, size: int, age_s: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    ts = NOW.timestamp() - age_s
    os.utime(path, (ts, ts))
    return path


def test_budget_unter_schwelle_loescht_nichts(tmp_path):
    logs = tmp_path / "logs"
    f = _make(logs / "a.log", 100, 7200)
    res = og.enforce_log_budget(logs, trim_from=1000, trim_to=500, error_log=logs / "errors" / "e.log", now=NOW)
    assert res["trimmed"] == 0 and f.exists()
    assert not (logs / "errors" / "e.log").exists()


def test_budget_loescht_aelteste_zuerst_und_schreibt_error(tmp_path):
    logs = tmp_path / "logs"
    old = _make(logs / "old.log", 400, 90000)
    mid = _make(logs / "mid.log", 400, 50000)
    new = _make(logs / "new.log", 400, 7200)
    err = logs / "errors" / "ops_guard_errors.log"
    res = og.enforce_log_budget(logs, trim_from=1000, trim_to=900, error_log=err, now=NOW)
    assert not old.exists() and mid.exists() and new.exists()
    assert res["trimmed"] == 1 and not res["stuck"]
    text = err.read_text("utf-8")
    assert " ERROR Log-Budget" in text and "old.log" in text


def test_budget_schuetzt_fehlerlogs_locks_und_junge_dateien(tmp_path):
    logs = tmp_path / "logs"
    err_dir_file = _make(logs / "errors" / "alt.log", 500, 99999)
    err_named = _make(logs / "bot_ERROR_2026.log", 500, 99999)
    lock = _make(logs / "x.lock", 500, 99999)
    keep = _make(logs / ".gitkeep", 10, 99999)
    young = _make(logs / "young.log", 500, 30)
    victim = _make(logs / "victim.log", 500, 80000)
    res = og.enforce_log_budget(logs, trim_from=1000, trim_to=100, error_log=logs / "errors" / "ops_guard_errors.log", now=NOW)
    assert not victim.exists()
    for p in (err_dir_file, err_named, lock, keep, young):
        assert p.exists(), p
    assert res["stuck"] is True
    assert "nur geschützte" in (logs / "errors" / "ops_guard_errors.log").read_text("utf-8")


def test_error_log_rotiert_und_bleibt_geschuetzt(tmp_path):
    err = tmp_path / "logs" / "errors" / "ops_guard_errors.log"
    err.parent.mkdir(parents=True)
    err.write_bytes(b"y" * (og.ERROR_LOG_MAX_BYTES + 1))
    og.append_error("neu", error_log=err, now=NOW)
    assert err.with_name(err.name + ".1").exists()
    assert err.read_text("utf-8").endswith("ERROR neu\n")


def test_classify():
    assert og.classify([1], [], None) == "running"
    assert og.classify([], [], True) == "running"
    assert og.classify([], [2], False) == "starting"
    assert og.classify([], [], False) == "down"


def test_find_processes_ignoriert_eigenen_guard():
    cmds = [(10, "venv/bin/python3 automation/momentum_ls_run.py --incubation"),
            (11, "venv/bin/python3 automation/daily_orchestrator.py --papertrading"),
            (12, "venv/bin/python -m automation.ops_guard"),
            (13, "bash -c grep momentum_ls_run.py")]
    found = og.find_processes(cmds)
    assert found == {"bot": [10], "orchestrator": [11]}


def _check(tmp_path, now, cmdlines, lock_held=False):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    return og.check_bot(state_path=tmp_path / "state.json", lock_path=tmp_path / "lock", log_dir=logs,
                        error_log=logs / "errors" / "e.log", now=now, cmdlines=cmdlines, lock_held=lock_held)


def test_bot_ausfall_wird_einmal_gemeldet_dann_stuendlich(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "incubation_bot_20261011.log").write_text("2026-10-11 Heartbeat: 3720 Ticks verarbeitet.\n", "utf-8")
    bot = [(10, "python3 automation/momentum_ls_run.py")]
    assert _check(tmp_path, NOW, bot) == "running"
    err = logs / "errors" / "e.log"
    assert not err.exists()
    assert _check(tmp_path, NOW + timedelta(minutes=5), []) == "down"
    first = err.read_text("utf-8")
    assert "läuft NICHT" in first and "HARTES Ende" in first and "Heartbeat: 3720" in first
    assert _check(tmp_path, NOW + timedelta(minutes=10), []) == "down"
    assert err.read_text("utf-8") == first            # keine Wiederholung innerhalb der Stunde
    _check(tmp_path, NOW + timedelta(minutes=70), [])
    assert "weiterhin NICHT" in err.read_text("utf-8")
    assert _check(tmp_path, NOW + timedelta(minutes=75), bot) == "running"


def test_sauberes_ende_wird_erkannt(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "incubation_bot_20261011.log").write_text("x\nBot erfolgreich beendet.\n", "utf-8")
    _check(tmp_path, NOW, [(10, "python3 momentum_ls_run.py")])
    _check(tmp_path, NOW + timedelta(minutes=5), [])
    assert "sauberes Ende" in (logs / "errors" / "e.log").read_text("utf-8")


def test_orchestrator_start_ist_kein_ausfall(tmp_path):
    orch = [(11, "python3 automation/daily_orchestrator.py --papertrading")]
    assert _check(tmp_path, NOW, orch) == "starting"
    assert not (tmp_path / "logs" / "errors" / "e.log").exists()
