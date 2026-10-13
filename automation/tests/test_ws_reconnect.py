"""Soft-Reconnect der eToro-WebSockets statt Prozess-Neustart."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from automation.adapters import etoro_data, etoro_execution
from automation.ws_reconnect import ReconnectBudget


def test_budget_erlaubt_bis_zur_grenze_und_vergisst_alte_ereignisse():
    now = [0.0]
    b = ReconnectBudget(max_events=3, window_s=100.0, clock=lambda: now[0])
    assert [b.allow(), b.allow(), b.allow(), b.allow()] == [True, True, True, False]
    now[0] = 101.0
    assert b.allow()


def _log():
    return SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None, info=lambda *a, **k: None)


def _exec_stub(budget_ok=True):
    ws = SimpleNamespace(close=AsyncMock())
    return SimpleNamespace(
        _closing=False, _ws=ws, _log=_log(), _reconnect_budget=SimpleNamespace(allow=lambda: budget_ok),
        _connect_ws=AsyncMock(), _reconcile_positions_on_connect=AsyncMock()), ws


def test_execution_verbindet_neu_und_gleicht_positionen_ab_ohne_hard_exit(monkeypatch):
    called = []
    monkeypatch.setattr(etoro_execution, "hard_exit", lambda *a, **k: called.append(a))
    stub, ws = _exec_stub()
    asyncio.run(etoro_execution.EToroExecutionClient._recover_ws(stub, RuntimeError("no close frame received")))
    ws.close.assert_awaited_once()
    stub._connect_ws.assert_awaited_once()
    stub._reconcile_positions_on_connect.assert_awaited_once()
    assert not called


def test_execution_hard_exit_wenn_budget_aufgebraucht(monkeypatch):
    called = []
    monkeypatch.setattr(etoro_execution, "hard_exit", lambda *a, **k: called.append(a))
    stub, _ = _exec_stub(budget_ok=False)
    asyncio.run(etoro_execution.EToroExecutionClient._recover_ws(stub, RuntimeError("x")))
    assert called and not stub._connect_ws.await_count


def test_beim_gewollten_schliessen_kein_reconnect(monkeypatch):
    called = []
    monkeypatch.setattr(etoro_execution, "hard_exit", lambda *a, **k: called.append(a))
    stub, _ = _exec_stub()
    stub._closing = True
    asyncio.run(etoro_execution.EToroExecutionClient._recover_ws(stub, None))
    assert not called and not stub._connect_ws.await_count


def test_datenclient_verbindet_neu_ohne_instrumente_neu_zu_registrieren(monkeypatch):
    called = []
    monkeypatch.setattr(etoro_data, "hard_exit", lambda *a, **k: called.append(a))
    ws = SimpleNamespace(close=AsyncMock())
    stub = SimpleNamespace(_closing=False, _ws=ws, _log=_log(), _reconnect_budget=SimpleNamespace(allow=lambda: True),
                           _connect=AsyncMock())
    asyncio.run(etoro_data.EToroDataClient._recover_ws(stub, "keepalive ping timeout"))
    stub._connect.assert_awaited_once_with(register_instruments=False)
    ws.close.assert_awaited_once()
    assert not called


def test_datenclient_hard_exit_wenn_budget_aufgebraucht(monkeypatch):
    called = []
    monkeypatch.setattr(etoro_data, "hard_exit", lambda *a, **k: called.append(a))
    stub = SimpleNamespace(_closing=False, _ws=None, _log=_log(), _reconnect_budget=SimpleNamespace(allow=lambda: False),
                           _connect=AsyncMock())
    asyncio.run(etoro_data.EToroDataClient._recover_ws(stub, "x"))
    assert called and not stub._connect.await_count
