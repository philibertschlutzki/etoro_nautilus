"""Issue #1298 (GH #1175, P0) — Tick- und Bar-Population als fail-loud Preflight mit
maschinenlesbarer Telemetrie.

Symptom. 2 810 Trials über fünf Läufe und drei Symbole ohne einen einzigen Trade; keine Bar-
Telemetrie; kein Artefakt, aus dem hervorgeht, ob überhaupt Ticks geladen wurden.

Fix.
1. ``backtest_runner.load_ticks_from_catalog`` stempelt Tick-/Fensterzähler in ein optionales
   ``out``-Dict (``n_ticks_raw``, ``n_ticks_after_session_filter``, ``session_window``,
   ``asset_class_key``).
2. ``run_single_backtest_worker`` reicht sie als ``_tick_population`` im Trial-Ergebnis-Dict durch.
3. ``parsing.parse_tournament``/``run_optimization.make_symbol_objective`` heben sie als
   Trial-User-Attrs; ``report._study_record`` bildet Study-Mediane.
4. ``invariants.check_tick_population`` (severity ``blocking``) FAILt, wenn eine Study
   ``n_ticks_after_session_filter_median == 0`` ODER ``n_bars_delivered_median == 0`` trägt.
5. ``sweep.run_per_symbol_sweep`` weist ein Symbol mit 0 Ticks nach dem Session-Filter (Probe VOR
   Phase 1) mit ``REJECT_DATA_UNAVAILABLE`` ab, bevor eine einzige Study startet.
"""
import json
import logging

import pytest

from automation import backtest_runner as br
from automation.optimizer import invariants as inv
from automation.optimizer import sweep


_NS_PER_HOUR = 3_600_000_000_000


# ---------------------------------------------------------------------------------------------
# backtest_runner.load_ticks_from_catalog — out-Parameter
# ---------------------------------------------------------------------------------------------

def _write_synthetic_catalog(tmp_path, symbol, ts_ns_list, price=100.0):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from automation._serde import encode_price_fsb16, encode_qty_fsb16

    price_prec, size_prec = 2, 2
    n = len(ts_ns_list)
    bid = [encode_price_fsb16(price, price_prec)] * n
    ask = [encode_price_fsb16(price + 0.02, price_prec)] * n
    sz = [encode_qty_fsb16(1.0, size_prec)] * n
    _FSB16 = pa.binary(16)
    schema = pa.schema([
        pa.field("bid_price", _FSB16), pa.field("ask_price", _FSB16),
        pa.field("bid_size", _FSB16), pa.field("ask_size", _FSB16),
        pa.field("ts_event", pa.uint64()), pa.field("ts_init", pa.uint64()),
    ])
    meta = {b"price_precision": str(price_prec).encode(), b"size_precision": str(size_prec).encode(),
           b"instrument_id": symbol.encode()}
    table = pa.table({
        "bid_price": pa.array(bid, type=_FSB16), "ask_price": pa.array(ask, type=_FSB16),
        "bid_size": pa.array(sz, type=_FSB16), "ask_size": pa.array(sz, type=_FSB16),
        "ts_event": pa.array(ts_ns_list, type=pa.uint64()),
        "ts_init": pa.array(ts_ns_list, type=pa.uint64()),
    }, schema=schema).replace_schema_metadata(meta)
    d = tmp_path / "data" / "quote_tick" / symbol
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(d / "data.parquet"))


