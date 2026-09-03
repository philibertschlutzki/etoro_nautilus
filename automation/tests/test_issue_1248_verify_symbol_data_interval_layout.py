"""Issue #1248 (Katalog #1353, P1) Regression Tests
=====================================================
``verify_symbol_data.py`` suchte Quote-Tick-Parquet-Dateien ausschliesslich flach unter
``.../data/quote_tick/<symbol>/`` — seit Issue #1331 (GH #1225) schreibt der tatsächliche
Katalog-Writer (``api_backfiller.py``/``historical_fetcher.py``) jede Auflösung jedoch in ein
eigenes Interval-Unterverzeichnis (``.../<symbol>/<interval>/``). Nach einem vollständigen
Katalog-Rebuild existierte das alte flache Layout für kein Symbol mehr, wodurch das
Verifikationsskript für 100% der Symbole faelschlich ``READ_FAILED`` meldete, obwohl die Daten
tatsächlich vorhanden waren.

Diese Tests prüfen, dass ``resolve_quote_tick_files`` zuerst im Interval-Unterverzeichnis sucht
(gleiche Präferenzreihenfolge wie ``automation.catalog_paths.resolve_quote_tick_files``, hier
bewusst weiterhin als eigenständige Kopie, nicht als Import), mit Fallback auf das klassische
flache Alt-Layout, und dass die neue ``--interval``-CLI-Option korrekt durchgereicht wird.
"""
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from automation.tests.verify_symbol_data import read_raw_ticks, resolve_quote_tick_files


def _write_quote_tick_parquet(path: Path, n_rows: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({
        "bid_price": pa.array([100.0 + i for i in range(n_rows)]),
        "ask_price": pa.array([100.1 + i for i in range(n_rows)]),
        "ts_event": pa.array(
            [1_700_000_000_000_000_000 + i * 3_600_000_000_000 for i in range(n_rows)], type=pa.int64()),
    })
    pq.write_table(table, str(path))


def test_resolve_quote_tick_files_prefers_interval_subdirectory(tmp_path):
    symbol = "TSLA.ETORO"
    interval_file = tmp_path / "data" / "quote_tick" / symbol / "OneHour" / "data.parquet"
    flat_file = tmp_path / "data" / "quote_tick" / symbol / "data.parquet"
    _write_quote_tick_parquet(interval_file)
    _write_quote_tick_parquet(flat_file)

    result = resolve_quote_tick_files(tmp_path, symbol, "OneHour")

    assert result == [interval_file]


def test_resolve_quote_tick_files_falls_back_to_flat_layout(tmp_path):
    """Alt-Katalog vor #1331 (oder eine Auflösung ohne eigenes Unterverzeichnis): kein
    Interval-Unterverzeichnis vorhanden — der Fallback auf das flache Layout darf NICHT als
    READ_FAILED behandelt werden."""
    symbol = "NVDA.ETORO"
    flat_file = tmp_path / "data" / "quote_tick" / symbol / "data.parquet"
    _write_quote_tick_parquet(flat_file)

    result = resolve_quote_tick_files(tmp_path, symbol, "OneHour")

    assert result == [flat_file]


def test_resolve_quote_tick_files_missing_returns_empty(tmp_path):
    result = resolve_quote_tick_files(tmp_path, "GHOST.ETORO", "OneHour")
    assert result == []


def test_resolve_quote_tick_files_default_interval_is_onehour(tmp_path):
    """Default muss mit automation.catalog_paths.resolve_quote_tick_files und
    api_backfiller.DEFAULT_INTERVAL konsistent bleiben ('OneHour')."""
    symbol = "AAPL.ETORO"
    interval_file = tmp_path / "data" / "quote_tick" / symbol / "OneHour" / "data.parquet"
    _write_quote_tick_parquet(interval_file)

    result = resolve_quote_tick_files(tmp_path, symbol)  # no interval passed -> default

    assert result == [interval_file]


def test_read_raw_ticks_succeeds_against_interval_layout(tmp_path, capsys):
    """Regression fuer das eigentliche Symptom: ein post-#1331-Katalog (nur Interval-
    Unterverzeichnis, kein flaches Layout mehr) darf NICHT READ_FAILED melden."""
    symbol = "TSLA.ETORO"
    interval_file = tmp_path / "data" / "quote_tick" / symbol / "OneHour" / "data.parquet"
    _write_quote_tick_parquet(interval_file, n_rows=10)

    df = read_raw_ticks(tmp_path, symbol, max_ticks=None, interval="OneHour")

    assert df is not None
    assert len(df) == 10
    captured = capsys.readouterr()
    assert "FEHLER" not in captured.err


def test_read_raw_ticks_reports_both_checked_paths_on_miss(tmp_path, capsys):
    symbol = "MISSING.ETORO"
    df = read_raw_ticks(tmp_path, symbol, max_ticks=None, interval="OneHour")

    assert df is None
    captured = capsys.readouterr()
    assert "OneHour" in captured.err
    assert symbol in captured.err
