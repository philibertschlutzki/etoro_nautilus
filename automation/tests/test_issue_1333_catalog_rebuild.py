"""Issue #1333 (GH #1227) — Katalog-Schema-Version-Gate und letzte-Zeile-gewinnt-Dedup.

Akzeptanzkriterien:
- ``_merge_and_save`` gegen eine Datei mit abweichender ``catalog_schema_version`` wirft und
  schreibt nichts.
- zwei Zeilen mit identischem ``(ts_event, bar_interval_ns)`` ⇒ die spätere überlebt.
"""
import logging
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from automation import api_backfiller as bf

_START_DT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_log = logging.getLogger("t")


def _legacy_v1_table(symbol: str) -> pa.Table:
    """Ein Alt-Katalog (Version 1, EIN Tick je Kerze, KEINE bar_interval_ns-Spalte)."""
    _FSB16 = pa.binary(16)
    price = bf._encode_fsb16(100.0, 2)
    size = bf._encode_qty_fsb16(1.0, 2)
    return pa.table({
        "bid_price": pa.array([price], type=_FSB16),
        "ask_price": pa.array([price], type=_FSB16),
        "bid_size": pa.array([size], type=_FSB16),
        "ask_size": pa.array([size], type=_FSB16),
        "ts_event": pa.array([1_700_000_000_000_000_000], type=pa.uint64()),
        "ts_init": pa.array([1_700_000_000_000_000_000], type=pa.uint64()),
    })


def test_merge_and_save_raises_on_schema_version_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "QUOTE_TICK_PATH", tmp_path / "data" / "quote_tick")
    dest_dir = tmp_path / "data" / "quote_tick" / "SYM.ETORO" / "OneHour"
    dest_dir.mkdir(parents=True)
    legacy = _legacy_v1_table("SYM.ETORO")
    # Legacy-Datei OHNE catalog_schema_version-Metadatum (Alt-Katalog).
    pq.write_table(legacy, str(dest_dir / "data.parquet"))

    candles = [{"fromDate": "2026-01-01T13:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}]
    new_table = bf._candles_to_arrow_table(candles, "SYM.ETORO", 2, 2, _START_DT, interval="OneHour")

    with pytest.raises(bf.CatalogSchemaVersionMismatch):
        bf._merge_and_save(_log, new_table, "SYM.ETORO", 2, 2, interval="OneHour")

    # Nichts wurde geschrieben — die Datei ist unveraendert (noch die Legacy-Zeile, kein Merge).
    t = pq.read_table(str(dest_dir / "data.parquet"))
    assert len(t) == 1


def test_last_write_wins_dedup_on_ts_event_and_bar_interval_ns(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "QUOTE_TICK_PATH", tmp_path / "data" / "quote_tick")
    candles_v1 = [{"fromDate": "2026-01-01T13:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}]
    table_v1 = bf._candles_to_arrow_table(candles_v1, "DUP.ETORO", 2, 2, _START_DT, interval="OneHour")
    assert bf._merge_and_save(_log, table_v1, "DUP.ETORO", 2, 2, interval="OneHour")

    # Korrigierte Kerze mit identischem Zeitstempel, aber anderem Preis -> muss die alte Zeile
    # ueberschreiben (letzte-Zeile-gewinnt), nicht die alte behalten (erste-Zeile-gewinnt).
    candles_v2 = [{"fromDate": "2026-01-01T13:00:00Z", "open": 200.0, "high": 201.0, "low": 199.0, "close": 200.5}]
    table_v2 = bf._candles_to_arrow_table(candles_v2, "DUP.ETORO", 2, 2, _START_DT, interval="OneHour")
    assert bf._merge_and_save(_log, table_v2, "DUP.ETORO", 2, 2, interval="OneHour")

    dest_file = tmp_path / "data" / "quote_tick" / "DUP.ETORO" / "OneHour" / "data.parquet"
    t = pq.read_table(str(dest_file))
    assert len(t) == 4  # nur die korrigierte Kerze ueberlebt, nicht 8 (4+4)
    from automation.catalog_paths import decode_fsb16_price
    prices = [decode_fsb16_price(p) for p in t.column("bid_price").to_pylist()]
    assert prices[0] == 200.0  # O-Tick der KORRIGIERTEN Kerze, nicht der ersten


def test_merge_and_save_stamps_catalog_schema_version_2(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "QUOTE_TICK_PATH", tmp_path / "data" / "quote_tick")
    candles = [{"fromDate": "2026-01-01T13:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}]
    table = bf._candles_to_arrow_table(candles, "VER.ETORO", 2, 2, _START_DT, interval="OneHour")
    assert bf._merge_and_save(_log, table, "VER.ETORO", 2, 2, interval="OneHour")

    dest_file = tmp_path / "data" / "quote_tick" / "VER.ETORO" / "OneHour" / "data.parquet"
    version = bf._read_catalog_schema_version(dest_file)
    assert version == bf.CATALOG_SCHEMA_VERSION == 2
