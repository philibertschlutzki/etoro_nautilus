"""Issue #1334 (GH #1228) — ``check_catalog_resolution_homogeneity``: eine Spanne aus
``latest - earliest`` ist kein Nachweis über die Belegung dazwischen. Ein Katalog aus 900 Tagen
``OneDay``- und 70 Tagen ``OneHour``-Ticks liefert genau EIN Stunden-Segment von 70 Tagen als
``effective_span_days``, nicht die volle rohe Spanne.
"""
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from automation.optimizer import sweep

_NS_PER_HOUR = 3_600_000_000_000
_NS_PER_DAY = 86_400_000_000_000


def _write_mixed_resolution_catalog(tmp_path, symbol, *, n_hourly_days=70, n_daily_days=900):
    """900 Tage OneDay-Ticks (aeltestes Segment) gefolgt von 70 Tagen OneHour-Ticks (juengstes
    Segment) — dieselbe Heterogenitaet wie die Vor-#1331-Kaskade."""
    d = tmp_path / "data" / "quote_tick" / symbol / "OneHour"
    d.mkdir(parents=True, exist_ok=True)
    _FSB16 = pa.binary(16)

    now = datetime(2026, 8, 31, 19, tzinfo=timezone.utc)
    hourly_start = now - timedelta(days=n_hourly_days)
    daily_start = hourly_start - timedelta(days=n_daily_days)

    ts_events: list[int] = []
    intervals: list[int] = []

    cur = daily_start
    while cur < hourly_start:
        ts_events.append(int(cur.timestamp() * 1e9))
        intervals.append(_NS_PER_DAY)
        cur += timedelta(days=1)

    cur = hourly_start
    while cur <= now:
        if cur.weekday() < 5:
            ts_events.append(int(cur.timestamp() * 1e9))
            intervals.append(_NS_PER_HOUR)
        cur += timedelta(hours=1)

    n = len(ts_events)
    table = pa.table({
        "bid_price": pa.array([b"\x00" * 16] * n, type=_FSB16),
        "ask_price": pa.array([b"\x00" * 16] * n, type=_FSB16),
        "ts_event": pa.array(ts_events, type=pa.uint64()),
        "bar_interval_ns": pa.array(intervals, type=pa.uint64()),
    })
    pq.write_table(table, str(d / "data.parquet"))
    return daily_start, hourly_start, now


def test_mixed_resolution_catalog_yields_single_hourly_segment_of_70_days(tmp_path):
    daily_start, hourly_start, now = _write_mixed_resolution_catalog(tmp_path, "NVDA.ETORO")

    result = sweep.check_catalog_resolution_homogeneity(
        "NVDA.ETORO", catalog_path=tmp_path, required_span_days=426)

    raw_span = (now - daily_start).days
    assert result["raw_span_days"] == pytest.approx(raw_span, abs=1)
    # effective_span_days misst NUR das zusammenhaengende Stunden-Segment (Monatsgranularitaet:
    # die 70 Tage spannen die vollen Kalendermonate Juni-August ⇒ 92 Tage), nicht die volle rohe
    # Spanne (~970 Tage inkl. des OneDay-Segments).
    assert 60 <= result["effective_span_days"] <= 100
    assert result["passed"] is False
    assert result["raw_span_days"] > result["effective_span_days"] * 5


def test_sufficient_hourly_history_passes(tmp_path):
    _write_mixed_resolution_catalog(tmp_path, "GOOD.ETORO", n_hourly_days=500, n_daily_days=0)
    result = sweep.check_catalog_resolution_homogeneity(
        "GOOD.ETORO", catalog_path=tmp_path, required_span_days=426)
    assert result["passed"] is True


def test_missing_bar_interval_ns_column_yields_catalog_interval_unknown(tmp_path):
    d = tmp_path / "data" / "quote_tick" / "LEGACY.ETORO" / "OneHour"
    d.mkdir(parents=True, exist_ok=True)
    _FSB16 = pa.binary(16)
    table = pa.table({
        "bid_price": pa.array([b"\x00" * 16] * 5, type=_FSB16),
        "ask_price": pa.array([b"\x00" * 16] * 5, type=_FSB16),
        "ts_event": pa.array([1, 2, 3, 4, 5], type=pa.uint64()),
    })
    pq.write_table(table, str(d / "data.parquet"))

    result = sweep.check_catalog_resolution_homogeneity(
        "LEGACY.ETORO", catalog_path=tmp_path, required_span_days=426)
    assert result["passed"] is False
    assert result["reason"] == "CATALOG_INTERVAL_UNKNOWN"
    assert result["severity"] == "blocking"


def test_missing_catalog_is_inconclusive_not_fail(tmp_path):
    result = sweep.check_catalog_resolution_homogeneity(
        "NOPE.ETORO", catalog_path=tmp_path, required_span_days=426)
    assert result["passed"] is None
    assert result["reason"] == "FILE_NOT_FOUND"


def test_no_required_span_days_is_inconclusive(tmp_path):
    _write_mixed_resolution_catalog(tmp_path, "NVDA.ETORO")
    result = sweep.check_catalog_resolution_homogeneity("NVDA.ETORO", catalog_path=tmp_path)
    assert result["passed"] is None
