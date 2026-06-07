# Momentum-LS Smart Portfolio Integration

## Überblick

Die Momentum-LS Integration ist ein vollautomatischer Orchestrator, der dynamisch das aktuelle Symbol-Universum eines eToro Smart Portfolios abruft, alle verfügbaren Strategien im Backtest gegeneinander antreten lässt (Tournament), die beste Strategie pro Symbol auswählt und schließlich den Live-Trading-Bot startet.

```text
automation/universe_fetcher.py      ← Holt Portfolio-Symbole von eToro Smart Portfolio
        │
        ▼  data/universe/momentum_ls.json
automation/api_backfiller.py        ← Lädt fehlende Tick-Daten (7 Tage)
automation/historical_fetcher.py    ← Deep Backfill (12 Monate)
        │
        ▼  data/nautilus/data/quote_tick/
automation/backtest_runner.py       ← Matrix-Backtest aller Strategien, Tournament
        │
        ▼  logs/tournament_YYYY-MM-DD.json
automation/momentum_ls_run.py       ← Startet Live-Bot mit Gewinner-Konfiguration
        │
        ▼
MomentumLSAllocator                 ← Dynamische Kapitalzuteilung pro Instrument
```

**Alle Schritte werden automatisch vom Master-Orchestrator ausgeführt:**
```bash
python3 automation/daily_orchestrator.py --skip-api-fetch
```

---

## Voraussetzungen

- Python 3.10+ mit aktiviertem Virtual Environment.
- Abhängigkeiten installiert: `pip install -r automation/requirements.txt`
- `.env`-Datei im Projekt-Root:
  ```env
  ETORO_API_KEY=dein_api_key
  ETORO_USER_KEY=dein_user_key
  MOMENTUM_LS_USERNAME=etoro_username_des_smart_portfolios
  ETORO_CONFIRM_LIVE=1  # NUR setzen, wenn Live-Trading mit echtem Geld gewünscht
  ```
  > **Hinweis:** `MOMENTUM_LS_USERNAME` ist der öffentliche eToro-Benutzername des Smart Portfolios (z. B. "OutSmartNSDQ"), nicht dein eigener Benutzername.

---

## Tagesablauf (tägliche Pipeline)

Der Ablauf folgt einer strikten sequentiellen Abhängigkeit. Die Schritte bauen aufeinander auf.

### Schritt 1: Universe aktualisieren

Holt die aktuellen Bestandteile des Smart Portfolios und ordnet die eToro-internen IDs den Nautilus-kompatiblen Symbolen zu (z. B. `TSLA.ETORO`).

```bash
python3 automation/universe_fetcher.py
```

**Ergebnis:** `data/universe/momentum_ls.json` wird erstellt oder aktualisiert.
**Fehler:** Bei `Universe data is stale` diesen Schritt erneut ausführen.

> **Mapping-Tabelle:** Die Zuordnung `eToro-ID → Symbol` liegt in `automation/config/instrument_map.json`. Details zum Hinzufügen neuer Instrumente: `manuals/new_tickers.md`.

---

### Schritt 2: Historische Tick-Daten sicherstellen

Das Tournament benötigt historische Tick-Daten der letzten Wochen/Monate. Prüfe, ob `data/nautilus/data/quote_tick/` für alle Symbole im Universe Daten enthält.

**Fehlende Daten automatisch laden:**
```bash
# Letzte 7 Tage via API:
python3 automation/api_backfiller.py --days 7

# Vollständiger historischer Backfill (Erstbefüllung, 12 Monate):
python3 automation/historical_fetcher.py --months 12
```

> **Hinweis:** Im Orchestrator (`daily_orchestrator.py`) läuft dieser Schritt automatisch ab. Bei manuellem Betrieb muss er explizit ausgeführt werden.

---

### Schritt 3: Matrix-Backtest + Tournament

Das Tournament testet alle aktiven Strategien gegen alle Symbole und wählt die beste Kombination aus.

```bash
# Wird automatisch vom Orchestrator ausgeführt.
# Manuell (Dry-Run, kein Live-Bot-Start):
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

**Ergebnis:** `logs/tournament_YYYY-MM-DD.json` mit den Gewinner-Strategien pro Symbol.

**Eligibilitätskriterien (alle müssen erfüllt sein):**
- `min_trades ≥ 20` — statistische Mindestbasis
- `total_return > 0%` — profitabel im Backtest

**Plus mindestens EINE dieser Bedingungen:**
- `min_sortino ≥ 0.3` — ODER
- `min_profit_factor ≥ 1.1`

**Score-Formel:** `sortino×0.4 + profit_factor×0.3 + win_rate×0.2 − max_drawdown×0.1`

---

### Schritt 4: Live-Bot starten

Der Orchestrator startet den Bot automatisch nach erfolgreichem Tournament. Für einen manuellen Start:

```bash
# Dry-Run (keine echten Orders — zum Testen empfohlen):
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json

