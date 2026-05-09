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
from nautilus_trader.analysis import TearsheetConfig, create_tearsheet


def load_config(filepath: str) -> Dict[str, Any]:
    with open(filepath, 'r') as f:
        return json.load(f)


def create_mock_instrument(instrument_id_str: str) -> Equity:
    """Generiert ein dynamisches Mock-Instrument, falls Metadaten fehlen."""
    inst_id = InstrumentId.from_str(instrument_id_str)
    return Equity(
        instrument_id=inst_id,
        raw_symbol=Symbol(inst_id.symbol.value),
        currency=USD,
        price_precision=5,
        price_increment=Price(1e-5, precision=5),
        lot_size=Quantity(1, precision=0),
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

    catalog_path = global_settings.get("catalog_path", "./data/nautilus")

    if not os.path.exists(catalog_path):
        print(f"⚠️ Warnung: Datenverzeichnis {catalog_path} nicht gefunden.")
    
    catalog = ParquetDataCatalog(catalog_path) if os.path.exists(catalog_path) else None

    # Dynamische Instrumenten-Erkennung
    bar_dir = os.path.join(catalog_path, "bar")
    dynamic_instruments = []
    if os.path.exists(bar_dir):
        for folder_name in os.listdir(bar_dir):
            if os.path.isdir(os.path.join(bar_dir, folder_name)):
                bar_type = folder_name
                # Extrahieren der Instrumenten-ID (vor dem ersten Bindestrich)
                instrument_id_str = bar_type.split('-')[0]
                # ETORO als Venue explizit dran lassen oder prüfen
                # Normalerweise sieht bar_type so aus: 01211.HK.ETORO-1-MINUTE-MID-INTERNAL
                dynamic_instruments.append({
                    "id": instrument_id_str,
                    "bar_type": bar_type
                })

    if not dynamic_instruments:
        print(f"⚠️ Keine Bar-Daten im Verzeichnis {bar_dir} gefunden. Breche ab.")
        return

    print(f"✅ {len(dynamic_instruments)} Instrumente dynamisch gefunden.")

    start_capital = global_settings.get("start_capital", 100000.0)

    # Output-Ordner fuer Reports erstellen
    os.makedirs("reports", exist_ok=True)

    # 2. Matrix generieren: Jedes Instrument x Jede Strategie
    for inst in dynamic_instruments:
        for strat in strategies_list:
            inst_id_str = inst["id"]
            bar_type = inst["bar_type"]

            module_name = strat["strategy_module"]
            strategy_class_name = strat["strategy_class"]
            config_class_name = strat["config_class"]

            print(f"\n🚀 Starte Backtest: Instrument {inst_id_str} | Strategie {strategy_class_name}")

            # Neue BacktestEngine fuer isolierten Run
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

            # Instrument hinzufuegen
            inst_id = InstrumentId.from_str(inst_id_str)
            if catalog:
                instrument_loaded = False
                for cat_inst in catalog.instruments():
                    if cat_inst.id == inst_id:
                        engine.add_instrument(cat_inst)
                        instrument_loaded = True
                        break
                if not instrument_loaded:
                    dummy_inst = create_mock_instrument(inst_id_str)
                    engine.add_instrument(dummy_inst)
            else:
                dummy_inst = create_mock_instrument(inst_id_str)
                engine.add_instrument(dummy_inst)

            # Daten in Engine laden
            if catalog:
                try:
                    bars = catalog.bars(bar_type_strs=[bar_type])
                    if bars and len(bars) > 0:
                        engine.add_data(bars)
                        print(f"   ✅ {len(bars)} Kerzen geladen für {bar_type}.")
                    else:
                        print(f"   ⚠️ Keine Kerzen gefunden für {bar_type}. Ueberspringe.")
                        continue
                except ValueError as e:
                    print(f"   ⚠️ Fehler beim Laden von Bars für {bar_type}: {e}. Ueberspringe.")
                    continue
            else:
                continue

            try:
                module = importlib.import_module(module_name)
                StrategyClass = getattr(module, strategy_class_name)
                ConfigClass = getattr(module, config_class_name)

                # Kopie der Params erstellen und mit aktuellem Instrument/Bar-Type ueberschreiben
                params = strat.get("params", {}).copy()
                params["instrument_id"] = inst_id_str
                params["bar_type"] = bar_type

                # Config instanziieren
                strategy_config = ConfigClass(**params)

                # Strategie instanziieren und hinzufügen
                strategy = StrategyClass(config=strategy_config)
                engine.add_strategy(strategy)

            except Exception as e:
                print(f"   ❌ Fehler beim Laden/Konfigurieren der Strategie {strategy_class_name}: {e}")
                continue

            # Backtest ausfuehren und Report generieren
            try:
                engine.run()

                results = engine.get_backtest_results()

                # Wenn wir hier sind, versuchen wir das Tearsheet zu generieren
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                report_filename = f"reports/tearsheet_{inst_id_str}_{strategy_class_name}_{timestamp}.html"

                config = TearsheetConfig(
                    title=f"eToro Backtest - {inst_id_str} - {strategy_class_name}",
                    output_path=report_filename,
                    include_equity=True,
                    include_drawdown=True,
                    include_returns=True,
                    include_daily_returns=True,
                    include_positions=True,
                )

                create_tearsheet(results=results, config=config)
                print(f"   ✅ Tearsheet gespeichert: {report_filename}")

            except Exception as e:
                print(f"   ⚠️ Warnung: Konnte Backtest/Tearsheet für {inst_id_str} mit {strategy_class_name} nicht vollständig erstellen: {e}")
                continue

    print("\n✅ Alle Kombinationen abgearbeitet!")

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()
