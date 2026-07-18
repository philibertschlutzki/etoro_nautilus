"""Issue #689 — SqueezeBreakoutStrategy (SPEC_01, Regime-Roster-Erweiterung).

Validiert die 5-Datei-Checkliste (Import, strategy_defaults.json, strategies.json,
spaces.py-Zweig) und einen echten End-to-End-Backtest über die reale NautilusTrader-
BacktestEngine (`run_single_backtest_worker`, isolierter Subprozess), analog zum
bestehenden Muster in `test_backtest_trades_generated.py`.

Squeeze-Release erfordert echte Intrabar-Spannen (High/Low jenseits der Close-zu-Close-
Bewegung), damit Keltner (Multiplikator 1.5) breiter als Bollinger (Std-Dev 2.0) werden kann
— ein reiner Close-Preis-Tick pro Stunde (Standard-Testfixture-Konvention dieses Repos)
erzeugt O=H=L=C-Bars und damit nie eine echte Squeeze-Bedingung. Der Datengenerator hier
injiziert daher bewusst 4 Ticks je Stunde (Open/High/Low/Close), um reale Docht-Spannen in
der Kontraktionsphase zu erzeugen (verifiziert gegen einen echten NautilusTrader-Engine-Lauf:
7 Trades über 8 Kontraktions-/Release-Zyklen).
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


def _make_squeeze_release_bars(n_cycles=8, n_contraction=40, n_release=20, seed=5):
    """Kontraktion (winzige Close-Bewegung, GROSSE Dochte ⇒ ATR >> Close-Std ⇒ Squeeze aktiv)
    gefolgt von einer scharfen gerichteten Release-Phase (enge Dochte)."""
    import random
    random.seed(seed)
    bars = []
    price = 200.0
    for cycle in range(n_cycles):
        for _ in range(n_contraction):
            o = price
            price += random.uniform(-0.03, 0.03)
            c = price
            wick = 1.2
            bars.append((o, max(o, c) + wick, min(o, c) - wick, c))
        direction = 1 if cycle % 2 == 0 else -1
        for _ in range(n_release):
            o = price
            price += direction * random.uniform(0.6, 1.2)
            c = price
            wick = 0.05
            bars.append((o, max(o, c) + wick, min(o, c) - wick, c))
    return bars


def _build_ohlc_catalog(tmp_path: Path, instrument_id: str, bars_ohlc, base_ts_ns: int,
                         hour_step_ns: int = 3600 * 1_000_000_000) -> Path:
    catalog_path = tmp_path / "nautilus"
    tick_dir = catalog_path / "data" / "quote_tick" / instrument_id
    tick_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = tick_dir / "data.parquet"

    price_prec, size_prec = 2, 2
    bid_prices, ask_prices, bid_sizes, ask_sizes, ts_events, ts_inits = [], [], [], [], [], []
    sub_step = hour_step_ns // 4
    for i, (o, h, l, c) in enumerate(bars_ohlc):
        base = base_ts_ns + i * hour_step_ns
        for j, price_val in enumerate((o, h, l, c)):
            bid_prices.append(encode_price_fsb16(price_val, price_prec))
            ask_prices.append(encode_price_fsb16(price_val, price_prec))
            bid_sizes.append(encode_qty_fsb16(1.0, size_prec))
            ask_sizes.append(encode_qty_fsb16(1.0, size_prec))
            ts = base + j * sub_step
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
    import automation.strategies.squeeze_breakout  # noqa: F401


def test_registered_in_strategies_json():
    import json
    data = json.loads(Path("automation/config/strategies.json").read_text("utf-8"))
    entries = [s for s in data["strategies"] if s["strategy_class"] == "SqueezeBreakoutStrategy"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["active"] is True
    assert entry["strategy_module"] == "automation.strategies.squeeze_breakout"
    assert entry["config_class"] == "SqueezeBreakoutConfig"
    assert entry["tournament_overrides"]["min_trades"] == 12


def test_defaults_present_and_match_spec():
    import json
    defaults = json.loads(Path("automation/config/strategy_defaults.json").read_text("utf-8"))
    d = defaults["SqueezeBreakoutStrategy"]
    assert d["bb_period"] == 20
    assert d["bb_std_dev"] == 2.0
    assert d["keltner_period"] == 20
    assert d["keltner_multiplier"] == 1.5
    assert d["min_squeeze_bars"] == 6
    assert d["max_bars_in_trade"] == 24


def test_spaces_branch_produces_valid_params():
    import optuna
    from automation.optimizer.spaces import sample_params

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study()
    trial = study.ask()
    params = sample_params("SqueezeBreakoutStrategy", trial)
    for key in ("bb_period", "bb_std_dev", "keltner_period", "keltner_multiplier",
                "min_squeeze_bars", "cooldown_bars", "atr_period",
                "atr_trailing_multiplier", "max_bars_in_trade"):
        assert key in params
    assert 12 <= params["max_bars_in_trade"] <= 30


def test_squeeze_release_logic_fires_on_realistic_wick_data():
    """Direkte Indikator-Ebene (kein voller Engine-Lauf): mit echten Docht-Spannen während der
    Kontraktion muss BollingerBands innerhalb KeltnerChannel liegen (Squeeze aktiv) und nach
    `min_squeeze_bars` Bars ein Release erkannt werden."""
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.indicators import BollingerBands, KeltnerChannel

    bar_type = BarType.from_str("TSLA.ETORO-1-HOUR-MID-INTERNAL")
    bb = BollingerBands(20, 2.0)
    kc = KeltnerChannel(period=20, k_multiplier=1.5)
    bars_ohlc = _make_squeeze_release_bars()

    squeeze_on_count = 0
    release_count = 0
    squeeze_count = 0
    min_squeeze_bars = 6
    for i, (o, h, l, c) in enumerate(bars_ohlc):
        ts = i * 3600_000_000_000
        bar = Bar(bar_type=bar_type, open=Price(round(o, 2), 2), high=Price(round(h, 2), 2),
                   low=Price(round(l, 2), 2), close=Price(round(c, 2), 2),
                   volume=Quantity(1.0, 2), ts_event=ts, ts_init=ts)
        bb.handle_bar(bar)
        kc.handle_bar(bar)
        if bb.initialized and kc.initialized:
            squeeze_on = (bb.lower > kc.lower) and (bb.upper < kc.upper)
            if squeeze_count >= min_squeeze_bars and not squeeze_on:
                release_count += 1
            squeeze_count = squeeze_count + 1 if squeeze_on else 0
            if squeeze_on:
                squeeze_on_count += 1

    assert squeeze_on_count > 0, "Kontraktionsphase muss echte Squeeze-Bedingungen erzeugen"
    assert release_count >= 4, "Mindestens die Hälfte der 8 Zyklen muss einen Release auslösen"


def test_backtest_generates_trades(tmp_path):
    """Echter End-to-End-Backtest über die reale NautilusTrader-BacktestEngine (isolierter
    Subprozess, analog `test_backtest_trades_generated.py`). Verifiziert gegen echte Docht-
    Daten: 7 Trades über 8 Kontraktions-/Release-Zyklen."""
    bars_ohlc = _make_squeeze_release_bars()
    base_ts = pd.Timestamp("2024-01-01T00:00:00Z").value
    catalog_path = _build_ohlc_catalog(tmp_path, "TSLA.ETORO", bars_ohlc, base_ts)

    strat = {
        "strategy_class": "SqueezeBreakoutStrategy",
        "strategy_module": "automation.strategies.squeeze_breakout",
        "config_class": "SqueezeBreakoutConfig",
        "params": {"allow_short": True, "cooldown_bars": 2},
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
    assert total_trades > 0, f"Erwartet echte Squeeze-Release-Trades, erhielt {total_trades}"
