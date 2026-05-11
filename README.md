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

## 📚 Dokumentation & Handbücher

Alle detaillierten Anleitungen, operativen Prozesse und Konfigurationsdetails sind im `manuals/` Verzeichnis konsolidiert. Bitte lies diese Dokumente für ein tiefgreifendes Verständnis des Systems:

1.  [**☁️ Deployment & Operations Guide (`manuals/deployment.md`)**](./manuals/deployment.md)
    *   Einrichtung und Betrieb auf einer Cloud-VM (inkl. 1GB RAM Optimierung via Swap).
    *   Native Prozesssteuerung mit `systemd` und Log-Analyse (`journalctl`).
    *   **Wichtig:** Detailliertes Kapitel zum WebSocket Debugging, Connection Drops und Timeout-Handling.
2.  [**📊 Backtesting & Tearsheet Manual (`manuals/backtesting_manual.md`)**](./manuals/backtesting_manual.md)
    *   Konfiguration und Ausführung lokaler Backtests (`run_backtest.py` & `backtesting_config.json`).
    *   Generierung und Interpretation von interaktiven HTML-Tearsheets.
    *   Performance-Optimierung für Backtests auf lokalen Rechnern.
3.  [**🎯 Neue Instrumente hinzufügen (`manuals/new_tickers.md`)**](./manuals/new_tickers.md)
    *   Schritt-für-Schritt-Anleitung zur Integration neuer Märkte/Aktien in `adapters/instrument_map.py` und `config/setups.py`.

---

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
