"""Issue #1354 (GH #1251, P0) — die Backtest-Engine liest die Stunden-Ticks.

Akzeptanzkriterien:
- (a) Produktions-Schreiber → Sicht → ``quote_ticks`` == 4 · n_Kerzen (echte Bibliothek);
- (b) ohne Sicht == 0 (dokumentiert den Bibliotheksvertrag und schlägt an, falls NautilusTrader ihn ändert);
- ein flaches ``data.parquet`` mit abweichenden Preisen ändert die Engine-Ticks nicht;
- ``check_engine_reader_parity`` je Symbol im Strom, auch bei PASS.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from automation import api_backfiller as bf
from automation.catalog_paths import (
    EngineCatalogViewError, decode_fsb16_price, engine_catalog_view, resolve_quote_tick_files,
)

_SYM = "TSLA.ETORO"
_START = datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc)      # Montag


def _nautilus_is_real() -> bool:
    """Ältere Testmodule installieren unvollständige ``nautilus_trader``-Mocks in ``sys.modules`` (siehe
    conftest.py); gegen einen Mock ist der Bibliotheksvertrag nicht prüfbar."""
    import sys
    import types
    mod = sys.modules.get("nautilus_trader")
    catalog_mod = sys.modules.get("nautilus_trader.persistence.catalog")
    # ``vars(...)`` statt ``getattr``: die Mock-Module überschreiben ``__getattr__`` und liefern für
    # jedes fehlende Attribut (auch ``__file__``) ein MagicMock.
    def _has_file(m) -> bool:
        return isinstance(m, types.ModuleType) and vars(m).get("__file__") is not None
    return _has_file(mod) and (catalog_mod is None or _has_file(catalog_mod))


# Die Bibliotheks-Vertragstests laufen IMMER in einem sauberen Interpreter (Subprozess-Test unten setzt
# ``_CLEAN_SUBPROCESS_ENV``): im Suite-Prozess (insbesondere unter pytest-xdist) installieren andere Module
# ``nautilus_trader``-Mocks zur Import- UND Laufzeit — ein Prüfergebnis zur Sammelzeit ist nicht belastbar.
_CLEAN_SUBPROCESS_ENV = "ETORO_REAL_NAUTILUS_SUBPROCESS"
_NAUTILUS_REAL = os.environ.get(_CLEAN_SUBPROCESS_ENV) == "1" and _nautilus_is_real()
real_engine = pytest.mark.skipif(
    not _NAUTILUS_REAL,
    reason="läuft im sauberen Subprozess (Subprozess-Test unten), nie im Suite-Prozess.")


def _write_production_hourly(catalog: Path, n_days: int = 5, per_day: int = 7, price: float = 100.0,
                             interval: str = "OneHour") -> int:
    """Schreibt Stundenkerzen über den PRODUKTIONS-Schreiber (``_candles_to_arrow_table`` +
    ``_merge_and_save``) ins Layout ``<symbol>/<interval>/data.parquet``. Rückgabe: Kerzenzahl."""
    candles = []
    for d in range(n_days):
        for h in range(per_day):
            t = _START + timedelta(days=d, hours=h)
            candles.append({"fromDate": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "open": price,
                            "high": price + 1.0, "low": price - 1.0, "close": price + 0.5})
    table = bf._candles_to_arrow_table(candles, _SYM, 2, 2, _START - timedelta(days=1), interval=interval)
    old = bf.QUOTE_TICK_PATH
    bf.QUOTE_TICK_PATH = catalog / "data" / "quote_tick"
    try:
        assert bf._merge_and_save(logging.getLogger("t1354"), table, _SYM, 2, 2, interval=interval)
    finally:
        bf.QUOTE_TICK_PATH = old
    return len(candles)


def _engine_ticks(root: Path, **kw):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    return ParquetDataCatalog(str(root)).quote_ticks(instrument_ids=[_SYM], **kw)


# ─── (a)/(b): der Bibliotheksvertrag gegen die ECHTE NautilusTrader-Bibliothek ───────

@real_engine
def test_production_writer_through_the_view_loads_four_ticks_per_candle(tmp_path):
    n_candles = _write_production_hourly(tmp_path)                        # 5 Tage × 7 Kerzen
    with engine_catalog_view(tmp_path, _SYM) as view:
        ticks = _engine_ticks(view.root)
    assert n_candles == 35
    assert len(ticks) == 4 * n_candles == 140


@real_engine
def test_without_the_view_the_engine_loads_zero_ticks_documenting_the_library_contract(tmp_path):
    """Heutiger Produktionszustand: ``<symbol>/OneHour/data.parquet`` + ``<symbol>/OneDay/data.parquet``.
    NautilusTraders Katalog identifiziert das Instrument über den Elternordner (``OneHour``) ⇒ 0 Ticks.
    Schlägt dieser Test an, hat NautilusTrader den Vertrag geändert und die Sicht ist zu überdenken."""
    _write_production_hourly(tmp_path)
    _write_production_hourly(tmp_path, n_days=1, interval="OneDay")
    assert (tmp_path / "data" / "quote_tick" / _SYM / "OneHour" / "data.parquet").exists()
    assert (tmp_path / "data" / "quote_tick" / _SYM / "OneDay" / "data.parquet").exists()
    assert len(_engine_ticks(tmp_path)) == 0


@real_engine
def test_view_carries_only_the_hour_resolution_never_oneday_ticks(tmp_path):
    n_candles = _write_production_hourly(tmp_path)
    _write_production_hourly(tmp_path, n_days=3, interval="OneDay", price=555.0)
    with engine_catalog_view(tmp_path, _SYM) as view:
        ticks = _engine_ticks(view.root)
    assert len(ticks) == 4 * n_candles
    assert all(float(t.bid_price) < 200.0 for t in ticks)                 # kein OneDay-Preis (555)


@real_engine
def test_view_does_not_pick_up_realtick_files_or_a_flat_file_with_different_prices(tmp_path):
    n_candles = _write_production_hourly(tmp_path, price=100.0)
    inst = tmp_path / "data" / "quote_tick" / _SYM
    # Echt-Ticks (RealTick/) und ein flaches data.parquet mit ABWEICHENDEN Preisen:
    for target in (inst / "RealTick" / "data.parquet", inst / "data.parquet"):
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pq.read_table(str(inst / "OneHour" / "data.parquet")).replace_schema_metadata(
            {b"price_precision": b"2", b"size_precision": b"2", b"instrument_id": _SYM.encode()}),
            str(target))
    # das flache File bekommt andere Preise
    flat = pq.read_table(str(inst / "data.parquet"))
    f16 = pa.binary(16)
    bad = [bf._encode_fsb16(999.0, 2)] * len(flat)
    flat = flat.set_column(flat.schema.get_field_index("bid_price"), "bid_price", pa.array(bad, type=f16))
    flat = flat.set_column(flat.schema.get_field_index("ask_price"), "ask_price", pa.array(bad, type=f16))
    pq.write_table(flat.replace_schema_metadata(
        {b"price_precision": b"2", b"size_precision": b"2", b"instrument_id": _SYM.encode()}),
        str(inst / "data.parquet"))

    # resolve_quote_tick_files bevorzugt OneHour/ — dieselbe Datei wie die Sicht.
    assert resolve_quote_tick_files(tmp_path, _SYM)[0] == inst / "OneHour" / "data.parquet"
    with engine_catalog_view(tmp_path, _SYM) as view:
        ticks = _engine_ticks(view.root)
    assert len(ticks) == 4 * n_candles
    assert max(float(t.bid_price) for t in ticks) < 200.0                 # nie die 999er des flachen Files


# ─── Sicht-Mechanik ──────────────────────────────────────────────────────────────

def test_view_is_a_hardlink_and_is_removed_on_exit_and_never_touches_the_original(tmp_path):
    _write_production_hourly(tmp_path)
    src = tmp_path / "data" / "quote_tick" / _SYM / "OneHour" / "data.parquet"
    before = src.read_bytes()
    with engine_catalog_view(tmp_path, _SYM) as view:
        root = view.root
        assert view.link_kind == "hardlink"
        assert os.path.samefile(view.data_file, src)
        # Precision-Normalisierung (Sicht-Datei neu schreiben) löst den Link und verändert das Original NICHT.
        table = pq.read_table(str(view.data_file))
        view.replace_data_file(lambda t: pq.write_table(
            table.replace_schema_metadata({b"size_precision": b"8"}), str(t)))
        assert view.link_kind == "copy"
        assert not os.path.samefile(view.data_file, src)
        assert src.read_bytes() == before
    assert not root.exists()


@real_engine
def test_view_falls_back_to_symlink_then_copy(tmp_path, monkeypatch):
    _write_production_hourly(tmp_path)
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("no hardlinks")))
    with engine_catalog_view(tmp_path, _SYM) as view:
        assert view.link_kind == "symlink"
        assert len(_engine_ticks(view.root)) == 140
    monkeypatch.setattr(os, "symlink", lambda *a, **k: (_ for _ in ()).throw(OSError("no symlinks")))
    with engine_catalog_view(tmp_path, _SYM) as view:
        assert view.link_kind == "copy"
        assert len(_engine_ticks(view.root)) == 140


def test_view_without_a_source_file_raises(tmp_path):
    with pytest.raises(EngineCatalogViewError):
        engine_catalog_view(tmp_path, "GHOST.ETORO")


# ─── Worker-Pfad: Normalisierung schreibt in die Sicht, nie ins Original ──────────

def test_runner_size_precision_normalization_writes_into_the_view_only(tmp_path):
    from automation import backtest_runner as br
    _write_production_hourly(tmp_path)
    src = tmp_path / "data" / "quote_tick" / _SYM / "OneHour" / "data.parquet"
    # size_precision 0 im Katalog (falsche Metadaten) ⇒ Normalisierung auf den Fallback (>0).
    table = pq.read_table(str(src))
    meta = dict(table.schema.metadata or {})
    meta[b"size_precision"] = b"0"
    pq.write_table(table.replace_schema_metadata(meta), str(src))
    original_bytes = src.read_bytes()
    with engine_catalog_view(tmp_path, _SYM) as view:
        assert br._normalize_view_size_precision(view, _SYM) is True
        patched_meta = pq.read_schema(str(view.data_file)).metadata
        assert int(patched_meta[b"size_precision"]) > 0
    assert src.read_bytes() == original_bytes                              # Original unverändert


def test_normalize_parquet_metadata_does_not_patch_other_resolutions(tmp_path):
    """``rglob('*.parquet')`` über OneHour/OneDay/RealTick glich vor #1354 deren (bewusst abweichende)
    Metadaten an die zuletzt sortierte Datei an."""
    from automation import backtest_runner as br
    _write_production_hourly(tmp_path)
    _write_production_hourly(tmp_path, n_days=2, interval="OneDay")
    inst = tmp_path / "data" / "quote_tick" / _SYM
    day_file = inst / "OneDay" / "data.parquet"
    meta_before = pq.read_schema(str(day_file)).metadata
    assert br.normalize_parquet_metadata(str(tmp_path), _SYM) is False
    assert pq.read_schema(str(day_file)).metadata == meta_before
    assert pq.read_schema(str(day_file)).metadata[b"catalog_interval"] == b"OneDay"


# ─── Leser-Parität (Preflight ↔ Engine) ────────────────────────────────────────────

@real_engine
def test_engine_reader_parity_passes_on_the_production_layout(tmp_path):
    from automation.optimizer import sweep
    n_candles = _write_production_hourly(tmp_path)
    res = sweep.check_engine_reader_parity(_SYM, tmp_path)
    assert res["passed"] is True and res["reason"] is None
    assert res["n_engine"] == res["n_preflight"] == 4 * n_candles
    assert res["severity"] == "blocking"


@real_engine
def test_engine_reader_parity_restricts_to_the_holdout_window(tmp_path):
    from automation.optimizer import sweep
    _write_production_hourly(tmp_path)                                    # 5 Tage
    res = sweep.check_engine_reader_parity(_SYM, tmp_path, holdout_days=2)
    assert res["passed"] is True
    assert 0 < res["n_engine"] == res["n_preflight"] < 140


@real_engine
def test_engine_reader_parity_fails_with_reject_reason_when_the_readers_disagree(tmp_path, monkeypatch):
    """Simuliert den Vor-#1354-Zustand: die Engine bekommt eine Sicht OHNE die Stunden-Ticks."""
    from automation import catalog_paths
    from automation.optimizer import sweep
    _write_production_hourly(tmp_path)
    real = catalog_paths.engine_catalog_view

    def _empty_view(catalog_path, symbol, interval="OneHour"):
        view = real(catalog_path, symbol, interval)
        os.unlink(view.data_file)                                         # Engine sieht nichts
        pq.write_table(pq.read_table(str(tmp_path / "data/quote_tick" / _SYM / "OneHour/data.parquet")
                                     ).slice(0, 0), str(view.data_file))
        return view

    monkeypatch.setattr(catalog_paths, "engine_catalog_view", _empty_view)
    res = sweep.check_engine_reader_parity(_SYM, tmp_path)
    assert res["passed"] is False
    assert res["n_engine"] == 0 and res["n_preflight"] == 140
    assert "REJECT_ENGINE_READER_MISMATCH" in res["reason"]


