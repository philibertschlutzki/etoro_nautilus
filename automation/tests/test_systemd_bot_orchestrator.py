import json

from automation import systemd_bot
from automation.tests import test_issue_1368_incubation as base

_WINNERS = {"AAA.ETORO": {"strategy": "SmaCrossoverStrategy", "oos_eligible": True, "oos_metrics": {"psr": 0.9}}}


def _setup(tmp_path, monkeypatch):
    orch = base._orch_env(tmp_path, monkeypatch)
    monkeypatch.delenv("ETORO_ENV", raising=False)
    defaults, raw = base.load_live_param_sources(base.REPO / "automation" / "config")
    monkeypatch.setattr(orch, "_load_live_param_sources", lambda: (defaults, raw))
    return orch


def test_systemd_modus_startet_dienst_statt_popen(tmp_path, monkeypatch):
    orch = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv(systemd_bot.UNIT_ENV, "etoro-paper-bot.service")
    started = []
    monkeypatch.setattr(systemd_bot, "start_unit", lambda unit, **kw: (started.append(unit) or (True, "")))
    popen = base._Popen()
    res = orch.phase5b_incubation(orch.logging.getLogger("t"), popen=popen,
                                  selection_fn=base._selection(tmp_path, _WINNERS), now=base._T0)
    assert res["status"] == "started" and res["incubating"] == ["AAA.ETORO"]
    assert started == ["etoro-paper-bot.service"] and popen.calls == []


def test_systemd_start_fehler_ist_start_failed(tmp_path, monkeypatch):
    orch = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv(systemd_bot.UNIT_ENV, "etoro-paper-bot.service")
    monkeypatch.setattr(systemd_bot, "start_unit", lambda unit, **kw: (False, "Unit not found"))
    popen = base._Popen()
    res = orch.phase5b_incubation(orch.logging.getLogger("t"), popen=popen,
                                  selection_fn=base._selection(tmp_path, _WINNERS), now=base._T0)
    assert res["status"] == "start_failed" and popen.calls == []


def test_ohne_variable_bleibt_popen(tmp_path, monkeypatch):
    orch = _setup(tmp_path, monkeypatch)
    monkeypatch.delenv(systemd_bot.UNIT_ENV, raising=False)
    monkeypatch.setattr(systemd_bot, "start_unit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nicht aufrufen")))
    popen = base._Popen()
    res = orch.phase5b_incubation(orch.logging.getLogger("t"), popen=popen,
                                  selection_fn=base._selection(tmp_path, _WINNERS), now=base._T0)
    assert res["status"] == "started" and len(popen.calls) == 1
