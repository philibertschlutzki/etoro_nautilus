# Testing & Verifikation: eToro Nautilus

*Zuletzt aktualisiert: 2026-06-07*

---

## 1. Voraussetzungen

- Python 3.10+ (venv aktiviert)
- `.env` konfiguriert (API-Keys gesetzt)
- Abhängigkeiten installiert: `pip install -r automation/requirements.txt`

---

## 2. Konnektivitätstest

Immer als erstes ausführen, bevor echte Orders getestet werden:

```bash
python3 dev_scripts/etoro_connectivity_test.py
```

Erwartete Ausgabe: Überprüft REST-Authentifizierung, WebSocket-Verbindung, Quote-Feed und liest den aktuellen Kontostand (offene Positionen) aus. Alle Tests sollten mit einem grünen `OK` markiert sein.

---

## 3. API-Diagnose (kein Order-Risiko)

Diese Skripte helfen bei der Untersuchung von API-Problemen, Endpunkt-Änderungen oder Balance-Problemen:

```bash
# Testet alle bekannten eToro REST-Endpoints und sucht den korrekten Balance/Real-Trading Endpunkt.
python3 dev_scripts/etoro_api_probe.py

# (Optional falls vorhanden) Alle Endpunkte aus einer Swagger/OpenAPI Spec testen
python3 dev_scripts/etoro_api_probe_all.py
```

Was geloggt wird: Statuscodes und JSON-Bodies der verschiedenen eToro API-Endpoints. Hilfreich, um zu sehen, ob der API-Key Demo- oder Real-Trading-Berechtigungen hat.

---

## 4. Kontostand und Portfolio prüfen

```bash
python3 dev_scripts/etoro_balance.py
```

Erwartete Ausgabe: Gibt den aktuellen Cash-Bestand, das investierte Kapital und eine Liste der offenen Positionen auf dem eToro-Konto aus.

---

## 5. Order-Ausführungstests

> **ACHTUNG:** Diese Skripte platzieren echte Orders auf dem konfigurierten Account. Es wird dringend empfohlen, `environment='demo'` zu nutzen.

### 5.1 Einzelner Ping-Pong-Test

```bash
# Erfordert interaktive Bestätigung (Eingabe "j")
python3 dev_scripts/etoro_execution_test.py
```

### 5.2 Vollständiger Order-Test (alle Order-Typen)

```bash
python3 dev_scripts/etoro_execution_tests_all_orders.py
```

Getestete Order-Typen: Market Buy, Market Sell, Limit Buy, Limit Sell, Cancel.

Das Skript ruft am Ende automatisch `emergency_cleanup()` auf, um offene Positionen wieder zu schließen.

---

## 6. Unit Tests

Das Repository verwendet `pytest` für automatisierte Modul- und Funktionstests.

```bash
# Alle Unit Tests ausführen
python3 -m pytest automation/tests/ -v
```

Einzelne Test-Dateien:
```bash
python3 -m pytest automation/tests/test_etoro_execution.py -v
python3 -m pytest automation/tests/test_execution.py -v
python3 -m pytest automation/tests/test_momentum_ls_allocator.py -v
python3 -m pytest automation/tests/test_stop_loss_payload.py -v
python3 -m pytest automation/tests/test_tournament_metrics.py -v
python3 -m pytest automation/tests/test_universe_fetcher.py -v
```

Alle Tests respektieren das Standalone-Prinzip des `automation/`-Pakets (geprüft via AST in `test_automation_isolation.py`).

---

## 7. Backtesting

Vollständige Anleitung: `manuals/backtesting_manual.md`

Das Backtesting läuft jetzt vollständig über den Orchestrator (Phasen 3+4). Der frühere direkte Aufruf von `backtesting/run_backtest.py` ist Legacy.

```bash
# Dry-Run: Führt Backtest + Tournament aus, startet aber keinen Live-Bot:
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

---

## 8. Momentum-LS Verifikation

Diese Befehle validieren den gesamten Momentum-LS Workflow gefahrlos.

```bash
# Schritt 1: Universe laden (kein Risiko)
python3 automation/universe_fetcher.py

# Schritt 2: Fehlende Daten herunterladen (kein Risiko)
python3 automation/api_backfiller.py --days 7

# Schritt 3: Backtest + Tournament im Trockenlauf (kein Risiko, kein Bot-Start)
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch

# Schritt 4: Live-Bot manuell im Dry-Run (keine echten Orders)
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json
```

> **Hinweis:** Der `momentum_ls_run.py` startet ohne `ETORO_CONFIRM_LIVE=1` automatisch im Dry-Run-Modus — keine echten Orders werden platziert.

---

## 9. Pre-Flight-Checks (schnelle Systemverifikation)

```bash
# Modul-Imports prüfen:
python3 -c "from automation.backtest_runner import read_precisions_from_parquet; print('OK')"
python3 -c "from automation.universe_fetcher import is_universe_stale; print('OK')"

# Instrument-Map prüfen:
python3 -c "import json; d=json.load(open('automation/config/instrument_map.json')); print(len(d['instruments']), 'Instrumente bekannt')"

# Universe-Aktualität prüfen:
python3 -c "
from automation.universe_fetcher import is_universe_stale
print('Universe stale:', is_universe_stale())
"
```

---

## 10. Parquet-Daten prüfen

Zur Analyse der gespeicherten Marktdaten:

```bash
# Vorhandene Parquet-Dateien auflisten und Zeilen zählen
python3 dev_scripts/read_parquet.py

# Mehrere kleine Parquet-Dateien pro Symbol zu einer großen komprimieren
python3 dev_scripts/compact_parquet.py
```

---

## 11. Logs interpretieren

| Log-Datei | Inhalt |
|-----------|--------|
| `logs/orchestrator_YYYYMMDD.log` | Pipeline-Phasen-Status, JSON-Events, Fehler |
| `logs/live_bot_YYYYMMDD.log` | Live-Trading-Bot (Market Data, Orders, Errors) |
| `logs/tournament_YYYY-MM-DD.json` | Turnier-Ergebnisse und Parameter der Siegerstrategien |
| `data/state/execution_mapping.json` | Aktive Order-ID-Mappings (Nautilus UUIDs ↔ eToro IDs) |

Relevante Log-Muster:
- `[FEHLER]` / `[ERROR]` → Sofortiger Handlungsbedarf.
- `os._exit(1)` im Log → systemd hat den Prozess absichtlich neu gestartet (normales Verhalten bei WebSocket-Disconnects).
- `[JSON_EVENT] {"event_type": "ORCHESTRATOR_EXIT", "exit_code": 0}` → Pipeline erfolgreich abgeschlossen.

---

## Weiterführende Dokumente
- [`manuals/deployment.md`](./deployment.md) — VM-Setup und systemd
- [`manuals/backtesting_manual.md`](./backtesting_manual.md) — Backtesting-Details
- [`manuals/momentum_ls.md`](./momentum_ls.md) — Momentum-LS Workflow
- [`manuals/new_tickers.md`](./new_tickers.md) — Neue Instrumente hinzufügen

---
*Zuletzt aktualisiert: 2026-06-07 — Überprüft gegen automation/AGENTS.md*
