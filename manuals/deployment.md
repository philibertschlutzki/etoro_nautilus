# ☁️ Deployment & Operations Guide: eToro Nautilus

Dieses Dokument ist das zentrale Handbuch für die Einrichtung, den Betrieb und die Wartung der eToro Nautilus Plattform auf einer Linux-Instanz. Es ist optimiert für Cloud-VMs mit stark limitierten Ressourcen (wie z. B. eine Google Cloud `e2-micro` mit 1GB RAM) und verwendet eine native Ausführungsumgebung ohne Docker-Overhead.

---

## 1. System-Voraussetzungen & Vorbereitung

Die Architektur ist so konzipiert, dass sie ressourcenschonend arbeitet.

*   **Betriebssystem:** Ubuntu 22.04 LTS oder Debian 11/12.
*   **Hardware:** Mindestens 1 GB RAM (Google Cloud `e2-micro` empfohlen).
*   **Software:** Python 3.10+, `git`, `systemd`.

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

### Logfile-Analyse (Journalctl)

```bash
# Nur Trading-Bot Logs (Echtzeit)
sudo journalctl -u nautilus-bot.service -f

# Nur Data-Catalog Logs
sudo journalctl -u nautilus-catalog.service -f

# Fehler der letzten 24 Stunden
sudo journalctl -u nautilus-bot.service --since "24h" | grep -i "error"
```

### Daten-Management

```bash
# Größe des Datenverzeichnisses prüfen
du -sh /data/nautilus

# Anzahl der gespeicherten Parquet-Dateien zählen
find /data/nautilus -name "*.parquet" | wc -l
```

---

## 5. WebSocket Debugging & Connection Handling

Die asynchrone WebSocket-Verbindung in `adapters/etoro_data.py` ist das kritischste Element des Systems. Hier erfährst du, wie du Verbindungsprobleme identifizierst und behebst.

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