# Live-Modus (erfordert ETORO_CONFIRM_LIVE=1 in .env):
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json
```

---

## MomentumLSAllocator: Dynamische Kapitalzuteilung

Der Allocator teilt das verfügbare Kapital dynamisch auf alle Symbole im Universe auf.

- **No-Interference-Regel:** Für ein Symbol mit einer bereits offenen Position wird `0` Kapital allokiert — der Allocator greift nicht in laufende Positionen ein.
- **Dynamische Scheiben (Slices):** Das verfügbare Gesamtkapital wird gleichmäßig auf alle Symbole ohne aktive Position aufgeteilt. Wenn das Universe wächst oder schrumpft, passt sich die Positionsgröße automatisch an.
- **Mindest-Floor:** Allokationen unter $11 werden nicht ausgeführt (eToro-Minimum).

---

## Safety Interlock (Echtgeld-Schutzmechanismus)

Um versehentliches Echtgeld-Trading zu verhindern, müssen **alle drei** Bedingungen erfüllt sein:

1. `environment == 'real'`
2. `dry_run == False`
3. `ETORO_CONFIRM_LIVE=1` in der `.env`-Datei

Fehlt eine dieser Bedingungen: `sys.exit(1)` — der Bot startet nicht. Dies verhindert, dass ein falsch konfigurierter Bot echte Orders platziert.

---

## Tournament-Ergebnis interpretieren

Das JSON-Output `logs/tournament_YYYY-MM-DD.json` enthält:

```python
# Schnellauswertung:
python3 -c "
import json
d = json.load(open('logs/tournament_$(date +%Y-%m-%d).json'))
for sym, w in d['per_symbol_winners'].items():
    print(f\"{sym:<30} {w['strategy']:<35} sortino={w['sortino']:.2f}\")
"
```

- Symbole **ohne** Gewinner-Eintrag werden nicht gehandelt (keine Strategie hat die Schwellenwerte erreicht)
- `PF=999` ist ein Artefakt aus Runs ohne einen einzigen Verlust-Trade — wird im Tournament penalisiert
- Der "Aggregat-Gewinner" ist die Strategie mit den meisten Symbol-Wins (wird für alle Symbole ohne spezifischen Gewinner eingesetzt)

---

## Neue Instrumente hinzufügen

Wenn das eToro Smart Portfolio ein neues Asset enthält:

1. **Universe neu laden:**
   ```bash
   python3 automation/universe_fetcher.py
   ```
   Das neue Asset wird erkannt und in `data/universe/momentum_ls.json` aufgenommen.

2. **Instrument-Map aktualisieren** (falls das Symbol noch nicht bekannt ist):
   Trage es in `automation/config/instrument_map.json` ein. Details: `manuals/new_tickers.md`.

3. **Historische Daten laden:**
   ```bash
   python3 automation/api_backfiller.py --days 7
   # oder für vollständigen Backfill:
   python3 automation/historical_fetcher.py --months 12
   ```

4. **Orchestrator neu starten** — das neue Symbol wird automatisch ins Tournament aufgenommen.

---

## Troubleshooting

| Problem | Ursache | Lösung |
|---------|---------|--------|
| `No valid symbols to trade after cross-referencing` | Keine Strategie erreichte PF > 1.1 / Sortino > 0.3, oder Parquet-Daten fehlen | Schritt 2 ausführen, dann Schritt 3 wiederholen |
| `Simulation failed for [Symbol]` | Parquet-Daten defekt oder Strategie-Konfiguration fehlerhaft | Logs auf `ERROR` prüfen, Daten neu laden |
| `Universe data is stale` | `fetched_at` in `momentum_ls.json` älter als 24 Stunden | `python3 automation/universe_fetcher.py` ausführen |
| `MomentumLSAllocator: zero allocation` | No-Interference-Regel aktiv (Position bereits offen) | Normales Verhalten — kein Fehler |
| `Tournament läuft, aber Bot startet nicht` | `--dry-run` Flag aktiv, oder `ETORO_CONFIRM_LIVE` nicht gesetzt | Prüfe `.env` und CLI-Argumente |

---

## Weiterführende Dokumente
- [`manuals/deployment.md`](./deployment.md) — VM-Setup, systemd und Cron
- [`manuals/TESTING.md`](./TESTING.md) — Tests und Verifikation
- [`manuals/feature_automation_LS.md`](./feature_automation_LS.md) — Implementierungsstatus
- [`manuals/new_tickers.md`](./new_tickers.md) — Neue Instrumente hinzufügen
- [`manuals/run_bot_manual.md`](./run_bot_manual.md) — Tournament-Selektion und Log-Diagnose

---
*Zuletzt aktualisiert: 2026-06-07 — Überprüft gegen automation/AGENTS.md*
