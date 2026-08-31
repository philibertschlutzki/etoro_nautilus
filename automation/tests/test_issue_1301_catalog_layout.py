"""Issue #1301 (GH #1178, P0) — Bar-Qualitäts-Stichprobe (``sweep._load_symbol_bar_quality_sample``)
scheitert nicht mehr stumm an Katalog-Layout und Spaltennamen.

Symptom. ``check_bar_quality`` meldete ``CATALOG_SAMPLE_UNAVAILABLE_DESPITE_CATALOG`` für JEDES
geplante Symbol, weil die Stichproben-Funktion drei unbelegte Annahmen traf: genau eine Datei
``data.parquet`` je Instrument, exakt die Spaltennamen ``bid_price``/``ask_price``/``ts_event``,
und ein stummes ``None`` bei fehlenden Spalten.

Fix.
1. Datei-Auflösung über ``automation.catalog_paths.resolve_quote_tick_files`` (Glob-Muster:
   ``data.parquet`` -> ``part-*.parquet`` -> ``*.parquet``).
2. Spalten-Auflösung über ``automation.catalog_paths.resolve_quote_tick_columns`` (Alias-Tabelle).
3. Jeder ``None``-Rückgabepfad emittiert ``BAR_QUALITY_SAMPLE_UNAVAILABLE`` mit strukturiertem
   ``reason`` (``FILE_NOT_FOUND``/``COLUMNS_MISSING``/``EMPTY_AFTER_RESAMPLE``/``EXCEPTION``).
4. Die aggregierten ``reason``-Zähler stehen im ``CATALOG_SAMPLE_UNAVAILABLE_DESPITE_CATALOG``-
   Ereignis unter ``actual.reasons``.
"""
import logging

import pytest

from automation.optimizer import sweep


def _write_quote_tick_parquet(path, ts_ns_list, price=100.0,
                              bid_col="bid_price", ask_col="ask_price", ts_col="ts_event"):
    # Issue #1213 — bid_price/ask_price sind im echten Katalog rohe pa.binary(16)-FSB16-Werte
    # (automation._serde.encode_price_fsb16, siehe automation.catalog_service/api_backfiller),
    # kein pa.float64() — eine float64-Fixture wuerde den #1213-Decode-Pfad nicht abdecken.
    import pyarrow as pa
    import pyarrow.parquet as pq
    from automation._serde import encode_price_fsb16
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(ts_ns_list)
    _FSB16 = pa.binary(16)
    table = pa.table({
        bid_col: pa.array([encode_price_fsb16(price, 2)] * n, type=_FSB16),
        ask_col: pa.array([encode_price_fsb16(price + 0.02, 2)] * n, type=_FSB16),
        ts_col: pa.array(ts_ns_list, type=pa.int64()),
    })
    pq.write_table(table, str(path))


def _capture_events(monkeypatch):
    events = []

    def _capture(logger, event_type, payload, level=logging.INFO):
        events.append((event_type, payload, level))

    monkeypatch.setattr(sweep, "emit_execution_event", _capture)
    return events


_NS_PER_HOUR = 3_600_000_000_000


def _hourly_ticks(n=48):
    return [i * _NS_PER_HOUR for i in range(n)]


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium 1 — part-*.parquet-Layout liefert eine gültige Stichprobe.
# ---------------------------------------------------------------------------------------------

def test_partitioned_layout_yields_valid_sample(tmp_path, monkeypatch):
    _capture_events(monkeypatch)
    d = tmp_path / "data" / "quote_tick" / "TSLA.ETORO"
    ts_list = _hourly_ticks(48)
    half = len(ts_list) // 2
    _write_quote_tick_parquet(d / "part-0.parquet", ts_list[:half])
    _write_quote_tick_parquet(d / "part-1.parquet", ts_list[half:])
    sample = sweep._load_symbol_bar_quality_sample("TSLA.ETORO", catalog_path=tmp_path)
    assert sample is not None
    assert sample["ticks_per_bar_median"] == 1.0


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium 2 — Spalten-Alias (bid/ask statt bid_price/ask_price).
# ---------------------------------------------------------------------------------------------

