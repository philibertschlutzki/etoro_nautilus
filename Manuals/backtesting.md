# 📊 Backtesting Guide: Lokale Simulationen mit Nautilus

Dieses Handbuch beschreibt den Workflow, um die auf der Cloud-VM gesammelten Live-Marktdaten auf deinen lokalen Rechner zu übertragen und dort performante, risikofreie Backtests durchzuführen.

## 🧠 1. Warum lokales Backtesting?
Der `nautilus-catalog.service` auf deiner Cloud-VM sammelt kontinuierlich 24/7 Tick- und Bar-Daten und speichert sie als hochkomprimierte `.parquet`-Dateien.
Da Backtesting-Engines diese historischen Daten komplett in den Arbeitsspeicher (RAM) laden, um Simulationen in Millisekunden durchzuführen, würde eine kleine Cloud-VM (wie die e2-micro) sofort abstürzen. Daher lagern wir diesen Prozess auf deinen lokalen PC/Laptop (idealerweise via VS Code) aus.

---

## 📥 2. Marktdaten herunterladen (VM ➔ Lokal)

Der erste Schritt vor jedem Backtest ist das Synchronisieren der neuesten Marktdaten von deinem Server.

**Option A: Via Terminal (SCP)**
Öffne ein Terminal in deinem **lokalen** Projektverzeichnis und führe folgenden Befehl aus (ersetze `<user>` und `<ip>`):
```bash
scp -r <user>@<ip>:/data/nautilus ./data/

```

**Option B: Via VS Code (SFTP / Remote-SSH)**

1. Verbinde dich via "Remote - SSH" Erweiterung mit deiner VM.
2. Navigiere im Explorer zum Ordner `/data/nautilus`.
3. Lade den Ordner per Rechtsklick (Download) in dein lokales `./data/`-Verzeichnis herunter.

---

## 💻 3. Lokale Umgebung einrichten

Falls du das Projekt lokal frisch geklont hast, richte die Python-Umgebung ein:

```bash
# Virtuelle Umgebung erstellen und aktivieren
python3 -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate

# Abhängigkeiten installieren
pip install -r requirements.txt

```

*Wichtig: Für Backtests und Parquet-Dateien müssen `pandas` und `pyarrow` installiert sein (sind in aktuellen Nautilus-Versionen meist enthalten).*

---

## ⚙️ 4. Backtest-Skript konfigurieren

Öffne die Datei `run_backtest.py`. Achte darauf, dass der Pfad zum Datenkatalog auf dein lokales Verzeichnis zeigt, in das du die Daten gerade kopiert hast:

```python
def run_backtest():
    # MUSS auf den lokalen relativen Pfad zeigen
    catalog_path = "./data/nautilus" 
    
    catalog = ParquetDataCatalog(catalog_path)
    # ...

```

---

## 🚀 5. Strategie testen & iterieren

Wir testen standardmäßig die `TeslaComboStrategy` (SMA + MACD + Bollinger Bands + VWAP).

**Den Backtest starten:**
Führe im lokalen VS Code Terminal aus:

```bash
python run_backtest.py

```

**Der Iterations-Workflow (Tuning):**
Backtesting bedeutet, Parameter so lange zu optimieren, bis die Strategie profitabel ist.

1. Öffne `strategies/tesla_combo_strategy.py`.
2. Ändere die Parameter in der `TeslaComboConfig` (z.B. den `sma_period` von 50 auf 20 oder den `bb_period`).
3. Führe `python run_backtest.py` erneut aus und bewerte das Ergebnis.
4. Erst wenn das Setup im Backtest überzeugt, wird es in die Live-Konfiguration (`config/setups.py`) übernommen.

---

## ⚡ 6. Performance & Analyse

Wenn dein Datenkatalog über die Monate wächst, kann der Backtest langsam werden. Nutze diese Tricks:

### Zeitfenster einschränken

Um RAM zu sparen und schneller zu iterieren, grenze den Backtest in der `run_backtest.py` auf spezifische Tage ein:

```python
    engine_config = BacktestEngineConfig(
        trader_id="Backtester-01",
        start_time="2026-05-01", # Aktiviere diese Zeilen
        end_time="2026-05-14",   # für schnellere Tests
    )

```

### Detaillierte Reports generieren

Um tiefgreifende Statistiken (Win-Rate, Max Drawdown, Sharpe Ratio) zu erhalten, entkommentiere am Ende der `run_backtest.py` die Report-Funktionen:

```python
from nautilus_trader.analysis.statistic import PortfolioStatistics
stats = PortfolioStatistics(engine.trader.generate_account_report(Venue("ETORO")))
print(stats)

```

*(Hinweis: Erfordert eventuell `pip install nautilus_trader[analysis]`)*

```

```
