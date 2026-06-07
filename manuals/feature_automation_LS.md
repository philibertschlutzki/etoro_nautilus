# Anforderungsspezifikation: Automatisierung der Momentum-LS Smart Portfolio Integration

---
**Implementierungsstand (Stand: 2026-06-07)**

| Phase | Status |
|-------|--------|
| Phase 1: Daily Orchestrator | ✅ Umgesetzt (`automation/daily_orchestrator.py`) |
| Phase 2: Dynamisches Mapping | ✅ Umgesetzt |
| Phase 3: Auto-Fetch fehlender Daten | ✅ Umgesetzt |
| Phase 4: Logging & Alerting | ✅ Umgesetzt (`automation/log_manager.py`) |

*(Status anhand des tatsächlichen Code-Stands im Repository — alle Phasen vollständig implementiert)*

---

## 1. Einleitung & Zielsetzung

Ziel dieses Projekts war die Überführung der manuell getriebenen „Momentum-LS Smart Portfolio Integration" in einen vollständig automatisierten, fehlertoleranten Workflow ("Set-and-Forget"). Das System erkennt Portfolio-Umschichtungen (Rebalancing) durch eToro selbstständig, ordnet neue Finanzinstrumente dynamisch zu, lädt fehlende historische Daten nach und startet das tägliche Strategie-Turnier sowie den Live-Bot autonom.

**Alle vier Phasen sind vollständig umgesetzt.** Der Einstiegspunkt ist:
```bash
python3 automation/daily_orchestrator.py --skip-api-fetch
```

---

## 2. Detaillierte Anforderungen und Umsetzungsstatus

### Phase 1: Der "Daily Orchestrator" (Master-Skript) ✅

**Ziel:** Zusammenführung der sequentiellen Einzelschritte in einen überwachten, robusten Gesamtprozess.

**Umgesetzt als:** `automation/daily_orchestrator.py`

Die 5-Phasen-Pipeline läuft vollautomatisch:
1. Universe & Mapping (`universe_fetcher.py`)
2. Multi-ZIP-Import + Merge + API-Backfill (`api_backfiller.py`)
3. Matrix-Backtest (`backtest_runner.py`)
4. Tournament (beste Strategie pro Symbol)
5. Live Deployment (`momentum_ls_run.py`)

Strikte Abhängigkeitsprüfung: Schlägt ein Schritt fehl, wird die Pipeline sofort abgebrochen.

**Cron-Integration:**
```cron
0 1 * * * /usr/local/bin/python3 /path/to/etoro_nautilus/automation/daily_orchestrator.py --skip-api-fetch >> /path/to/etoro_nautilus/logs/cron.log 2>&1
```

**CLI-Optionen:**
```bash
# Täglicher Standard-Run:
python3 automation/daily_orchestrator.py --skip-api-fetch

# Mit API-Backfill (wenn data/import/ leer):
python3 automation/daily_orchestrator.py

# Dry-Run (kein Live-Bot-Start):
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

---

### Phase 2: Dynamisches Mapping neuer eToro-Assets (Auto-Discovery) ✅

**Ziel:** Abschaffung der hartcodierten eToro-IDs und Automatisierung der Asset-Erkennung.

**Umgesetzt als:** `automation/config/instrument_map.json` (JSON-Konfiguration, nicht mehr als Python-Datei)

> **Hinweis für Bestandsnutzer:** Die frühere `adapters/instrument_map.py` ist Legacy. Die aktuelle Mapping-Tabelle liegt ausschließlich unter `automation/config/instrument_map.json`.

Das System gleicht fehlende IDs aus `momentum_ls.json` mit der eToro-Metadaten-API ab und ergänzt die `instrument_map.json` automatisch. Unbekannte Assets werden erkannt, ihr Ticker-Symbol ermittelt und im Format `SYMBOL.ETORO` eingetragen.

---

### Phase 3: Automatischer Download fehlender Historien-Daten ✅

**Ziel:** Lückenlose Parquet-Daten aller Assets für das Backtesting-Tournament.

**Umgesetzt als:** `automation/api_backfiller.py` und `automation/historical_fetcher.py`

```bash
# 7-Tage-Delta-Update:
python3 automation/api_backfiller.py --days 7

# 12-Monate-Deep-Backfill (Erstbefüllung):
python3 automation/historical_fetcher.py --months 12
```

Im Orchestrator läuft Phase 2 automatisch ab: ZIPs werden gemergt, Lücken erkannt und via API-Backfill gefüllt. Erst wenn alle Symbole verifiziert sind, startet der Matrix-Backtest (Phase 3).

---

### Phase 4: Logging, Alerting & Error-Handling ✅

**Ziel:** Transparenz und Alarmierung bei kritischen Systemzuständen.

**Umgesetzt als:** `automation/log_manager.py`

Das Logging-System arbeitet mit strukturierten JSON-Events (LLM-optimiert) neben menschenlesbaren Log-Einträgen:

```
[JSON_EVENT] {"event_type": "ORCHESTRATOR_START", ...}
[JSON_EVENT] {"event_type": "PHASE1_COMPLETE", "universe_size": N, ...}
[JSON_EVENT] {"event_type": "TOURNAMENT_COMPLETE", "winner_count": N, ...}
[JSON_EVENT] {"event_type": "BOT_STARTED", "pid": N, ...}
[JSON_EVENT] {"event_type": "ORCHESTRATOR_EXIT", "exit_code": 0}
```

**Log-Dateien:**
- `logs/orchestrator_YYYYMMDD.log` — Pipeline-Status
- `logs/live_bot_YYYYMMDD.log` — Bot-Laufzeit
- `logs/tournament_YYYY-MM-DD.json` — Turnier-Vollresultat

---

## 3. Akzeptanzkriterien (Definition of Done) — alle erfüllt ✅

- [x] Ein neues Asset im eToro Portfolio führt nicht mehr zum Abbruch oder manuellen Eingriff — es wird erkannt, gemappt, heruntergeladen und im Turnier berücksichtigt.
- [x] Das Gesamtsystem startet über einen einzigen Befehl (`python3 automation/daily_orchestrator.py`) und arbeitet alle Schritte sequentiell ab.
- [x] Ein Fehlschlag (z. B. kein Internet) führt zum sicheren Abbruch der Pipeline.
- [x] Alle Änderungen sind rückwärtskompatibel zum bestehenden Nautilus-Framework.
- [x] Strukturiertes Logging für maschinelle Auswertung und Diagnose.

---

## Weiterführende Dokumente
- [`manuals/momentum_ls.md`](./momentum_ls.md) — Momentum-LS Pipeline im Detail
- [`manuals/end_to_end_workflow.md`](./end_to_end_workflow.md) — Vollständiger 5-Phasen-Workflow
- [`manuals/deployment.md`](./deployment.md) — VM-Setup, systemd und Cron
- [`manuals/run_bot_manual.md`](./run_bot_manual.md) — Tournament-Selektion und Log-Diagnose

---
*Zuletzt aktualisiert: 2026-06-07 — Überprüft gegen automation/AGENTS.md*
