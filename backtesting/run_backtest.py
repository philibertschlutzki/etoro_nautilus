import os
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

# Korrekter Import fuer das Tearsheet gemaess Manual
from nautilus_trader.analysis import TearsheetConfig
from nautilus_trader.analysis.visualisation import Tearsheet


def load_config(filepath: str) -> Dict[str, Any]:
    with open(filepath, 'r') as f:
        return json.load(f)


def create_mock_instrument(instrument_id_str: str) -> Equity:
    """Generiert ein dynamisches Mock-Instrument, falls Metadaten fehlen."""
    inst_id = InstrumentId.from_str(instrument_id_str)
    return Equity(
        instrument_id=inst_id,
        raw_symbol=Symbol(inst_id.symbol.value),
        venue=inst_id.venue,
        base_currency=USD,
        quote_currency=USD,
        price_precision=5,
        price_increment=Price(1e-5, precision=5),
        lot_size=Quantity(1, precision=0),
        multiplier=Quantity(1, precision=0),
        ts_event=0,
        ts_init=0,
    )


def run_backtest():
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

    # --- ROBUSTE PFADVERWALTUNG ---
    catalog_path = global_settings.get("catalog_path", "./data/nautilus")
    
    # Fallback: Pruefen, ob die Daten eine Ebene tiefer im Ordner 'nautilus_data' liegen (z.B. nach SCP-Download)
    if not os.path.exists(os.path.join(catalog_path, "bar")) and os.path.exists(os.path.join(catalog_path, "nautilus_data", "bar")):
        catalog_path = os.path.join(catalog_path, "nautilus_data")
        print(f"🔄 Pfad korrigiert: Nutze verschachteltes Verzeichnis {catalog_path}")

    if not os.path.exists(catalog_path):
        print(f"⚠️ Warnung: Datenverzeichnis {catalog_path} nicht gefunden.")
        return
    
    catalog = ParquetDataCatalog(catalog_path)

    # --- DYNAMISCHE INSTRUMENTEN-ERKENNUNG ---
    bar_dir = os.path.join(catalog_path, "bar")
    dynamic_instruments = []
    
    if os.path.exists(bar_dir):
        for folder_name in os.listdir(bar_dir):
            if os.path.isdir(os.path.join(bar_dir, folder_name)):
                bar_type = folder_name
                # Extrahiere die Instrumenten-ID (vor dem ersten Bindestrich)
                instrument_id_str = bar_type.split('-')[0]
                dynamic_instruments.append({
                    "id": instrument_id_str,
                    "bar_type": bar_type
                })

    if not dynamic_instruments:
        print(f"⚠️ Keine Bar-Daten im Verzeichnis {bar_dir} gefunden. Breche ab.")
        return

    print(f"✅ {len(dynamic_instruments)} Instrumente dynamisch im Datenkatalog gefunden.")

    start_capital = global_settings.get("start_capital", 10000.0)

    # Output-Ordner fuer Reports erstellen
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

            # Jede Kombination benoetigt eine isolierte Backtest-Engine
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

            # Instrument hinzufuegen (Echt oder Mock)
            inst_id = InstrumentId.from_str(inst_id_str)
            instrument_loaded = False
            
            for cat_inst in catalog.instruments():
                if cat_inst.id == inst_id:
                    engine.add_instrument(cat_inst)
                    instrument_loaded = True
                    break
                    
            if not instrument_loaded:
                dummy_inst = create_mock_instrument(inst_id_str)
                engine.add_instrument(dummy_inst)

            # Daten fuer dieses spezifische Instrument laden
            try:
                bars = catalog.bars(bar_type_strs=[bar_type])
                if bars and len(bars) > 0:
                    engine.add_data(bars)
                else:
                    print(f"   ⚠️ Keine Kerzen im Katalog fuer {bar_type}. Ueberspringe Kombination.")
                    continue
            except ValueError as e:
                print(f"   ⚠️ Fehler beim Laden von Bars fuer {bar_type}: {e}. Ueberspringe.")
                continue

            # --- DYNAMISCHER PARAMETER-OVERRIDE ---
            try:
                module = importlib.import_module(module_name)
                StrategyClass = getattr(module, strategy_class_name)
                ConfigClass = getattr(module, config_class_name)

                # Wichtig: Hardcodierte Instrumente aus der JSON ueberschreiben!
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

                # Generiere isoliertes Tearsheet pro Run
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                report_filename = os.path.join(reports_dir, f"tearsheet_{inst_id_str}_{strategy_class_name}_{timestamp}.html")

                # Konfiguration des Tearsheets gemaess Manual
                ts_config = TearsheetConfig(
                    title=f"Backtest: {inst_id_str} | {strategy_class_name}",
                    output_path=report_filename,
                    include_equity=True,
                    include_drawdown=True,
                    include_returns=True,
                    include_daily_returns=True,
                    include_positions=True,
                )

                # Objekt instanziieren, bauen und speichern
                tearsheet = Tearsheet(results=results, config=ts_config)
                tearsheet.build()
                tearsheet.save()
                
                print(f"   ✅ Tearsheet erfolgreich gespeichert: {report_filename}")

            except Exception as e:
                print(f"   ⚠️ Warnung: Backtest/Tearsheet-Generierung fuer {inst_id_str} ({strategy_class_name}) fehlgeschlagen: {e}")
                # Schleife laeuft trotzdem fuer die naechste Kombination weiter
                continue

    print("\n✅ Matrix-Backtest vollstaendig abgeschlossen!")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()