# ☁️ Deployment & Operations Guide: eToro Nautilus

Dieses Dokument ist das zentrale Handbuch für die Einrichtung, den Betrieb und die Wartung der eToro Nautilus Plattform auf einer Linux-Instanz. Es ist optimiert für Cloud-VMs mit stark limitierten Ressourcen (wie z. B. eine Google Cloud `e2-micro` mit 1GB RAM) und verwendet eine native Ausführungsumgebung ohne Docker-Overhead.

---

## 1. System-Voraussetzungen & Vorbereitung

Die Architektur ist so konzipiert, dass sie ressourcenschonend arbeitet.

*   **Betriebssystem:** Ubuntu 22.04 LTS oder Debian 11/12.
*   **Hardware:** Mindestens 1 GB RAM (Google Cloud `e2-micro` empfohlen).
*   **Software:** Python 3.10+, `git`, `systemd`
*   **Python Dependencies:** `nautilus_trader>=1.226.0`, `websockets`, `aiohttp`, `python-dotenv`, `requests`, `pandas`, `pyarrow`, `plotly`, `pytest`, `pytest-asyncio`.

### 1.1. Swap-File Einrichtung (WICHTIG für 1GB RAM VMs)

Da die Instanz lediglich 1GB RAM besitzt, können temporäre Spitzen (z. B. bei der Installation bestimmter pip-Pakete wie `pandas` oder `pyarrow`) zu "Out of Memory" (OOM) Kills führen. Die Einrichtung einer Swap-Datei ist zwingend erforderlich:

```bash
# Erstelle eine 2GB Swap-Datei
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Mache die Änderung permanent (für Reboots)
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 1.2. Systembenutzer anlegen

Aus Sicherheitsgründen wird die Plattform unter einem dedizierten Systembenutzer ausgeführt:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git

sudo useradd -r -s /bin/false tradingbot

sudo mkdir -p /opt/etoro_nautilus
sudo mkdir -p /data/nautilus

# Verzeichnisstruktur anlegen
mkdir -p data/state data/nautilus data/universe logs
```

---

## 2. Installation & Konfiguration

Klone das Repository und richte das Python Virtual Environment ein:

```bash
cd /opt/etoro_nautilus
sudo git clone https://github.com/philibertschlutzki/etoro_nautilus.git .
sudo chown -R tradingbot:tradingbot /opt/etoro_nautilus
sudo chown -R tradingbot:tradingbot /data/nautilus

# Virtual Environment erstellen und Pakete installieren
sudo -u tradingbot python3 -m venv venv
sudo -u tradingbot ./venv/bin/pip install --upgrade pip
sudo -u tradingbot ./venv/bin/pip install -r requirements.txt
```

### API-Keys konfigurieren

Erstelle die `.env`-Datei für die API-Zugangsdaten:

```bash
sudo -u tradingbot nano .env
```

Inhalt:

```env
ETORO_API_KEY=DEIN_API_KEY
ETORO_USER_KEY=DEIN_USER_KEY
ETORO_CONFIRM_LIVE=1            # ACHTUNG: Nur setzen, wenn echtes Trading gewünscht ist (Safety Interlock)
MOMENTUM_LS_USERNAME=USERNAME   # eToro Benutzername des Smart Portfolios
```

---

## 3. Prozesssteuerung über `systemd`

Wir nutzen eine **Two-Service-Architektur**, um die Datenaufzeichnung strikt von der Handelslogik zu trennen. Dies garantiert, dass ein Problem im Trading-Bot den Datenrekorder nicht stoppt.

### Dienst 1: Data-Catalog (`run_catalog.py`)

Erstelle `/etc/systemd/system/nautilus-catalog.service`:

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

### Dienst 2: Trading-Bot (`run_bot.py`)

Erstelle `/etc/systemd/system/nautilus-bot.service`:

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


### Dienst 3 (Optional): Momentum-LS Daily Orchestrator via Cron

Da das Subsystem einen täglichen Workflow hat, kann dieser per Cron automatisiert werden:

