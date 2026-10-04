"""Issue #1364 (GH #1260) — ``--rebuild-catalog`` löscht keine Historie mehr.

Akzeptanzkriterien:
- Katalog mit 120 Tagen Stunden-Historie, Fake-API mit 92 Tagen ⇒ nach dem Rebuild weiterhin
  120 Tage, Archiv vorhanden.
- vorhergesagter Verlust ohne ``--accept-history-loss`` ⇒ Exit ≠ 0, nichts verschoben.
- ``optimizer/retention.py`` und ``optimizer/disk_guard.py`` schliessen ``archive/`` aus.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from automation import api_backfiller as bf
from automation import historical_fetcher as hf
from automation.catalog_paths import catalog_archive_root, is_catalog_archive_path

_log = logging.getLogger("t1364")
_NOW = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
_SYM = "SYM.ETORO"


def _candles(start: datetime, n_hours: int, price: float = 100.0) -> list[dict]:
    out = []
    for i in range(n_hours):
        t = start + timedelta(hours=i)
        out.append({
            "fromDate": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": price, "high": price + 1, "low": price - 1, "close": price + 0.5,
        })
    return out


def _write_v2_hourly(days: int, *, end: datetime = _NOW, price: float = 100.0, symbol: str = _SYM) -> None:
    start = end - timedelta(days=days)
    table = bf._candles_to_arrow_table(
        _candles(start, days * 24, price), symbol, 2, 2, start, interval="OneHour")
    assert bf._merge_and_save(_log, table, symbol, 2, 2, interval="OneHour")


def _legacy_v1_file(path: Path, days: int, end: datetime = _NOW) -> None:
    """Alt-Katalog v1: EIN Tick je Kerze, KEINE bar_interval_ns-Spalte, KEINE Schema-Version."""
    f16 = pa.binary(16)
    price = bf._encode_fsb16(100.0, 2)
    size = bf._encode_qty_fsb16(1.0, 2)
    start = end - timedelta(days=days)
    ts = [int((start + timedelta(hours=i)).timestamp() * 1e9) for i in range(days * 24)]
    table = pa.table({
        "bid_price": pa.array([price] * len(ts), type=f16),
        "ask_price": pa.array([price] * len(ts), type=f16),
        "bid_size": pa.array([size] * len(ts), type=f16),
        "ask_size": pa.array([size] * len(ts), type=f16),
        "ts_event": pa.array(ts, type=pa.uint64()),
        "ts_init": pa.array(ts, type=pa.uint64()),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(path))


def _span_days(parquet_file: Path) -> float:
    rng = hf._file_range_ns(parquet_file)
    assert rng is not None
    return (rng[1] - rng[0]) / hf._DAY_NS


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    qt = tmp_path / "nautilus" / "data" / "quote_tick"
    qt.mkdir(parents=True)
    monkeypatch.setattr(bf, "QUOTE_TICK_PATH", qt)
    monkeypatch.setattr(hf, "QUOTE_TICK_PATH", qt)
    return qt


def _fake_api(days: int):
    """Fake-API: liefert nur die letzten ``days`` Tage (feste API-Tiefe)."""
    def _fetch(to_fetch: dict[str, str]) -> list[str]:
        for sym in to_fetch.values():
            _write_v2_hourly(days, price=100.0, symbol=sym)
        return list(to_fetch.values())
    return _fetch


def _oldest_probe(days: int):
    oldest = int((_NOW - timedelta(days=days)).timestamp() * 1e9)

    def _probe(needed: dict[str, list[str]]) -> dict[str, dict[str, int | None]]:
        return {sym: {itv: oldest for itv in itvs} for sym, itvs in needed.items()}
    return _probe


# ─── Akzeptanz: 120 Tage bleiben nach dem Rebuild erhalten ─────────────────────

def test_rebuild_keeps_120_days_when_api_serves_only_92(catalog):
    _write_v2_hourly(120)
    live = catalog / _SYM / "OneHour" / "data.parquet"
    assert _span_days(live) == pytest.approx(120, abs=0.2)

    report = hf.rebuild_catalog_with_report(
        "k", "u", {"1": _SYM}, _SYM, now=_NOW, fetch_fn=_fake_api(92), probe_fn=_oldest_probe(92))

    assert _span_days(live) == pytest.approx(120, abs=0.2)  # Historie erhalten
    archive_dir = Path(report["archive_dir"])
    assert archive_dir.parent == catalog_archive_root(catalog.parent.parent)
    assert (archive_dir / _SYM / "OneHour" / "data.parquet").exists()  # Archiv vorhanden
    assert report["symbols"][_SYM]["OneHour"]["history_lost_days"] == pytest.approx(0.0, abs=1e-9)
    assert report["history_lost_days_total"] == pytest.approx(0.0, abs=1e-9)
    assert (archive_dir / "rebuild_report.json").exists()
    assert bf._read_catalog_schema_version(live) == bf.CATALOG_SCHEMA_VERSION


def test_restore_lets_fresh_api_rows_win_over_archived_rows(catalog):
    _write_v2_hourly(120, price=100.0)
    hf.rebuild_catalog_with_report(
        "k", "u", {"1": _SYM}, _SYM, now=_NOW,
        fetch_fn=lambda m: (_write_v2_hourly(92, price=200.0) or list(m.values())),
        probe_fn=_oldest_probe(92))
    t = pq.read_table(str(catalog / _SYM / "OneHour" / "data.parquet"))
    from automation.catalog_paths import decode_fsb16_price
    bids = [decode_fsb16_price(b) for b in t.column("bid_price").to_pylist()]
    assert bids[0] == 100.0   # nur aus dem Archiv (älter als die API-Tiefe)
    assert bids[-4] == 200.0  # O-Tick der letzten Kerze: frische API-Zeile überschreibt die archivierte


# ─── Akzeptanz: vorhergesagter Verlust ohne Flag ⇒ nichts verschoben ───────────

def test_predicted_loss_without_flag_refuses_before_any_move(catalog):
    legacy = catalog / _SYM / "OneHour" / "data.parquet"
    _legacy_v1_file(legacy, 120)
    before = legacy.read_bytes()

    with pytest.raises(hf.HistoryLossRefused):
        hf.rebuild_catalog_with_report(
            "k", "u", {"1": _SYM}, _SYM, now=_NOW,
            fetch_fn=_fake_api(92), probe_fn=_oldest_probe(92))

    assert legacy.read_bytes() == before                      # nichts verschoben
    assert not catalog_archive_root(catalog.parent.parent).exists()


def test_cli_exit_nonzero_and_nothing_moved_without_accept_flag(catalog, tmp_path, monkeypatch):
    legacy = catalog / _SYM / "OneHour" / "data.parquet"
    _legacy_v1_file(legacy, 120)
    monkeypatch.setattr(hf, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(hf, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("ETORO_API_KEY", "k")
    monkeypatch.setenv("ETORO_USER_KEY", "u")
    monkeypatch.setattr(hf, "_load_etoro_id_map", lambda _p: {"1": _SYM})
    monkeypatch.setattr(hf, "_default_probe_fn", lambda *a, **k: _oldest_probe(92))
    monkeypatch.setattr(sys, "argv", ["historical_fetcher.py", "--rebuild-catalog", _SYM])

    assert hf.main() != 0
    assert legacy.exists()
    assert not catalog_archive_root(catalog.parent.parent).exists()


def test_accept_flag_proceeds_and_keeps_unrepresentable_rows_in_archive(catalog):
    legacy = catalog / _SYM / "OneHour" / "data.parquet"
    _legacy_v1_file(legacy, 120)
    legacy_bytes = legacy.read_bytes()

    report = hf.rebuild_catalog_with_report(
        "k", "u", {"1": _SYM}, _SYM, now=_NOW, accept_history_loss=True,
        fetch_fn=_fake_api(92), probe_fn=_oldest_probe(92))

    entry = report["symbols"][_SYM]["OneHour"]
    assert entry["representable"] is False
    assert entry["restored_rows"] == 0
    assert entry["history_lost_days"] == pytest.approx(28, abs=0.2)
    archived = Path(report["archive_dir"]) / _SYM / "OneHour" / "data.parquet"
    assert archived.read_bytes() == legacy_bytes               # v1-Zeilen unangetastet im Archiv
    assert _span_days(legacy) == pytest.approx(92, abs=0.2)    # live nur noch API-Tiefe
    assert report["predicted"][_SYM]["OneHour"]["predicted_history_lost_days"] == pytest.approx(
        28, abs=0.2)


# ─── Echt-Ticks (flaches Layout) werden zurückgeführt ──────────────────────────

def test_flat_realtick_file_is_restored_as_realtick(catalog):
    _write_v2_hourly(30)
    f16 = pa.binary(16)
    p = bf._encode_fsb16(100.0, 2)
    q = bf._encode_fsb16(100.1, 2)
    s = bf._encode_qty_fsb16(1.0, 2)
    ts = [int((_NOW - timedelta(minutes=i)).timestamp() * 1e9) for i in range(10)]
    flat = pa.table({
        "bid_price": pa.array([p] * 10, type=f16), "ask_price": pa.array([q] * 10, type=f16),
        "bid_size": pa.array([s] * 10, type=f16), "ask_size": pa.array([s] * 10, type=f16),
        "ts_event": pa.array(ts, type=pa.uint64()), "ts_init": pa.array(ts, type=pa.uint64()),
    }).replace_schema_metadata({b"price_precision": b"2", b"size_precision": b"2",
                                b"instrument_id": _SYM.encode()})
    pq.write_table(flat, str(catalog / _SYM / "data.parquet"))

    report = hf.rebuild_catalog_with_report(
        "k", "u", {"1": _SYM}, _SYM, now=_NOW, fetch_fn=_fake_api(30), probe_fn=_oldest_probe(30))

    restored = catalog / _SYM / "RealTick" / "data.parquet"
    assert restored.exists()
    assert len(pq.read_table(str(restored))) == 10
    assert report["symbols"][_SYM]["RealTick"]["restored_rows"] == 10


# ─── Reine Hilfsfunktionen ─────────────────────────────────────────────────────

def test_uncovered_days_range_arithmetic():
    d = hf._DAY_NS
    assert hf.uncovered_days((0, 120 * d), (28 * d, 120 * d)) == pytest.approx(28)
    assert hf.uncovered_days((0, 120 * d), (0, 130 * d)) == 0.0
    assert hf.uncovered_days((0, 120 * d), None) == pytest.approx(120)
    assert hf.uncovered_days((10 * d, 20 * d), (0, 5 * d)) == pytest.approx(10)


def test_archive_path_predicate():
    assert is_catalog_archive_path("/x/data/nautilus/archive/20261004T000000Z/TSLA.ETORO")
    assert not is_catalog_archive_path("/x/data/nautilus/data/quote_tick/TSLA.ETORO")
    assert not is_catalog_archive_path("/x/data/optimizer/study_1/trial_0")


def test_schema_mismatch_message_names_migration_first_and_rebuild_only_with_warning(monkeypatch):
    msg_no_migration = bf.schema_mismatch_message(_SYM, "OneHour", None)
    assert "--rebuild-catalog" in msg_no_migration and "WARNUNG" in msg_no_migration
    assert "--migrate-catalog" not in msg_no_migration

    monkeypatch.setitem(bf.SCHEMA_MIGRATIONS, (1, 2), lambda t: t)
    msg = bf.schema_mismatch_message(_SYM, "OneHour", None)
    assert "--migrate-catalog" in msg
    assert "--rebuild-catalog" not in msg


def test_migrate_catalog_schema_rewrites_metadata_atomically(catalog, monkeypatch):
    legacy = catalog / _SYM / "OneHour" / "data.parquet"
    _legacy_v1_file(legacy, 2)
    with pytest.raises(bf.CatalogSchemaMigrationUnavailable):
        bf.migrate_catalog_schema(1, 2)

    def _add_interval(t: pa.Table) -> pa.Table:
        return t.append_column("bar_interval_ns", pa.array([3_600_000_000_000] * len(t), type=pa.uint64()))

    monkeypatch.setitem(bf.SCHEMA_MIGRATIONS, (1, 2), _add_interval)
    migrated = bf.migrate_catalog_schema(1, 2)
    assert migrated == [legacy]
    assert bf._read_catalog_schema_version(legacy) == 2
    assert "bar_interval_ns" in pq.read_table(str(legacy)).column_names


# ─── Fix Punkt 5: archive/ ist von automatischer Bereinigung ausgenommen ───────

def test_retention_refuses_to_delete_inside_catalog_archive(tmp_path):
    from automation.optimizer import retention
    victim = tmp_path / "nautilus" / "archive" / "20261004T000000Z" / _SYM / "trial_0"
    victim.mkdir(parents=True)
    (victim / "x.parquet").write_bytes(b"1")
    assert retention.release_trial_dir(victim, keep=False) is False
    assert victim.exists()

    normal = tmp_path / "optimizer" / "study_a" / "trial_0"
    normal.mkdir(parents=True)
    assert retention.release_trial_dir(normal, keep=False) is True
    assert not normal.exists()


def test_disk_guard_excludes_archive_from_measured_usage(tmp_path):
    from automation.optimizer import disk_guard
    work = tmp_path / "nautilus"
    (work / "data").mkdir(parents=True)
    (work / "data" / "a.bin").write_bytes(b"x" * 10)
    (work / "archive" / "ts").mkdir(parents=True)
    (work / "archive" / "ts" / "b.bin").write_bytes(b"x" * 1000)
    assert disk_guard.measure_usage(work, use_cache=False) == 10


def test_reset_catalog_archives_instead_of_deleting(catalog):
    from automation.historical_fetcher import archive_all_instrument_catalogs
    _write_v2_hourly(5, symbol="A.ETORO")
    _write_v2_hourly(5, symbol="B.ETORO")
    archive_dir = catalog_archive_root(catalog.parent.parent) / "20261004T000000Z"
    moved = archive_all_instrument_catalogs(catalog, archive_dir)
    assert sorted(p.name for p in moved) == ["A.ETORO", "B.ETORO"]
    assert (archive_dir / "A.ETORO" / "OneHour" / "data.parquet").exists()
    assert not (catalog / "A.ETORO").exists()
