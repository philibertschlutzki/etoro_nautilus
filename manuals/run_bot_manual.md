Hier ist das vollständig überarbeitete und erweiterte Operations Manual. Die Struktur wurde speziell für den ressourceneffizienten Standalone-Betrieb auf einem Raspberry Pi mit 2 GB RAM optimiert, indem das rechen- und speicherintensive Matrix-Backtesting komplett entkoppelt wird. Jedes Thema ist in einem eigenen, logisch aufeinander aufbauenden Kapitel detailliert beschrieben.

---

# Operations Manual: Standalone-Betrieb des Nautilus Trader Live-Bots auf dem Raspberry Pi (eToro)

`run_bot_manual_pi.md` — Technische Dokumentation für den speichereffizienten Live-Betrip ohne lokales Backtesting.

---

## 1. Systemarchitektur & Ressourcen-Optimierung (2GB RAM)

Der Betrieb eines algorithmischen Handelssystems auf einem Einplatinencomputer wie dem Raspberry Pi mit 2 GB RAM erfordert eine strikte Trennung von ressourcenintensiven Entwicklungsarbeiten und der schlanken Live-Ausführung. Während das Matrix-Backtesting (Phase 3) und die Tournament-Gewinnerermittlung (Phase 4) aufgrund ihres hohen Speicherbedarfs und der parallelen CPU-Auslastung auf einer separaten Hochleistungs-Workstation oder einem größeren VPS stattfinden müssen, läuft auf dem Raspberry Pi ausschließlich die schlanke Live-Execution-Engine.

### Speicherreduktion (Shift-Left Prinzip)

Um Out-of-Memory (OOM) Abstürze auf dem Raspberry Pi zu verhindern, wird der `daily_orchestrator.py` im Standalone-Modus betrieben. Es werden keine historischen Candlesticks im RAM aggregiert und keine parallelen Simulations-Subprozesse gestartet. Die Live-Engine lädt die Gewinner-Strategien statisch aus einer vordefinierten Konfigurationsstruktur und verarbeitet eingehende WebSocket-Marktdatenzustände sequentiell über den Rust-Core von Nautilus Trader.

### Aktivierung der isolierten Laufzeitumgebung

Alle Befehle, Skripte und Systemd-Units setzen die Aktivierung der isolierten virtuellen Python-Umgebung voraus, um Versionskonflikte mit dem globalen Betriebssystem zu verhindern.

```bash
cd /home/user/etoro_nautilus
source venv/bin/activate

```

---

## 2. Vorbereitung der Konfigurationsdateien (Workstation → Pi)

Bevor der Bot auf dem Raspberry Pi gestartet werden kann, müssen die Ergebnisse deiner lokalen Strategie-Optimierung auf den Pi übertragen werden. Der Pi führt diese Berechnungen **nicht** selbst aus.

### Erforderliche Transfer-Artefakte

Du musst exakt zwei Dateien von deiner Workstation auf den Raspberry Pi kopieren (z. B. via `scp` oder SFTP):

1. **`data/universe/momentum_ls.json`**: Enthält das aktuelle Asset-Universum und die eToro Instrument-IDs.
2. **`logs/tournament_YYYY-MM-DD.json`**: Das Resultat deines lokalen Optimierungs-Runs. Der Live-Bot liest hieraus ab, welche Strategie für welches Symbol die höchste risikoadjustierte Qualität (Composite Score) erzielt hat.

### Synchronisations-Befehl (Beispiel von Workstation ausführen)

```bash
scp data/universe/momentum_ls.json user@raspberrypi:/home/user/etoro_nautilus/data/universe/
scp logs/tournament_$(date +%Y-%m-%d).json user@raspberrypi:/home/user/etoro_nautilus/logs/

```

### Überprüfung der Importe auf dem Pi

Stelle sicher, dass die Dateien am korrekten Ort liegen und lesbar sind:

```bash
ls -l /home/user/etoro_nautilus/data/universe/momentum_ls.json
ls -l /home/user/etoro_nautilus/logs/tournament_$(date +%Y-%m-%d).json

```

---

## 3. Konfiguration der eToro-API & Umgebungsvariablen

