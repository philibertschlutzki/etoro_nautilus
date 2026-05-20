import os
import sys
import json
import math
import shutil
import argparse
import importlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any
import traceback
import contextlib
import io
import multiprocessing
import atexit
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool as _BrokenPool

import pandas as pd

# Add the project root to sys.path so that 'adapters' can be imported correctly
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

_worker_log_files: list[str] = []


def _cleanup_worker_logs():
    """Wird bei normalem Exit und bei Crash aufgerufen."""
    for path in _worker_log_files:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


atexit.register(_cleanup_worker_logs)


class DualLogger:
    """Fängt Konsolen-Outputs ab und schreibt sie ins Terminal UND in eine Datei."""
    def __init__(self, filepath: str):
        self.terminal = sys.stdout
        self.log = open(filepath, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        self.terminal.flush()
        self.log.flush()


def load_config(filepath: str) -> Dict[str, Any]:
    with open(filepath, 'r') as f:
        return json.load(f)


def discover_instruments_from_catalog(catalog_path: str) -> list[str]:
    """Sucht verfügbare Instrument-IDs direkt aus dem Verzeichnisbaum des Katalogs."""
    tick_dir = os.path.join(catalog_path, "data", "quote_tick")
    instruments = []
    if os.path.exists(tick_dir):
        for entry in os.listdir(tick_dir):
            full = os.path.join(tick_dir, entry)
            if os.path.isdir(full) and not entry.startswith('.'):
                clean = entry.replace("instrument_id=", "")
                if '.' in clean or clean.endswith('.ETORO'):
                    instruments.append(clean)
    return instruments


def load_ticks_from_catalog(
    catalog: ParquetDataCatalog,
    instrument_id_str: str,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> list:
    """Lädt QuoteTick-Objekte via ParquetDataCatalog."""
    try:
        ticks = catalog.quote_ticks(
            instrument_ids=[instrument_id_str],
            start=start,
            end=end,
        )
        return ticks if ticks else []
    except Exception as e:
        print(f"   ⚠️ Katalog-Ladefehler für {instrument_id_str}: {e}")
        return []


def infer_precision_from_ticks(ticks: list) -> int:
    if not ticks:
        return 5
    precisions = []
    for t in ticks[:10]:
        if hasattr(t.bid_price, 'precision'):
            precisions.append(t.bid_price.precision)
    return int(max(precisions)) if precisions else 5


def create_mock_instrument(instrument_id_str: str, price_precision: int = 5) -> Equity:
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


def extract_metrics(engine: BacktestEngine, starting_capital: float, log_fn=None) -> dict:
    """Extrahiert Tournament-Metriken aus geschlossenen Positionen."""
    NULL = {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "sortino_ratio": 0.0, "calmar_ratio": 0.0,
            "max_drawdown": 0.0, "total_return": 0.0}

    pnls: list[float] = []

    try:
        all_positions = engine.cache.positions()
        closed = [p for p in all_positions if not p.is_open]

        if not closed:
            if log_fn:
                log_fn("[Backtest] Keine abgeschlossenen Positionen für Metriken-Extraktion")
            return NULL

        for pos in closed:
            try:
                pnls.append(float(pos.realized_pnl.as_decimal()))
            except AttributeError:
                try:
                    pnls.append(float(pos.realized_pnl))
                except (TypeError, ValueError):
                    pass
    except Exception:
        return NULL

    if not pnls:
        return NULL

    n = len(pnls)
    wins = sum(1 for v in pnls if v > 0)
    gross_profit = sum(v for v in pnls if v > 0)
    gross_loss = abs(sum(v for v in pnls if v < 0))

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 \
                    else (999.0 if gross_profit > 0 else 0.0)
    win_rate = wins / n if n > 0 else 0.0

    rets = [v / starting_capital for v in pnls]
    cum = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        cum *= (1.0 + r)
        if cum > peak:
            peak = cum
        dd = (peak - cum) / peak
        if dd > max_dd:
            max_dd = dd

    total_return = cum - 1.0

    # OPTIMIERUNG: Behebt das Sortino-Artefakt bei unzureichender Trade-Anzahl
    if n < 5:
        sortino = 0.0
    else:
        down_sq = [min(r, 0.0) ** 2 for r in rets]
        dd_dev = math.sqrt(sum(down_sq) / len(down_sq))
        mean_ret = sum(rets) / len(rets)
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
            cur_key = (curr["metrics"]["sortino_ratio"], curr["metrics"]["calmar_ratio"])
            if new_key > cur_key:
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
            )
        }

    return per_symbol_winners, aggregate_winner


