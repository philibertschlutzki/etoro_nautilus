"""Risikorechnung direkt auf den von eToro gelesenen Positionen (account_view)."""
from __future__ import annotations

import urllib.error
from types import SimpleNamespace

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from automation.account_view import EToroAccountView, parse_snapshot
from automation.live_risk import LiveCircuitBreakerWatchdog
from automation.momentum_ls_allocator import MomentumLSAllocator

BTC_EID = 100000      # BTC.ETORO in automation/config/instrument_map.json


def _pnl(credit=9619.58, extra=()):
    positions = [{"positionID": 1, "instrumentID": BTC_EID, "isBuy": True, "amount": 199.56, "leverage": 1,
                  "unrealizedPnL": {"pnL": 0.21, "closeRate": 83029.11}, "stopLossRate": 74646.58}]
    positions += list(extra)
    return {"clientPortfolio": {"credit": credit, "positions": positions}}


def _foreign(pid, eid, amount=100.0):
    return {"positionID": pid, "instrumentID": eid, "isBuy": True, "amount": amount, "leverage": 1,
            "unrealizedPnL": {"pnL": 0.0}}


class _Cache:
    def positions_open(self, instrument_id=None):
        return []


def _view(payload, clock=lambda: 1000.0, **kw):
    return EToroAccountView("k", "u", "real", fetch=lambda *a: payload, clock=clock, **kw)


def test_equity_is_credit_plus_invested_plus_unrealized_and_flags_missing_stops():
    snap = parse_snapshot(_pnl(extra=[_foreign(2, 100330, 156.10)]))
    assert snap.equity == pytest.approx(9619.58 + 199.56 + 156.10 + 0.21)
    assert snap.notional == pytest.approx(355.66)
    assert [p.position_id for p in snap.unprotected()] == ["2"]


def test_snapshot_is_cached_and_fails_closed_when_stale():
    calls, now = [], [1000.0]

    def fetch(*a):
        calls.append(1)
        if len(calls) > 1 and now[0] > 1015:
            raise urllib.error.URLError("offline")
        return _pnl()
    view = EToroAccountView("k", "u", "real", fetch=fetch, clock=lambda: now[0], ttl_s=10, max_age_s=60)
    assert view.snapshot() is not None and view.snapshot() is not None
    assert len(calls) == 1                          # zweiter Aufruf aus dem Zwischenspeicher
    now[0] = 1030.0
    assert view.snapshot() is not None              # Abruf scheitert, Stand (30 s) noch jung genug
    now[0] = 1100.0
    assert view.snapshot() is None                  # zu alt: kein Urteil


def test_allocation_counts_foreign_positions_and_uses_equity_not_free_cash():
    alloc = MomentumLSAllocator(["EURUSD.ETORO", "BTC.ETORO", "SYM9.ETORO"], max_total_exposure_fraction=0.60,
                                max_symbol_exposure_fraction=0.10)
    inst = InstrumentId.from_str("SYM9.ETORO")
    # ohne Kontosicht: Alt-Verhalten, Bemessung auf dem übergebenen (freien) Guthaben
    assert alloc.get_allocation(inst, _Cache(), 4000.0) == pytest.approx(400.0)
    # mit Kontosicht: Equity 4000 + 5900 + 100 = 10000, 5900 USD fremde Positionen belegen das Budget (59 % von 60 %)
    foreign = [_foreign(10 + i, 200000 + i, 590.0) for i in range(10)]
    alloc.set_account_view(_view(_pnl(credit=4000.0 - 199.56, extra=foreign)))
    snap_equity = alloc.risk_equity()
    assert snap_equity == pytest.approx(3800.44 + 199.56 + 5900.0 + 0.21)
    # Budget 60 % von ~10000 = 6000, belegt sind 6099,56 (inkl. BTC): nichts frei -> keine neue Position
    assert alloc.get_allocation(InstrumentId.from_str("SYM9.ETORO"), _Cache(), 1.0) == 0.0


def test_allocation_blocked_when_etoro_already_holds_the_instrument_or_account_unreadable():
    alloc = MomentumLSAllocator(["BTC.ETORO", "ETH.ETORO"], max_total_exposure_fraction=0.60,
                                max_symbol_exposure_fraction=0.10)
    alloc.set_account_view(_view(_pnl()))
    assert alloc.get_allocation(InstrumentId.from_str("BTC.ETORO"), _Cache(), 9000.0) == 0.0   # Position bei eToro offen
    eth = alloc.get_allocation(InstrumentId.from_str("ETH.ETORO"), _Cache(), 1.0)              # Bemessung auf Equity
    assert eth == pytest.approx(0.10 * (9619.58 + 199.56 + 0.21), rel=1e-3)
    broken = MomentumLSAllocator(["ETH.ETORO"])
    broken.set_account_view(EToroAccountView("k", "u", "real", fetch=lambda *a: (_ for _ in ()).throw(
        urllib.error.URLError("offline"))))
    assert broken.get_allocation(InstrumentId.from_str("ETH.ETORO"), _Cache(), 9000.0) == 0.0   # fail-closed


def test_breaker_equity_comes_from_etoro_including_foreign_positions():
    node = SimpleNamespace(
        portfolio=SimpleNamespace(equity=lambda venue: {"USD": 9619.58}, unrealized_pnls=lambda venue: {}),
        cache=_Cache())
    foreign = [_foreign(10 + i, 200000 + i, 100.0) for i in range(20)]       # 2000 USD fremde Testpositionen
    wd = LiveCircuitBreakerWatchdog(node, venue="ETORO", account_view=_view(_pnl(credit=9619.58 - 2000, extra=foreign)))
    equity = wd._read_equity()
    assert equity == pytest.approx(9619.58 - 2000 + 199.56 + 2000 + 0.21)
    assert 1.0 - equity / (9619.58 + 199.56) < 0.001                         # kein falscher Tagesverlust
