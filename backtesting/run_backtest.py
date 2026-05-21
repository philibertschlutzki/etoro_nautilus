#!/usr/bin/env python3
"""
run_backtest.py

NautilusTrader Multi-Strategie Backtesting Engine mit Tournament-Modus.

Optimierungen & Fixes:
  - OmsType.NETTING (Klassischer Netting-Modus für korrekte Order-Exekution)
  - extract_metrics: Behebt den AttributeError (Cache hat kein .fills()). Verwendet 
    jetzt robust den offiziellen trader.generate_fills_report() DataFrame.
  - Dynamisches Upscaling von 'trade_amount_usd': Verhindert das lautlose Verwerfen 
    von Signalen bei US-Aktien (Bug 1: units < size_increment).
  - Zeitfilter: pd.Timestamp → Nanosekunden (Katalog-Kompatibilität)
  - normalize_parquet_metadata() vor Backtesting (Arrow-Schema-Konflikte)
  - Robuste Fehlerbehandlung bei KeyboardInterrupt im Multiprocessing.
"""

import os
import sys
import json
import math
import argparse
import importlib
import contextlib
import io
import multiprocessing
import atexit
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool as _BrokenPool
from collections import deque

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.identifiers import Venue, InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import Equity
from nautilus_trader.analysis.tearsheet import create_tearsheet

from adapters.instrument_utils import get_size_precision

# ---------------------------------------------------------------------------
# Globale Housekeeping
# ---------------------------------------------------------------------------

_worker_log_files: list[str] = []


def _cleanup_worker_logs() -> None:
    for path in _worker_log_files:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


atexit.register(_cleanup_worker_logs)


# ---------------------------------------------------------------------------
# DualLogger
# ---------------------------------------------------------------------------

class DualLogger:
    def __init__(self, filepath: str) -> None:
        self.terminal = sys.stdout
        self.log = open(filepath, "a", encoding="utf-8")

    def write(self, message: str) -> None:
        self.terminal.write(message)
        self.log.write(message)

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()


# ---------------------------------------------------------------------------
# Parquet-Metadaten-Normalisierung
# ---------------------------------------------------------------------------

def normalize_parquet_metadata(catalog_path: str, instrument_id_str: str) -> bool:
    inst_dir = Path(catalog_path) / "data" / "quote_tick" / instrument_id_str
    if not inst_dir.exists():
        return False

    parquet_files = sorted(inst_dir.rglob("*.parquet"))
    if len(parquet_files) <= 1:
        return False

    file_metas: list[tuple[Path, dict]] = []
    for f in parquet_files:
        try:
            schema = pq.read_schema(str(f))
            file_metas.append((f, schema.metadata or {}))
        except Exception:
            pass

    if len(file_metas) <= 1:
        return False

    def get_precision(meta: dict) -> str | None:
        for key in (b'price_precision', 'price_precision'):
            val = meta.get(key)
            if val is not None:
                return val.decode() if isinstance(val, bytes) else str(val)
        return None

    precisions = {get_precision(m) for _, m in file_metas}
    precisions.discard(None)
    all_metas = [m for _, m in file_metas]

    if len(precisions) <= 1 and all(m == all_metas[0] for m in all_metas):
        return False

    ref_meta = file_metas[-1][1]
    ref_precision = get_precision(ref_meta) or '?'
    print(f"  🔧 [{instrument_id_str}] Konflikt {precisions} → precision={ref_precision}")

    patched = 0
    for f, meta in file_metas:
        if meta == ref_meta:
            continue
        try:
            table = pq.read_table(str(f))
            pq.write_table(table.replace_schema_metadata(ref_meta), str(f), compression="snappy")
            patched += 1
        except Exception as e:
            print(f"  ⚠️  [{instrument_id_str}] Patch-Fehler {f.name}: {e}")

    if patched:
        print(f"  ✅ [{instrument_id_str}] {patched}/{len(file_metas)} Dateien gepatcht")
    return patched > 0


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def load_config(filepath: str) -> dict[str, Any]:
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def discover_instruments_from_catalog(catalog_path: str) -> list[str]:
    tick_dir = os.path.join(catalog_path, "data", "quote_tick")
    instruments = []
    if os.path.exists(tick_dir):
        for entry in os.listdir(tick_dir):
            full = os.path.join(tick_dir, entry)
            if os.path.isdir(full) and not entry.startswith('.'):
                clean = entry.replace("instrument_id=", "")
                if clean:
                    instruments.append(clean)
    return sorted(instruments)


