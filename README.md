# 🚀 eToro Nautilus Multi-Bot Plattform & Data Catalog

Willkommen beim **eToro Nautilus** Projekt! Dies ist ein professionelles, hochskalierbares Grundgerüst für algorithmisches Trading in Python. Das Projekt nutzt das [Nautilus Trader](https://nautilustrader.io/) Framework in Kombination mit eigens geschriebenen eToro-Adaptern, um eine robuste und effiziente Ausführung von Handelsstrategien zu gewährleisten.

## 🏗️ Systemarchitektur

Das System ist modular aufgebaut und trennt die Handelslogik strikt von der Datenerfassung und der API-Kommunikation. Die Architektur läuft nativ auf Linux (z. B. auf ressourcenarmen Cloud-VMs) ohne Docker-Overhead.

*   **Nautilus Trader Core:** Die hochperformante Engine für Backtesting und Live-Trading.
*   **eToro Adapter (`/adapters`):**
    *   `etoro_data.py`: Verwaltet die asynchrone WebSocket-Verbindung zur eToro-API. Sie ist verantwortlich für das Routing von Echtzeit-Kursdaten (Ticks/Bars) und das Handling von Connection Drops sowie Reconnects.
    *   `etoro_execution.py`: Die Ausführungs-Schnittstelle, welche die Order-Logik (Kauf, Verkauf, Stop-Loss) an eToro übermittelt.
*   **Live-Trading Orchestrator (`run_bot.py`):** Startet den Trading-Bot, verbindet die Adapter und führt parallele Strategien (z.B. SMA Crossover, MACD) basierend auf der Konfiguration in `config/setups.py` aus.
*   **Data Catalog Recorder (`run_catalog.py`):** Ein isolierter Hintergrunddienst, der Marktdaten (Ticks/Bars) kontinuierlich und ressourcenschonend im `.parquet`-Format speichert.
*   **Momentum-LS Subsystem:** Ein Orchestrator (`momentum_ls_run.py`) der ein dynamisches Universum an Instrumenten filtert, ein simuliertes Turnier für die Strategieauswahl durchführt und Trades mittels eines zentralen Allocators gewichtet.


## Testing

Das Projekt enthält eine umfassende Test-Suite. Details zur Testausführung finden sich in `.agents/testing.md` (sofern vorhanden, ansonsten Platzhalter für künftige Test-Dokumentation).

## 📚 Dokumentation & Handbücher (Manuals)

Alle detaillierten Anleitungen, operativen Prozesse und Konfigurationsdetails sind im `manuals/` Verzeichnis konsolidiert:

1.  [**☁️ Deployment & Operations Guide (`manuals/deployment.md`)**](./manuals/deployment.md)
    *   Einrichtung und Betrieb auf einer Cloud-VM (inkl. 1GB RAM Optimierung via Swap).
2.  [**📊 Backtesting & Tearsheet Manual (`manuals/backtesting_manual.md`)**](./manuals/backtesting_manual.md)
    *   Konfiguration und Ausführung lokaler Backtests.
3.  [**🎯 Neue Instrumente hinzufügen (`manuals/new_tickers.md`)**](./manuals/new_tickers.md)
    *   Anleitung zur Integration neuer Märkte/Aktien.
4.  [**Momentum-LS Strategy (`manuals/momentum_ls.md`)**](./manuals/momentum_ls.md)
    *   Detaillierte Beschreibung der Momentum-LS Strategie.
5.  [**Feature Automation LS (`manuals/feature_automation_LS.md`)**](./manuals/feature_automation_LS.md)
    *   Dokumentation zur Feature Automation im Momentum-LS System.

---


## Reports & Logs

Verschiedene Systemkomponenten speichern ihre Daten und Status in dedizierten Verzeichnissen:
*   `logs/`: Enthält die täglichen `.log` Dateien der Trading-Bots sowie die generierten `.md` Berichte (z.B. Turnier-Ergebnisse des Momentum-LS).
*   `data/state/`: Speichert Laufzeit-Mapping-Dateien (z.B. `execution_mapping.json`) für die Persistenz von Order/Position IDs.
*   `data/universe/`: Speichert die dynamisch abgerufenen Universum-Konfigurationen (z.B. `momentum_ls.json`).
*   `data/nautilus/`: Enthält die heruntergeladenen Parquet-Dateien und QuoteTicks für Backtesting.

## 🚀 Lokaler Schnellstart (Entwicklung)

Für die lokale Entwicklung und das Testen von Strategien (Python 3.10+ erforderlich):

### 1. Setup & Installation
```bash
git clone https://github.com/philibertschlutzki/etoro_nautilus.git
cd etoro_nautilus/

python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. API-Keys konfigurieren

Erstelle eine `.env` Datei im Root-Verzeichnis:

```env
ETORO_API_KEY=DEIN_API_KEY_HIER
ETORO_USER_KEY=DEIN_USER_KEY_HIER
```

### 3. System starten

Starte die isolierten Hauptprozesse:

```bash
# Startet den aktiven Trading-Bot
python run_bot.py

# Startet den passiven Daten-Rekorder
python run_catalog.py
```

*(Für den dauerhaften Server-Betrieb siehe `manuals/deployment.md`)*
