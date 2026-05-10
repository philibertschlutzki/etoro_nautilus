import os
import sys
import json
import importlib
from datetime import datetime
from typing import Dict, Any

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.identifiers import Venue, InstrumentId, Symbol
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.instruments import Equity

from nautilus_trader.analysis import TearsheetConfig, create_tearsheet

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
    # Logging Setup
    logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(logs_dir, f"backtest_{timestamp}.log")
    
    sys.stdout = DualLogger(log_file)
    sys.stderr = sys.stdout 
    print(f"📝 Logging aktiv. Ausgabe wird gespeichert in: {log_file}\n" + "="*60)

    # 1. Konfiguration einlesen
    config_path = os.path.join(os.path.dirname(__file__), "backtesting_config.json")
    if not os.path.exists(config_path):
        print(f"❌ Fehler: Konfigurationsdatei {config_path} nicht gefunden.")
        return

    config_data = load_config(config_path)
    global_settings = config_data.get("global_settings", {})
    strategies_list = config_data.get("strategies", [])

    if not strategies_list:
        print("⚠️ Keine Strategien in der Config definiert. Breche ab.")
        return

    # Robuste Pfadverwaltung
    catalog_path = global_settings.get("catalog_path", "./data/nautilus")
    if not os.path.exists(os.path.join(catalog_path, "quote_tick")) and os.path.exists(os.path.join(catalog_path, "nautilus_data", "quote_tick")):
        catalog_path = os.path.join(catalog_path, "nautilus_data")
        print(f"🔄 Pfad korrigiert: Nutze verschachteltes Verzeichnis {catalog_path}")

    if not os.path.exists(catalog_path):
        print(f"⚠️ Warnung: Datenverzeichnis {catalog_path} nicht gefunden.")
        return
    
    catalog = ParquetDataCatalog(catalog_path)

    # --- DYNAMISCHE INSTRUMENTEN-ERKENNUNG (JETZT VIA QUOTE_TICKS!) ---
    # Wir suchen jetzt im quote_tick Ordner, da wir wissen, dass dort die echten Daten liegen
    tick_dir = os.path.join(catalog_path, "quote_tick")
    dynamic_instruments = []
    
    if os.path.exists(tick_dir):
        for folder_name in os.listdir(tick_dir):
            if os.path.isdir(os.path.join(tick_dir, folder_name)):
                instrument_id_str = folder_name
                # Bar-Type dynamisch zusammenbauen für die Strategien
                bar_type = f"{instrument_id_str}-1-MINUTE-MID-INTERNAL"
                dynamic_instruments.append({
                    "id": instrument_id_str,
                    "bar_type": bar_type
                })

    if not dynamic_instruments:
        print(f"⚠️ Keine Tick-Daten im Verzeichnis {tick_dir} gefunden. Breche ab.")
        return

    print(f"✅ {len(dynamic_instruments)} Instrumente anhand von Tick-Daten gefunden.")

    start_capital = global_settings.get("start_capital", 10000.0)
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)

    # 2. MATRIX TESTING: Jedes Instrument x Jede Strategie
    for inst in dynamic_instruments:
        inst_id_str = inst["id"]
        bar_type = inst["bar_type"]

        for strat in strategies_list:
            module_name = strat["strategy_module"]
            strategy_class_name = strat["strategy_class"]
            config_class_name = strat["config_class"]

            print(f"\n🚀 Starte Backtest: Instrument {inst_id_str} | Strategie {strategy_class_name}")

            engine_config = BacktestEngineConfig(
                trader_id=f"Matrix-{inst_id_str}-{strategy_class_name}",
            )
            engine = BacktestEngine(config=engine_config)

            engine.add_venue(
                venue=Venue("ETORO"),
                oms_type=OmsType.HEDGING,
                account_type=AccountType.MARGIN,
                base_currency=USD,
                starting_balances=[Money(start_capital, USD)]
            )

            # Instrument erzwingen
            inst_id = InstrumentId.from_str(inst_id_str)
            dummy_inst = create_mock_instrument(inst_id_str)
            engine.add_instrument(dummy_inst)

            # --- ON-THE-FLY AGGREGATION: TICKS LADEN ---
            try:
                # Wir laden die QuoteTicks. Nautilus aggregiert daraus automatisch die MID-Bars!
                ticks = catalog.quote_ticks(instrument_ids=[inst_id])
                if ticks and len(ticks) > 0:
                    engine.add_data(ticks)
                    print(f"   ✅ {len(ticks)} rohe Ticks geladen. Bars werden live berechnet!")
                else:
                    print(f"   ⚠️ Keine Ticks im Katalog fuer {inst_id_str}. Ueberspringe Kombination.")
                    continue
            except ValueError as e:
                print(f"   ⚠️ Fehler beim Laden von Ticks fuer {inst_id_str}: {e}. Ueberspringe.")
                continue

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
                print(f"   ❌ Fehler beim Laden/Konfigurieren von {strategy_class_name}: {e}")
                continue

            # --- ENGINE STARTEN ---
            try:
                engine.run()
                results = engine.get_backtest_results()

                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                report_filename = os.path.join(reports_dir, f"tearsheet_{inst_id_str}_{strategy_class_name}_{timestamp}.html")

                ts_config = TearsheetConfig(
                    title=f"Backtest: {inst_id_str} | {strategy_class_name}",
                    output_path=report_filename,
                    include_equity=True,
                    include_drawdown=True,
                    include_returns=True,
                    include_daily_returns=True,
                    include_positions=True,
                )

                create_tearsheet(results=results, config=ts_config)
                print(f"   📈 Tearsheet erfolgreich gespeichert: {report_filename}")

            except Exception as e:
                print(f"   ⚠️ Warnung: Backtest/Tearsheet-Generierung fuer {inst_id_str} fehlgeschlagen: {e}")
                continue

    print("\n✅ Matrix-Backtest vollstaendig abgeschlossen!")

if __name__ == "__main__":
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()