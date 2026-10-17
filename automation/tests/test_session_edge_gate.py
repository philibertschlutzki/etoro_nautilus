"""Randzeitgate: Entries nur 14:30-19:30 UTC (Sommerzeit), Exits unberührt; 24/7-Märkte ohne Gate."""
from __future__ import annotations

import datetime as dt

from automation.live_params import resolve_live_entry_edge_gate
from automation.session_windows import local_day, parse_session_window, session_bounds_utc_ns

WIN = parse_session_window({"tz": "America/New_York", "open": "09:30", "close": "16:00"})


def allowed(ts: dt.datetime, om: float = 60.0, cm: float = 30.0) -> bool:
    """Spiegelt die Prüfung in hourly_strategy_base._compute_quantity."""
    ns = int(ts.timestamp() * 1e9)
    o, c = session_bounds_utc_ns(local_day(ns, WIN), WIN)
    return o + int(om * 60e9) <= ns <= c - int(cm * 60e9)


def t(h, m=0):
    return dt.datetime(2026, 10, 14, h, m, tzinfo=dt.timezone.utc)      # Mittwoch, EDT


def test_edge_hours_blocked_core_hours_allowed():
    assert not allowed(t(13, 30)) and not allowed(t(14, 0)) and not allowed(t(14, 29))   # Eroeffnungsphase
    assert allowed(t(14, 30)) and allowed(t(15)) and allowed(t(19)) and allowed(t(19, 30))
    assert not allowed(t(19, 31)) and not allowed(t(20))                                 # Schlussphase


def test_winter_time_shifts_with_the_exchange():
    w = dt.datetime(2026, 11, 4, 15, 0, tzinfo=dt.timezone.utc)          # EST: Sitzung 14:30-21:00 UTC
    assert not allowed(w) and allowed(w.replace(hour=16)) and not allowed(w.replace(hour=21))


def test_kwargs_only_for_windowed_markets():
    assert resolve_live_entry_edge_gate(None) == {}
    k = resolve_live_entry_edge_gate('{"tz": "America/New_York"}')
    assert k == {"entry_open_margin_min": 60.0, "entry_close_margin_min": 30.0}