def validate_strategy_params(strat: dict) -> list[str]:
    warnings: list[str] = []
    params = strat.get("params", {})
    name = strat.get("strategy_class", "?")

    macd_fast = params.get("macd_fast")
    macd_slow = params.get("macd_slow")
    if macd_fast is not None and macd_slow is not None and macd_fast >= macd_slow:
        warnings.append(
            f"{name}: macd_fast({macd_fast}) >= macd_slow({macd_slow}) — ungültig"
        )
    for key in ("bb_std_dev", "keltner_multiplier", "atr_multiplier", "volume_multiplier"):
        val = params.get(key)
        if val is not None and val <= 0:
            warnings.append(f"{name}: {key}={val} muss > 0 sein")
    return warnings


def ts_to_ns(ts: pd.Timestamp | None) -> int | None:
    return int(ts.value) if ts is not None else None


def load_ticks_from_catalog(
    catalog: ParquetDataCatalog,
    instrument_id_str: str,
    start_ns: int | None,
    end_ns: int | None,
) -> list:
    try:
        ticks = catalog.quote_ticks(
            instrument_ids=[instrument_id_str],
            start=start_ns,
            end=end_ns,
        )
        return ticks if ticks else []
    except Exception as e:
        raise RuntimeError(f"catalog.quote_ticks() fehlgeschlagen: {e}") from e


def infer_precision_from_ticks(ticks: list) -> int:
    if not ticks:
        return 2
    precisions = [
        t.bid_price.precision
        for t in ticks[:20]
        if hasattr(t.bid_price, 'precision')
    ]
    return int(max(precisions)) if precisions else 2


def create_mock_instrument(instrument_id_str: str, price_precision: int = 2) -> Equity:
    inst_id = InstrumentId.from_str(instrument_id_str)
    price_increment_val = round(10 ** (-price_precision), price_precision)
    size_prec = get_size_precision(instrument_id_str)
    size_inc_val = round(10 ** (-size_prec), size_prec) if size_prec > 0 else 1.0

    return Equity(
        instrument_id=inst_id,
        raw_symbol=inst_id.symbol,
        currency=USD,
        price_precision=price_precision,
        price_increment=Price(price_increment_val, precision=price_precision),
        lot_size=Quantity(size_inc_val, precision=size_prec),
        ts_event=0,
        ts_init=0,
    )


# ---------------------------------------------------------------------------
# Metriken — Präzisions-FIFO-Matching für Netting Accounts via Fills Report
# ---------------------------------------------------------------------------

