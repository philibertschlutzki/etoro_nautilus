```python
# We need to read the previous generated content or re-create it exactly, ensuring it is 100% complete and pristine as requested.
content = """# Operations Manual: Nautilus Trader Live-Bot (eToro)
`run_bot_manual.md` — Technische Dokumentation für den manuellen Betrieb und die Systemwartung.

---

## 1. Systemarchitektur & Laufzeitumgebung

Der Live-Trading-Bot und die dazugehörigen Teilsysteme zur Marktdaten-Aggregierung operieren auf einer dedizierten virtuellen Maschine (VM). Sämtliche manuellen Interaktionen, Skriptaufrufe und administrativen Tasks setzen zwingend die Aktivierung des isolierten Python Virtual Environments (`venv`) voraus, um Abhängigkeitskonflikte zu vermeiden.

### Environment-Initialisierung:

```

```text
Pristine run_bot_manual.md written.

```bash
cd /home/user/etoro_nautilus
source venv/bin/activate

```

---

## 2. Prozessmanagement & Live-Monitoring

Der Live-Bot wird im Regelfall im Rahmen der automatisierten Pipeline durch den `daily_orchestrator.py` initiiert (Phase 5) und läuft als entkoppelter Hintergrundprozess. Für das manuelle Lifecycle-Management gelten folgende Mechanismen:

### 2.1 PID-Tracking & Statusüberwachung

Die Prozess-ID (PID) des aktiven Bots wird zur Laufzeit in einer dedizierten Datei gespeichert.

* **Pfad zur PID-Datei:** `/home/user/etoro_nautilus/logs/live_bot.pid`
* **Zustandsprüfung des Prozesses:**
```bash
ps -p $(cat /home/user/etoro_nautilus/logs/live_bot.pid)

```



### 2.2 Log-Analyse & Monitoring-Streams

Zur Überwachung der Order-Execution, WebSocket-Verbindungen und Adapter-Stabilität stehen zwei primäre Log-Schnittstellen zur Verfügung:

1. **Live-Bot Anwendungslog (Strategie- und Adapter-Ebene):**
```bash
tail -f /home/user/etoro_nautilus/logs/live_bot_$(date +%Y%m%d).log

```


2. **Nautilus Trader Core Engine Log (Kern-Zustandsänderungen):**
```bash
tail -f /home/user/etoro_nautilus/logs/nautilus_mls_*.log

```


*Hier werden die Low-Level-Zustände der `DataEngine`, `RiskEngine` und `ExecEngine` protokolliert.*

---

## 3. Essentielle Wartungsskripte (Manual Run Execution)

Bei manuellen Eingriffen, unerwarteten Stopps oder Systemrestarts muss die Ausführung der Skripte in der exakt vorgegebenen Reihenfolge erfolgen.

### 3.1 Instrumenten- und Universe-Aktualisierung

* **Skript:** `automation/universe_fetcher.py`
* **Kritikalität:** **Kritisch (Täglich)**
* **Beschreibung:** Die Nautilus Core Engine deklariert Instrumentendaten, deren Metadaten-Zeitstempel älter als 24 Stunden ist (`fetched_at > 24 hours ago`), als *Stale*. Ein Start mit veralteten Daten führt dazu, dass Ticker-Abonnements über die eToro-WebSockets übersprungen werden und betroffene Instrumente unaufgelöst bleiben.
* **Manuelles Kommando:**
```bash
python3 automation/universe_fetcher.py

```


* **Auswirkung:** Aktualisiert die Konfigurationsdatei `/home/user/etoro_nautilus/data/universe/momentum_ls.json`.

### 3.2 Datenintegrität & Historisches Backfilling

* **Skripte:** `check_data.py` und `automation/historical_fetcher.py`
* **Beschreibung:** Stellt sicher, dass die lokalen `.parquet`-Dateien im Nautilus-Katalog lückenlose Tick- und Bar-Daten aufweisen.
* **Integritätsprüfung:**
```bash
python3 check_data.py

```


*Scannt das Verzeichnis `data/nautilus/data/cfd/` auf korrupte Dateien.*
* **Historischer API-Abruf (Backfill):**
```bash
python3 automation/historical_fetcher.py

```


*Wichtig: Das Skript nutzt den `etoro_rate_limiter.py`, um API-Penalties seitens eToro zu verhindern.*

### 3.3 Isolierter Manueller Bot-Start

Sollte der automatische Orchestrator fehlschlagen, kann der Bot direkt mit der Angabe des aktuellen Tournament-Ergebnisses gestartet werden:

```bash
python3 automation/momentum_ls_run.py \
  --universe /home/user/etoro_nautilus/data/universe/momentum_ls.json \
  --tournament /home/user/etoro_nautilus/logs/tournament_$(date +%Y-%m-%d).json

```

---

## 4. State Management & Emergency Operations

### 4.1 Graceful Shutdown-Routine

Ein unkontrollierter Abbruch (z. B. via `kill -9`) beschädigt potentiell das Mapping aktiver Orders innerhalb der zustandsbehafteten Komponenten des eToro-Adapters.

* **Prozedur:** Sende ein `SIGTERM`-Signal (Signal 15), um der Engine das saubere Schließen aller eToro-WebSocket-Kanäle und das Persistieren des Order-Zustands zu erlauben.
```bash
kill -15 $(cat /home/user/etoro_nautilus/logs/live_bot.pid)

```


* **Verifizierung:** Prüfe im `live_bot`-Log das Schreiben des Events `ORCHESTRATOR_EXIT` mit Code `0`.

### 4.2 Behebung von API Rate-Limits & WebSocket-Disconnects

Falls eToro HTTP-429-Fehler (Too Many Requests) wirft oder WebSocket-Verbindungen permanent abbrechen:

1. **Sofortiger kontrollierter Stopp:** Bot über `kill -15` herunterfahren.
2. **Cooldown-Phase:** Mindestens 15 Minuten warten, um den IP-basierten Request-Counter bei eToro zurückzusetzen.
3. **Validierung des lokalen Zustands:** Die Datei `/home/user/etoro_nautilus/data/state/execution_mapping.json` auf verwaiste (*Orphaned*) Orders prüfen, die serverseitig bereits ausgeführt wurden, aber lokal noch als offen gelistet sind.
4. **Normaler Neustart:** Ausführung des `daily_orchestrator.py` zur Wiederaufnahme des automatisierten Betriebs.
"""

with open("run_bot_manual.md", "w", encoding="utf-8") as f:
f.write(content)
print("Pristine run_bot_manual.md written.")

```
Die finale, vollständig formatierte Markdown-Datei `run_bot_manual.md` ist bereit und detailliert ausgearbeitet. 

[file-tag: code-generated-file-0-1780087808791606892]

Die Struktur ist in 4 dedizierte Blöcke aufgeteilt, die genau festhalten, welche Pfade, Skripte, und Kommandos bei Systemstörungen und zur Routine-Wartung angewandt werden müssen (besonders bei der Behebung von "Stale Data" und der Handhabung der PID).

```
