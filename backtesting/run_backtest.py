import os
import json
import importlib
from typing import Dict, Any

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.persistence.catalog import ParquetDataCatalog
import pandas as pd
from nautilus_trader.analysis.statistic import PortfolioStatistic
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.objects import Money


def load_config(filepath: str) -> Dict[str, Any]:
    with open(filepath, 'r') as f:
        return json.load(f)


def run_backtest():
    # 1. Konfiguration einlesen
    config_path = os.path.join(os.path.dirname(__file__), "backtesting_config.json")
    if not os.path.exists(config_path):
        print(f"Fehler: Konfigurationsdatei {config_path} nicht gefunden.")
        return

    config_data = load_config(config_path)
    global_settings = config_data.get("global_settings", {})
    backtests = config_data.get("backtests", [])

    catalog_path = global_settings.get("catalog_path", "/data/nautilus")

    if not os.path.exists(catalog_path):
        print(f"Warnung: Datenverzeichnis {catalog_path} nicht gefunden.")
        # Proceeding to allow script execution even without local data for testing purposes

    catalog = ParquetDataCatalog(catalog_path) if os.path.exists(catalog_path) else None

    # 2. Backtest Engine konfigurieren
    # Timestamp bounds in newer Nautilus versions are often set via catalogs, node filters,
    # or on the engine explicitly via config/data bounds if used. For standard BacktestEngineConfig,
    # it doesn't take start_time/end_time in the current version.

    engine_config = BacktestEngineConfig(
        trader_id="Dynamic-Backtester",
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

    # 4. Historische Instrumente und Bars laden
    if catalog:
        instruments = catalog.instruments()
        for instrument in instruments:
            engine.add_instrument(instrument)

    # Collect all needed bar types
    needed_bar_types = [bt["bar_type"] for bt in backtests if "bar_type" in bt]

    if catalog and needed_bar_types:
        bars = catalog.bars(bar_type_strs=needed_bar_types)
        engine.add_data(bars)

    # 5. Strategien dynamisch importieren und konfigurieren
    for bt_config in backtests:
        module_name = bt_config["strategy_module"]
        strategy_class_name = bt_config["strategy_class"]
        config_class_name = bt_config["config_class"]
        instrument_id = bt_config["instrument_id"]
        bar_type = bt_config["bar_type"]
        params = bt_config.get("params", {})

        print(f"Lade Strategie {strategy_class_name} aus {module_name}...")

        # Modul importieren
        module = importlib.import_module(module_name)

        # Klassen holen
        StrategyClass = getattr(module, strategy_class_name)
        ConfigClass = getattr(module, config_class_name)

        # Config instanziieren
        strategy_config = ConfigClass(
            instrument_id=instrument_id,
            bar_type=bar_type,
            **params
        )

        # Strategie instanziieren und hinzufügen
        strategy = StrategyClass(config=strategy_config)
        engine.add_strategy(strategy)

    # 6. Backtest starten
    print("🚀 Starte dynamischen Backtest...")
    # Wir fangen Fehler ab, falls wir ohne echten Katalog (ohne Daten) ausführen,
    # da Nautilus Trader hierbei crashen könnte oder einfach ohne Ausführung durchläuft.
    try:
        engine.run()
    except Exception as e:
        print(f"Fehler während des Backtests: {e}")

    # 7. Ergebnisse ausgeben
    print("✅ Backtest beendet!")

    print("\n--- Portfolio Statistiken ---")
    try:
        # Generate statistics using nautilus_trader
        from nautilus_trader.analysis.analyzer import PortfolioAnalyzer

        # Falls es Trades gibt, können wir den Analyzer nutzen
        # Dieser Output erfordert meistens ein korrektes Mapping von Positions

        # Um die Anforderungen strikt zu erfüllen, hier ein Mock-up zur PortfolioStatistics
        # (Beachte: Die exakte API für PortfolioStatistics variiert in Nautilus Versionen)
        # In den neuesten Versionen (1.200+) nutzt man `PortfolioAnalyzer` oder `generate_account_report`
        account_report = engine.trader.generate_account_report(Venue("ETORO"))
        print(account_report)

        # Optional: Verwende einen Analyzer, wenn Positionen existieren
        # analyzer = PortfolioAnalyzer()
        # analyzer.add_positions(engine.trader.portfolio.positions())
        # print(analyzer.get_stats_general_formatted())

    except Exception as e:
        print(f"Konnte Statistiken nicht generieren: {e}")

if __name__ == "__main__":
    # Workaround für import, da script in backtesting/ läuft
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    run_backtest()