def write_tournament_json(all_results: list[dict], output_path: str, universe_snapshot: str = "") -> None:
    per_symbol_winners, aggregate_winner = select_winners(all_results)
    eligible_count = sum(1 for r in all_results if r["metrics"]["profit_factor"] > 1.5 and r["metrics"]["total_trades"] >= 5)

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
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n📄 Tournament-Ergebnisse gespeichert: {output_path}")


def print_tournament_table(all_results: list[dict], per_symbol_winners: dict) -> tuple[int, list[str]]:
    print(f"\n{'Symbol':<20} | {'Strategy':<30} | {'Sortino':>7} | {'Calmar':>7} | {'PF':>7} | {'Trades':>6} | Win?")
    print("-" * 95)

    winner_count = 0
    all_symbols = set()
    winning_symbols = set()

    for r in all_results:
        sym = r["symbol"]
        strat = r["strategy"]
        m = r["metrics"]
        all_symbols.add(sym)

        is_winner = (sym in per_symbol_winners and per_symbol_winners[sym]["strategy"] == strat)
        if is_winner:
            winner_count += 1
            winning_symbols.add(sym)

        win_mark = "✓" if is_winner else ""
        print(f"{sym:<20} | {strat:<30} | {m['sortino_ratio']:>7.2f} | {m['calmar_ratio']:>7.2f} | {m['profit_factor']:>7.2f} | {m['total_trades']:>6} | {win_mark}")

    return winner_count, sorted(all_symbols - winning_symbols)


