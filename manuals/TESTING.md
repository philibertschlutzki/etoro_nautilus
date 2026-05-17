# 🧪 Testing & Verifikation: eToro Nautilus

*Zuletzt aktualisiert: 2026-05-17*

---

## 1. Voraussetzungen

- Python 3.10+ (venv aktiviert)
- `.env` konfiguriert (API-Keys gesetzt)
- Abhängigkeiten installiert: `pip install -r requirements.txt`

---

## 2. Konnektivitätstest

Immer als erstes ausführen, bevor echte Orders getestet werden:

```bash
python3 dev_scripts/etoro_connectivity_test.py
```

Erwartete Ausgabe:
Überprüft REST-Authentifizierung, WebSocket-Verbindung, Quote-Feed und liest den aktuellen Kontostand (offene Positionen) aus. Alle Tests sollten mit einem grünen `✅` markiert sein.

---

## 3. API-Diagnose (kein Order-Risiko)

Diese Skripte helfen bei der Untersuchung von API-Problemen, Endpunkt-Änderungen oder Balance-Problemen:

```bash
# Testet alle bekannten eToro REST-Endpoints und sucht den korrekten Balance/Real-Trading Endpunkt.
python3 dev_scripts/etoro_api_probe.py

# (Optional falls vorhanden) Alle Endpunkte aus einer Swagger/OpenAPI Spec testen
python3 dev_scripts/etoro_api_probe_all.py
```

Was geloggt wird:
Statuscodes und JSON-Bodies der verschiedenen eToro API-Endpoints. Hilfreich um zu sehen, ob der API-Key Demo- oder Real-Trading Berechtigungen hat.

---

## 4. Kontostand und Portfolio prüfen

```bash
python3 dev_scripts/etoro_balance.py
```

Erwartete Ausgabe: Gibt den aktuellen Cash-Bestand, das investierte Kapital und eine Liste der offenen Positionen auf dem eToro Konto aus.

---

## 5. Order-Ausführungstests

⚠️ **ACHTUNG:** Diese Skripte platzieren echte Orders auf dem konfigurierten Account. Es wird empfohlen, `environment='demo'` zu nutzen, falls verfügbar.

### 5.1 Einzelner Ping-Pong-Test

```bash
# Erfordert interaktive Bestätigung (Eingabe "j")
python3 dev_scripts/etoro_execution_test.py
```

Vorher sicherstellen:
- `ETORO_API_TEST` in `config/setups.py` (oder wo konfiguriert) ist korrekt gesetzt.
- `environment` und `dry_run` sind auf den gewünschten Wert gesetzt.

### 5.2 Vollständiger Order-Test (alle Order-Typen)

```bash
python3 dev_scripts/etoro_execution_tests_all_orders.py
```

Getestete Order-Typen: Market Buy, Market Sell, Limit Buy, Limit Sell, Cancel.

Cleanup: Das Skript ruft am Ende automatisch `emergency_cleanup()` auf, um offene Positionen und Orders wieder zu schließen.

---

## 6. Unit Tests

Das Repository verwendet `pytest` für automatisierte Modul- und Funktionstests.

```bash
# Alle Unit Tests ausführen
python3 -m pytest tests/ -v
```

Einzelne Test-Dateien:
```bash
python3 -m pytest tests/test_etoro_execution.py -v
python3 -m pytest tests/test_execution.py -v
python3 -m pytest tests/test_momentum_ls_allocator.py -v
python3 -m pytest tests/test_stop_loss_payload.py -v
python3 -m pytest tests/test_tournament_metrics.py -v
python3 -m pytest tests/test_universe_fetcher.py -v
```

---

## 7. Backtesting

Vollständige Anleitung: siehe `manuals/backtesting_manual.md`

Kurzbefehl:
```bash
python3 backtesting/run_backtest.py
```

---

## 8. Momentum-LS Verifikation

Diese Befehle validieren den gesamten Momentum-LS Workflow gefahrlos.

```bash
# Schritt 1: Universe laden (kein Risiko)
python3 dev_scripts/momentum_ls_universe.py --output data/universe/momentum_ls.json

# Schritt 2: Fehlende Daten herunterladen (kein Risiko)
python3 dev_scripts/momentum_ls_fetch_candles_auto.py --universe data/universe/momentum_ls.json

# Schritt 3: Tournament im Trockenlauf (kein Risiko)
python3 dev_scripts/momentum_ls_tournament.py \
    --universe data/universe/momentum_ls.json \
    --output logs/test_tournament.json

# Schritt 4: Live-Bot im Dry-Run (keine echten Orders)
python3 dev_scripts/momentum_ls_run.py \
    --tournament logs/test_tournament.json \
    --dry-run
```

---

## 9. Parquet-Daten prüfen

Zur Analyse der gespeicherten Marktdaten:

```bash
# Vorhandene Parquet-Dateien auflisten und Zeilen zählen
python3 dev_scripts/read_parquet.py

# Mehrere kleine Parquet-Dateien pro Symbol zu einer großen komprimieren
python3 dev_scripts/compact_parquet.py
```

---

## 10. Logs interpretieren

| Log-Datei | Inhalt |
|-----------|--------|
| `logs/bot_*.log` | Live-Trading-Bot Logs (Market Data, Orders, Errors) |
| `logs/daily_run.log` | Output des Cron-Orchestrators (Momentum-LS) |
| `logs/tournament_*.json` | Turnier-Ergebnisse und Parameter der Siegerstrategien |
| `data/state/execution_mapping.json` | Aktive Order-ID-Mappings (Nautilus UUIDs ↔ eToro IDs) |

Relevante Log-Muster:
- `[FEHLER]` / `[ERROR]` → Sofortiger Handlungsbedarf.
- `os._exit(1)` im Log → Systemd hat den Prozess absichtlich neu gestartet (normales Verhalten bei WebSocket-Disconnects).

---
## Weiterführende Dokumente
- `manuals/deployment.md`
- `manuals/backtesting_manual.md`
- `manuals/momentum_ls.md`
- `manuals/new_tickers.md`

---
*Zuletzt aktualisiert: 2026-05-17 — Überprüft gegen Repository-Stand vom 2026-05-14*