def extract_metrics(engine: BacktestEngine, starting_capital: float, log_fn=None) -> dict:
    """
    Extrahiert Tournament-Metriken.
    
    Korrektur: Nutzt trader.generate_fills_report() statt des fehlerhaften Cache-Zugriffs.
    Unterstützt robustes FIFO-Position-Matching über DataFrames.
    """
    NULL = {
        "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "sortino_ratio": 0.0, "calmar_ratio": 0.0,
        "max_drawdown": 0.0, "total_return": 0.0,
    }

    try:
        try:
            df_fills = engine.trader.generate_fills_report()
        except Exception:
            df_fills = pd.DataFrame()

        if df_fills.empty:
            try:
                df_fills = engine.trader.generate_order_fills_report()
            except Exception:
                df_fills = pd.DataFrame()

        if df_fills.empty:
            if log_fn:
                log_fn("[Metriken] Keine Fills oder ausgeführten Orders im Report dokumentiert.")
            return NULL

        # Fills nach Instrumenten gruppieren
        instrument_fills: dict[str, list] = {}
        for row in df_fills.itertuples():
            iid = str(getattr(row, 'instrument_id', ''))
            if not iid:
                continue
            instrument_fills.setdefault(iid, []).append(row)

        pnls: list[float] = []

        # Chronologisches FIFO-Matching pro Instrument
        for iid, f_list in instrument_fills.items():
            sorted_fills = sorted(f_list, key=lambda x: getattr(x, 'ts_event', getattr(x, 'ts_init', 0)))
            buy_queue: deque[tuple[float, float]] = deque()  # (Stückzahl, Preis)
            sell_queue: deque[tuple[float, float]] = deque() # (Stückzahl, Preis)

            for f in sorted_fills:
                try:
                    qty = float(getattr(f, 'last_qty', getattr(f, 'filled_qty', getattr(f, 'quantity', 0.0))))
                    price = float(getattr(f, 'last_px', getattr(f, 'avg_px', getattr(f, 'price', 0.0))))
                    side_str = str(getattr(f, 'order_side', getattr(f, 'side', ''))).upper()
                except Exception:
                    continue

                if qty <= 0:
                    continue

                is_buy = "BUY" in side_str

                if is_buy:
                    while qty > 0 and sell_queue:
                        s_qty, s_price = sell_queue[0]
                        match_qty = min(qty, s_qty)
                        pnl = match_qty * (s_price - price)
                        pnls.append(pnl)
                        qty -= match_qty
                        sell_queue[0] = (s_qty - match_qty, s_price)
                        if sell_queue[0][0] <= 1e-9:
                            sell_queue.popleft()
                    if qty > 0:
                        buy_queue.append((qty, price))
                else:  # SIDE IS SELL
                    while qty > 0 and buy_queue:
                        b_qty, b_price = buy_queue[0]
                        match_qty = min(qty, b_qty)
                        pnl = match_qty * (price - b_price)
                        pnls.append(pnl)
                        qty -= match_qty
                        buy_queue[0] = (b_qty - match_qty, b_price)
                        if buy_queue[0][0] <= 1e-9:
                            buy_queue.popleft()
                    if qty > 0:
                        sell_queue.append((qty, price))

        if not pnls:
            if log_fn:
                log_fn("[Metriken] Fills vorhanden, jedoch keine Trade-Schließungen (FIFO) generiert.")
            return NULL

        if log_fn:
            log_fn(f"[Metriken] FIFO-Extraktion: {len(pnls)} Trades erfolgreich berechnet.")

        n = len(pnls)
        wins = sum(1 for v in pnls if v > 0)
        gross_profit = sum(v for v in pnls if v > 0)
        gross_loss = abs(sum(v for v in pnls if v < 0))

        profit_factor = (
            (gross_profit / gross_loss) if gross_loss > 0
            else (999.0 if gross_profit > 0 else 0.0)
        )
        win_rate = wins / n

        rets = [v / starting_capital for v in pnls]
        cum = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in rets:
            cum *= (1.0 + r)
            peak = max(peak, cum)
            max_dd = max(max_dd, (peak - cum) / peak)

        total_return = cum - 1.0

        if n < 5:
            sortino = 0.0
        else:
            down_sq = [min(r, 0.0) ** 2 for r in rets]
            dd_dev = math.sqrt(sum(down_sq) / len(down_sq))
            mean_ret = sum(rets) / n
            sortino = (mean_ret / dd_dev * math.sqrt(252)) if dd_dev > 0 else 0.0

        calmar = (total_return / max_dd) if max_dd > 0 else 0.0

        return {
            "total_trades": n,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            "sortino_ratio": round(sortino, 4),
            "calmar_ratio": round(calmar, 4),
            "max_drawdown": round(max_dd, 4),
            "total_return": round(total_return, 4),
        }
    except Exception as e:
        if log_fn:
            log_fn(f"[Metriken-Fehler] FIFO-Verarbeitung fehlgeschlagen: {e}")
        return NULL


