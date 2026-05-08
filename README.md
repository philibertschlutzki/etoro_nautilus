Hier ist eine vollständig überarbeitete und erweiterte Version deiner `README.md`.

Sie beinhaltet nun neben dem ursprünglichen Setup auch die professionelle **Two-Service-Architektur (systemd)** für den Cloud-Betrieb sowie das **Data-Catalog-Konzept** (Parquet-Recording) für dein späteres Backtesting.

Kopiere diesen Text einfach und ersetze damit den Inhalt deiner aktuellen `README.md` auf GitHub:

---

```markdown
# 🚀 eToro Nautilus Multi-Bot Plattform & Data Catalog

Willkommen beim **eToro Nautilus** Projekt! Dies ist ein professionelles, hochskalierbares Grundgerüst für algorithmisches Trading in Python. Das Projekt nutzt das [Nautilus Trader](https://nautilustrader.io/) Framework, um eine Echtzeit-WebSocket-Verbindung zur eToro-API herzustellen.

Neben dem **Live-Trading** (Multi-Asset & Multi-Strategie) bietet diese Plattform nun auch eine integrierte **Market Data Recording Engine**. Diese zeichnet hochfrequente Marktdaten ressourcenschonend im Parquet-Format auf, um später präzises Backtesting durchführen zu können.

---

## 📋 1. Projekt-Übersicht

Dieses Projekt verbindet professionelle Trading-Architektur mit der eToro-API:
- **Live-Trading Orchestrator:** Empfängt Echtzeit-Updates und führt Strategien (z.B. SMA Crossover) für beliebig viele Aktien parallel aus.
- **Data Catalog Recorder (NEU):** Ein passiver Zuhörer, der Ticks und Kerzen (Bars) im RAM sammelt und ressourcenschonend als komprimierte `.parquet`-Dateien abspeichert.
- **Cloud-Ready (Systemd):** Optimiert für ressourcenarme Cloud-VMs (wie Google Cloud `e2-micro`) durch eine strikte Trennung von Trading- und Daten-Aufzeichnungs-Prozessen.

---

## 🛠️ 2. Voraussetzungen (Prerequisites)

- **Python-Version:** Python 3.10 oder neuer.
- **Benötigte Bibliotheken** (siehe `requirements.txt`):
  - `nautilus_trader>=1.226.0` (Core-Framework)
  - `websockets` (eToro-Verbindung)
  - `python-dotenv` (Sichere API-Keys)
  - `pandas` & `pyarrow` (Für die Parquet-Datenspeicherung)
- **eToro API-Zugangsdaten:** API-Key und User-Key von eToro.

---

## 💻 3. Lokales Setup (Für Entwicklung & Test)

### Schritt 1: Repository klonen & Environment erstellen
```bash
git clone [https://github.com/philibertschlutzki/etoro_nautilus.git](https://github.com/philibertschlutzki/etoro_nautilus.git)
cd etoro_nautilus/

python3 -m venv venv
source venv/bin/activate  # Unter Windows: venv\Scripts\activate
pip install -r requirements.txt

```

### Schritt 2: Konfiguration der `.env`-Datei 🔐

Erstelle eine neue Datei namens `.env` im Hauptverzeichnis:

```env
ETORO_API_KEY=DEIN_API_KEY_HIER
ETORO_USER_KEY=DEIN_USER_KEY_HIER

```

*(WICHTIG: Die `.env` darf niemals in Git versioniert werden!)*

### Schritt 3: Skripte lokal ausführen

Du kannst nun wahlweise den Trading-Bot oder den Data-Recorder starten:

```bash
# Startet den Trading-Bot (führt Strategien aus)
python run_bot.py

# Startet den Daten-Rekorder (speichert Parquet-Dateien lokal)
python run_catalog.py

```

---

## ☁️ 4. Produktivbetrieb auf einer Linux-VM (Cloud/VPS)

Für den 24/7 Betrieb (z.B. auf einer Google Cloud e2-micro Instanz) nutzen wir eine **Two-Service-Architektur** via `systemd`. Dies garantiert, dass der Trading-Bot und die Datenaufzeichnung komplett isoliert voneinander laufen. Fällt ein Dienst aus, startet das System ihn automatisch neu.

### 4.1. Systembenutzer & Verzeichnisse anlegen

Wir führen die Bots aus Sicherheitsgründen nicht als Root aus.

```bash
sudo useradd -r -s /bin/false tradingbot
sudo mkdir -p /opt/etoro_nautilus    # App-Verzeichnis
sudo mkdir -p /data/nautilus         # Ziel für Parquet-Dateien

# Code klonen und Rechte setzen
cd /opt/etoro_nautilus
sudo -u tradingbot git clone [https://github.com/philibertschlutzki/etoro_nautilus.git](https://github.com/philibertschlutzki/etoro_nautilus.git) .
sudo -u tradingbot python3 -m venv venv
sudo -u tradingbot ./venv/bin/pip install -r requirements.txt
sudo chown -R tradingbot:tradingbot /data/nautilus

```

### 4.2. Dienst 1: Der Data-Catalog-Service

Dieser Dienst verbindet sich mit eToro, sammelt alle Ticks/Bars im RAM und schreibt sie alle 60 Sekunden auf die Festplatte (In-Memory Batching).

Erstelle die Datei `/etc/systemd/system/nautilus-catalog.service`:

```ini
[Unit]
Description=Nautilus eToro Data Catalog Service
After=network-online.target

[Service]
Type=simple
User=tradingbot
Group=tradingbot
WorkingDirectory=/opt/etoro_nautilus
ExecStart=/opt/etoro_nautilus/venv/bin/python /opt/etoro_nautilus/run_catalog.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target

```

### 4.3. Dienst 2: Der Trading-Bot

Dieser Dienst ist isoliert und führt ausschließlich deine Handelslogik aus.

Erstelle die Datei `/etc/systemd/system/nautilus-bot.service`:

```ini
[Unit]
Description=Nautilus eToro Trading Bot
After=network-online.target nautilus-catalog.service

[Service]
Type=simple
User=tradingbot
Group=tradingbot
WorkingDirectory=/opt/etoro_nautilus
ExecStart=/opt/etoro_nautilus/venv/bin/python /opt/etoro_nautilus/run_bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target

```

### 4.4. Dienste aktivieren & verwalten

```bash
sudo systemctl daemon-reload
sudo systemctl enable nautilus-catalog.service nautilus-bot.service
sudo systemctl start nautilus-catalog.service nautilus-bot.service

# Logs in Echtzeit überwachen:
sudo journalctl -u nautilus-bot.service -f
sudo journalctl -u nautilus-catalog.service -f

```

---

## 📁 5. Architektur-Übersicht

Das Projekt ist in logische Bereiche unterteilt:

1. **Das Gehirn (`config/setups.py`):** Zentrales Array `ACTIVE_BOTS`. Hier definierst du, welche Strategie auf welcher Aktie läuft. Beide System-Dienste greifen auf diese Config zurück.
2. **Die Wörterbücher (`adapters/instrument_map.py`):** Mapped eToro-IDs (z.B. `1111`) zu Nautilus-Namen (z.B. `TSLA.ETORO`). *Tipp: Nutze `get_instruments_id.py` um neue IDs zu finden.*
3. **Die Logik (`strategies/...`):** Hier liegen deine Handelsstrategien (z.B. `sma_crossover.py`).
4. **Der Bot (`run_bot.py`):** Verbindet sich mit eToro, lädt Strategien aus `setups.py` und handelt.
5. **Der Recorder (`run_catalog.py`):** Ein passiver Listener. Nutzt den `ParquetDataCatalog`, um die Live-Daten für spätere Backtests in `/data/nautilus/` zu archivieren.

---

## 🎯 6. So fügst du neue Aktien/Strategien hinzu

1. Finde die eToro-ID mit dem Hilfsskript:
`python get_instruments_id.py` (Suchbegriff im Code anpassen).
2. Trage die ID in `adapters/instrument_map.py` ein:
`"1001": "AAPL.ETORO"`
3. Füge in `config/setups.py` einen neuen Block zum `ACTIVE_BOTS`-Array hinzu.
4. **Cloud-Nutzer:** Starte beide Dienste kurz neu, damit auch der Katalog die neue Aktie abhört:
`sudo systemctl restart nautilus-catalog.service nautilus-bot.service`

Viel Erfolg und Happy Algorithmic Trading! 📈🤖

```