def test_engine_reader_parity_is_none_without_a_catalog_file(tmp_path):
    from automation.optimizer import sweep
    assert sweep.check_engine_reader_parity("GHOST.ETORO", tmp_path) is None


def test_sweep_emits_the_parity_result_per_symbol_also_on_pass_and_rejects_on_fail(
        tmp_path, monkeypatch, caplog):
    from automation.optimizer import manifest, sweep
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sweep, "emit_execution_event", lambda lg, name, payload, **kw: events.append((name, payload)))
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda: ["OK.ETORO", "BAD.ETORO"])
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {"walk_forward": {"holdout_days": 60}})
    monkeypatch.setattr(sweep, "count_available_bars", lambda syms, **kw: {})
    calls: list[str] = []

    def _parity(symbol, **kw):
        calls.append(symbol)
        ok = symbol == "OK.ETORO"
        return {"passed": ok, "n_engine": 140 if ok else 0, "n_preflight": 140, "window": {},
                "severity": "blocking", "view_link_kind": "hardlink",
                "reason": None if ok else "REJECT_ENGINE_READER_MISMATCH: n_engine=0 n_preflight=140"}

    caplog.set_level(logging.ERROR, logger="optimizer")
    try:
        sweep.run_per_symbol_sweep(
            ["SmaCrossoverStrategy"], ["OK.ETORO", "BAD.ETORO"],
            optimize_symbol=lambda pair: None, confirm=lambda *a, **kw: None,
            tick_population_fn=lambda symbol: {"n_ticks_raw": 100, "n_ticks_after_session_filter": 100},
            bar_quality_fn=lambda symbol: None, engine_reader_parity_fn=_parity,
            run_id="test-1354-parity")
    except Exception:
        pass

    stream = [(p["scope"], p["passed"]) for n, p in events
              if n == "INVARIANT_STREAM_RESULT" and p.get("name") == "check_engine_reader_parity"]
    assert ("OK.ETORO", True) in stream and ("BAD.ETORO", False) in stream      # auch bei PASS im Strom
    assert any(r.getMessage().startswith("[#1354] BAD.ETORO: REJECT_ENGINE_READER_MISMATCH")
               for r in caplog.records)


