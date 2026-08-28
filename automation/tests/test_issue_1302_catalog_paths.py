"""Issue #1302 (GH #1179) — automation/catalog_paths.py als Single Source of Truth für die
Quote-Tick-Datei-/Spaltenauflösung."""
import ast
from pathlib import Path

import pytest

from automation import catalog_paths


# --- resolve_quote_tick_files ----------------------------------------------

def test_resolve_quote_tick_files_classic_single_file(tmp_path):
    d = tmp_path / "data" / "quote_tick" / "AAA"
    d.mkdir(parents=True)
    (d / "data.parquet").write_bytes(b"x")
    files = catalog_paths.resolve_quote_tick_files(tmp_path, "AAA")
    assert files == [d / "data.parquet"]


def test_resolve_quote_tick_files_partitioned_layout(tmp_path):
    d = tmp_path / "data" / "quote_tick" / "BBB"
    d.mkdir(parents=True)
    (d / "part-1.parquet").write_bytes(b"a")
    (d / "part-0.parquet").write_bytes(b"b")
    files = catalog_paths.resolve_quote_tick_files(tmp_path, "BBB")
    assert files == [d / "part-0.parquet", d / "part-1.parquet"]  # sortiert


def test_resolve_quote_tick_files_arbitrary_parquet_fallback(tmp_path):
    d = tmp_path / "data" / "quote_tick" / "CCC"
    d.mkdir(parents=True)
    (d / "20260101.parquet").write_bytes(b"a")
    files = catalog_paths.resolve_quote_tick_files(tmp_path, "CCC")
    assert files == [d / "20260101.parquet"]


def test_resolve_quote_tick_files_missing_dir(tmp_path):
    assert catalog_paths.resolve_quote_tick_files(tmp_path, "NOPE") == []


def test_resolve_quote_tick_files_empty_dir(tmp_path):
    d = tmp_path / "data" / "quote_tick" / "DDD"
    d.mkdir(parents=True)
    assert catalog_paths.resolve_quote_tick_files(tmp_path, "DDD") == []


def test_resolve_quote_tick_files_prefers_data_parquet_over_parts(tmp_path):
    d = tmp_path / "data" / "quote_tick" / "EEE"
    d.mkdir(parents=True)
    (d / "data.parquet").write_bytes(b"a")
    (d / "part-0.parquet").write_bytes(b"b")
    files = catalog_paths.resolve_quote_tick_files(tmp_path, "EEE")
    assert files == [d / "data.parquet"]


# --- resolve_quote_tick_columns ---------------------------------------------

def test_resolve_quote_tick_columns_canonical_names():
    resolved = catalog_paths.resolve_quote_tick_columns(["bid_price", "ask_price", "ts_event", "extra"])
    assert resolved == {"bid_price": "bid_price", "ask_price": "ask_price", "ts_event": "ts_event"}


def test_resolve_quote_tick_columns_aliases():
    resolved = catalog_paths.resolve_quote_tick_columns(["bid", "ask", "ts_init"])
    assert resolved == {"bid_price": "bid", "ask_price": "ask", "ts_event": "ts_init"}


def test_resolve_quote_tick_columns_missing_returns_none():
    assert catalog_paths.resolve_quote_tick_columns(["bid_price", "ts_event"]) is None
    assert catalog_paths.resolve_quote_tick_columns([]) is None


# --- Standalone-Prinzip (Akzeptanzkriterium #1302) --------------------------

def test_catalog_paths_has_no_heavy_imports():
    src = Path("automation/catalog_paths.py").read_text("utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("nautilus_trader", "optuna", "pandas"):
        assert forbidden not in imported


def test_no_hardcoded_data_parquet_outside_catalog_paths():
    for rel in ("automation/backtest_runner.py", "automation/optimizer/sweep.py"):
        src = Path(rel).read_text("utf-8")
        assert "data.parquet" not in src, f"{rel} still hardcodes 'data.parquet'"
