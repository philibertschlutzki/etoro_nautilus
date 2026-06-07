# Deployment & Operations Guide: eToro Nautilus

Dieses Dokument ist das zentrale Handbuch für die Einrichtung, den Betrieb und die Wartung der eToro Nautilus Plattform auf einer Linux-Instanz. Es ist optimiert für Cloud-VMs mit stark limitierten Ressourcen (wie z. B. eine Google Cloud `e2-micro` mit 1 GB RAM) und verwendet eine native Ausführungsumgebung ohne Docker-Overhead.

---

## 1. System-Voraussetzungen & Vorbereitung

Die Architektur ist so konzipiert, dass sie ressourcenschonend arbeitet.

- **Betriebssystem:** Ubuntu 22.04 LTS oder Debian 11/12
- **Hardware:** Mindestens 1 GB RAM (Google Cloud `e2-micro` empfohlen)
- **Software:** Python 3.10+, `git`, `systemd`
- **Python-Abhängigkeiten:** `nautilus_trader>=1.226.0`, `websockets`, `aiohttp`, `python-dotenv`, `requests`, `pandas`, `pyarrow`, `plotly`, `pytest`, `pytest-asyncio`

### 1.1. Swap-File Einrichtung (WICHTIG für 1 GB RAM VMs)

Da die Instanz lediglich 1 GB RAM besitzt, können temporäre Spitzen (z. B. bei der Installation bestimmter pip-Pakete wie `pandas` oder `pyarrow`) zu „Out of Memory" (OOM) Kills führen. Die Einrichtung einer Swap-Datei ist zwingend erforderlich:

```bash
# Erstelle eine 2 GB Swap-Datei
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
mkdir -p data/state data/nautilus data/universe data/import logs
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
sudo -u tradingbot ./venv/bin/pip install -r automation/requirements.txt
```

### 2.1. API-Keys konfigurieren

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

## 3. Prozesssteuerung: 1 systemd-Service + 1 Cron-Job

Die Plattform verwendet eine klare Aufgabentrennung:

- **`automation/catalog_service.py`** läuft als dauerhafter systemd-Service (24/7). Er empfängt Tick-Daten über WebSocket und speichert sie stündlich als ZIP-Dateien unter `data/import/`.
- **`automation/daily_orchestrator.py`** wird einmal täglich per Cron gestartet. Er führt die komplette 5-Phasen-Pipeline aus (Universe → Daten-Merge → Backtest → Tournament → Live-Bot-Start).

> **Warum kein dauerhafter systemd-Service für den Bot?**
> Der Trading-Bot hat einen klaren täglichen Lifecycle (starten, handeln, stoppen). Er wird vom Orchestrator gestartet und beendet sich nach dem Trading-Fenster selbst. Ein dauerhafter Service wäre konzeptionell falsch und würde die Pipeline-Logik des Orchestrators umgehen.

### Dienst: Data-Catalog (`automation/catalog_service.py`)

Erstelle `/etc/systemd/system/nautilus-catalog.service`:

```ini
[Unit]
Description=eToro Nautilus Catalog Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/etoro_nautilus/automation/catalog_service.py
WorkingDirectory=/opt/etoro_nautilus
Restart=always
RestartSec=5
EnvironmentFile=/opt/etoro_nautilus/.env

[Install]
WantedBy=multi-user.target
```

> **Hinweis:** Ersetze `/opt/etoro_nautilus` durch den tatsächlichen Pfad zum Repository auf deiner VM.

### Dienst aktivieren und starten

```bash
sudo systemctl daemon-reload
sudo systemctl enable nautilus-catalog.service
sudo systemctl start nautilus-catalog.service

# Status prüfen:
sudo systemctl status nautilus-catalog.service
```

### Cron-Job: Tägliche Pipeline (`automation/daily_orchestrator.py`)

Richte den täglichen Orchestrator per Cron ein (täglich um 01:00 UTC):

```bash
# Crontab bearbeiten (als Benutzer tradingbot):
sudo -u tradingbot crontab -e
```

Füge folgenden Eintrag hinzu:

```cron
0 1 * * * /usr/local/bin/python3 /opt/etoro_nautilus/automation/daily_orchestrator.py --skip-api-fetch >> /opt/etoro_nautilus/logs/cron.log 2>&1
```

> **Was macht `--skip-api-fetch`?**
> Der `catalog_service.py` sammelt die Daten bereits fortlaufend als ZIP-Dateien im `data/import/`-Verzeichnis. Der Orchestrator kann diese ZIP-Dateien direkt verarbeiten, ohne nochmals die API abzufragen. Wenn `data/import/` leer ist (z. B. nach einem Server-Neustart), lass `--skip-api-fetch` weg — dann holt der Orchestrator die Daten selbst via `api_backfiller.py`.

---

## 4. Operationelle Befehle (Cheatsheet)

### Updates & Neustarts

Wenn du Code auf GitHub aktualisiert hast:

```bash
cd /opt/etoro_nautilus
sudo -u tradingbot git pull origin main
sudo -u tradingbot ./venv/bin/pip install -r automation/requirements.txt
sudo systemctl restart nautilus-catalog.service
```

### Service Management

