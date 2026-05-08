# 🚀 eToro Nautilus Multi-Bot Plattform & Data Catalog

Willkommen beim **eToro Nautilus** Projekt! Dies ist ein professionelles, hochskalierbares Grundgerüst für algorithmisches Trading in Python. Das Projekt nutzt das [Nautilus Trader](https://nautilustrader.io/) Framework, um eine Echtzeit-WebSocket-Verbindung zur eToro-API herzustellen.

## ✨ Kern-Features

- **Live-Trading Orchestrator:** Empfängt Echtzeit-Updates und führt parallele Strategien (z.B. SMA Crossover, MACD, VWAP) für beliebig viele Aktien aus.
- **Data Catalog Recorder:** Ein passiver Hintergrunddienst, der hochfrequente Marktdaten (Ticks/Bars) ressourcenschonend im `.parquet`-Format archiviert.
- **Micro-Cloud Ready:** Optimiert für ressourcenarme VMs durch strikte Service-Trennung via `systemd`.

---

## 📚 Dokumentation & Handbücher

Alle detaillierten Anleitungen und operativen Prozesse sind im `Manuals/` Verzeichnis dokumentiert:

* [☁️ Cloud-VM Installation & Systemd-Setup](./Manuals/vm_install.md) - *Wie das System 24/7 auf einem Server betrieben wird.*
* [📊 Backtesting Guide](./Manuals/backtesting.md) - *Anleitung zum Herunterladen der Marktdaten und lokalen Backtesting in VS Code.*
* [💻 Nützliche Befehle (Cheatsheet)](./Manuals/useful_commands.md) - *Befehle für Systemd, Logs, Git und eToro-Schnittstellen.*

---

## 🚀 Lokaler Schnellstart (Entwicklung)

Für die lokale Entwicklung und das Testen von Strategien auf deinem eigenen Rechner (Python 3.10+ erforderlich):

### 1. Setup & Installation
```bash
git clone [https://github.com/philibertschlutzki/etoro_nautilus.git](https://github.com/philibertschlutzki/etoro_nautilus.git)
cd etoro_nautilus/

python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

```

### 2. API-Keys konfigurieren

Erstelle eine `.env` Datei im Hauptverzeichnis (wird von Git ignoriert):

```env
ETORO_API_KEY=DEIN_API_KEY_HIER
ETORO_USER_KEY=DEIN_USER_KEY_HIER

```

### 3. System starten

Du kannst die Kernkomponenten unabhängig voneinander ausführen:

```bash
# Startet den aktiven Trading-Bot
python run_bot.py

# Startet den passiven Daten-Rekorder
python run_catalog.py

```

*(Für den dauerhaften Server-Betrieb siehe ./Manuals/vm_install.md)*

---

## 📁 Architektur-Übersicht

Das System ist in klare logische Module unterteilt:

1. **`config/setups.py`:** Das "Gehirn". Hier definierst du im `ACTIVE_BOTS`-Array, welche Strategie auf welchem Instrument läuft.
2. **`adapters/instrument_map.py`:** Mappt eToro-IDs (z.B. `1111`) zu Nautilus-Namen (z.B. `TSLA.ETORO`).
3. **`strategies/`:** Beinhaltet deine Handelslogik (z.B. `tesla_combo_strategy.py`).
4. **`run_bot.py` / `run_catalog.py`:** Die beiden isolierten Hauptprozesse für Trading und Datenerfassung.

---

## 🎯 Neue Instrumente hinzufügen

Um eine neue Aktie in das System aufzunehmen:

1. Finde die eToro-ID mit: `python get_instruments_id.py`
2. Trage die ID in `adapters/instrument_map.py` ein.
3. Füge in `config/setups.py` einen neuen Block zum `ACTIVE_BOTS`-Array hinzu.
4. Starte den Bot (und in der Cloud die Systemd-Dienste) neu.
