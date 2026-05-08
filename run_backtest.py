import os
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.persistence.catalog import ParquetDataCatalog

# Deine neue Strategie importieren
from strategies.tesla_combo_strategy import TeslaComboStrategy, TeslaComboConfig

def run_backtest():
    # 1. Pfad zu deinen aufgezeichneten Parquet-Daten (wie im README definiert)
    catalog_path = "/data/nautilus"
    
    if not os.path.exists(catalog_path):
        print(f"Fehler: Datenverzeichnis {catalog_path} nicht gefunden.")
        return

    catalog = ParquetDataCatalog(catalog_path)
    
    # 2. Backtest Engine konfigurieren
    engine_config = BacktestEngineConfig(
        trader_id="Backtester-01",
        # Setze Start- und Enddatum passend zu deinen gesammelten Daten
        # start_time="2026-05-01",
        # end_time="2026-05-30",
    )
    engine = BacktestEngine(config=engine_config)
    
    # 3. Dummy Venue (Börse) hinzufügen, da wir offline testen
    engine.add_venue(
        venue=Venue("ETORO"),
        oms_type="HEDGING",
        account_type="MARGIN",
        base_currency="USD",
    )

    # 4. Historische Instrumente und Bars aus dem Katalog in die Engine laden
    instruments = catalog.instruments()
    for instrument in instruments:
        engine.add_instrument(instrument)
        
    # Lade alle 1-Minuten Bars (Passe den String an deinen Bar-Type an)
    bars = catalog.bars(bar_type_strs=["TSLA.ETORO-1-MINUTE-MID-INTERNAL"])
    engine.add_data(bars)

    # 5. Strategie konfigurieren und hinzufügen
    config = TeslaComboConfig(
        strategy_id="Combo_TSLA_01",
        instrument_id="TSLA.ETORO",
        bar_type="TSLA.ETORO-1-MINUTE-MID-INTERNAL"
    )
    strategy = TeslaComboStrategy(config)
    engine.add_strategy(strategy)

    # 6. Backtest starten
    print("🚀 Starte Backtest für Tesla Combo Strategie...")
    engine.run()
    
    # 7. Ergebnisse ausgeben
    print("✅ Backtest beendet!")
    
    # Optional: Report generieren (Benötigt nautilus_trader[analysis] installiert)
    # from nautilus_trader.analysis.statistic import PortfolioStatistics
    # stats = PortfolioStatistics(engine.trader.generate_account_report(Venue("ETORO")))
    # print(stats)

if __name__ == "__main__":
    run_backtest()