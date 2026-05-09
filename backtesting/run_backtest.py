import os
import json
import importlib
from typing import Dict, Any

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.objects import Money
from nautilus_trader.model.instruments.base import Instrument


def load_config(filepath: str) -> Dict[str, Any]:
    with open(filepath, 'r') as f:
        return json.load(f)


def run_backtest():
    # 1. Konfiguration einlesen
    config_path = os.path.join(os.path.dirname(__file__), "backtesting_config.json")
    if not os.path.exists(config_path):
        print(f"❌ Fehler: Konfigurationsdatei {config_path} nicht gefunden.")
        return

    config_data = load_config(config_path)
    global_settings = config_data.get("global_settings", {})
    instruments_list = config_data.get("instruments", [])
    strategies_list = config_data.get("strategies", [])

    if not instruments_list or not strategies_list:
        print("⚠️ Keine Instrumente oder Strategien in der Config definiert. Breche ab.")
        return

    catalog_path = global_settings.get("catalog_path", "./data/nautilus")

    if not os.path.exists(catalog_path):
        print(f"⚠️ Warnung: Datenverzeichnis {catalog_path} nicht gefunden.")
    
    catalog = ParquetDataCatalog(catalog_path) if os.path.exists(catalog_path) else None

    # 2. Backtest Engine konfigurieren
    engine_config = BacktestEngineConfig(
        trader_id="Matrix-Backtester",
    )
    engine = BacktestEngine(config=engine_config)

    # 3. Dummy Venue (Börse) hinzufügen
    start_capital = global_settings.get("start_capital", 100000.0)
    engine.add_venue(
        venue=Venue("ETORO"),
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(start_capital, USD)]
    )

    # =====================================================================
    # 4. Historische Instrumente und Bars laden (MIT NEUEM FALLBACK)
    # =====================================================================
    instruments_added = 0
    if catalog:
        catalog_instruments = catalog.instruments()
        for instrument in catalog_instruments:
            engine.add_instrument(instrument)
            instruments_added += 1

    # WICHTIGER FIX: Wenn der Daten-Katalog keine Instrumente beinhaltet,
    # laden wir sie dynamisch aus dem Quellcode des Bots (instrument_map.py)
    if instruments_added == 0:
        print("⚠️ Keine Instrument-Metadaten im Katalog gefunden. Lade Fallback aus adapters/instrument_map.py...")
        try:
            import adapters.instrument_map as imap
            
            # Wir durchsuchen die Datei dynamisch nach allen Nautilus-Instrumenten
            for name, obj in vars(imap).items():
                if isinstance(obj, Instrument):
                    engine.add_instrument(obj)
                    instruments_added += 1
                elif isinstance(obj, dict):
                    for val in obj.values():
                        if isinstance(val, Instrument):
                            engine.add_instrument(val)
                            instruments_added += 1
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, Instrument):
                            engine.add_instrument(item)
                            instruments_added += 1
            print(f"✅ {instruments_added} Instrumente erfolgreich in die Engine geladen.")
        except Exception as e:
            print(f"❌ Fehler beim Laden der Instrumente aus dem Projekt: {e}")

    # =====================================================================
    # Kerzendaten (Bars) in die Engine laden
    # =====================================================================
    needed_bar_types = list(set([inst["bar_type"] for inst in instruments_list]))
    
    if catalog and needed_bar_types:
        print(f"Lese Kerzendaten für: {needed_bar_types} ...")
        bars = catalog.bars(bar_type_strs=needed_bar_types)
        
        if bars and len(bars) > 0:
            try:
                engine.add_data(bars)
                print(f"✅ {len(bars)} historische Kerzen in den Arbeitsspeicher geladen.")
            except ValueError as e:
                print(f"⚠️ WARNUNG: Konnte Bars nicht hinzufügen: {e}")
        else:
            print("⚠️ WARNUNG: Die Daten-Ordner für diese Instrumente sind leer!")
            print("Der Backtest wird daher keine Trades ausführen können (Total events: 0).")

    # 5. Matrix generieren: Jedes Instrument x Jede Strategie
    for inst in instruments_list:
        for strat in strategies_list:
            module_name = strat["strategy_module"]
            strategy_class_name = strat["strategy_class"]
            config_class_name = strat["config_class"]
            params = strat.get("params", {})

            try:
                module = importlib.import_module(module_name)
                StrategyClass = getattr(module, strategy_class_name)
                ConfigClass = getattr(module, config_class_name)

                # Config instanziieren
                strategy_config = ConfigClass(
                    instrument_id=inst["id"],
                    bar_type=inst["bar_type"],
                    **params
                )

                # Strategie instanziieren und hinzufügen
                strategy = StrategyClass(config=strategy_config)
                engine.add_strategy(strategy)

            except Exception as e:
                print(f"❌ Fehler beim Laden von {strategy_class_name}_{inst['id']}: {e}")

    # 6. Backtest starten
    print(f"\n🚀 Starte Matrix-Backtest mit {len(instruments_list)} Instrumenten und {len(strategies_list)} Strategien ({len(instruments_list) * len(strategies_list)} Kombinationen)...")
    try:
        engine.run()
    except Exception as e:
        print(f"❌ Fehler während des Backtests: {e}")

    # 7. Ergebnisse ausgeben
    print("\n✅ Backtest beendet!")
    print("\n--- Portfolio Statistiken ---")
    try:
        account_report = engine.trader.generate_account_report(Venue("ETORO"))
        print(account_report)
    except Exception as e:
        print(f"Konnte Statistiken nicht generieren: {e}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()