| Aktion | Befehl |
| :--- | :--- |
| **Status prüfen** | `sudo systemctl status nautilus-catalog` |
| **Starten / Stoppen** | `sudo systemctl [start/stop] nautilus-catalog` |
| **Neu starten** | `sudo systemctl restart nautilus-catalog` |
| **Daten herunterladen** | `scp "user@server:/opt/etoro_nautilus/data/nautilus" ./data/ -r` |

### Logfile-Analyse

```bash
# Catalog-Service Logs (Echtzeit)
sudo journalctl -u nautilus-catalog.service -f

# Fehler der letzten 24 Stunden
sudo journalctl -u nautilus-catalog.service --since "24h" | grep -i "error"

# Orchestrator-Log vom heutigen Tag
tail -f /opt/etoro_nautilus/logs/orchestrator_$(date +%Y%m%d).log

# Live-Bot-Log vom heutigen Tag
tail -f /opt/etoro_nautilus/logs/live_bot_$(date +%Y%m%d).log
```

### Momentum-LS Pipeline manuell ausführen

```bash
# Täglicher Standard-Run (ZIPs aus data/import/ verwenden):
python3 automation/daily_orchestrator.py --skip-api-fetch

# Mit API-Backfill (wenn data/import/ leer):
python3 automation/daily_orchestrator.py

# Dry-Run (kein Bot-Start, sicher zum Testen):
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch

# Einzelne Schritte manuell ausführen:
python3 automation/universe_fetcher.py
python3 automation/api_backfiller.py --days 7
python3 automation/historical_fetcher.py --months 12
python3 automation/catalog_service.py

# Live-Bot manuell starten (nur wenn Orchestrator nicht läuft):
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json
```

### Daten-Management Befehle

```bash
# Universe-Datei prüfen
cat data/universe/momentum_ls.json | python3 -m json.tool | head -50

# State-Datei prüfen (aktive Order-Mappings)
cat data/state/execution_mapping.json

# Größe des Datenverzeichnisses prüfen
du -sh data/nautilus/

# Anzahl der gespeicherten Parquet-Dateien zählen
find data/nautilus/ -name "*.parquet" | wc -l

# Instrument-Map prüfen (wie viele Instrumente bekannt sind)
python3 -c "import json; d=json.load(open('automation/config/instrument_map.json')); print(len(d['instruments']), 'Instrumente')"
```

---

## 5. WebSocket Debugging & Connection Handling

Der `automation/catalog_service.py` empfängt Tick-Daten über WebSocket von der eToro-API. Dieser Abschnitt erklärt, wie du Verbindungsprobleme erkennst und behebst.

> **Hinweis:** Das System verwendet absichtlich `os._exit(1)` statt internen asyncio-Reconnects. Dies ist gewollt, damit systemd den Prozess vollständig neu startet (`Restart=always`). Ein Neustart ist sauberer als ein halbfertiger Reconnect-Versuch.

### 5.1. Häufige Probleme erkennen

Achte in den Logs (`journalctl`) auf folgende Indikatoren:

- **"Connection dropped" / "WebSocket closed":** Die Verbindung zur eToro-API wurde getrennt.
- **"Timeout during event routing":** Ein Event brauchte zu lange zur Verarbeitung und blockierte die Event-Loop.
- **Keine Ticks trotz offener Märkte:** Die Verbindung scheint offen, aber es fließen keine Daten (stillschweigendes Connection Drop).

### 5.2. Debugging-Ansätze

1. **Loglevel erhöhen:** Setze das Loglevel des Nautilus Traders auf `DEBUG` für detaillierte WebSocket-Meldungen.
2. **Ping/Pong Monitoring:** Ein fehlender Pong ist ein sicheres Zeichen für eine tote Verbindung.
3. **Ressourcen prüfen:** RAM-Auslastung via `free -h`. OOM-Kills stoppen den Prozess stillschweigend.

**Checkliste bei hartnäckigen Drops:**
- Prüfen, ob `ETORO_API_KEY` noch gültig ist
- Prüfen, ob die VM Netzwerkprobleme hat (`ping google.com`)
- RAM-Auslastung prüfen (`free -h`) — hier hilft das aktivierte Swap-File

### 5.3. Konnektivität prüfen

```bash
# Verbindungstest ohne Risiko:
python3 -c "from automation.universe_fetcher import is_universe_stale; print('OK')"
```

---

## 6. Datensicherung & Wartung

- **State-Dateien sichern:** Bei kritischen Eingriffen.
  ```bash
  mkdir -p data/state/backup
  cp data/state/execution_mapping.json data/state/backup/
  ```
- **Logs rotieren:** Entweder manuell bereinigen oder Logrotate konfigurieren.
- **Instrument-Map aktualisieren:** Bei neuen eToro-Assets die `automation/config/instrument_map.json` aktualisieren (Details: `manuals/new_tickers.md`).

---

## Weiterführende Dokumente
- [`manuals/backtesting_manual.md`](./backtesting_manual.md) — Backtest-Workflow
- [`manuals/momentum_ls.md`](./momentum_ls.md) — Momentum-LS Pipeline im Detail
- [`manuals/new_tickers.md`](./new_tickers.md) — Neue Instrumente hinzufügen
- [`manuals/TESTING.md`](./TESTING.md) — Tests und Verifikation
- [`manuals/run_bot_manual.md`](./run_bot_manual.md) — Bot-Betrieb und Log-Diagnose

---
*Zuletzt aktualisiert: 2026-06-07 — Überprüft gegen automation/AGENTS.md*