Die Steuerung von Sicherheitsverriegelungen und API-Zugangsdaten erfolgt über Umgebungsvariablen. Auf dem Raspberry Pi wird hierzu eine restriktive `.env`-Datei im Projektwurzelverzeichnis hinterlegt.

### Erstellen und Editieren der `.env`

```bash
nano /home/user/etoro_nautilus/.env

```

### Erforderlicher Inhalt der `.env`-Datei

```ini
# eToro API-Konnektivität
ETORO_API_KEY=dein_erhaltener_api_key_hier
ETORO_USER_KEY=dein_erhaltener_user_key_hier

# Sicherheits-Interlock für den Echtzeithandel
# 0 = Dry-Run-Modus (Simulation auf Live-Daten, keine echten Orders)
# 1 = Live-Trading-Modus (Echte Kapitalausführung auf eToro)
ETORO_CONFIRM_LIVE=0

# System-Integrität
STRICT_PRECISION_FAIL=1

```

> **Sicherheitshinweis für Einsteiger:** Belasse `ETORO_CONFIRM_LIVE` bei der Ersteinrichtung zwingend auf `0`. Schalte den Wert erst auf `1`, wenn der Bot im systemd-Hintergrunddienst über mindestens 24 Stunden fehlerfrei und ohne WebSocket-Verbindungsabrisse im Dry-Run-Modus operiert hat.

---

## 4. Manueller Bot-Start im Standalone-Modus

Der manuelle Start dient primär der ersten Validierung der WebSocket-Verbindungen, der eToro-Authentifizierung und dem fehlerfreien Einlesen der Tournament-Ergebnisse. Hierbei wird das Backtesting explizit übersprungen.

### Der Standalone-Startbefehl

Führe den Bot unter direkter Übergabe der importierten Tournament-Ergebnisse und des Universums aus:

```bash
python3 automation/momentum_ls_run.py \
  --universe /home/user/etoro_nautilus/data/universe/momentum_ls.json \
  --tournament /home/user/etoro_nautilus/logs/tournament_$(date +%Y-%m-%d).json

```

### Erwartete Initialisierungssequenz im Terminal

Achte penibel darauf, dass die folgenden Zeilen beim Start in genau dieser Reihenfolge ohne Fehlermeldung ausgegeben werden:

```
1. [INFO] Lade Tournament-Gewinner aus logs/tournament_2026-06-09.json...
2. [INFO] Strategie registriert: MLS_MeanReversionStrategy_TSLA.ETORO_0
3. TradingNode: STARTING
4. DataClient-ETORO_WS_CLIENT: RUNNING
5. ExecClient-ETORO: RUNNING
6. DataClient-ETORO_WS_CLIENT: WebSocket verbunden. Authentifiziere...
7. ExecClient-ETORO: Connected
8. ExecEngine: Reconciliation for ETORO succeeded
9. TradingNode: RUNNING
10. DataClient-ETORO_WS_CLIENT: Subscribed TSLA.ETORO quotes

```

Wenn am Ende der Sequenz `TradingNode: RUNNING` erscheint, arbeitet die Engine stabil. Du kannst den manuellen Prozess nun mit der Tastenkombination `STRG + C` beenden, um mit der Einrichtung des automatisierten Hintergrunddienstes fortzufahren.

---

## 5. Automatisierung mittels systemd-Hintergrunddienst

Für den unbeaufsichtigten 24/7-Betrieb auf dem Raspberry Pi wird die Ausführung an das Linux-Init-System `systemd` übergeben. Dies garantiert, dass der Bot nach unerwarteten Verbindungsabbrüchen, API-Timeouts oder einem Neustart des Pi automatisch reorganisiert und neu gestartet wird.

### Erstellen der systemd-Service-Unit

Erstelle eine neue Service-Datei mit Root-Rechten:

```bash
sudo nano /etc/systemd/system/nautilus-bot.service

```

### Konfiguration der Service-Unit

Füge den folgenden Block ein. Passe den Benutzernamen (`user`) an dein tatsächliches Pfadprofil an:

```ini
[Unit]
Description=Nautilus Trader Live-Bot Standalone auf eToro
After=network.target

[Service]
Type=simple
User=user
WorkingDirectory=/home/user/etoro_nautilus
Environment=PYTHONPATH=/home/user/etoro_nautilus
ExecStart=/home/user/etoro_nautilus/venv/bin/python3 automation/momentum_ls_run.py --universe /home/user/etoro_nautilus/data/universe/momentum_ls.json --tournament /home/user/etoro_nautilus/logs/tournament_current.json
Restart=always
RestartSec=10
StandardOutput=append:/home/user/etoro_nautilus/logs/systemd_bot.log
StandardError=append:/home/user/etoro_nautilus/logs/systemd_bot.err

[Install]
WantedBy=multi-user.target

```

### Wichtiger Kniff für den automatischen Tageswechsel

Da der Pfad oben auf eine statische Datei verweist, erstellen wir auf dem Pi einen symbolischen Link (Symlink) namens `tournament_current.json`. Dieser zeigt immer auf das aktuellste importierte Turnier-Ergebnis. So muss die systemd-Unit bei einem Datumswechsel nie modifiziert werden:

```bash
ln -sf /home/user/etoro_nautilus/logs/tournament_2026-06-09.json /home/user/etoro_nautilus/logs/tournament_current.json

```

### Dienst aktivieren und starten

```bash
# Systemd-Konfiguration neu laden
sudo systemctl daemon-reload

# Automatischen Start bei Boot aktivieren
sudo systemctl enable nautilus-bot.service

# Bot jetzt im Hintergrund starten
sudo systemctl start nautilus-bot.service

```

---

## 6. Live-Log-Monitoring & Diagnose

Da der Bot unsichtbar im Hintergrund operiert, ist das regelmäßige Auslesen der Log-Streams die einzige Kontrollinstanz für den fehlerfreien Betrieb.

### Echtzeit-Überwachung des Haupt-Streams

Mit diesem Befehl siehst du jede Preisaktualisierung und jede Order-Generierung live im Terminal ein:

```bash
tail -f /home/user/etoro_nautilus/logs/live_bot_$(date +%Y%m%d).log

```

### Filterung nach Fehlern und Warnungen

Unterdrücke das normale Marktdatenrauschen, um kritische Systemereignisse zu prüfen:

```bash
tail -f /home/user/etoro_nautilus/logs/live_bot_$(date +%Y%m%d).log | grep -E "\[WARN\]|\[ERROR\]|\[CRIT\]"

```

### Überwachung des systemd-Prozessstatus

Falls der Bot unerwartet stoppt, liefert der Status-Befehl den exakten Linux-Exit-Code:

```bash
sudo systemctl status nautilus-bot.service

```

---

## 7. Diagnose häufiger Log-Muster auf dem Pi

Beim Betrieb auf dem Raspberry Pi können spezifische Meldungen in den Logs auftreten. Hier erfährst du, was sie bedeuten und wie du reagieren musst.

### Muster A: `⚠️ DRY-RUN MODE: no real orders will be sent.`

* **Bedeutung:** Der Bot läuft im Simulationsmodus. Signale werden berechnet, aber es wird kein echtes Geld bewegt.
* **Ursache:** Die Variable `ETORO_CONFIRM_LIVE=1` fehlt in deiner `.env`-Datei oder wurde nicht korrekt geladen.
* **Lösung:** Wenn du den Bot scharf schalten möchtest, editiere deine `.env` (siehe Kapitel 3), setze den Wert auf `1` und starte den Hintergrunddienst neu:
```bash
sudo systemctl restart nautilus-bot.service

```



### Muster B: `Massenwarnungen: Aggregator for X is currently in use`

* **Bedeutung:** `[WARN] eToro-Momentum-LS.DataEngine: Aggregator for ... is currently in use, subscription can't be started.`
* **Ursache:** Dies ist **kein Fehler** und kein Grund zur Sorge. NautilusTrader erstellt pro Bar-Typ (z. B. 1-Stunden-Kerze) genau einen Daten-Aggregator. Wenn sich mehrere deiner optimierten Strategien für dasselbe Symbol registrieren, erzeugt die erste Instanz den Aggregator. Alle nachfolgenden Instanzen geben diese informative Warnung aus. Alle Strategien erhalten die Daten trotzdem fehlerfrei.