def test_column_aliases_yield_valid_sample(tmp_path, monkeypatch):
    _capture_events(monkeypatch)
    d = tmp_path / "data" / "quote_tick" / "NVDA.ETORO"
    _write_quote_tick_parquet(d / "data.parquet", _hourly_ticks(24),
                              bid_col="bid", ask_col="ask")
    sample = sweep._load_symbol_bar_quality_sample("NVDA.ETORO", catalog_path=tmp_path)
    assert sample is not None
    assert sample["ticks_per_bar_median"] == 1.0


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium 3 — fehlende Preis-Spalten -> BAR_QUALITY_SAMPLE_UNAVAILABLE(COLUMNS_MISSING).
# ---------------------------------------------------------------------------------------------

def test_missing_price_columns_emits_columns_missing_with_schema(tmp_path, monkeypatch):
    events = _capture_events(monkeypatch)
    d = tmp_path / "data" / "quote_tick" / "GHOST.ETORO"
    import pyarrow as pa
    import pyarrow.parquet as pq
    d.mkdir(parents=True)
    table = pa.table({"open_interest": pa.array([1, 2, 3], type=pa.int64())})
    pq.write_table(table, str(d / "data.parquet"))

    sample = sweep._load_symbol_bar_quality_sample("GHOST.ETORO", catalog_path=tmp_path)
    assert sample is None

    unavailable = [p for (etype, p, _lvl) in events if etype == "BAR_QUALITY_SAMPLE_UNAVAILABLE"]
    assert len(unavailable) == 1
    assert unavailable[0]["reason"] == "COLUMNS_MISSING"
    assert unavailable[0]["schema_names"] == ["open_interest"]


def test_missing_file_emits_file_not_found(tmp_path, monkeypatch):
    events = _capture_events(monkeypatch)
    sample = sweep._load_symbol_bar_quality_sample("NOPE.ETORO", catalog_path=tmp_path)
    assert sample is None
    unavailable = [p for (etype, p, _lvl) in events if etype == "BAR_QUALITY_SAMPLE_UNAVAILABLE"]
    assert len(unavailable) == 1
    assert unavailable[0]["reason"] == "FILE_NOT_FOUND"


def test_unavailable_reasons_sink_collects_reason(tmp_path, monkeypatch):
    _capture_events(monkeypatch)
    sink: list[str] = []
    sample = sweep._load_symbol_bar_quality_sample(
        "NOPE.ETORO", catalog_path=tmp_path, unavailable_reasons=sink)
    assert sample is None
    assert sink == ["FILE_NOT_FOUND"]


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium 4 — aggregierte reason-Zähler im CATALOG_SAMPLE_UNAVAILABLE_DESPITE_CATALOG-
# Ereignis (actual.reasons), sobald das Katalog-Wurzelverzeichnis existiert.
# ---------------------------------------------------------------------------------------------

def test_despite_catalog_event_carries_aggregated_reason_counts(tmp_path, monkeypatch):
    fake_cfg_dir = tmp_path / "automation" / "config"
    fake_cfg_dir.mkdir(parents=True)
    catalog_root = tmp_path / "data" / "nautilus"
    catalog_root.mkdir(parents=True)
    monkeypatch.setattr(sweep, "config_dir", lambda: fake_cfg_dir)

    events = _capture_events(monkeypatch)
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda: ["TSLA.ETORO", "NVDA.ETORO"])
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {"walk_forward": {}})
    monkeypatch.setattr(sweep, "count_available_bars", lambda syms, **kw: {})

    try:
        sweep.run_per_symbol_sweep(
            ["NonexistentStrategy"], ["TSLA.ETORO", "NVDA.ETORO"],
            optimize_symbol=lambda pair: None, confirm=lambda *a, **kw: None,
            # explizit die ECHTE Default-Funktion (Identitaetsvergleich in run_per_symbol_sweep
            # aktiviert dieselbe unavailable_reasons-Sammlung wie ein weggelassenes bar_quality_fn,
            # ohne den vollen optimize_symbol-Pfad zu benoetigen) -> Datei fehlt -> FILE_NOT_FOUND.
            bar_quality_fn=sweep._load_symbol_bar_quality_sample,
            run_id="test-1301-reason-agg",
        )
    except Exception:
        pass

    despite_catalog = [
        p for (etype, p, _lvl) in events
        if etype == "INVARIANT_STREAM_RESULT" and p.get("name") == "check_bar_quality"
        and p.get("scope") == "global"
    ]
    assert despite_catalog, "kein globales check_bar_quality-Ereignis emittiert"
    actual = despite_catalog[-1]["actual"]
    assert actual["reasons"] == {"FILE_NOT_FOUND": 2}