# ─── Orchestrator: Echt-Ticks landen in RealTick/, nie im flachen File ──────────────

def test_merge_symbol_writes_realticks_to_the_realtick_dir_and_migrates_a_flat_file(tmp_path, monkeypatch):
    from automation import daily_orchestrator as orch

    def _table(ts: list[int], bid: float) -> pa.Table:
        f16 = pa.binary(16)
        p = bf._encode_fsb16(bid, 2)
        q = bf._encode_fsb16(bid + 0.1, 2)
        s = bf._encode_qty_fsb16(1.0, 2)
        n = len(ts)
        return pa.table({
            "bid_price": pa.array([p] * n, type=f16), "ask_price": pa.array([q] * n, type=f16),
            "bid_size": pa.array([s] * n, type=f16), "ask_size": pa.array([s] * n, type=f16),
            "ts_event": pa.array(ts, type=pa.uint64()), "ts_init": pa.array(ts, type=pa.uint64()),
        })

    qt = tmp_path / "data" / "quote_tick"
    monkeypatch.setattr(orch, "QUOTE_TICK_PATH", qt)
    inst = qt / _SYM
    inst.mkdir(parents=True)
    pq.write_table(_table([1, 2, 3], 100.0).replace_schema_metadata(
        {b"price_precision": b"2", b"size_precision": b"2", b"instrument_id": _SYM.encode()}),
        str(inst / "data.parquet"))                                       # Altbestand: flaches File
    log = logging.getLogger("t1354-merge")

    assert orch._merge_symbol(log, _SYM, [_table([3, 4, 5], 100.0)], {}) is True

    real = inst / "RealTick" / "data.parquet"
    assert real.exists() and not (inst / "data.parquet").exists()         # verschoben, nie gelöscht
    t = pq.read_table(str(real))
    assert t.column("ts_event").to_pylist() == [1, 2, 3, 4, 5]
    assert set(t.column("bar_interval_ns").to_pylist()) == {0}
    meta = t.schema.metadata
    assert meta[b"catalog_interval"] == b"RealTick" and b"catalog_schema_version" not in meta
    # Die Engine-Sicht kennt RealTick nicht: ohne Kerzen-Datei keine Sicht.
    with pytest.raises(EngineCatalogViewError):
        engine_catalog_view(tmp_path, _SYM)


def test_decode_helper_roundtrip_used_by_this_module():
    assert decode_fsb16_price(bf._encode_fsb16(100.0, 2)) == 100.0


@pytest.mark.skipif(os.environ.get(_CLEAN_SUBPROCESS_ENV) == "1", reason="bereits der saubere Subprozess.")
def test_real_engine_tests_in_a_clean_subprocess_when_this_process_is_polluted():
    """Im Suite-Prozess sind ``nautilus_trader``-Module gemockt: dieselben Bibliotheks-Vertragstests
    laufen dann in einem sauberen Interpreter (``-p no:cacheprovider``, ohne die Mock-Module)."""
    import subprocess
    import sys
    repo = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(Path(__file__)), "-q", "-p", "no:cacheprovider",
         "-k", "not subprocess"],
        cwd=str(repo), capture_output=True, text=True, timeout=600,
        env={**os.environ, _CLEAN_SUBPROCESS_ENV: "1"})
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1000:]
    assert "skipped" not in proc.stdout.splitlines()[-1], proc.stdout[-500:]
