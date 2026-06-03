# Operations Manual: Nautilus Trader Live-Bot (eToro)
`run_bot_manual.md` — Technische Dokumentation für manuellen Betrieb, Systemwartung und Log-Diagnose.

---

## Inhaltsverzeichnis

1. [Systemarchitektur & Laufzeitumgebung](#1-systemarchitektur--laufzeitumgebung)
2. [Tournament-Selektion: Wie Gewinner entstehen](#2-tournament-selektion-wie-gewinner-entstehen)
3. [Log-Monitoring-Guide](#3-log-monitoring-guide)
4. [Diagnose häufiger Log-Muster](#4-diagnose-häufiger-log-muster)
5. [Prozessmanagement & Live-Monitoring](#5-prozessmanagement--live-monitoring)
6. [Essentielle Wartungsskripte](#6-essentielle-wartungsskripte)
7. [State Management & Emergency Operations](#7-state-management--emergency-operations)

---

## 1. Systemarchitektur & Laufzeitumgebung

Der Live-Trading-Bot und die Teilsysteme zur Marktdaten-Aggregierung operieren auf einer dedizierten VM. Alle manuellen Interaktionen setzen die Aktivierung des isolierten Python Virtual Environments voraus.

```bash
cd /home/user/etoro_nautilus
source venv/bin/activate
```

### Systemkomponenten im Überblick

| Komponente | Skript | Verantwortlichkeit |
|---|---|---|
| Orchestrator | `daily_orchestrator.py` | End-to-End-Pipeline (5 Phasen) |
| API-Backfiller | `api_backfiller.py` | 7-Tage-Lückenfüllung via REST |
| Historical Fetcher | `historical_fetcher.py` | Deep Backfill bis 12 Monate |
| Backtest Runner | `backtest_runner.py` | Matrix-Backtest + Tournament |
| Live Bot | `momentum_ls_run.py` | Live-Execution via NautilusTrader |
| Catalog Service | `catalog_service.py` | 24/7 WebSocket Tick-Sammlung |

---

## 2. Tournament-Selektion: Wie Gewinner entstehen

Der Tournament-Prozess läuft in **Phase 3+4** des Orchestrators als separater Subprocess (`backtest_runner.py`). Er bestimmt vollautomatisch, welche Strategie für welches Instrument live deployt wird.

### 2.1 Eingabe: Das Backtesting-Universum

Der Backtest läuft über ein **Zeitfenster von 30 Tagen** (konfigurierbar über `backtest.json`: `is_window_days` + `oos_window_days`). Jedes Instrument × jede Strategie bildet einen eigenen **Backtest-Job**, der parallel über `ProcessPoolExecutor(max_workers=min(cpu//2, 6))` abgearbeitet wird.

**Aktive Strategien** (aus `strategies.json`, `active=true`):
- `SmaCrossoverStrategy` — SMA(5)-Crossover
- `MeanReversionStrategy` — Keltner-Channel-Reversion
- `DynamicBreakoutStrategy` — Price-Range-Breakout
- `FlashCrashReversalStrategy` — BB(10) + RSI(7) Reversal
- `VolatilityBreakoutPumpStrategy` — BB(10)-Pump
- `ComboTrendVwapStrategy` — SMA + MACD + BB + ATR + VWAP
- `VwapExhaustionStrategy` — VWAP-Deviation

### 2.2 Metriken-Extraktion pro Job

Nach der Backtest-Engine wird `extract_metrics()` aufgerufen. Der Prozess:

1. **FIFO-Matching** über alle generierten Fills (Entry/Exit-Paare werden via FIFO zugeordnet).
2. **IS/OOS-Split:** Die PnL-Tupel `(pnl, ts_event, holding_time, match_qty)` werden anhand des konfigurierten `oos_start_ns`-Cutoffs aufgeteilt. Das FIFO-Matching läuft zwingend über das **gesamte** Dataset, erst danach erfolgt der Split — andernfalls würden offene Queues korrumpieren (Pitfall #32).
3. **Kennzahlenberechnung** aus den IS-PnLs:

| Kennzahl | Berechnung | Mindestanforderung |
|---|---|---|
| `total_trades` | Anzahl geschlossener Positionen | ≥ 20 |
| `total_return` | Σ PnL / start_capital × 100 | > 0 % |
| `sortino` | Ø(PnL) / Std(neg. PnL) × √(Jahresperioden) | ≥ 0.3 (any) |
| `profit_factor` | Σ(Gewinne) / Σ(Verluste) | ≥ 1.1 (any) |
| `win_rate` | Gewinner / total_trades | — |
| `max_drawdown` | Max. kumulativer Rückgang | — |

> **Wichtig:** Sortino wird nur berechnet, wenn n ≥ 5 Trades mit negativem PnL vorhanden sind. Bei `PF=999` handelt es sich um einen Artefakt aus Runs ohne Verluste — diese werden im Tournament penalisiert.

### 2.3 Eligibilitätsprüfung (Doppeltes Gate)

Ein Instrument-Strategie-Paar gilt als **eligible**, wenn **alle** der folgenden Bedingungen erfüllt sind:

```
eligible_requires_all:
    min_trades       >= 20     ← statistische Mindestbasis
    min_total_return  > 0 %    ← profitabel im IS-Fenster

eligible_requires_any:
    min_sortino      >= 0.3   ← ODER
    min_profit_factor >= 1.1   ← risikoadjustierte Qualität
```

Diese Konfiguration liegt in `automation/config/tournament.json`.

### 2.4 Score-Berechnung & Gewinnerselektion

Pro Symbol wird der **Score** aller eligiblen Strategien berechnet:

```
Score = sortino × 0.4
      + profit_factor × 0.3
      + win_rate × 0.2
      - max_drawdown × 0.1
```

Die Strategie mit dem **höchsten Score** gewinnt für dieses Symbol. Bei Gleichstand entscheidet der `sortino`-Wert.

### 2.5 Aggregierter Gewinner & OOS-Gate

Nach der Per-Symbol-Selektion wird ein **aggregierter Gewinner** ermittelt:
- Strategie mit den **meisten Symbol-Wins** gewinnt
- Tie-Break: **Median Sortino** über alle Wins

**OOS-Gate (Phase 5):** Bevor der Bot gestartet wird, prüft der Orchestrator die OOS-Performance des aggregierten Gewinners. Ist die OOS-Rendite negativ (`oos_return < 0`), blockiert das Gate den Live-Deploy (Fail-Closed).

### 2.6 Beispiel aus dem aktuellen Run (2026-06-03)

```
49 Symbole getestet × 7 Strategien = 343 Backtest-Jobs
→ 47 eligible Paare (min_trades ≥ 20 + return > 0)
→ 22 Gewinner-Symbole (die restlichen haben keine eligiblen Strategien)
→ MeanReversionStrategy: 19 Wins, Median Sortino: 9.04
→ OOS-Gate: BESTANDEN → Bot-Start
```

Die 27 Symbole ohne Gewinner (CPRT, WDAY, FISV etc.) werden im Live-Bot **übersprungen** — daher die WARNING-Zeilen `No tournament winner for X.ETORO. Skipping.` zu Beginn des Bot-Logs.

---

## 3. Log-Monitoring-Guide

### 3.1 Übersicht aller relevanten Log-Dateien

| Log-Datei | Erzeugt von | Inhalt | Kritikalität |
|---|---|---|---|
| `logs/orchestrator_YYYYMMDD.log` | `daily_orchestrator.py` | Phasen-Status, JSON-Events, Backtest-Summary | **Hoch** |
| `logs/live_bot_YYYYMMDD.log` | `momentum_ls_run.py` | Strategie-Registrierung, WS-Status, Order-Events | **Hoch** |
| `logs/tournament_YYYY-MM-DD.json` | `backtest_runner.py` | Vollständige Metriken aller IS/OOS-Jobs | **Mittel** |
| `logs/backtest_*.log` | `backtest_runner.py` | Subprocess-Output des Matrix-Backtests | **Mittel** |
| `logs/live_bot.pid` | `momentum_ls_run.py` | Aktuelle PID des laufenden Bots | Referenz |

### 3.2 Orchestrator-Log: Was zu beachten ist

**Kritische JSON-Events** (immer prüfen):

```
[JSON_EVENT] {"event_type": "ORCHESTRATOR_START", ...}     ← Startbedingungen
[JSON_EVENT] {"event_type": "PHASE1_COMPLETE", ...}        ← universe_size, unmapped_count
[JSON_EVENT] {"event_type": "PHASE2_COMPLETE", ...}        ← api_filled_count
[JSON_EVENT] {"event_type": "TOURNAMENT_COMPLETE", ...}    ← winner_count, aggregate_winner
[JSON_EVENT] {"event_type": "BOT_STARTED", ...}            ← PID des Live-Bots
[JSON_EVENT] {"event_type": "ORCHESTRATOR_EXIT", ...}      ← exit_code: 0 = OK
```

**Alarmsignale im Orchestrator-Log:**

| Muster | Bedeutung | Aktion |
|---|---|---|
| `[Phase 1] Universe-Daten sind Xh alt (> 24h)` | Universe-Fetch schlägt fehl oder nie gelaufen | `universe_fetcher.py` manuell ausführen |
| `Backtest beendet (Exit-Code: ≠ 0)` | Backtest-Subprocess gecrasht | `logs/backtest_*.log` analysieren |
| `OOS-GATE GESCHEITERT` | Aggregate-Winner OOS negativ | Keine Live-Deployment; Daten prüfen |
| `Precision-API lieferte 0/N Instrumente` | eToro-API antwortet nicht korrekt | Fallback aktiv, aber API-Konnektivität prüfen |
| `exit_code: 1` im ORCHESTRATOR_EXIT | Pipeline fehlgeschlagen | Log vollständig auf `ERROR`-Zeilen durchsuchen |

### 3.3 Live-Bot-Log: Was zu beachten ist

Der Live-Bot-Log enthält zwei verschachtelte Log-Streams mit unterschiedlichem Format:

```
# Python-Logger (momentum_ls_run.py):
2026-06-03 17:11:40,416 [INFO] Strategie registriert: MLS_MeanReversionStrategy_ESLT.ETORO_0

# NautilusTrader Rust-Core (nanosekunden-präzise):
2026-06-03T15:11:40.416267481Z [INFO] eToro-Momentum-LS.MLS_MeanReversionStrategy_ESLT.ETORO_0: READY
```

**Kritische Sequenz beim Start — muss in dieser Reihenfolge erscheinen:**

```
1. TradingNode: STARTING
2. DataClient-ETORO_WS_CLIENT: RUNNING
3. ExecClient-ETORO: RUNNING
4. DataClient-ETORO_WS_CLIENT: WebSocket verbunden. Authentifiziere...
5. ExecClient-ETORO: Connected
6. TradingNode: Awaiting execution state reconciliation (30.0s timeout)...
7. ExecEngine: Reconciliation for ETORO succeeded
8. TradingNode: RUNNING
9. DataClient-ETORO_WS_CLIENT: Subscribed SYMBOL.ETORO quotes  ← für jedes Instrument
```

**Alarmsignale im Live-Bot-Log:**

| Muster | Bedeutung | Aktion |
|---|---|---|
| `⚠️ DRY-RUN MODE: no real orders will be sent.` | Env-Variable nicht gesetzt | Siehe Abschnitt 4.1 |
| `WebSocket Verbindungsversuch N/5` | WS-Reconnect läuft | Ab Versuch 5 → `os._exit(1)` → systemd-Restart |
| `Reconciliation for ETORO failed` | Order-State inkonsistent | Bot stoppen, `execution_mapping.json` prüfen |
| `Universe data is stale (fetched_at > 24 hours ago)` | Universe zu alt | Bot läuft weiter aber mit veralteten Symbolen |
| `No tournament winner for X. Skipping.` | Normal für Symbole ohne Gewinner | Nur kritisch wenn **alle** Symbole übersprungen |
| `Fatal Python error: Aborted` | Rust-Engine FFI-Crash | Signatur-Konflikt (Pitfall #30); Code prüfen |

**Gesunde laufende Bot-Signale** (erwartetes Rauschen):

```
Heartbeat: N Ticks verarbeitet.          ← alle ~15s, zeigt WebSocket-Aktivität
Snapshot SYMBOL.ETORO: MarketOpen=True   ← beim Start, zeigt Marktdaten-Empfang
Aggregator for X is currently in use     ← beim Start, KEIN Fehler (Abschnitt 4.2)
Checking in-flight orders status         ← alle 2s, KEIN Fehler (Abschnitt 4.3)
```

### 3.4 Tournament-JSON: Manuelle Auswertung

```bash
# Alle Gewinner anzeigen:
python3 -c "
import json
d = json.load(open('logs/tournament_2026-06-03.json'))
for sym, w in d['per_symbol_winners'].items():
    print(f\"{sym:<30} {w['strategy']:<35} sortino={w['sortino']:.2f} trades={w['total_trades']}\")
"

# Aggregierten Gewinner prüfen:
python3 -c "
import json
d = json.load(open('logs/tournament_2026-06-03.json'))
ag = d['aggregate_winner']
print(f\"Winner: {ag['strategy']}, Wins: {ag['win_count']}, Median Sortino: {ag['median_sortino']:.4f}\")
print(f\"OOS eligible: {ag.get('oos_eligible', 'n/a')}\")
"
```

---

## 4. Diagnose häufiger Log-Muster

### 4.1 DRY-RUN MODE: no real orders will be sent

**Log-Eintrag:**
```
2026-06-03T15:11:40.743626983Z [INFO] eToro-Momentum-LS.ExecClient-ETORO: ⚠️  DRY-RUN MODE: no real orders will be sent.
```

**Ursache:** Der eToro-Execution-Client (`etoro_execution.py`) prüft **unabhängig vom Orchestrator** das Safety-Interlock in `momentum_ls_run.py`. Dieser vergleicht:

```python
# Safety-Interlock in momentum_ls_run.py:
if not (environment == 'real' and dry_run == False and os.getenv('ETORO_CONFIRM_LIVE') == '1'):
    sys.exit(1)  # oder: dry_run wird True gesetzt
```

Der Orchestrator selbst kann ohne `--dry-run` laufen (`DRY-RUN: NEIN` im Orchestrator-Log), aber wenn die **Umgebungsvariable `ETORO_CONFIRM_LIVE`** nicht auf `'1'` gesetzt ist, startet der ExecClient im Dry-Run-Modus. Der Bot läuft technisch vollständig — alle Strategien empfangen Bars, berechnen Signale, und loggen Orders — aber **kein einziger HTTP-Request** wird an die eToro-Order-API gesendet.

**Behebung:**
```bash
# In der Shell-Session oder in /etc/environment / .env:
export ETORO_CONFIRM_LIVE=1

# Dann Bot neu starten:
kill -15 $(cat logs/live_bot.pid)
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json
```

> ⚠️ **Vorsicht:** Erst nach vollständiger Verifikation des Tournament-Outputs, Datenintegrität und WebSocket-Stabilität setzen.

**Im aktuellen Run (2026-06-03):** Das Dry-Run-Flag war aktiv. Der Bot hat Strategien registriert und Bars empfangen, aber keine realen Orders erzeugt. Alle `ExecClient`-Logs zeigen erfolgreiche Reconciliation mit 0 Orders — konsistent mit Dry-Run.

---

### 4.2 Aggregator currently in use — Massenwarnungen

**Log-Muster:**
```
[WARN] eToro-Momentum-LS.DataEngine: Aggregator for EEFT.ETORO-1-HOUR-MID-INTERNAL is currently in use, subscription can't be started.
[WARN] eToro-Momentum-LS.DataEngine: Aggregator for EEFT.ETORO-1-HOUR-MID-INTERNAL is currently in use, subscription can't be started.
... (26× für EEFT, 46× für INSW, etc.)
```

**Ursache:** NautilusTrader's `DataEngine` erstellt pro Bar-Typ **genau einen** Aggregator. Wenn sich mehrere Strategie-Instanzen für denselben Bar-Typ registrieren, erzeugt die erste Instanz den Aggregator. Alle weiteren rufen zwar ebenfalls `subscribe_bars(bar_type)` auf, bekommen aber diese WARN, weil der Aggregator bereits existiert und läuft.

**Dies ist kein Fehler.** Alle Strategien, egal ob sie den Aggregator erstellt haben oder nicht, empfangen die Bar-Events. Der WARN ist rein informativ.

**Warum so viele Instanzen?** Das Tournament produziert pro Symbol-Strategie-Kombination ggf. mehrere Gewinner-Einträge (verschiedene Parameter-Sets). `momentum_ls_run.py` registriert alle qualifizierten Einträge als separate Strategie-Instanzen — z. B. 26 Instanzen von `FlashCrashReversalStrategy` für `EEFT.ETORO`, alle mit leicht unterschiedlichen Konfigurationsparametern. Die Anzahl der WARN-Zeilen entspricht der Anzahl der Instanzen **minus 1** (die erste erzeugt den Aggregator ohne WARN).

**Bewertung:** Solange die Anzahl der Instanzen pro Symbol plausibel erscheint (< 50), ist alles in Ordnung. Eine ungewöhnlich hohe Zahl (> 100 für ein Symbol) könnte auf einen Registrierungs-Bug in `momentum_ls_run.py` hinweisen.

**Monitoring-Befehl:**
```bash
grep "Aggregator for" logs/live_bot_$(date +%Y%m%d).log \
  | sed 's/.*Aggregator for \(.*\) is.*/\1/' \
  | sort | uniq -c | sort -rn | head -20
```

---

### 4.3 Checking in-flight orders status

**Log-Muster:**
```
2026-06-03T15:11:53.055388096Z [DEBUG] eToro-Momentum-LS.ExecEngine: Checking in-flight orders status
2026-06-03T15:11:55.056888776Z [DEBUG] eToro-Momentum-LS.ExecEngine: Checking in-flight orders status
... (alle 2 Sekunden)
```

**Ursache:** Die `ExecEngine` ist mit folgender Konfiguration gestartet:
```
inflight_check_interval_ms = 2000    ← Prüf-Intervall: alle 2 Sekunden
inflight_check_threshold_ms = 5000   ← Timeout: 5s bis zur Eskalation
inflight_check_retries       = 5     ← Eskalations-Versuche
```

Diese Checks überwachen Orders, die an die eToro-API gesendet wurden, aber noch keine Bestätigung (Fill, Reject, Cancel) erhalten haben. Bleibt eine Order länger als `threshold_ms` ohne Antwort, werden Retry-Mechanismen ausgelöst.

**Dies ist normales NautilusTrader-Verhalten** — Teil des Order Lifecycle Managements und der Reconciliation. Keine Aktion erforderlich.

**Zusammen mit dem Heartbeat-Muster:**
```
Heartbeat: 60 Ticks verarbeitet.   ← alle ~15s
Heartbeat: 120 Ticks verarbeitet.  ← alle ~15s
```

Zeigt ein gesundes System: Der WebSocket-Client verarbeitet ca. 4 Ticks/Sekunde über 22 aktive Instrumente (≈ 0.18 Ticks/Instrument/Sekunde, typisch für Aktien in der Haupthandelszeit).

---

## 5. Prozessmanagement & Live-Monitoring

### 5.1 PID-Tracking & Statusüberwachung

```bash
# PID des laufenden Bots ermitteln:
cat /home/user/etoro_nautilus/logs/live_bot.pid

# Prozessstatus prüfen:
ps -p $(cat /home/user/etoro_nautilus/logs/live_bot.pid) -o pid,stat,etime,cmd

# Vollständiger Prozessbaum:
pstree -p $(cat /home/user/etoro_nautilus/logs/live_bot.pid)
```

### 5.2 Live-Log-Monitoring

```bash
# Live-Bot-Log (Haupt-Monitoring-Stream):
tail -f /home/user/etoro_nautilus/logs/live_bot_$(date +%Y%m%d).log

# Nur Warnungen und Fehler:
tail -f /home/user/etoro_nautilus/logs/live_bot_$(date +%Y%m%d).log \
  | grep -E "\[WARN\]|\[ERROR\]|\[CRIT\]"

# Nur Order-relevante Events:
tail -f /home/user/etoro_nautilus/logs/live_bot_$(date +%Y%m%d).log \
  | grep -E "SUBMIT|FILLED|REJECTED|CANCELED|DRY-RUN"

# Orchestrator-Log:
tail -f /home/user/etoro_nautilus/logs/orchestrator_$(date +%Y%m%d).log
```

### 5.3 Ressourcenüberwachung

```bash
# CPU/RAM des Bot-Prozesses:
top -p $(cat /home/user/etoro_nautilus/logs/live_bot.pid)

# Dateideskriptoren (WebSocket-Verbindungen):
ls -la /proc/$(cat /home/user/etoro_nautilus/logs/live_bot.pid)/fd | wc -l

# Netzwerkverbindungen zur eToro-WS:
ss -tnp | grep $(cat /home/user/etoro_nautilus/logs/live_bot.pid)
```

---

## 6. Essentielle Wartungsskripte

### 6.1 Instrumenten- und Universe-Aktualisierung

Das Universe muss täglich aktualisiert werden. Ein Timestamp älter als 24 Stunden führt zu der WARNING `Universe data is stale` und dazu, dass neue Instrumente im Portfolio nicht gehandelt werden.

```bash
python3 automation/universe_fetcher.py
# Aktualisiert: data/universe/momentum_ls.json
```

**Manuelle Stale-Prüfung:**
```bash
python3 -c "
from automation.universe_fetcher import is_universe_stale
print('Stale:', is_universe_stale())
"
```

### 6.2 Vollständiger täglicher Orchestrator-Lauf

```bash
# Standard-Lauf (mit API-Fetch und Universe-Update):
python3 automation/daily_orchestrator.py

# Wenn ZIPs bereits vorhanden (API-Fetch überspringen):
python3 automation/daily_orchestrator.py --skip-api-fetch

# Dry-Run (kein Bot-Start, kein Live-Trading):
python3 automation/daily_orchestrator.py --dry-run
```

### 6.3 Isolierter Manueller Bot-Start

```bash
# Mit aktuellem Tournament:
export ETORO_CONFIRM_LIVE=1
python3 automation/momentum_ls_run.py \
  --universe /home/user/etoro_nautilus/data/universe/momentum_ls.json \
  --tournament /home/user/etoro_nautilus/logs/tournament_$(date +%Y-%m-%d).json

# Dry-Run (kein ETORO_CONFIRM_LIVE erforderlich):
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json
```

### 6.4 Daten-Integrität prüfen

```bash
# API-Backfill (letzte 7 Tage):
python3 automation/api_backfiller.py --days 7

# Historical Fetcher (12 Monate Deep Backfill):
python3 automation/historical_fetcher.py --months 12

# Pre-Flight-Check:
python3 -c "from automation.backtest_runner import read_precisions_from_parquet; print('OK')"
python3 -c "from automation.universe_fetcher import is_universe_stale; print('OK')"
python3 -c "import json; d=json.load(open('automation/config/instrument_map.json')); print(len(d['instruments']), 'Instrumente')"
```

### 6.5 Katalog-Reset (Datenfehler beheben)

```bash
# Katalog vollständig neu aufbauen (WARNUNG: löscht alle lokalen Tick-Daten):
python3 automation/daily_orchestrator.py --reset-catalog

# Danach vollständigen Backfill ausführen:
python3 automation/historical_fetcher.py --months 12
```

---

## 7. State Management & Emergency Operations

### 7.1 Graceful Shutdown

Ein unkontrollierter Abbruch (z. B. `kill -9`) kann das Order-Mapping im eToro-Adapter korrumpieren.

```bash
# Korrekter Shutdown via SIGTERM:
kill -15 $(cat /home/user/etoro_nautilus/logs/live_bot.pid)

# Verifizierung: Im Live-Bot-Log muss erscheinen:
# TradingNode: STOPPING
# ExecClient-ETORO: Disconnected
# TradingNode: STOPPED
```

### 7.2 Behebung von API Rate-Limits & WebSocket-Disconnects

1. **Bot stoppen:** `kill -15 $(cat logs/live_bot.pid)`
2. **Cooldown:** Mindestens 15 Minuten warten
3. **State-Datei prüfen:** `/home/user/etoro_nautilus/data/state/execution_mapping.json` auf verwaiste Orders prüfen
4. **Neustart:** `python3 automation/daily_orchestrator.py` oder manueller Bot-Start

### 7.3 Bot reagiert nicht auf SIGTERM

```bash
# Sanfter Versuch mit SIGHUP:
kill -1 $(cat /home/user/etoro_nautilus/logs/live_bot.pid)
sleep 10

# Falls kein Effekt, kontrollierter SIGKILL:
kill -9 $(cat /home/user/etoro_nautilus/logs/live_bot.pid)

# Danach zwingend: State-Konsistenz prüfen und alle offenen Positionen
# manuell auf der eToro-Plattform verifizieren.
```

### 7.4 Rust FFI Abort (Fatal Python error: Aborted)

Wenn der Bot mit `Fatal Python error: Aborted` aus `nautilus_trader.system.kernel` crasht:

- **Ursache:** NautilusTrader Rust-Engine crasht, wenn ein Python-Worker unsauber stirbt, bevor `engine.dispose()` aufgerufen wurde (Pitfall #30)
- **Häufige Auslöser:** Signaturänderungen in Worker-Funktionen, TypeError in `run_single_backtest_worker`
- **Massnahme:** Git-Log auf kürzliche Änderungen an `backtest_runner.py` prüfen, besonders `run_single_backtest_worker`

### 7.5 Quick-Reference: Wichtige Pfade

```
Konfig:
  automation/config/backtest.json          ← Backtest-Fenster, Spread-Modeling
  automation/config/tournament.json        ← Eligibilitätskriterien, Score-Gewichte
  automation/config/strategy_defaults.json ← Strategie-Defaults (ATR, max_bars)
  automation/config/instrument_map.json    ← Symbol → price/size_precision

State:
  data/state/execution_mapping.json        ← eToro-Order-IDs ↔ Nautilus-Mapping
  data/state/live_bot.pid                  ← Aktuelle Bot-PID (Kopie)
  data/state/size_increment_cache.json     ← Precision-Cache

Daten:
  data/universe/momentum_ls.json           ← Universe-Snapshot + fetched_at
  data/nautilus/data/quote_tick/           ← FSB(16) QuoteTick-Parquet-Dateien
  data/import/                             ← ZIP-Drop-Zone (auto-gelöscht nach Merge)

Logs:
  logs/orchestrator_YYYYMMDD.log           ← Pipeline-Hauptlog
  logs/live_bot_YYYYMMDD.log              ← Bot-Laufzeit-Log
  logs/tournament_YYYY-MM-DD.json          ← Tournament-Vollresultat
  logs/live_bot.pid                        ← Aktuelle PID
```
