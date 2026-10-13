import subprocess
from pathlib import Path

from automation import systemd_bot as sb


def _proc(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def test_unit_nur_mit_variable():
    assert sb.configured_unit({}) is None
    assert sb.configured_unit({sb.UNIT_ENV: "  "}) is None
    assert sb.configured_unit({sb.UNIT_ENV: "etoro-paper-bot.service"}) == "etoro-paper-bot.service"


def test_start_unit_nutzt_systemctl_user():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return _proc(0)
    ok, err = sb.start_unit("u.service", run=run)
    assert ok and err == "" and calls == [["systemctl", "--user", "start", "u.service"]]


def test_start_unit_fehler_wird_gemeldet():
    ok, err = sb.start_unit("u.service", run=lambda cmd, **kw: _proc(5, err="Unit not found"))
    assert not ok and "Unit not found" in err


def test_stop_fn_wartet_auf_freie_sperre():
    calls, states = [], iter([True, True, False])

    def run(cmd, **kw):
        calls.append(cmd)
        return _proc(0)
    t = [0.0]
    stop = sb.make_stop_fn("u.service", run=run, sleep=lambda s: t.__setitem__(0, t[0] + s),
                           monotonic=lambda: t[0], held=lambda p: next(states))
    assert stop(Path("/x/lock"), 123, timeout_s=10) is True
    assert calls == [["systemctl", "--user", "stop", "u.service"]]


def test_stop_fn_timeout_bei_gehaltener_sperre():
    t = [0.0]
    stop = sb.make_stop_fn("u.service", run=lambda cmd, **kw: _proc(0), sleep=lambda s: t.__setitem__(0, t[0] + s),
                           monotonic=lambda: t[0], held=lambda p: True)
    assert stop(Path("/x/lock"), None, timeout_s=3) is False


def test_is_active():
    assert sb.is_active("u", run=lambda cmd, **kw: _proc(0)) is True
    assert sb.is_active("u", run=lambda cmd, **kw: _proc(3)) is False
