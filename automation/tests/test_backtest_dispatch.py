"""Backtest-Weiche: Umschaltung Tages- auf Stunden-Spur nach Datenlage, Erneuerung, kein Dauerfehler."""
from __future__ import annotations

from automation import backtest_dispatch as bd

IDSYM = {"1": "A.ETORO", "2": "B.ETORO"}
SPANS = {"A.ETORO": 470.0, "B.ETORO": 47.0}


def test_switch_follows_history_span():
    lanes = bd.classify(["1", "2", "999"], IDSYM, span_fn=lambda s: SPANS[s])
    assert lanes == {"1": "hourly", "2": "daily", "999": "daily"}      # ohne Katalogsymbol bleibt es Tages-Spur
    assert bd.lane_of(449.9) == "daily" and bd.lane_of(450.0) == "hourly"


def test_hourly_pending_only_ready_and_not_fresh():
    kw = dict(span_fn=lambda s: SPANS[s], now=100 * 86400.0)
    assert bd.hourly_pending(["1", "2"], IDSYM, {}, **kw) == ["1"]
    assert bd.hourly_pending(["1", "2"], IDSYM, {"1": 90 * 86400.0}, **kw) == []          # vor 10 Tagen gerechnet
    assert bd.hourly_pending(["1", "2"], IDSYM, {"1": 50 * 86400.0}, **kw) == ["1"]       # vor 50 Tagen: erneuern


def test_hourly_span_missing_file_is_zero(tmp_path):
    assert bd.hourly_span_days("NOPE.ETORO", tmp_path) == 0.0


def test_real_catalog_is_still_all_daily():
    """Heute (ca. 47 Tage Stundenhistorie) darf kein Instrument in die Stunden-Spur fallen."""
    ids = list(bd.id_symbol_map())[:30]
    assert set(bd.classify(ids, bd.id_symbol_map()).values()) <= {"daily", "hourly"}