def run_single_backtest_worker(
    inst_id_str: str,
    bar_type: str,
    strat: dict,
    catalog_path: str,       # OPTIMIERUNG: Übergabe des Pfads statt Objektliste zur Eliminierung des IPC-Overheads
    bt_start: pd.Timestamp | None,
    bt_end: pd.Timestamp | None,
    start_capital: float,
    generate_html_report: bool,
    reports_dir: str,
    worker_log_file: str,
) -> dict:
    """Isolierter Worker-Prozess: Instanziiert den Katalog lokal und führt das Backtesting aus."""
    def worker_log(msg):
        with open(worker_log_file, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    def worker_log_error(msg, exc_info=False):
        full_msg = f"ERROR: {msg}"
        if exc_info:
            full_msg += f"\n{traceback.format_exc()}"
        worker_log(full_msg)

    strategy_class_name = strat["strategy_class"]
    module_name = strat["strategy_module"]
    config_class_name = strat["config_class"]

    worker_log(f"\n🚀 Starte Backtest: Instrument {inst_id_str} | Strategie {strategy_class_name}")

    try:
        # Lokale Instanziierung im Worker verhindert IPC-Flaschenhälse
        catalog = ParquetDataCatalog(catalog_path)
        ticks = load_ticks_from_catalog(catalog, inst_id_str, bt_start, bt_end)

        if not ticks:
            worker_log(f"   ⚠️ 0 Ticks für {inst_id_str} im gewählten Zeitraum gefunden.")
            return {}

        price_precision = infer_precision_from_ticks(ticks)
        engine_config = BacktestEngineConfig(trader_id=f"Matrix-{inst_id_str.replace('.', '_')}-{strategy_class_name}")
        engine = BacktestEngine(config=engine_config)

        engine.add_venue(
            venue=Venue("ETORO"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            starting_balances=[Money(start_capital, USD)]
        )

        mock_inst = create_mock_instrument(inst_id_str, price_precision)
        engine.add_instrument(mock_inst)
        engine.add_data(ticks)
    except Exception as e:
        worker_log_error(f"   ❌ Fehler beim Setup der Engine für {inst_id_str}: {e}.", exc_info=True)
        return {}

    try:
        module = importlib.import_module(module_name)
        StrategyClass = getattr(module, strategy_class_name)
        ConfigClass = getattr(module, config_class_name)

        params = strat.get("params", {}).copy()
        params["instrument_id"] = inst_id_str
        params["bar_type"] = bar_type

        strategy_config = ConfigClass(**params)
        strategy = StrategyClass(config=strategy_config)
        engine.add_strategy(strategy)
    except Exception as e:
        worker_log_error(f"   ❌ Fehler beim Konfigurieren von {strategy_class_name}: {e}", exc_info=True)
        return {}

    try:
        engine.run()
    except Exception as e:
        worker_log_error(f"   ❌ engine.run() fehlgeschlagen: {e}", exc_info=True)
        return {}

    # OPTIMIERUNG: Behebt das Brittle Spalten-Parsing durch nativer API-Zugriff auf den Cache
    try:
        open_positions = engine.cache.positions_open()
        if open_positions:
            worker_log(f"   ⚠️ {len(open_positions)} Positionen nach Backtest-Ende offen — unrealized PnL nicht enthalten")
    except Exception as e:
        worker_log(f"   ⚠️ Konnte offene Positionen nicht prüfen: {e}")

    metrics = extract_metrics(engine, start_capital, log_fn=worker_log)
    worker_log(f"   📊 Metriken: Trades={metrics['total_trades']}, WinRate={metrics['win_rate']:.2%}, PF={metrics['profit_factor']:.2f}, Sortino={metrics['sortino_ratio']:.2f}")

    run_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if generate_html_report and metrics.get('profit_factor', 0.0) > 1.0:
        report_filename = os.path.join(reports_dir, f"tearsheet_{inst_id_str}_{strategy_class_name}_{run_timestamp}.html")
        try:
            create_tearsheet(engine=engine, output_path=report_filename, title=f"Tearsheet {inst_id_str} - {strategy_class_name}")
            worker_log(f"   📈 Tearsheet erfolgreich gespeichert: {report_filename}")
        except Exception as e:
            worker_log_error(f"   ⚠️ Tearsheet fehlgeschlagen: {e}. Erstelle CSV-Fallback...", exc_info=False)
            try:
                positions_df = engine.trader.generate_positions_report()
                fills_df = engine.trader.generate_order_fills_report()
                if not positions_df.empty:
                    positions_df.to_csv(os.path.join(reports_dir, f"positions_{inst_id_str}_{strategy_class_name}_{run_timestamp}.csv"))
                if not fills_df.empty:
                    fills_df.to_csv(os.path.join(reports_dir, f"fills_{inst_id_str}_{strategy_class_name}_{run_timestamp}.csv"))
                worker_log("   ✅ CSV-Fallbacks gespeichert.")
            except Exception as fallback_e:
                worker_log_error(f"   ❌ CSV-Fallback ebenfalls fehlgeschlagen: {fallback_e}", exc_info=True)

    engine.dispose()
    return {"symbol": inst_id_str, "strategy": strategy_class_name, "metrics": metrics}


def run_backtest():
    parser = argparse.ArgumentParser(description="NautilusTrader Backtesting Engine")
    parser.add_argument("--momentum", action="store_true", help="Tournament mode")
    parser.add_argument("--htmlreport", action="store_true", help="Generate HTML tearsheets")
    parser.add_argument("--catalog-path", type=str, default=None)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_script_dir)
    logs_dir = os.path.join(_project_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(logs_dir, f"backtest_{timestamp}.log")
    error_log_file = os.path.join(logs_dir, f"errors_{timestamp}.log")

    sys.stdout = DualLogger(log_file)
    sys.stderr = DualLogger(log_file)
    print(f"📝 Logging aktiv. Ausgabe wird gespeichert in: {log_file}")
    print(f"🚨 Fehler-Log wird separat geschrieben in: {error_log_file}\n" + "=" * 60)

    def log_error(msg, exc_info=False):
        print(msg)
        with open(error_log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            if exc_info:
                f.write(traceback.format_exc() + "\n")

    if args.config:
        config_path = args.config
    else:
        config_path = os.path.join(_script_dir, "backtesting_config.json")

    if not os.path.exists(config_path):
        log_error(f"❌ Fehler: Config {config_path} nicht gefunden.")
        return

    config_data = load_config(config_path)
    global_settings = config_data.get("global_settings", {})
    strategies_list = config_data.get("strategies", [])

    start_time_str = global_settings.get("start_time")
    end_time_str = global_settings.get("end_time")
    bt_start = pd.Timestamp(start_time_str, tz="UTC") if start_time_str else None
    bt_end   = pd.Timestamp(end_time_str,   tz="UTC") if end_time_str   else None

    if not strategies_list:
        log_error("⚠️ Keine Strategien in Config definiert. Breche ab.")
        return

    if args.catalog_path:
        catalog_path = args.catalog_path
    else:
        catalog_path = global_settings.get("catalog_path", "./data/nautilus")

    if not os.path.exists(os.path.join(catalog_path, "quote_tick")) and \
       os.path.exists(os.path.join(catalog_path, "nautilus_data", "quote_tick")):
        catalog_path = os.path.join(catalog_path, "nautilus_data")

    expected_data_dir = os.path.join(catalog_path, "data")
    os.makedirs(expected_data_dir, exist_ok=True)

    for folder in ["quote_tick", "bar"]:
        wrong_source = os.path.join(catalog_path, folder)
        correct_target = os.path.join(expected_data_dir, folder)
        if os.path.exists(wrong_source) and not os.path.exists(correct_target):
            print(f"📂 Optimiere Ordnerstruktur: Verschiebe {folder} -> data/{folder}")
            shutil.move(wrong_source, correct_target)

    instrument_ids = discover_instruments_from_catalog(catalog_path)
    dynamic_instruments = [{"id": iid, "bar_type": f"{iid}-1-MINUTE-MID-INTERNAL"} for iid in instrument_ids]

    if not dynamic_instruments:
        log_error(f"⚠️ Keine Tick-Daten im Verzeichnis {expected_data_dir}/quote_tick gefunden.")
        return

    print(f"¼ {len(dynamic_instruments)} Instrumente gefunden. Registriere im Katalog...")

    catalog = ParquetDataCatalog(catalog_path)
    dummy_instruments = [create_mock_instrument(inst["id"]) for inst in dynamic_instruments]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            catalog.write_data(dummy_instruments)
        except Exception:
            pass

    start_capital = global_settings.get("start_capital", 100000.0)
    reports_dir = os.path.join(_project_root, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    tournament_output = args.output if args.output else os.path.join(_project_root, "logs", f"tournament_{date_str}.json")

    all_results = []
    executor = None
    _use_multiprocessing = True
    futures = {}

    try:
        if _use_multiprocessing:
            _max_workers = max(1, min((os.cpu_count() or 1) // 2, 6))
            if sys.version_info >= (3, 11):
                executor = ProcessPoolExecutor(max_workers=_max_workers, max_tasks_per_child=1)
            else:
                executor = ProcessPoolExecutor(max_workers=_max_workers)

        for inst in dynamic_instruments:
            inst_id_str = inst["id"]
            bar_type = inst["bar_type"]

            for strat in strategies_list:
                worker_log_file = os.path.join(logs_dir, f"worker_{inst_id_str.replace('.', '_')}_{strat['strategy_class']}_{timestamp}.log")
                _worker_log_files.append(worker_log_file)

                if _use_multiprocessing and executor is not None:
                    future = executor.submit(
                        run_single_backtest_worker,
                        inst_id_str,
                        bar_type,
                        strat,
                        catalog_path, # Übergabe des Verzeichnis-Pfads statt der geladenen Ticks
                        bt_start,
                        bt_end,
                        start_capital,
                        args.htmlreport,
                        reports_dir,
                        worker_log_file,
                    )
                    futures[future] = (inst_id_str, strat["strategy_class"], worker_log_file)
                else:
                    result = run_single_backtest_worker(
                        inst_id_str, bar_type, strat, catalog_path, bt_start, bt_end,
                        start_capital, args.htmlreport, reports_dir, worker_log_file,
                    )
                    if os.path.exists(worker_log_file):
                        with open(worker_log_file, "r", encoding="utf-8") as wf:
                            content = wf.read().strip()
                            if content:
                                print(content)
                        try:
                            os.remove(worker_log_file)
                        except OSError:
                            pass
                    if result and result.get("metrics"):
                        all_results.append(result)

        if _use_multiprocessing and executor is not None:
            total = len(futures)
            done_count = 0
            print(f"\n⏳ {total} Backtest-Jobs gestartet. Warte auf Ergebnisse...")

            for future in as_completed(futures):
                inst_id_str, strategy_class_name, worker_log_file = futures[future]
                done_count += 1

                if os.path.exists(worker_log_file):
                    with open(worker_log_file, "r", encoding="utf-8") as wf:
                        content = wf.read().strip()
                        if content:
                            print(content)
                    try:
                        os.remove(worker_log_file)
                    except OSError:
                        pass

                try:
                    result = future.result()
                    if result and result.get("metrics"):
                        all_results.append(result)
                except _BrokenPool:
                    log_error(f"   💥 Worker-Pool gecrasht (OOM/SIGKILL) bei {inst_id_str}/{strategy_class_name}. Wechsle zu sequenziellem Fallback.", exc_info=False)
                    _use_multiprocessing = False
                    remaining = {f: v for f, v in futures.items() if not f.done() and f is not future}
                    for rem_future, (rem_inst, rem_strat_name, rem_log) in remaining.items():
                        rem_strat = next((s for s in strategies_list if s["strategy_class"] == rem_strat_name), None)
                        if rem_strat is None:
                            continue
                        res = run_single_backtest_worker(
                            rem_inst, f"{rem_inst}-1-MINUTE-MID-INTERNAL", rem_strat,
                            catalog_path, bt_start, bt_end, start_capital, args.htmlreport, reports_dir, rem_log,
                        )
                        if rem_log in _worker_log_files:
                            _worker_log_files.remove(rem_log)
                        if os.path.exists(rem_log):
                            with open(rem_log, "r", encoding="utf-8") as wf:
                                log_content = wf.read().strip()
                                if log_content:
                                    print(log_content)
                            try:
                                os.remove(rem_log)
                            except OSError:
                                pass
                        done_count += 1
                        print(f"   [{done_count}/{total}] Abgeschlossen (sequenziell): {rem_inst} / {rem_strat_name}")
                        if res and res.get("metrics"):
                            all_results.append(res)
                    break
                except Exception as e:
                    log_error(f"   ❌ Worker-Prozess für {inst_id_str}/{strategy_class_name} fehlgeschlagen: {e}", exc_info=True)

                print(f"   [{done_count}/{total}] Abgeschlossen: {inst_id_str} / {strategy_class_name}")

            executor.shutdown(wait=True)

    finally:
        _cleanup_worker_logs()
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)

    if args.momentum and all_results:
        per_symbol_winners, aggregate_winner = select_winners(all_results)
        winner_count, no_winner_symbols = print_tournament_table(all_results, per_symbol_winners)
        total_symbols = len(set(r["symbol"] for r in all_results))
        print(f"\n✅ Tournament abgeschlossen: {total_symbols} Symbole, {winner_count} Gewinner")

        if aggregate_winner:
            print(f"🏆 Aggregat-Sieger: {aggregate_winner['strategy']} ({aggregate_winner['win_count']} Wins, Ø Sortino: {aggregate_winner['mean_sortino']})")
        if no_winner_symbols:
            print(f"⚠️  Keine qualifizierte Strategie: {', '.join(no_winner_symbols)}")
            for sym in no_winner_symbols:
                log_error(f"⚠️ {sym}: Keine qualifizierte Strategie — wird nicht gehandelt")
        write_tournament_json(all_results, tournament_output)

    print("\n✅ Matrix-Backtest vollständig abgeschlossen!")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()