"""Issue #691 — DonchianRegimeBreakoutStrategy (SPEC_03, Regime-Roster-Erweiterung).

Validiert die 5-Datei-Checkliste (Import, strategy_defaults.json, strategies.json,
spaces.py-Zweig) und einen echten End-to-End-Backtest über die reale NautilusTrader-
BacktestEngine (`run_single_backtest_worker`, isolierter Subprozess).

Wichtiger Trockenlauf-Befund (Pitfall #9 des Implementierungs-Leitfadens #688): ein direkter
Test von ``DirectionalMovement(period).value`` gegen die installierte NautilusTrader-Version
(1.230.0) zeigte den Wert konstant bei ``0.0`` (nie ein plausibler ADX-Wert — ``.pos``/``.neg``
liefern dagegen reale, trend-abhängige Werte). Ein Regime-Gate auf ``adx.value >= threshold``
(Option A) würde daher NIE feuern — exakt das bereits dokumentierte
``AdxAtrMomentumStrategy``-"ADX-Initialisierungsproblem". Die Strategie nutzt daher aktiv
Option B (EMA-Steigung); dieser Test pinnt das Ergebnis dieser Entscheidung (34 Trades über
einen echten Engine-Lauf mit persistenten, alternierenden Trend-Phasen).
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


def _make_persistent_trend_closes(n_days=40, seed=3):
    """Lange, monotone Trend-Phasen (~10 Tage je Richtung) — erzeugt reale EMA-Steigungs-
    Persistenz und saubere Donchian-Kanal-Ausbrüche an jedem Phasenwechsel."""
    import random
    random.seed(seed)
    closes = []
    price = 200.0
    for day in range(n_days):
        trend = 1.0 if (day // 10) % 2 == 0 else -1.0
        for _ in range(24):
            price += trend * random.uniform(0.3, 1.0)
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
    import automation.strategies.donchian_regime_breakout  # noqa: F401


def test_registered_in_strategies_json():
    import json
    data = json.loads(Path("automation/config/strategies.json").read_text("utf-8"))
    entries = [s for s in data["strategies"] if s["strategy_class"] == "DonchianRegimeBreakoutStrategy"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["active"] is True
    assert entry["strategy_module"] == "automation.strategies.donchian_regime_breakout"
    assert entry["config_class"] == "DonchianRegimeBreakoutConfig"


def test_defaults_present_and_match_spec():
    import json
    defaults = json.loads(Path("automation/config/strategy_defaults.json").read_text("utf-8"))
    d = defaults["DonchianRegimeBreakoutStrategy"]
    assert d["donchian_period"] == 20
    assert d["adx_period"] == 14
    assert d["adx_threshold"] == 20.0
    assert d["ema_period"] == 50
    assert d["max_bars_in_trade"] == 24


def test_spaces_branch_produces_valid_params_without_dead_adx_tuning():
    """Issue #691-Trockenlauf-Fix: `adx_period`/`adx_threshold` werden NICHT gesampelt (das
    ADX-Regime-Gate ist funktional tot, siehe Modul-Docstring) — sonst Phantom-Tuning."""
    import optuna
    from automation.optimizer.spaces import sample_params

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study()
    trial = study.ask()
    params = sample_params("DonchianRegimeBreakoutStrategy", trial)
    for key in ("donchian_period", "ema_period", "cooldown_bars", "atr_period",
                "atr_trailing_multiplier", "max_bars_in_trade"):
        assert key in params
    assert "adx_period" not in params
    assert "adx_threshold" not in params


def test_adx_directional_movement_value_is_broken_in_installed_nautilus_version():
    """Dokumentiert den konkreten Trockenlauf-Befund (Pitfall #9): `DirectionalMovement.value`
    bleibt in der installierten NautilusTrader-Version konstant 0.0, `.pos`/`.neg` liefern
    dagegen reale Werte. Dieser Test schlägt fehl (und macht die Config-Doku damit sichtbar
    veraltet), falls eine künftige NautilusTrader-Version dies behebt — dann kann Option A
    reaktiviert werden (siehe donchian_regime_breakout.py-Docstring)."""
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.indicators import DirectionalMovement

    bar_type = BarType.from_str("TSLA.ETORO-1-HOUR-MID-INTERNAL")
    adx = DirectionalMovement(14)
    closes = _make_persistent_trend_closes()
    prev_close = closes[0]
    for i, c in enumerate(closes):
        o = prev_close
        h, l = max(o, c) + 0.1, min(o, c) - 0.1
        ts = i * 3600_000_000_000
        bar = Bar(bar_type=bar_type, open=Price(round(o, 2), 2), high=Price(round(h, 2), 2),
                   low=Price(round(l, 2), 2), close=Price(round(c, 2), 2),
                   volume=Quantity(1.0, 2), ts_event=ts, ts_init=ts)
        adx.handle_bar(bar)
        prev_close = c
    assert adx.initialized
    assert adx.value == 0.0, (
        "DirectionalMovement.value liefert jetzt einen Nicht-Null-Wert — Option A (ADX-Gate) "
        "kann in donchian_regime_breakout.py reaktiviert werden (siehe Modul-Docstring)."
    )


def test_backtest_generates_trades_with_ema_slope_regime(tmp_path):
    closes = _make_persistent_trend_closes()
    base_ts = pd.Timestamp("2024-01-01T00:00:00Z").value
    catalog_path = _build_close_only_catalog(tmp_path, "TSLA.ETORO", closes, base_ts)

    strat = {
        "strategy_class": "DonchianRegimeBreakoutStrategy",
        "strategy_module": "automation.strategies.donchian_regime_breakout",
        "config_class": "DonchianRegimeBreakoutConfig",
        "params": {"allow_short": True, "donchian_period": 15, "cooldown_bars": 2},
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
    assert total_trades > 10, f"Erwartet echte Donchian-Ausbrüche im Trend-Regime, erhielt {total_trades}"
