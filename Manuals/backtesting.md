### Backtesting-Anleitung: eToro Nautilus (Lokal & Remote)

Dieses Handbuch beschreibt den Prozess, Marktdaten von der Cloud-VM auf einen lokalen Rechner zu übertragen und dort performante Backtests mit der `BacktestEngine` von Nautilus Trader durchzuführen.

#### 1. Übersicht & Motivation

Die Aufzeichnung der Marktdaten erfolgt kontinuierlich auf der Cloud-VM (z.B. Google Cloud e2-micro), um eine unterbrechungsfreie 24/7-Datenerfassung zu gewährleisten. Da Backtesting-Prozesse jedoch sehr RAM-intensiv sind (In-Memory-Verarbeitung der Daten), wird die Ausführung der Simulationen auf eine lokale Maschine (PC/Laptop) mit Visual Studio Code ausgelagert.

#### 2. Datenübertragung (VM ➔ Lokal)

Der `nautilus-catalog.service` speichert Ticks und Bars im Parquet-Format unter `/data/nautilus`.

**Vorgehensweise via Terminal (SCP):**

1. Öffne ein Terminal auf deinem lokalen Rechner.
2. Navigiere in dein lokales Projektverzeichnis.
3. Führe folgenden Befehl aus (ersetze `<user>` und `<ip>` durch deine VM-Zugangsdaten):
```bash
scp -r <user>@<ip>:/data/nautilus ./data/

```



**Vorgehensweise via VS Code (SFTP):**

* Nutze die Erweiterung "Remote - SSH".
* Navigiere im Explorer zum Pfad `/data/nautilus`.
* Rechtsklick auf den Ordner ➔ "Download", um die Daten in dein lokales `./data/`-Verzeichnis zu spiegeln.

#### 3. Lokale Umgebung einrichten

Stelle sicher, dass dein lokaler Python-Interpreter die notwendigen Abhängigkeiten erfüllt.

1. **Virtual Environment erstellen:**
```bash
python3 -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate

```


2. **Abhängigkeiten installieren:**
Die Datei `requirements.txt` muss lokal installiert sein:
```bash
pip install -r requirements.txt

```


*Hinweis: Für die Analyse der Backtest-Ergebnisse wird zusätzlich `pandas` und `pyarrow` benötigt.*

#### 4. Konfiguration der `run_backtest.py`

Damit das Skript die kopierten Daten findet, muss der Pfad zum `ParquetDataCatalog` angepasst werden.

Öffne die Datei `run_backtest.py` und korrigiere den Pfad:

```python
def run_backtest():
    # Lokaler Pfad zu den kopierten Daten
    catalog_path = "./data/nautilus" 
    
    # ... Rest des Codes ...
    catalog = ParquetDataCatalog(catalog_path)

```

#### 5. Durchführung des Backtests

Der Backtest wird über das Skript `run_backtest.py` gestartet, welches die `TeslaComboStrategy` verwendet.

1. **Strategie-Parameter prüfen:**
In `strategies/tesla_combo_strategy.py` können die Indikatoren (SMA, MACD, Bollinger Bands) angepasst werden.
2. **Ausführung:**
Starte den Test im VS Code Terminal:
```bash
python run_backtest.py

```



#### 6. Performance-Optimierung

* **Zeitfenster einschränken:** Nutze in der `BacktestEngineConfig` die Parameter `start_time` und `end_time`, um nur spezifische Zeiträume zu testen und den RAM-Verbrauch zu minimieren.
* **Logging:** Setze das Log-Level in der Config bei großen Datenmengen auf `INFO` statt `DEBUG`, um die Ausführung zu beschleunigen.