### Muster C: `No tournament winner for X.ETORO. Skipping.`

* **Bedeutung:** Das Asset wird heute im Live-Bot nicht gehandelt.
* **Ursache:** Völlig normales Verhalten für Symbole, die in deinem lokalen Backtest auf der Workstation die Mindestkriterien (z. B. mindestens 20 Trades oder positive Rendite) nicht erfüllt haben. Der Bot überspringt diese Assets beim Start gezielt, um dein Kapital zu schützen.

---

## 8. State Management & Notfall-Abschaltung (Emergency Operations)

Im Live-Handel können unvorhergesehene Ereignisse (z. B. extreme Marktereignisse oder API-Ausfälle bei eToro) ein schnelles und kontrolliertes Eingreifen erforderlich machen.

### 8.1 Der Graceful Shutdown (Kontrolliertes Beenden)

Niemals darf der Bot-Prozess hart mittels `kill -9` abgebrochen werden. Ein unkontrollierter Abbruch führt dazu, dass die lokale State-Datei `data/state/execution_mapping.json` korrumpiert. Der Bot verliert dadurch die Zuordnung darüber, welche offene Position auf eToro zu welcher lokalen Strategie gehört.

**Korrekter, sanfter Abbruch:**

```bash
sudo systemctl stop nautilus-bot.service

```

*Auswirkung:* Der systemd-Dienst sendet ein `SIGTERM`-Signal an den Python- und Rust-Core. Die Engine wartet ausstehende Order-Bestätigungen ab, trennt die WebSocket-Verbindung sauber vom eToro-Server und schreibt den aktuellen Positions-Zustand konsistent auf die SD-Karte des Pi.

### 8.2 Der absolute Notfall-Stopp (Hard Kill)

Sollte der Bot auf ein `SIGTERM` (Stop-Befehl) über mehr als 30 Sekunden nicht reagieren (z. B. bei einem lokalen Thread-Lock), muss der Prozess erzwungen beendet werden:

```bash
# Prozess hart beenden
sudo kill -9 $(cat /home/user/etoro_nautilus/logs/live_bot.pid)

# Dienst im systemd zurücksetzen
sudo systemctl stop nautilus-bot.service

```

> **CRITICAL OPERATIONAL REQUIREMENT:** Nach einem harten `kill -9` musst du dich **zwingend** sofort über den Webbrowser oder die mobile App auf der eToro-Plattform einloggen. Überprüfe alle offenen Positionen manuell gegen deine Risikoparameter und schließe sie gegebenenfalls von Hand, da die lokale Status-Integrität des Bots für diesen Tag nicht mehr garantiert ist.

---

## 9. Quick-Reference: Wichtige Pfade auf dem Raspberry Pi

Für die tägliche Wartung findest du hier alle relevanten Pfade und Verzeichnisse auf einen Blick:

```
Projekt-Wurzelverzeichnis:
  /home/user/etoro_nautilus/

Konfigurations- und Importdaten:
  .env                                      ← API-Keys & Sicherheits-Interlock (LIVE/DRY)
  data/universe/momentum_ls.json           ← Das von der Workstation importierte Asset-Universum
  logs/tournament_current.json             ← Symlink auf das aktive optimierte Tournament-Ergebnis

Lokale State-Überwachung (Wichtig für Integrität):
  data/state/execution_mapping.json        ← Live-Verknüpfung eToro-Order-IDs ↔ Nautilus-Bot
  logs/live_bot.pid                        ← Aktuelle Prozess-ID des laufenden Hintergrund-Bots

Log-Dateien für die Fehleranalyse:
  logs/live_bot_YYYYMMDD.log              ← Haupt-Monitoring-Stream der Handelsstrategien
  logs/systemd_bot.log                     ← Standard-Konsolenausgabe des systemd-Dienstes
  logs/systemd_bot.err                     ← System-Fehlermeldungen und Python-Crash-Dumps

```
