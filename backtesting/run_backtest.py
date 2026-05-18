import os
import sys
import json
import shutil
import importlib
from datetime import datetime
from typing import Dict, Any
import traceback

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.identifiers import Venue, InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import Equity
from nautilus_trader.analysis.tearsheet import create_tearsheet
from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler

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

def create_mock_instrument(instrument_id_str: str) -> Equity:
    """Generiert ein dynamisches Mock-Instrument."""
    inst_id = InstrumentId.from_str(instrument_id_str)
    return Equity(
        instrument_id=inst_id,
        raw_symbol=inst_id.symbol,
        currency=USD,
        price_precision=5,
        price_increment=Price(1e-5, precision=5),
        lot_size=Quantity(1, precision=0),
        ts_event=0,
        ts_init=0,
    )

def run_backtest():
    # 1. Logging Setup
    logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(logs_dir, f"backtest_{timestamp}.log")
    error_log_file = os.path.join(logs_dir, f"errors_{timestamp}.log")
    
    sys.stdout = DualLogger(log_file)
    sys.stderr = DualLogger(log_file)
    print(f"📝 Logging aktiv. Ausgabe wird gespeichert in: {log_file}")
    print(f"🚨 Fehler-Log wird separat geschrieben in: {error_log_file}\n" + "="*60)

    def log_error(msg, exc_info=False):
        """Schreibt Fehler in die Konsole und in das dedizierte Error-Log."""
        print(msg)
        with open(error_log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            if exc_info:
                f.write(traceback.format_exc() + "\n")

    # 2. Config einlesen
    config_path = os.path.join(os.path.dirname(__file__), "backtesting_config.json")
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

    # 3. ROBUSTE PFADVERWALTUNG & AUTO-FIX FÜR NAUTILUS
    catalog_path = global_settings.get("catalog_path", "./data/nautilus")
    if not os.path.exists(os.path.join(catalog_path, "quote_tick")) and os.path.exists(os.path.join(catalog_path, "nautilus_data", "quote_tick")):
        catalog_path = os.path.join(catalog_path, "nautilus_data")

    # Nautilus erwartet zwingend einen 'data' Ordner IN catalog_path
    expected_data_dir = os.path.join(catalog_path, "data")
    os.makedirs(expected_data_dir, exist_ok=True)

    # Verschiebe Ordner, falls sie am falschen Ort entpackt wurden
    for folder in ["quote_tick", "bar"]:
        wrong_source = os.path.join(catalog_path, folder)
        correct_target = os.path.join(expected_data_dir, folder)
        if os.path.exists(wrong_source) and not os.path.exists(correct_target):
            print(f"📂 Optimiere Ordnerstruktur: Verschiebe {folder} -> data/{folder}")
            shutil.move(wrong_source, correct_target)

    # 4. INSTRUMENTE SUCHEN & REGISTRIEREN
    tick_dir = os.path.join(expected_data_dir, "quote_tick")

    dynamic_instruments = []
    if os.path.exists(tick_dir):
        for folder_name in os.listdir(tick_dir):
            if os.path.isdir(os.path.join(tick_dir, folder_name)):
                # Extrahiere die ID aus dem Hive-Partitioning (instrument_id=...)
                clean_id = folder_name.replace("instrument_id=", "")
                dynamic_instruments.append({
                    "id": clean_id,
                    "bar_type": f"{clean_id}-1-MINUTE-MID-INTERNAL"
                })

    if not dynamic_instruments:
        log_error(f"⚠️ Keine Tick-Daten im Verzeichnis {tick_dir} gefunden. Breche ab.")
        return

    print(f"✅ {len(dynamic_instruments)} Instrumente gefunden. Registriere im Katalog...")

    # Katalog instanziieren
    catalog = ParquetDataCatalog(catalog_path)
    
    # Mock-Instrumente gebündelt schreiben
    dummy_instruments = [create_mock_instrument(inst["id"]) for inst in dynamic_instruments]
    try:
        catalog.write_data(dummy_instruments)
    except Exception:
        pass # Falls Datei schon existiert, einfach ignorieren

    # WICHTIG: Katalog komplett neu laden, damit die frisch geschriebenen Instrumente im RAM sind!
    catalog = ParquetDataCatalog(catalog_path)

    start_capital = global_settings.get("start_capital", 100000.0)
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)

    # 5. MATRIX TESTING STARTEN
    for inst in dynamic_instruments:
        inst_id_str = inst["id"]
        bar_type = inst["bar_type"]

        for strat in strategies_list:
            module_name = strat["strategy_module"]
            strategy_class_name = strat["strategy_class"]
            config_class_name = strat["config_class"]

            print(f"\n🚀 Starte Backtest: Instrument {inst_id_str} | Strategie {strategy_class_name}")

            engine_config = BacktestEngineConfig(
                trader_id=f"Matrix-{inst_id_str.replace('.', '_')}-{strategy_class_name}",
            )
            engine = BacktestEngine(config=engine_config)

            engine.add_venue(
                venue=Venue("ETORO"),
                oms_type=OmsType.HEDGING,
                account_type=AccountType.MARGIN,
                base_currency=USD,
                starting_balances=[Money(start_capital, USD)]
            )

            inst_id = InstrumentId.from_str(inst_id_str)
            mock_inst = create_mock_instrument(inst_id_str)
            engine.add_instrument(mock_inst)

            # --- ECHTE DATEN LADEN DIREKT VIA PANDAS (BYPASS CATALOG) ---
            try:
                inst_dir = os.path.join(tick_dir, f"instrument_id={inst_id_str}")
                df_list = []
                if os.path.exists(inst_dir):
                    for file in os.listdir(inst_dir):
                        if file.endswith(".parquet"):
                            df_list.append(pd.read_parquet(os.path.join(inst_dir, file)))

                if not df_list:
                    print(f"   ⚠️ Keine Parquet-Dateien für {inst_id_str} gefunden.")
                    continue

                df_ticks = pd.concat(df_list, ignore_index=True)

                # 1. Zeitraum filtern (ts_event in Nanosekunden)
                start_ns = int(bt_start.timestamp() * 1e9) if bt_start else 0
                end_ns   = int(bt_end.timestamp() * 1e9)   if bt_end   else 9999999999999999999
                df_ticks = df_ticks[(df_ticks['ts_event'] >= start_ns) & (df_ticks['ts_event'] <= end_ns)]

                if df_ticks.empty:
                    print(f"   ⚠️ 0 Ticks für {inst_id_str} im gewählten Zeitraum. Ueberspringe.")
                    continue

                # 2. Daten für Nautilus formatieren
                df_ticks['instrument_id'] = inst_id_str 
                
                # BUGFIX: Nautilus benötigt zwingend einen DatetimeIndex inkl. Zeitzone (tzinfo)!
                df_ticks.index = pd.to_datetime(df_ticks['ts_event'], unit='ns', utc=True)
                
                wrangler = QuoteTickDataWrangler(mock_inst)
                ticks = wrangler.process(df_ticks)

                engine.add_data(ticks)
                print(f"   ✅ {len(ticks)} Ticks direkt via Pandas geladen und in die Engine injiziert!")

            except Exception as e:
                log_error(f"   ❌ Fehler beim manuellen Laden der Ticks fuer {inst_id_str}: {e}. Ueberspringe.", exc_info=True)
                continue

            # --- STRATEGIE INIT ---
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
                log_error(f"   ❌ Fehler beim Konfigurieren von {strategy_class_name}: {e}", exc_info=True)
                continue

            # --- ENGINE STARTEN ---
            try:
                engine.run()
            except Exception as e:
                log_error(f"   ❌ engine.run() fehlgeschlagen fuer {inst_id_str} / {strategy_class_name}: {e}", exc_info=True)
                continue

            # --- REPORTS ERSTELLEN ---
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            report_filename = os.path.join(reports_dir, f"tearsheet_{inst_id_str}_{strategy_class_name}_{timestamp}.html")

            try:
                create_tearsheet(
                    engine=engine,
                    output_path=report_filename,
                    title=f"Tearsheet {inst_id_str} - {strategy_class_name}"
                )
                print(f"   📈 Tearsheet erfolgreich gespeichert: {report_filename}")
            except Exception as e:
                log_error(f"   ⚠️ HTML-Tearsheet fehlgeschlagen: {e}. Erstelle CSV-Fallback...", exc_info=False)
                try:
                    positions_df = engine.trader.generate_positions_report()
                    fills_df = engine.trader.generate_order_fills_report()
                    account_df = engine.trader.generate_account_report(venue=Venue("ETORO"))

                    if not positions_df.empty:
                        positions_df.to_csv(os.path.join(reports_dir, f"positions_{inst_id_str}_{strategy_class_name}_{timestamp}.csv"))
                    if not fills_df.empty:
                        fills_df.to_csv(os.path.join(reports_dir, f"fills_{inst_id_str}_{strategy_class_name}_{timestamp}.csv"))
                    if not account_df.empty:
                        account_df.to_csv(os.path.join(reports_dir, f"account_{inst_id_str}_{strategy_class_name}_{timestamp}.csv"))
                    print(f"   ✅ CSV-Fallbacks gespeichert.")
                except Exception as fallback_e:
                    log_error(f"   ❌ CSV-Fallback ebenfalls fehlgeschlagen: {fallback_e}", exc_info=True)

    print("\n✅ Matrix-Backtest vollstaendig abgeschlossen!")

if __name__ == "__main__":
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()