def test_load_ticks_from_catalog_stamps_out_dict_for_synthetic_fixture(tmp_path):
    """30 Tage Stunden-Ticks (24/7): Zähler vor Filter = 720, nach EQUITY-Filter = die erwartete
    Session-Bar-Zahl (weniger als 720, > 0)."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    base_ts = 1_700_000_000 * 1_000_000_000  # ein fixer, deterministischer Anker.
    ts_list = [base_ts + i * _NS_PER_HOUR for i in range(720)]  # 30 Tage Stundenraster.
    _write_synthetic_catalog(tmp_path, "TSLA.ETORO", ts_list)

    catalog = ParquetDataCatalog(str(tmp_path))
    out: dict = {}
    ticks = br.load_ticks_from_catalog(
        catalog, "TSLA.ETORO", None, None,
        session_hours_by_asset_class={"EQUITY": {"open_utc": "13:00", "close_utc": "20:00"}},
        asset_class_key="EQUITY", out=out)
    assert out["n_ticks_raw"] == 720
    assert 0 < out["n_ticks_after_session_filter"] < 720
    assert len(ticks) == out["n_ticks_after_session_filter"]
    assert out["asset_class_key"] == "EQUITY"
    assert out["session_window"] == ("13:00", "20:00")
    # Issue #1300 (GH #1177) Fix Punkt 1 — die GESNAPPTE Fenstergrenze erscheint zusätzlich in
    # derselben #1298-out-Telemetrie (hier bereits grid-aligned, also unveraendert).
    assert out["session_window_snapped"] == ("13:00", "20:00")


def test_load_ticks_from_catalog_out_dict_with_empty_catalog(tmp_path):
    """Leere Tick-Liste (kein Katalog) -> out-Dict trägt n_ticks_raw=0, kein Absturz."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    catalog = ParquetDataCatalog(str(tmp_path))
    out: dict = {}
    ticks = br.load_ticks_from_catalog(catalog, "GHOST.ETORO", None, None, out=out)
    assert ticks == []
    assert out["n_ticks_raw"] == 0
    assert out["n_ticks_after_session_filter"] == 0


