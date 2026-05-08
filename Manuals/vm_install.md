### Cloud-VM Installationshandbuch: eToro Nautilus

Dieses Dokument beschreibt die vollständige Einrichtung der eToro Nautilus Plattform auf einer Linux-Instanz (optimiert für Ubuntu/Debian auf Google Cloud `e2-micro`).

#### 1. System-Voraussetzungen

* **Betriebssystem:** Ubuntu 22.04 LTS oder Debian 11/12.
* **Hardware:** Mindestens 1 GB RAM (z.B. Google Cloud `e2-micro`).
* **Software:** Python 3.10 oder neuer.

#### 2. Vorbereitung des Systems

Aus Sicherheitsgründen wird der Bot unter einem eigenen Systembenutzer ohne Root-Rechte ausgeführt.

```bash
# System aktualisieren
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git

# Systembenutzer anlegen
sudo useradd -r -s /bin/false tradingbot

# Verzeichnisse erstellen
sudo mkdir -p /opt/etoro_nautilus    # Applikations-Code
sudo mkdir -p /data/nautilus         # Datenbank für Parquet-Files

```

#### 3. Installation des Codes

Der Code wird direkt in das Zielverzeichnis geklont und die Python-Umgebung initialisiert.

```bash
cd /opt/etoro_nautilus
sudo git clone https://github.com/philibertschlutzki/etoro_nautilus.git .
sudo chown -R tradingbot:tradingbot /opt/etoro_nautilus

# Virtual Environment als 'tradingbot' erstellen
sudo -u tradingbot python3 -m venv venv
sudo -u tradingbot ./venv/bin/pip install --upgrade pip
sudo -u tradingbot ./venv/bin/pip install -r requirements.txt

# Berechtigungen für das Datenverzeichnis setzen
sudo chown -R tradingbot:tradingbot /data/nautilus

```

#### 4. Konfiguration

Erstelle die `.env`-Datei mit deinen eToro API-Zugangsdaten im Verzeichnis `/opt/etoro_nautilus/`.

```bash
sudo -u tradingbot nano .env

```

Inhalt der Datei:

```env
ETORO_API_KEY=DEIN_API_KEY
ETORO_USER_KEY=DEIN_USER_KEY

```

#### 5. Systemd-Dienste einrichten

Wir nutzen eine **Two-Service-Architektur**, um die Datenaufzeichnung (`catalog`) strikt von der Handelslogik (`bot`) zu trennen.

**Dienst 1: Data-Catalog (Datenaufzeichnung)**
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

**Dienst 2: Trading-Bot (Handelslogik)**
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

#### 6. Dienste aktivieren und Überwachen

```bash
# Konfiguration laden und Dienste aktivieren
sudo systemctl daemon-reload
sudo systemctl enable nautilus-catalog.service nautilus-bot.service

# Dienste starten
sudo systemctl start nautilus-catalog.service
sudo systemctl start nautilus-bot.service

# Status prüfen
sudo systemctl status nautilus-catalog.service
sudo systemctl status nautilus-bot.service

```

#### 7. Log-Einsicht (Fehlersuche)

Um die Ausgaben der Bots in Echtzeit zu verfolgen:

```bash
# Nur Trading-Bot Logs
sudo journalctl -u nautilus-bot.service -f

# Nur Data-Catalog Logs
sudo journalctl -u nautilus-catalog.service -f

```

---

Diese Struktur stellt sicher, dass dein System stabil läuft und nach einem Server-Neustart automatisch beide Dienste in der richtigen Reihenfolge wieder aufnimmt.