# ---------------------------------------------------------------------------
# Tournament-Logik
# ---------------------------------------------------------------------------

def select_winners(all_results: list[dict]) -> tuple[dict, dict | None]:
    eligible = [
        r for r in all_results
        if r["metrics"]["profit_factor"] > 1.5
        and r["metrics"]["total_trades"] >= 5
    ]
    per_symbol: dict[str, dict] = {}
    for r in eligible:
        sym = r["symbol"]
        curr = per_symbol.get(sym)
        if curr is None:
            per_symbol[sym] = r
        else:
            new_key = (r["metrics"]["sortino_ratio"], r["metrics"]["calmar_ratio"])
            if new_key > (curr["metrics"]["sortino_ratio"], curr["metrics"]["calmar_ratio"]):
                per_symbol[sym] = r

    per_symbol_winners = {
        sym: {"strategy": r["strategy"], "metrics": r["metrics"]}
        for sym, r in per_symbol.items()
    }

    win_counts: dict[str, int] = {}
    sortinos_by_strat: dict[str, list] = {}
    for r in per_symbol.values():
        s = r["strategy"]
        win_counts[s] = win_counts.get(s, 0) + 1
        sortinos_by_strat.setdefault(s, []).append(r["metrics"]["sortino_ratio"])

    aggregate_winner = None
    if win_counts:
        max_wins = max(win_counts.values())
        top = [s for s, w in win_counts.items() if w == max_wins]
        best = max(top, key=lambda s: sum(sortinos_by_strat[s]) / len(sortinos_by_strat[s]))
        aggregate_winner = {
            "strategy": best,
            "win_count": win_counts[best],
            "mean_sortino": round(
                sum(sortinos_by_strat[best]) / len(sortinos_by_strat[best]), 4
            ),
        }

    return per_symbol_winners, aggregate_winner


def write_tournament_json(
    all_results: list[dict],
    output_path: str,
    universe_snapshot: str = "",
) -> None:
    per_symbol_winners, aggregate_winner = select_winners(all_results)
    eligible_count = sum(
        1 for r in all_results
        if r["metrics"]["profit_factor"] > 1.5 and r["metrics"]["total_trades"] >= 5
    )
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_snapshot": universe_snapshot,
        "total_symbol_strategy_pairs": len(all_results),
        "eligible_pairs": eligible_count,
        "per_symbol_winners": per_symbol_winners,
        "aggregate_winner": aggregate_winner,
        "full_results": all_results,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n📄 Tournament-Ergebnisse gespeichert: {output_path}")


def print_tournament_table(
    all_results: list[dict],
    per_symbol_winners: dict,
) -> tuple[int, list[str]]:
    print(f"\n{'Symbol':<20} | {'Strategy':<30} | {'Sortino':>7} | {'Calmar':>7} | {'PF':>7} | {'Trades':>6} | Win?")
    print("-" * 95)
    winner_count = 0
    all_symbols: set[str] = set()
    winning_symbols: set[str] = set()

    for r in sorted(all_results, key=lambda x: (x["symbol"], x["strategy"])):
        sym, strat, m = r["symbol"], r["strategy"], r["metrics"]
        all_symbols.add(sym)
        is_winner = sym in per_symbol_winners and per_symbol_winners[sym]["strategy"] == strat
        if is_winner:
            winner_count += 1
            winning_symbols.add(sym)
        print(
            f"{sym:<20} | {strat:<30} | {m['sortino_ratio']:>7.2f} | "
            f"{m['calmar_ratio']:>7.2f} | {m['profit_factor']:>7.2f} | "
            f"{m['total_trades']:>6} | {'✓' if is_winner else ''}"
        )
    return winner_count, sorted(all_symbols - winning_symbols)


