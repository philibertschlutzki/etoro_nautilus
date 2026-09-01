"""Issue #1331 (GH #1225) — ``OneHour``/``OneDay``-Kerzen landen nicht mehr in einem
ununterscheidbaren Tick-Strom: getrennte Zielpfade (``<symbol>/<interval>/data.parquet``) UND
eine ``bar_interval_ns``-Spalte je Zeile.
"""
from datetime import datetime, timedelta, timezone

from automation import api_backfiller as bf
from automation.catalog_paths import resolve_quote_tick_files

_START_DT = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _hourly_candles(n: int, start: datetime):
    out = []
    for i in range(n):
        ts = start + timedelta(hours=i)
        out.append({
            "fromDate": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i,
        })
    return out


def _daily_candles(n: int, start: datetime):
    out = []
    for i in range(n):
        ts = start + timedelta(days=i)
        out.append({
            "fromDate": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": 100.0 + i, "high": 102.0 + i, "low": 98.0 + i, "close": 101.0 + i,
        })
    return out


def test_hourly_and_daily_cascade_produce_two_tables_not_one(tmp_path):
    """Akzeptanzkriterium #1331: eine Kaskade aus 100 OneHour- und 100 OneDay-Kerzen erzeugt
    zwei Tabellen mit je 100 Kerzen (400/100 Ticks) und disjunkten Zielpfaden — nicht eine
    Tabelle mit 200 Kerzen."""
    hourly = _hourly_candles(100, _START_DT)
    daily = _daily_candles(100, _START_DT - timedelta(days=200))

    table_hourly = bf._candles_to_arrow_table(hourly, "AAA.ETORO", 2, 2, _START_DT - timedelta(days=400), interval="OneHour")
    table_daily = bf._candles_to_arrow_table(daily, "AAA.ETORO", 2, 2, _START_DT - timedelta(days=400), interval="OneDay")

    assert table_hourly is not None and table_daily is not None
    # 100 Kerzen x 4 Ticks (O/L/H/C) je Auflösung.
    assert len(table_hourly) == 400
    assert len(table_daily) == 400
    assert set(table_hourly.column("bar_interval_ns").to_pylist()) == {bf.INTERVAL_TO_NS["OneHour"]}
    assert set(table_daily.column("bar_interval_ns").to_pylist()) == {bf.INTERVAL_TO_NS["OneDay"]}


def test_merge_and_save_writes_disjoint_interval_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "QUOTE_TICK_PATH", tmp_path / "data" / "quote_tick")
    hourly = _hourly_candles(10, _START_DT)
    daily = _daily_candles(10, _START_DT - timedelta(days=200))
    table_hourly = bf._candles_to_arrow_table(hourly, "BBB.ETORO", 2, 2, _START_DT - timedelta(days=400), interval="OneHour")
    table_daily = bf._candles_to_arrow_table(daily, "BBB.ETORO", 2, 2, _START_DT - timedelta(days=400), interval="OneDay")

    import logging
    log = logging.getLogger("t")
    assert bf._merge_and_save(log, table_hourly, "BBB.ETORO", 2, 2, interval="OneHour")
    assert bf._merge_and_save(log, table_daily, "BBB.ETORO", 2, 2, interval="OneDay")

    hour_dir = tmp_path / "data" / "quote_tick" / "BBB.ETORO" / "OneHour"
    day_dir = tmp_path / "data" / "quote_tick" / "BBB.ETORO" / "OneDay"
    assert (hour_dir / "data.parquet").exists()
    assert (day_dir / "data.parquet").exists()

    files_default = resolve_quote_tick_files(tmp_path, "BBB.ETORO")  # default interval=OneHour
    assert files_default == [hour_dir / "data.parquet"]
    files_daily = resolve_quote_tick_files(tmp_path, "BBB.ETORO", interval="OneDay")
    assert files_daily == [day_dir / "data.parquet"]


def test_resolve_quote_tick_files_falls_back_to_legacy_flat_layout(tmp_path):
    """Ein Alt-Katalog (vor #1331) ohne Auflösungs-Unterverzeichnis bleibt lesbar."""
    d = tmp_path / "data" / "quote_tick" / "LEGACY.ETORO"
    d.mkdir(parents=True)
    (d / "data.parquet").write_bytes(b"x")
    files = resolve_quote_tick_files(tmp_path, "LEGACY.ETORO")
    assert files == [d / "data.parquet"]
