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


def _http_429(retry_after=None):
    from email.message import Message
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://x", 429, "Too Many Requests", headers, None)


def test_rate_limit_backoff_stops_hammering_and_serves_the_last_good_snapshot():
    now = [1000.0]
    calls = []

    def fetch(*a):
        calls.append(now[0])
        if len(calls) == 1:
            return _pnl()
        raise _http_429(retry_after=20)

    view = EToroAccountView("k", "u", "demo", fetch=fetch, clock=lambda: now[0], ttl_s=10.0, max_age_s=120.0)
    assert view.snapshot() is not None                    # Abruf 1 ok
    now[0] = 1011.0
    assert view.snapshot() is not None                    # Abruf 2 -> 429, alter Stand bleibt gültig
    for t in (1012.0, 1020.0, 1030.0):                    # innerhalb von 20 s (Retry-After): kein weiterer Abruf
        now[0] = t
        view.snapshot(fresh=True)
    assert len(calls) == 2
    now[0] = 1032.0                                       # Retry-After vorbei: wieder ein Versuch
    view.snapshot()
    assert len(calls) == 3


def test_backoff_defaults_and_cap():
    from automation import account_view as av
    assert av._backoff_s(_http_429()) == av.RATE_LIMIT_BACKOFF_S
    assert av._backoff_s(_http_429(900)) == av.MAX_BACKOFF_S
    assert av._backoff_s(OSError("x")) == av.ERROR_BACKOFF_S


def _fx_position(pid, eid, amount, units, rate):
    return {"positionID": pid, "instrumentID": eid, "isBuy": False, "amount": amount, "leverage": 1, "units": units,
            "openRate": rate, "unrealizedPnL": {"pnL": 0.0}, "stopLossRate": 1.0}


def test_adopted_fx_position_counts_with_its_usd_amount_not_units_times_quote_price():
    """Regression 13.10.: übernommene USDMXN-/NZDJPY-Position (Einheiten × Kurs in der Quote-Währung) ergab im Cache ein
    Vielfaches ihres USD-Betrags, Exposure 668 % und gesperrte Einstiege. Hier USDZAR: 1.991,12 USD = 1.991,12 Einheiten @ 18,4."""
    usdzar_eid = 42
    snap = parse_snapshot({"clientPortfolio": {"credit": 8_100.0, "positions": [
        _fx_position(7, usdzar_eid, 1991.12, 1991.12, 18.4)]}})
    adopted = SimpleNamespace(quantity=1991.12, avg_px_open=18.4)            # Cache-Sicht der übernommenen Position: 36.637 "USD"

    class _FxCache:
        def positions_open(self, instrument_id=None):
            return [adopted] if str(instrument_id) == "USDZAR.ETORO" else []

    alloc = MomentumLSAllocator(["USDZAR.ETORO", "BTC.ETORO"], max_total_exposure_fraction=0.60,
                                max_symbol_exposure_fraction=0.02)
    alloc.set_account_view(SimpleNamespace(snapshot=lambda fresh=False: snap))
    result = alloc.get_allocation(InstrumentId.from_str("BTC.ETORO"), _FxCache(), 10_000.0)
    assert result == pytest.approx(0.02 * snap.equity)           # Budget frei: 1.991 USD sind nur ca. 2 % der Equity


def test_freshly_filled_position_not_yet_in_snapshot_still_counts_from_the_cache():
    snap = parse_snapshot({"clientPortfolio": {"credit": 10_000.0, "positions": []}})
    fresh = SimpleNamespace(quantity=20.0, avg_px_open=100.0)               # 2.000 USD, eToro zeigt sie noch nicht

    class _FreshCache:
        def positions_open(self, instrument_id=None):
            return [fresh] if str(instrument_id) == "NZDJPY.ETORO" else []

    alloc = MomentumLSAllocator(["NZDJPY.ETORO", "BTC.ETORO"], max_total_exposure_fraction=0.30,
                                max_symbol_exposure_fraction=0.20)
    alloc.set_account_view(SimpleNamespace(snapshot=lambda fresh=False: snap))
    # Equity 10.000, Gesamtbudget 30 % = 3.000, Cache belegt 2.000 → frei 1.000 (10 %), Symbol-Cap 20 % → 10 % Allokation
    assert alloc.get_allocation(InstrumentId.from_str("BTC.ETORO"), _FreshCache(), 10_000.0) == pytest.approx(1000.0)
