"""Rohdaten-Rekorder: inkrementelles Anhängen, Blackout, Rates-Filter, Instrumentliste."""
from __future__ import annotations

import datetime as dt
import gzip
import json

from automation import raw_recorder as rr


def _payload(*ts):
    return {"candles": [{"candles": [{"fromDate": t, "open": 1, "high": 2, "low": 0.5, "close": 1.5} for t in ts]}]}


def test_candles_incremental_and_deduplicated(tmp_path):
    p = tmp_path / "1.csv"
    first = rr.new_candles(_payload("2026-10-15T10:00:00Z", "2026-10-15T09:00:00Z"), rr.last_ts_of(p))
    assert rr.append_candles(p, first) == 2
    assert rr.last_ts_of(p) == "2026-10-15T10:00:00Z"
    again = rr.new_candles(_payload("2026-10-15T09:00:00Z", "2026-10-15T10:00:00Z", "2026-10-15T11:00:00Z"), rr.last_ts_of(p))
    assert [r[0] for r in again] == ["2026-10-15T11:00:00Z"]
    rr.append_candles(p, again)
    assert len(p.read_text().splitlines()) == 4                      # Kopf + 3 Kerzen


def test_blackout_window():
    t = lambda m: dt.datetime(2026, 10, 15, 12, m, tzinfo=dt.timezone.utc)
    assert rr.in_blackout(t(56)) and rr.in_blackout(t(3)) and not rr.in_blackout(t(30))


def test_rate_rows_drop_invalid_quotes():
    rows = rr.rate_rows({"rates": [{"instrumentID": 1, "bid": 1.0, "ask": 1.1, "date": "x"},
                                   {"instrumentID": 2, "bid": 0, "ask": 1.0},
                                   {"instrumentID": 3, "bid": 2.0, "ask": 1.0}]}, "T")
    assert [r["id"] for r in rows] == ["1"]


def test_record_rates_appends_gzip_members(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "RAW", tmp_path)
    monkeypatch.setattr(rr, "api_get", lambda url: {"rates": [{"instrumentID": 1, "bid": 1, "ask": 1.1}]})
    now = dt.datetime(2026, 10, 15, 12, 30, tzinfo=dt.timezone.utc)
    assert rr.record_rates(["1"], now) == 1 and rr.record_rates(["1"], now) == 1
    lines = gzip.open(tmp_path / "rates" / "2026-10-15.jsonl.gz", "rt").read().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["id"] == "1"


def test_instrument_ids_cover_whole_map():
    ids = rr.instrument_ids()
    assert len(ids) > 400 and ids == sorted(ids, key=int)


def test_unfinished_candle_is_never_stored():
    got = rr.new_candles(_payload("2026-10-15T10:00:00Z", "2026-10-15T11:00:00Z"), None, before="2026-10-15T11:00:00")
    assert [r[0] for r in got] == ["2026-10-15T10:00:00Z"]


def test_cli_default_covers_extra_universe(monkeypatch):
    """Regression: argparse-Default [] schaltete das EBC-Universum ab (nur 459 statt 1154 Instrumente)."""
    seen = {}
    monkeypatch.setattr(rr, "instrument_ids", lambda extra=None: seen.setdefault("extra", extra) or ["1"])
    monkeypatch.setattr(rr, "record_rates", lambda ids, now: 0)
    monkeypatch.setattr(rr, "in_blackout", lambda now: False)
    rr.main(["rates"])
    assert seen["extra"] is None                       # None = Standard-Universum, nicht "keine Zusatzdateien"
