## 2. Grundkonfiguration des Tearsheets
In Nautilus wird die Erstellung des Reports über die TearsheetConfig gesteuert. Diese Konfiguration definiert, welche Charts und Statistiken in der HTML-Datei enthalten sein sollen.
### Wichtige Parameter:
 * include_equity: Zeigt den Verlauf des Kapitals.
 * include_drawdown: Visualisiert die prozentualen Rücksetzer.
 * include_returns: Monatliche und jährliche Performance-Metriken.
 * include_positions: Zeigt die Haltedauer und Verteilung der Positionen.
## 3. Integration in das Backtesting-Skript
Hier ist ein Beispiel, wie du nach dem Durchlaufen der BacktestEngine das Tearsheet generierst und speicherst.
```python
from nautilus_trader.analysis.reports import TearsheetConfig
from nautilus_trader.backtest.engine import BacktestEngine
from pathlib import Path

# 1. Backtest wie gewohnt initialisieren und ausführen
engine = BacktestEngine(config=engine_config)
# ... Strategien hinzufügen, Daten laden, etc.
engine.run()

# 2. Ergebnisse abrufen
results = engine.get_backtest_results()

# 3. Tearsheet-Konfiguration erstellen
config = TearsheetConfig(
    title="eToro Strategie Backtest",
    output_path=str(Path("reports/tearsheet_etoro.html")),
    include_equity=True,
    include_drawdown=True,
    include_returns=True,
    include_daily_returns=True,
    include_positions=True,
)

# 4. Report generieren
from nautilus_trader.analysis.visualisation import Tearsheet
tearsheet = Tearsheet(results=results, config=config)
tearsheet.build()
tearsheet.save()

print(f"Tearsheet wurde unter {config.output_path} gespeichert.")

```
## 4. Visualisierung von Indikatoren auf dem Chart
Um eigene Indikatoren (z. B. SMA, RSI) aus deinem eToro-Datensatz auf dem Candlestick-Chart anzuzeigen, kannst du die BacktestResult Objekte nutzen oder direkt die Bar Daten plotten.
Nautilus ermöglicht es, Fills (ausgeführte Orders) automatisch auf den Preis-Charts zu markieren:
 * **Grüne Marker:** Buy/Long Fills.
 * **Rote Marker:** Sell/Short Fills.
## 5. Workflow-Optimierung für 32GB RAM
Da die Daten als Parquet-Dateien vorliegen, empfiehlt sich folgender Ablauf für das Backtesting auf deinem Laptop:
 1. **Gezieltes Laden:** Nutze die ParquetDataCatalog Klasse von Nautilus, um nur die benötigten Instrumente und Zeiträume in den RAM zu laden.
 2. **Iteratives Testen:** Generiere nach jedem Lauf ein Tearsheet mit einem Zeitstempel im Dateinamen (tearsheet_2026-05-09_v1.html), um verschiedene Strategie-Iterationen direkt im Browser vergleichen zu können.
 3. **Interaktivität:** Die erzeugten HTML-Dateien nutzen Plotly. Du kannst in die Charts zoomen, einzelne Datenpunkte inspizieren und die Legende nutzen, um Kurven ein- oder auszublenden.
*Hinweis: Da Nautilus Trader eine sehr aktive Entwicklung aufweist, stelle sicher, dass die installierte Version mit der API-Struktur deines etoro_nautilus Repositories übereinstimmt.*
"""
with open("Nautilus_Trader_Tearsheet_Anleitung.md", "w", encoding="utf-8") as f:
f.write(markdown_content)
```
Deine Markdown-Datei ist bereit:
[file-tag: code-generated-file-0-1778332724551310021]

Die Anleitung zeigt dir den kompletten Prozess, wie du aus deinen Nautilus-Backtests interaktive HTML-Reports generierst. Das Skript integriert sich nahtlos in den Workflow, bei dem du die aus den Parquet-Dateien gespeicherten Datensätze auf dem Laptop auswertest. Du kannst die erzeugte HTML-Datei dann einfach im Browser öffnen und die Plotly-Charts (inklusive Equity, Drawdown und Fills auf den Candlesticks) interaktiv analysieren.

```