```bash
# Crontab-Eintrag (crontab -e als tradingbot)
0 6 * * * cd /opt/etoro_nautilus && ./venv/bin/python dev_scripts/momentum_ls_universe.py --output data/universe/momentum_ls.json && ./venv/bin/python dev_scripts/momentum_ls_tournament.py --universe data/universe/momentum_ls.json --output logs/tournament_today.json && ./venv/bin/python dev_scripts/momentum_ls_run.py --tournament logs/tournament_today.json >> logs/daily_run.log 2>&1
```

### Dienste aktivieren und starten

```bash
sudo systemctl daemon-reload
sudo systemctl enable nautilus-catalog.service nautilus-bot.service
sudo systemctl start nautilus-catalog.service
sudo systemctl start nautilus-bot.service
```

---

## 4. Operationelle Befehle (Cheatsheet)

Hier sind die wichtigsten Befehle zur Verwaltung und Überwachung deiner VM.

### Updates & Neustarts

Wenn du Code auf GitHub aktualisiert hast:

```bash
cd /opt/etoro_nautilus
sudo -u tradingbot git pull origin main
sudo -u tradingbot ./venv/bin/pip install -r requirements.txt
sudo systemctl restart nautilus-catalog.service
sudo systemctl restart nautilus-bot.service
```

### Service Management

| Aktion | Befehl |
| :--- | :--- |
| **Status prüfen** | `sudo systemctl status nautilus-bot` |
| **Starten / Stoppen** | `sudo systemctl [start/stop] nautilus-bot` |
| **Neu starten** | `sudo systemctl restart nautilus-bot` |
| **Daten herunterladen** | `scp "username032@yourserver:/opt/etoro_nautilus/data/archive/*.zip" .` |

### Logfile-Analyse (Journalctl)

```bash
# Nur Trading-Bot Logs (Echtzeit)
sudo journalctl -u nautilus-bot.service -f

# Nur Data-Catalog Logs
sudo journalctl -u nautilus-catalog.service -f

# Fehler der letzten 24 Stunden
sudo journalctl -u nautilus-bot.service --since "24h" | grep -i "error"
```


### Momentum-LS Workflow Befehle

```bash
# Manuelle Ausführung des täglichen Workflows
python3 dev_scripts/momentum_ls_universe.py --output data/universe/momentum_ls.json
python3 dev_scripts/momentum_ls_tournament.py --universe data/universe/momentum_ls.json --output logs/tournament_today.json
python3 dev_scripts/momentum_ls_run.py --tournament logs/tournament_today.json --dry-run
```

### Daten-Management Befehle

```bash
# Universe-Datei prüfen
cat data/universe/momentum_ls.json | python3 -m json.tool | head -50

# State-Datei prüfen (aktive Order-Mappings)
cat data/state/execution_mapping.json

# Orphan-Positionen schließen (Notfall)
python3 dev_scripts/etoro_close_orphans.py

# Größe des Datenverzeichnisses prüfen
du -sh /data/nautilus

# Anzahl der gespeicherten Parquet-Dateien zählen
find /data/nautilus -name "*.parquet" | wc -l
```


---

## 5. WebSocket Debugging & Connection Handling

Die asynchrone WebSocket-Verbindung in `adapters/etoro_data.py` ist das kritischste Element des Systems. Hier erfährst du, wie du Verbindungsprobleme identifizierst und behebst.


> **Hinweis:** Das System verwendet absichtlich `os._exit(1)` statt `sys.exit()` oder internen asyncio-Reconnects. Dies ist gewollt, um das System vollständig von systemd neu starten zu lassen (`Restart=always`).

### 5.1. Häufige Probleme erkennen