# ---------------------------------------------------------------------------
# Worker-Prozess
# ---------------------------------------------------------------------------

def run_single_backtest_worker(
    inst_id_str: str,
    bar_type: str,
    strat: dict,
    catalog_path: str,
    start_ns: int | None,
    end_ns: int | None,
    start_capital: float,
    generate_html_report: bool,
    reports_dir: str,
    worker_log_file: str,
) -> dict:
    """
    Isolierter Worker-Prozess (1 Instrument × 1 Strategie).
    """
    def wlog(msg: str) -> None:
        with open(worker_log_file, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    def wlog_err(msg: str, exc: bool = False) -> None:
        full = f"[ERROR] {msg}"
        if exc:
            full += f"\n{traceback.format_exc()}"
        wlog(full)

    strategy_class_name = strat["strategy_class"]
    module_name         = strat["strategy_module"]
    config_class_name   = strat["config_class"]

    wlog(f"\n🚀 {inst_id_str} | {strategy_class_name}")

    # --- Ticks laden ---
    try:
        catalog = ParquetDataCatalog(catalog_path)
        ticks = load_ticks_from_catalog(catalog, inst_id_str, start_ns, end_ns)
    except RuntimeError as e:
        wlog_err(f"Tick-Ladefehler: {e}", exc=True)
        return {}

    if not ticks:
        wlog(f"   ⚠️ 0 Ticks im Zeitraum — überspringe.")
        return {}

    wlog(f"   📥 {len(ticks)} Ticks geladen.")

    # --- Engine-Setup ---
    try:
        price_precision = infer_precision_from_ticks(ticks)
        engine_config = BacktestEngineConfig(
            trader_id=f"BT-{inst_id_str.replace('.', '_')}-{strategy_class_name}"
        )
        engine = BacktestEngine(config=engine_config)

        # Fix Bug 2: Zurückrollen auf NETTING-Kontoerstellung
        engine.add_venue(
            venue=Venue("ETORO"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            starting_balances=[Money(start_capital, USD)],
        )

        mock_inst = create_mock_instrument(inst_id_str, price_precision)
        engine.add_instrument(mock_inst)
        engine.add_data(ticks)

    except Exception as e:
        wlog_err(f"Engine-Setup fehlgeschlagen: {e}", exc=True)
        return {}

    # --- Strategie konfigurieren ---
    try:
        module    = importlib.import_module(module_name)
        StratCls  = getattr(module, strategy_class_name)
        ConfigCls = getattr(module, config_class_name)

        params = strat.get("params", {}).copy()
        params["instrument_id"] = inst_id_str
        params["bar_type"]      = bar_type

        # Fix Bug 1: trade_amount_usd anheben, falls von der Config-Klasse unterstützt
        try:
            test_params = params.copy()
            test_params["trade_amount_usd"] = 50000.0
            ConfigCls(**test_params)
            if params.get("trade_amount_usd", 0) <= 1000.0:
                params["trade_amount_usd"] = 50000.0
        except Exception:
            if "trade_amount_usd" in params and strat.get("params", {}).get("trade_amount_usd") is None:
                params.pop("trade_amount_usd", None)

        strategy_config = ConfigCls(**params)
        strategy        = StratCls(config=strategy_config)
        engine.add_strategy(strategy)

    except Exception as e:
        wlog_err(f"Strategie-Konfiguration fehlgeschlagen ({strategy_class_name}): {e}", exc=True)
        return {}

    # --- Backtest ausführen ---
    try:
        engine.run()
    except Exception as e:
        wlog_err(f"engine.run() fehlgeschlagen: {e}", exc=True)
        return {}

    # --- Offene Positionen prüfen ---
    try:
        open_pos = engine.cache.positions_open()
        if open_pos:
            wlog(f"   ⚠️ {len(open_pos)} Netting-Position am Ende noch aktiv.")
    except Exception:
        pass

    # --- Metriken extrahieren ---
    metrics = extract_metrics(engine, start_capital, log_fn=wlog)
    wlog(
        f"   📊 Trades={metrics['total_trades']} | "
        f"WinRate={metrics['win_rate']:.1%} | "
        f"PF={metrics['profit_factor']:.2f} | "
        f"Sortino={metrics['sortino_ratio']:.2f} | "
        f"MaxDD={metrics['max_drawdown']:.1%}"
    )

    # --- HTML-Report ---
    run_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if generate_html_report and metrics.get("profit_factor", 0.0) > 1.0:
        report_path = os.path.join(
            reports_dir,
            f"tearsheet_{inst_id_str}_{strategy_class_name}_{run_ts}.html"
        )
        try:
            create_tearsheet(engine=engine, output_path=report_path,
                             title=f"Tearsheet {inst_id_str} — {strategy_class_name}")
            wlog(f"   📈 Tearsheet: {report_path}")
        except Exception as e:
            wlog(f"   ⚠️ Tearsheet fehlgeschlagen ({e}), erstelle CSV...")
            try:
                for name, df in [
                    ("positions", engine.trader.generate_positions_report()),
                    ("fills",     engine.trader.generate_order_fills_report()),
                ]:
                    if not df.empty:
                        df.to_csv(os.path.join(
                            reports_dir,
                            f"{name}_{inst_id_str}_{strategy_class_name}_{run_ts}.csv"
                        ))
                wlog("   ✅ CSV gespeichert.")
            except Exception as fe:
                wlog_err(f"CSV-Fallback fehlgeschlagen: {fe}", exc=True)

    engine.dispose()
    return {"symbol": inst_id_str, "strategy": strategy_class_name, "metrics": metrics}


# ---------------------------------------------------------------------------
# Haupt-Einstiegspunkt
# ---------------------------------------------------------------------------

def run_backtest() -> None:
    parser = argparse.ArgumentParser(description="NautilusTrader Backtesting Engine")
    parser.add_argument("--momentum",     action="store_true")
    parser.add_argument("--htmlreport",   action="store_true")
    parser.add_argument("--catalog-path", type=str, default=None)
    parser.add_argument("--config",       type=str, default=None)
    parser.add_argument("--output",       type=str, default=None)
    args = parser.parse_args()

    _script_dir   = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_script_dir)
    logs_dir      = os.path.join(_project_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file  = os.path.join(logs_dir, f"backtest_{timestamp}.log")
    error_log_file = os.path.join(logs_dir, f"errors_{timestamp}.log")

    sys.stdout = DualLogger(log_file)
    sys.stderr = DualLogger(log_file)
    print(f"📝 Log-Datei: {log_file}")
    print(f"🚨 Fehler-Log: {error_log_file}")
    print("=" * 70)

    def log_error(msg: str, exc: bool = False) -> None:
        print(msg)
        with open(error_log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            if exc:
                f.write(traceback.format_exc() + "\n")

    # --- Config ---
    config_path = args.config or os.path.join(_script_dir, "backtesting_config.json")
    if not os.path.exists(config_path):
        log_error(f"❌ Config nicht gefunden: {config_path}")
        return

    config_data     = load_config(config_path)
    global_settings = config_data.get("global_settings", {})
    strategies_list = config_data.get("strategies", [])

    if not strategies_list:
        log_error("⚠️ Keine Strategien in Config gefunden.")
        return

    # --- Parameter-Validierung ---
    param_warnings: list[str] = []
    for strat in strategies_list:
        param_warnings.extend(validate_strategy_params(strat))
    if param_warnings:
        print("\n⚠️  Parameter-Warnungen:")
        for w in param_warnings:
            print(f"   • {w}")
        print()

    # --- Zeitraum ---
    start_time_str = global_settings.get("start_time")
    end_time_str   = global_settings.get("end_time")
    bt_start = pd.Timestamp(start_time_str, tz="UTC") if start_time_str else None
    bt_end   = pd.Timestamp(end_time_str,   tz="UTC") if end_time_str   else None

    start_ns = ts_to_ns(bt_start)
    end_ns   = ts_to_ns(bt_end)

    if bt_start and bt_end:
        print(f"📅 Zeitraum: {bt_start.date()} → {bt_end.date()}")

    start_capital = global_settings.get("start_capital", 100_000.0)
    catalog_path = args.catalog_path or global_settings.get("catalog_path", "./data/nautilus")

    expected_data_dir = os.path.join(catalog_path, "data")
    os.makedirs(expected_data_dir, exist_ok=True)

    # --- Instrumente ---
    instrument_ids = discover_instruments_from_catalog(catalog_path)
    if not instrument_ids:
        log_error(f"⚠️ Keine Instrumente in {expected_data_dir}/quote_tick vorhanden.")
        return
    print(f"📋 {len(instrument_ids)} Instrumente gefunden.")

    # --- Metadaten-Normalisierung ---
    print("\n🔍 Prüfe Parquet-Schema-Konsistenz...")
    patched = sum(
        1 for iid in instrument_ids
        if normalize_parquet_metadata(catalog_path, iid)
    )
    print(
        f"  ✅ {patched} Instrument(e) gepatcht."
        if patched else
        "  ✅ Alle Schemas konsistent."
    )

    # --- Mock-Instrumente registrieren ---
    catalog = ParquetDataCatalog(catalog_path)
    dummy_instruments = [create_mock_instrument(iid) for iid in instrument_ids]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            catalog.write_data(dummy_instruments)
        except Exception:
            pass

    dynamic_instruments = [
        {"id": iid, "bar_type": f"{iid}-1-MINUTE-MID-INTERNAL"}
        for iid in instrument_ids
    ]

    reports_dir = os.path.join(_project_root, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    tournament_output = args.output or os.path.join(
        _project_root, "logs", f"tournament_{datetime.now().strftime('%Y-%m-%d')}.json"
    )

    total_jobs = len(dynamic_instruments) * len(strategies_list)
    print(
        f"\n⏳ Starte {total_jobs} Backtest-Jobs "
        f"({len(dynamic_instruments)} × {len(strategies_list)} Strategien)..."
    )

    # --- Multiprocessing ---
    _use_mp = True
    _max_workers = max(1, min((os.cpu_count() or 1) // 2, 6))
    executor = None
    futures: dict = {}
    all_results: list[dict] = []

    try:
        if _use_mp:
            if sys.version_info >= (3, 11):
                executor = ProcessPoolExecutor(max_workers=_max_workers, max_tasks_per_child=1)
            else:
                executor = ProcessPoolExecutor(max_workers=_max_workers)

        for inst in dynamic_instruments:
            inst_id_str = inst["id"]
            bar_type    = inst["bar_type"]

            for strat in strategies_list:
                wlf = os.path.join(
                    logs_dir,
                    f"worker_{inst_id_str.replace('.', '_')}"
                    f"_{strat['strategy_class']}_{timestamp}.log"
                )
                _worker_log_files.append(wlf)

                if _use_mp and executor is not None:
                    future = executor.submit(
                        run_single_backtest_worker,
                        inst_id_str, bar_type, strat,
                        catalog_path, start_ns, end_ns,
                        start_capital, args.htmlreport, reports_dir, wlf,
                    )
                    futures[future] = (inst_id_str, strat["strategy_class"], wlf)
                else:
                    result = run_single_backtest_worker(
                        inst_id_str, bar_type, strat,
                        catalog_path, start_ns, end_ns,
                        start_capital, args.htmlreport, reports_dir, wlf,
                    )
                    _flush_worker_log(wlf)
                    if result and result.get("metrics"):
                        all_results.append(result)

        if _use_mp and executor is not None:
            done_count = 0
            for future in as_completed(futures):
                inst_id_str, strat_name, wlf = futures[future]
                done_count += 1
                _flush_worker_log(wlf)

                try:
                    result = future.result()
                    if result and result.get("metrics"):
                        all_results.append(result)
                except _BrokenPool:
                    log_error(
                        f"💥 Worker-Pool abgestürzt bei {inst_id_str}/{strat_name}. "
                        "Falle auf sequenziellen Modus zurück."
                    )
                    _use_mp = False
                    _run_remaining_sequentially(
                        futures, future, strategies_list, catalog_path,
                        start_ns, end_ns, start_capital, args.htmlreport,
                        reports_dir, all_results, done_count, total_jobs,
                    )
                    break
                except Exception as e:
                    log_error(f"❌ Worker {inst_id_str}/{strat_name}: {e}", exc=True)

                print(f"   [{done_count:>4}/{total_jobs}] {inst_id_str} / {strat_name}")

            executor.shutdown(wait=True)

    except KeyboardInterrupt:
        print("\n🛑 Backtest manuell abgebrochen. Fahre Subprozesse herunter...")
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        sys.exit(1)
    finally:
        _cleanup_worker_logs()

    # --- Tournament ---
    if args.momentum and all_results:
        per_symbol_winners, aggregate_winner = select_winners(all_results)
        winner_count, no_winner_symbols = print_tournament_table(all_results, per_symbol_winners)
        total_symbols = len(set(r["symbol"] for r in all_results))
        print(f"\n✅ Tournament: {total_symbols} Symbole | {winner_count} Gewinner")
        if aggregate_winner:
            print(
                f"🏆 {aggregate_winner['strategy']} — "
                f"{aggregate_winner['win_count']} Wins, "
                f"Ø Sortino: {aggregate_winner['mean_sortino']}"
            )
        if no_winner_symbols:
            print(f"⚠️  Ohne eindeutigen Gewinner: {', '.join(no_winner_symbols)}")
        write_tournament_json(all_results, tournament_output)
    elif all_results:
        print(f"\n📊 {len(all_results)} Ergebnisse gesammelt (kein --momentum Flag aktiv)")

    print("\n✅ Matrix-Backtest vollständig abgeschlossen!")


def _flush_worker_log(worker_log_file: str) -> None:
    if os.path.exists(worker_log_file):
        try:
            with open(worker_log_file, "r", encoding="utf-8") as wf:
                content = wf.read().strip()
            if content:
                print(content)
            os.remove(worker_log_file)
        except OSError:
            pass
    if worker_log_file in _worker_log_files:
        _worker_log_files.remove(worker_log_file)


def _run_remaining_sequentially(
    futures: dict,
    failed_future,
    strategies_list: list,
    catalog_path: str,
    start_ns: int | None,
    end_ns: int | None,
    start_capital: float,
    generate_html: bool,
    reports_dir: str,
    all_results: list,
    done_count: int,
    total_jobs: int,
) -> None:
    remaining = {
        f: v for f, v in futures.items()
        if not f.done() and f is not failed_future
    }
    for _, (rem_inst, rem_strat_name, rem_log) in remaining.items():
        rem_strat = next(
            (s for s in strategies_list if s["strategy_class"] == rem_strat_name), None
        )
        if rem_strat is None:
            continue
        bar_type = f"{rem_inst}-1-MINUTE-MID-INTERNAL"
        res = run_single_backtest_worker(
            rem_inst, bar_type, rem_strat, catalog_path,
            start_ns, end_ns, start_capital, generate_html, reports_dir, rem_log,
        )
        _flush_worker_log(rem_log)
        done_count += 1
        print(f"   [{done_count:>4}/{total_jobs}] (seq) {rem_inst} / {rem_strat_name}")
        if res and res.get("metrics"):
            all_results.append(res)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()