"""Issue #693 — GapContinuationStrategy (SPEC_05, Regime-Roster-Erweiterung).

Validiert die 5-Datei-Checkliste (Import, strategy_defaults.json, strategies.json,
spaces.py-Zweig) und einen echten End-to-End-Backtest über die reale NautilusTrader-
BacktestEngine (`run_single_backtest_worker`, isolierter Subprozess). Verifiziert gegen einen
echten Engine-Lauf: 11 Trades über 60 Tage mit gelegentlichen Overnight-Gaps (Tag % 5) — die
seltenste der fünf neuen Strategien, konsistent mit dem gesenkten `min_trades`-Override in
strategies.json.
"""
import concurrent.futures
import multiprocessing
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from automation._serde import encode_price_fsb16, encode_qty_fsb16
from automation.backtest_runner import run_single_backtest_worker


def _run_isolated_worker(*args, **kwargs):
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as executor:
        future = executor.submit(run_single_backtest_worker, *args, **kwargs)
        return future.result()


def _make_mixed_regime_closes(n_days=60, seed=7):
    import random
    random.seed(seed)
    closes = []
    price = 200.0
    trend = 1
    for day in range(n_days):
        if day % 7 == 0:
            trend *= -1
        if day % 5 == 0 and day > 0:
            price *= (1 + trend * 0.025)
        for _ in range(24):
            price += trend * random.uniform(0.05, 0.6) + random.uniform(-0.3, 0.3)
            closes.append(max(price, 1.0))
    return closes


def _build_close_only_catalog(tmp_path: Path, instrument_id: str, closes, base_ts_ns: int,
                               hour_step_ns: int = 3600 * 1_000_000_000) -> Path:
    catalog_path = tmp_path / "nautilus"
    tick_dir = catalog_path / "data" / "quote_tick" / instrument_id
    tick_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = tick_dir / "data.parquet"

    price_prec, size_prec = 2, 2
    bid_prices, ask_prices, bid_sizes, ask_sizes, ts_events, ts_inits = [], [], [], [], [], []
    for i, price_val in enumerate(closes):
        bid_prices.append(encode_price_fsb16(price_val, price_prec))
        ask_prices.append(encode_price_fsb16(price_val, price_prec))
        bid_sizes.append(encode_qty_fsb16(1.0, size_prec))
        ask_sizes.append(encode_qty_fsb16(1.0, size_prec))
        ts = base_ts_ns + i * hour_step_ns
        ts_events.append(ts)
        ts_inits.append(ts)

    _FSB16 = pa.binary(16)
    schema = pa.schema([
        pa.field("bid_price", _FSB16), pa.field("ask_price", _FSB16),
        pa.field("bid_size", _FSB16), pa.field("ask_size", _FSB16),
        pa.field("ts_event", pa.uint64()), pa.field("ts_init", pa.uint64()),
    ])
    meta = {b"price_precision": str(price_prec).encode(), b"size_precision": str(size_prec).encode(),
            b"instrument_id": instrument_id.encode()}
    table = pa.table({
        "bid_price": pa.array(bid_prices, type=_FSB16), "ask_price": pa.array(ask_prices, type=_FSB16),
        "bid_size": pa.array(bid_sizes, type=_FSB16), "ask_size": pa.array(ask_sizes, type=_FSB16),
        "ts_event": pa.array(ts_events, type=pa.uint64()), "ts_init": pa.array(ts_inits, type=pa.uint64()),
    }, schema=schema)
    table = table.replace_schema_metadata(meta)
    pq.write_table(table, str(parquet_file))
    return catalog_path


def test_module_imports_cleanly():
    import automation.strategies.gap_continuation  # noqa: F401


def test_registered_in_strategies_json():
    """Issue #809 — active=false seit der #809-Deaktivierung (kein Session-Kalender verfuegbar,
    siehe strategies.json's _note); der Rest der Registrierung (Modul/Klasse/Overrides) bleibt
    fuer eine kuenftige Re-Aktivierung unveraendert bestehen."""
    import json
    data = json.loads(Path("automation/config/strategies.json").read_text("utf-8"))
    entries = [s for s in data["strategies"] if s["strategy_class"] == "GapContinuationStrategy"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["active"] is False
    assert entry["strategy_module"] == "automation.strategies.gap_continuation"
    assert entry["config_class"] == "GapContinuationConfig"
    assert entry["tournament_overrides"]["min_trades"] == 8


def test_defaults_present_and_match_spec():
    import json
    defaults = json.loads(Path("automation/config/strategy_defaults.json").read_text("utf-8"))
    d = defaults["GapContinuationStrategy"]
    assert d["gap_threshold_pct"] == 0.015
    assert d["max_bars_in_trade"] == 24
    assert d["max_daily_trades"] == 1


def test_spaces_branch_produces_valid_params():
    import optuna
    from automation.optimizer.spaces import sample_params

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study()
    trial = study.ask()
    params = sample_params("GapContinuationStrategy", trial)
    for key in ("gap_threshold_pct", "atr_period", "atr_trailing_multiplier", "max_bars_in_trade"):
        assert key in params
    assert 0.005 <= params["gap_threshold_pct"] <= 0.04


def test_backtest_generates_trades(tmp_path):
    closes = _make_mixed_regime_closes()
    base_ts = pd.Timestamp("2024-01-01T00:00:00Z").value
    catalog_path = _build_close_only_catalog(tmp_path, "TSLA.ETORO", closes, base_ts)

    strat = {
        "strategy_class": "GapContinuationStrategy",
        "strategy_module": "automation.strategies.gap_continuation",
        "config_class": "GapContinuationConfig",
        "params": {"allow_short": True, "gap_threshold_pct": 0.01},
    }
    res = _run_isolated_worker(
        inst_id_str="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL", strat=strat,
        catalog_path=str(catalog_path), start_ns=None, end_ns=None, start_capital=10000.0,
        generate_html_report=False, reports_dir=str(tmp_path / "reports"),
        worker_log_file=str(tmp_path / "worker.log"), span_tolerance_days=3.0,
    )
    assert res != {}, "Worker crashed / STRUCTURAL_ALL_UNEVALUABLE — kein Ergebnis"
    assert res["symbol"] == "TSLA.ETORO"
    total_trades = res.get("metrics", {}).get("total_trades", 0)
    assert total_trades > 0, f"Erwartet Gap-Continuation-Trades an Gap-Tagen, erhielt {total_trades}"