def test_load_ticks_from_catalog_out_none_is_backward_compatible(tmp_path):
    """out=None (Default) ⇒ bit-identisches Alt-Verhalten, kein KeyError/Crash."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    catalog = ParquetDataCatalog(str(tmp_path))
    ticks = br.load_ticks_from_catalog(catalog, "GHOST.ETORO", None, None)
    assert ticks == []


# ---------------------------------------------------------------------------------------------
# invariants.check_tick_population
# ---------------------------------------------------------------------------------------------

def test_check_tick_population_fails_with_pair_convention_on_zero_ticks():
    studies = [
        {"strategy": "SmaCrossoverStrategy", "symbol": "TSLA.ETORO",
         "n_ticks_after_session_filter_median": 0, "n_bars_delivered_median": 0},
    ]
    result = inv.check_tick_population(studies)
    assert result.passed is False
    assert result.severity == "blocking"
    assert result.actual == {
        "SmaCrossoverStrategy/TSLA.ETORO": {
            "n_ticks_after_session_filter_median": 0, "n_bars_delivered_median": 0,
        }
    }


def test_check_tick_population_fails_when_only_bars_are_zero():
    studies = [
        {"strategy": "S", "symbol": "X.ETORO",
         "n_ticks_after_session_filter_median": 100, "n_bars_delivered_median": 0},
    ]
    result = inv.check_tick_population(studies)
    assert result.passed is False


def test_check_tick_population_passes_with_positive_counts():
    studies = [
        {"strategy": "S", "symbol": "X.ETORO",
         "n_ticks_after_session_filter_median": 1500, "n_bars_delivered_median": 1750},
    ]
    result = inv.check_tick_population(studies)
    assert result.passed is True
    assert result.actual is None


def test_check_tick_population_inconclusive_without_any_telemetry():
    studies = [{"strategy": "S", "symbol": "X.ETORO"}]
    result = inv.check_tick_population(studies)
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


def test_check_tick_population_registered_in_fail_fast_invariants():
    cfg = json.loads(open("automation/config/optimizer.json", encoding="utf-8").read())
    assert "check_tick_population" in cfg["fail_fast_invariants"]


# ---------------------------------------------------------------------------------------------
# sweep.probe_symbol_tick_population — lightweight pyarrow-Probe
# ---------------------------------------------------------------------------------------------

def _write_pyarrow_quote_tick_parquet(tmp_path, symbol, ts_ns_list, price=100.0):
    import pyarrow as pa
    import pyarrow.parquet as pq
    d = tmp_path / "data" / "quote_tick" / symbol
    d.mkdir(parents=True, exist_ok=True)
    n = len(ts_ns_list)
    table = pa.table({
        "bid_price": pa.array([price] * n, type=pa.float64()),
        "ask_price": pa.array([price + 0.02] * n, type=pa.float64()),
        "ts_event": pa.array(ts_ns_list, type=pa.int64()),
    })
    pq.write_table(table, str(d / "data.parquet"))


def test_probe_symbol_tick_population_synthetic_fixture(tmp_path):
    base_ts = 1_700_000_000 * 1_000_000_000
    ts_list = [base_ts + i * _NS_PER_HOUR for i in range(720)]
    _write_pyarrow_quote_tick_parquet(tmp_path, "TSLA.ETORO", ts_list)
    probe = sweep.probe_symbol_tick_population(
        "TSLA.ETORO", catalog_path=tmp_path,
        session_hours_by_asset_class={"EQUITY": {"open_utc": "13:00", "close_utc": "20:00"}},
        asset_class_key="EQUITY")
    assert probe is not None
    assert probe["n_ticks_raw"] == 720
    assert 0 < probe["n_ticks_after_session_filter"] < 720


def test_probe_symbol_tick_population_missing_catalog_returns_none(tmp_path):
    assert sweep.probe_symbol_tick_population("GHOST.ETORO", catalog_path=tmp_path) is None


def test_probe_symbol_tick_population_no_window_configured_keeps_all_ticks(tmp_path):
    ts_list = [i * _NS_PER_HOUR for i in range(48)]
    _write_pyarrow_quote_tick_parquet(tmp_path, "BTC.ETORO", ts_list)
    probe = sweep.probe_symbol_tick_population("BTC.ETORO", catalog_path=tmp_path)
    assert probe["n_ticks_raw"] == probe["n_ticks_after_session_filter"] == 48


# ---------------------------------------------------------------------------------------------
# sweep.run_per_symbol_sweep — REJECT_DATA_UNAVAILABLE vor Phase 1, keine Studies/diagnosed_pairs.
# ---------------------------------------------------------------------------------------------

def test_sweep_rejects_symbol_with_zero_ticks_before_phase_1(tmp_path, monkeypatch):
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)  # kein backtest.json -> {} Config.

    events = []

    def _capture(logger, event_type, payload, level=logging.INFO):
        events.append((event_type, payload, level))

    monkeypatch.setattr(sweep, "emit_execution_event", _capture)
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda: ["TSLA.ETORO"])
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {"walk_forward": {}})
    monkeypatch.setattr(sweep, "count_available_bars", lambda syms, **kw: {})

    optimize_calls = []

    def _fake_optimize(pair):
        optimize_calls.append(pair)
        return None

    try:
        sweep.run_per_symbol_sweep(
            ["SmaCrossoverStrategy"], ["TSLA.ETORO"],
            optimize_symbol=_fake_optimize, confirm=lambda *a, **kw: None,
            tick_population_fn=lambda symbol: {"n_ticks_raw": 0, "n_ticks_after_session_filter": 0},
            bar_quality_fn=lambda symbol: None,
            run_id="test-1298-reject",
        )
    except Exception:
        pass

    reject_events = [p for (etype, p, _lvl) in events if etype == "REJECT_DATA_UNAVAILABLE"]
    assert reject_events, "kein REJECT_DATA_UNAVAILABLE-Ereignis emittiert"
    assert reject_events[0]["symbol"] == "TSLA.ETORO"
    assert reject_events[0]["n_ticks_raw"] == 0

    tick_population_stream_events = [
        p for (etype, p, _lvl) in events
        if etype == "INVARIANT_STREAM_RESULT" and p.get("name") == "check_tick_population"
    ]
    assert tick_population_stream_events
    assert tick_population_stream_events[0]["passed"] is False
    assert tick_population_stream_events[0]["source"] == "sweep"

    # Kein Symbol darf Phase 1 (optimize_symbol) erreicht haben.
    assert optimize_calls == []
