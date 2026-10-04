"""Issue #1365 (GH #1261, P1; Duplikat GH #1262) — ``effective_span_days`` war eine Monatsrundung: sie überstieg
die rohe Spanne (123,0 statt 91,2 Tage), liess zu kurze Kataloge passieren (457 statt 397,2 Tage ⇒ PASS gegen
441) und sah keine Lücken innerhalb eines Monats.

Fix: Segmentierung auf Tick-Ebene (Lücke > ``max_contiguity_gap_days`` bricht das Segment; mit Session-Kalender
nur, wenn ein Handelstag dazwischen fehlt), ``effective_span_days = last − first`` des längsten Segments,
Assertion ``effective <= raw``, ``resolution_segments`` als ``{start_utc, end_utc, days}``, ``largest_gap_days``.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from automation import session_windows as sw
from automation.optimizer import sweep

_H = 3_600_000_000_000
_D = 86_400_000_000_000


def _write(tmp_path, symbol, ts_list, intervals=None):
    d = tmp_path / "data" / "quote_tick" / symbol / "OneHour"
    d.mkdir(parents=True, exist_ok=True)
    n = len(ts_list)
    pq.write_table(pa.table({
        "bid_price": pa.array([b"\x00" * 16] * n, type=pa.binary(16)),
        "ask_price": pa.array([b"\x00" * 16] * n, type=pa.binary(16)),
        "ts_event": pa.array(ts_list, type=pa.uint64()),
        "bar_interval_ns": pa.array(intervals or [_H] * n, type=pa.uint64()),
    }), str(d / "data.parquet"))


def _weekday_candles(start: datetime, end: datetime, *, skip=None) -> list[int]:
    """Stundenkerzen 13–19 UTC an Werktagen zwischen ``start`` und ``end`` (inklusive Tage)."""
    out, day = [], start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        if day.weekday() < 5 and not (skip and skip[0] <= day < skip[1]):
            for h in range(13, 20):
                t = day + timedelta(hours=h)
                if start <= t <= end:
                    out.append(int(t.timestamp()) * 1_000_000_000)
        day += timedelta(days=1)
    return out


def _utc(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_this_run_catalog_effective_equals_raw_91_2(tmp_path):
    _write(tmp_path, "A.ETORO", _weekday_candles(_utc(2026, 7, 3, 13), _utc(2026, 10, 2, 19)))
    r = sweep.check_catalog_resolution_homogeneity("A.ETORO", catalog_path=tmp_path, required_span_days=441)
    assert r["raw_span_days"] == pytest.approx(91.25, abs=0.01)
    assert r["effective_span_days"] == pytest.approx(r["raw_span_days"], abs=1e-9)
    assert r["passed"] is False
    assert r["largest_gap_days"] == pytest.approx(2.75, abs=0.01)     # Wochenende Fr 19:00 → Mo 13:00


def test_397_day_catalog_now_fails_against_441(tmp_path):
    _write(tmp_path, "B.ETORO", _weekday_candles(_utc(2025, 7, 31, 13), _utc(2026, 9, 1, 19)))
    r = sweep.check_catalog_resolution_homogeneity("B.ETORO", catalog_path=tmp_path, required_span_days=441)
    assert r["raw_span_days"] == pytest.approx(397.25, abs=0.01)
    assert r["effective_span_days"] == pytest.approx(397.25, abs=0.01)
    assert r["passed"] is False                       # vorher 457,0 ⇒ fälschlich PASS


def test_three_week_gap_splits_the_segment(tmp_path):
    ts = _weekday_candles(_utc(2025, 6, 2, 13), _utc(2026, 9, 30, 19), skip=(_utc(2026, 5, 4), _utc(2026, 5, 25)))
    _write(tmp_path, "C.ETORO", ts)
    r = sweep.check_catalog_resolution_homogeneity("C.ETORO", catalog_path=tmp_path, required_span_days=441)
    assert r["raw_span_days"] == pytest.approx(485.25, abs=0.01)
    assert len(r["resolution_segments"]) == 2
    first, second = r["resolution_segments"]
    assert first["end_utc"] == "2026-05-01T19:00:00Z" and second["start_utc"] == "2026-05-25T13:00:00Z"
    assert r["effective_span_days"] == pytest.approx(max(first["days"], second["days"]))
    assert r["largest_gap_days"] > 20
    assert r["passed"] is False                       # vorher 487,0 ⇒ Lücke unsichtbar, PASS


def test_a_foreign_resolution_tick_ends_the_segment(tmp_path):
    hourly = _weekday_candles(_utc(2026, 1, 5, 13), _utc(2026, 3, 27, 19))
    mid = len(hourly) // 2
    intervals = [_H] * len(hourly)
    intervals[mid] = _D
    _write(tmp_path, "D.ETORO", hourly, intervals)
    r = sweep.check_catalog_resolution_homogeneity("D.ETORO", catalog_path=tmp_path, required_span_days=10)
    assert len(r["resolution_segments"]) == 2


def test_holiday_weekend_does_not_break_the_segment_with_a_session_calendar(tmp_path):
    # Thanksgiving 2026 (Do 26.11.) + Brückentag Fr 27.11. ohne Daten: Mi 19:00 → Mo 13:00 = 4,75 Tage.
    ts = [t for t in _weekday_candles(_utc(2026, 11, 16, 13), _utc(2026, 12, 4, 19))
          if datetime.fromtimestamp(t / 1e9, tz=timezone.utc).date().isoformat() not in ("2026-11-26", "2026-11-27")]
    _write(tmp_path, "E.ETORO", ts)
    plain = sweep.check_catalog_resolution_homogeneity("E.ETORO", catalog_path=tmp_path, required_span_days=10)
    assert len(plain["resolution_segments"]) == 2          # 4,75 Tage > 4,0 und ohne Kalender
    # Mit NYSE-Kalender: der 27.11. ist ein Handelstag ⇒ fehlt ⇒ bricht weiterhin.
    ny = sw.parse_session_window({"tz": "America/New_York", "open": "09:30", "close": "16:00"})
    cal = sweep.check_catalog_resolution_homogeneity("E.ETORO", catalog_path=tmp_path, required_span_days=10,
                                                     session_window=ny)
    assert len(cal["resolution_segments"]) == 2
    # Nur der Feiertag fehlt (Fr 27.11. vorhanden) — Weihnachten 2026 (Fr 25.12.) + Wochenende + Mo 28.12. fehlt nicht.
    ts2 = [t for t in _weekday_candles(_utc(2026, 12, 14, 13), _utc(2027, 1, 8, 19))
           if datetime.fromtimestamp(t / 1e9, tz=timezone.utc).date().isoformat() not in ("2026-12-25", "2027-01-01")]
    _write(tmp_path, "F.ETORO", ts2)
    cal2 = sweep.check_catalog_resolution_homogeneity("F.ETORO", catalog_path=tmp_path, required_span_days=10,
                                                      session_window=ny, max_contiguity_gap_days=2.0)
    assert len(cal2["resolution_segments"]) == 1          # nur Nicht-Handelstage in den Lücken


@pytest.mark.parametrize("seed", range(25))
def test_property_effective_never_exceeds_raw(tmp_path, seed):
    rng = random.Random(seed)
    t, ts, intervals = 1_750_000_000 * 1_000_000_000, [], []
    for _ in range(rng.randint(5, 400)):
        t += rng.choice([_H, _H, 2 * _H, _D, 3 * _D, 10 * _D])
        ts.append(t)
        intervals.append(_H if rng.random() > 0.1 else _D)
    _write(tmp_path, f"P{seed}.ETORO", ts, intervals)
    r = sweep.check_catalog_resolution_homogeneity(f"P{seed}.ETORO", catalog_path=tmp_path, required_span_days=30)
    assert r["effective_span_days"] <= r["raw_span_days"] + 1e-9
    assert all(seg["days"] <= r["raw_span_days"] + 1e-9 for seg in r["resolution_segments"])