Achte in den Logs (`journalctl`) auf folgende Indikatoren:
*   **"Connection dropped" / "WebSocket closed":** Die Verbindung zur eToro-API wurde serverseitig getrennt oder es gab ein Netzwerkproblem.
*   **"Timeout during event routing":** Ein Event (Tick oder Bar) brauchte zu lange, um in der `etoro_data.py` verarbeitet zu werden, was die Event-Loop blockiert.
*   **Keine Ticks trotz offener Märkte:** Die Verbindung scheint offen zu sein, aber es fließen keine Daten. Dies deutet oft auf ein stillschweigendes Connection Drop hin.

### 5.2. Debugging-Ansätze

1.  **Loglevel erhöhen:** Stelle sicher, dass die Logs detailliert genug sind. In `etoro_data.py` oder deiner Hauptkonfiguration kannst du das Loglevel des Nautilus Traders auf `DEBUG` setzen, um detaillierte Meldungen der WebSocket-Kommunikation zu erhalten.
2.  **Ping/Pong Monitoring:** Überprüfe, ob die Keep-Alive Pings erfolgreich gesendet und beantwortet werden. Ein fehlender Pong ist ein sicheres Zeichen für eine tote Verbindung.
3.  **Timeout Limits anpassen:**
    *   Wenn deine VM stark ausgelastet ist (CPU > 90%), kann das asynchrone Routing ins Stocken geraten.
    *   In `adapters/etoro_data.py`, prüfe die Konfiguration der `aiohttp` ClientSession oder der WebSocket-Bibliothek. Erhöhe gegebenenfalls die internen Timeout-Werte (z.B. `ping_interval` oder `ping_timeout`).
4.  **Reconnect-Logik validieren:**
    *   Die `etoro_data.py` sollte über eine robuste automatische Reconnect-Schleife verfügen.
    *   Suche im Code nach dem `try/except`-Block um die Haupt-`receive()`-Schleife. Wenn ein `ConnectionClosedError` auftritt, muss ein Exponential Backoff (z. B. Warten von 2, 4, 8 Sekunden) vor dem nächsten Reconnect-Versuch implementiert sein, um Rate-Limits der eToro-API zu vermeiden.
5.  **Event-Loop Blockaden:**
    *   Da Python asynchron arbeitet, darf kein Code in der `on_quote_tick` oder `on_bar` Methode der Strategie (`strategies/...`) "blockieren" (z. B. lange `time.sleep()` oder synchrone Datenbank-Queries).
    *   Blockiert eine Strategie, stauen sich die WebSocket-Events in der `etoro_data.py` und verursachen Timeout-Fehler.

**Checkliste bei hartnäckigen Drops:**
- Prüfen, ob `ETORO_API_KEY` noch gültig ist.
- Prüfen, ob die VM Netzwerkprobleme hat (`ping google.com`).
- RAM-Auslastung prüfen (`free -h`), da OOM-Kills den Prozess stillschweigend beenden können. (Hier hilft das aktivierte Swap-File!).

### 5.3. Konnektivität prüfen
Vor tiefem Debugging sollte immer das Testskript laufen:
```bash
python3 dev_scripts/etoro_connectivity_test.py
```

---

## 6. Datensicherung & Wartung

*   **Parquet-Daten komprimieren:** Um Platz zu sparen, können viele kleine Dateien zusammengefasst werden.
    ```bash
    python3 dev_scripts/compact_parquet.py
    ```
*   **State-Dateien sichern:** Bei kritischen Eingriffen.
    ```bash
    mkdir -p data/state/backup
    cp data/state/execution_mapping.json data/state/backup/
    ```
*   **Logs rotieren:** Entweder manuell bereinigen oder Logrotate konfigurieren.
*   **Orphan Positionen:** Werden durch Bugs oder API-Fehler Orders nicht getrackt, können sie mit `python3 dev_scripts/etoro_close_orphans.py` geschlossen werden.


---
## Weiterführende Dokumente
- `manuals/backtesting_manual.md`
- `manuals/momentum_ls.md`
- `manuals/new_tickers.md`
- `manuals/TESTING.md`

---
*Zuletzt aktualisiert: 2026-05-17 — Überprüft gegen Repository-Stand vom 2026-05-14*
