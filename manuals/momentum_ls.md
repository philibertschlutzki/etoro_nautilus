# Momentum-LS Smart Portfolio Integration

## Overview
The Momentum-LS integration is a top-level orchestrator that dynamically fetches the current symbol universe from a specific eToro Smart Portfolio, backtests all available strategies against that universe to pick the optimal strategy per symbol, and automatically manages dynamic capital allocation when running the live system.

```text
momentum_ls_universe.py     ← Holt Portfolio-Symbole von eToro Smart Portfolio
        │
        ▼ data/universe/momentum_ls.json
momentum_ls_fetch_candles.py ← Lädt fehlende historische Parquet-Daten
        │
        ▼ data/nautilus/data/quote_tick/
momentum_ls_tournament.py   ← Backtest aller Strategien, Auswahl des Gewinners
        │
        ▼ logs/tournament_YYYY-MM-DD.json
momentum_ls_run.py          ← Startet Live-Bot mit Gewinner-Konfiguration
        │
        ▼
MomentumLSAllocator         ← Dynamische Kapitalzuteilung pro Instrument
```

## Prerequisites
- Standard Python 3.10+ virtual environment.
- Required dependencies installed via `pip install -r requirements.txt`.
- `.env` file populated with:
  ```env
  ETORO_API_KEY=your_key
  ETORO_USER_KEY=your_user_key
  MOMENTUM_LS_USERNAME=etoro_username_of_the_smart_portfolio
  ETORO_CONFIRM_LIVE=1  # Required ONLY if environment='real' and dry_run=False
  ```
  *(Hinweis: Der `MOMENTUM_LS_USERNAME` ist der eToro-Benutzername des Smart Portfolios, z.B. "OutSmartNSDQ")*

## Daily Workflow
The workflow enforces a strict sequential dependency. You **must** run these steps in order, otherwise the tournament will fail or produce incomplete results.

### Step 1: Fetch the Current Universe
This script retrieves the current holdings of the Smart Portfolio and resolves their eToro internal IDs to Nautilus-compatible symbols.
```bash
python3 dev_scripts/momentum_ls_universe.py --output data/universe/momentum_ls.json
```
**Erfolg:** Erstellt `data/universe/momentum_ls.json` mit allen gefundenen Symbolen.
**Fehler:** Bei `Universe data is stale` diesen Schritt erneut ausführen.

### Step 2: Ensure Historical Data Exists
The backtest tournament evaluates the past 6 months of tick data. Check your `data/nautilus/data/quote_tick/` directory. If any new tickers were added to the universe that you do not have Parquet data for, you must fetch the fallback candles:
```bash
# Example for ADA.ETORO
python3 dev_scripts/momentum_ls_fetch_candles.py --etoro-id 100017 --symbol ADA.ETORO --months 6
```
*(If the eToro API returns no data, check the `instrumentId` mapping or your API keys).*

### Step 2b: Automated Data Fetch (Neu)
Anstatt jedes Symbol einzeln abzufragen, lade automatisch alle fehlenden Daten für das gesamte Universum herunter:
```bash
python3 dev_scripts/momentum_ls_fetch_candles_auto.py --universe data/universe/momentum_ls.json
```

### Step 3: Run the Backtest Tournament
The tournament **requires** the universe JSON and the historical Parquet data to exist. It ranks all implemented strategies for each symbol using the Sortino ratio (primary), Calmar ratio (secondary), and a Profit Factor > 1.5.
```bash
python3 dev_scripts/momentum_ls_tournament.py \
    --universe data/universe/momentum_ls.json \
    --output logs/tournament_$(date +%Y-%m-%d).json
```
**Erfolg:** Erstellt ein JSON-File mit den Siegern und gibt eine Tabelle in der Konsole aus.
**Fehler:** Bei `Simulation failed for [Symbol]` das Parquet-Schema prüfen.

### Step 4: Launch the Live Bot
The orchestrator ties everything together. It reads the universe, parses the tournament winners, sets up the `MomentumLSAllocator` for dynamic sizing, and launches the Nautilus engine.
```bash
# Trockenlauf (Testet das Setup, platziert keine echten Orders)
python3 dev_scripts/momentum_ls_run.py --tournament logs/tournament_today.json --dry-run

# Live-Modus (Erfordert ETORO_CONFIRM_LIVE=1 in .env)
python3 dev_scripts/momentum_ls_run.py --tournament logs/tournament_today.json
```


## MomentumLSAllocator
Der Allocator ist das Herzstück des Kapitalmanagements und wird in `momentum_ls_run.py` initialisiert.
*   **No-Interference-Regel:** Wenn eine Position für ein Instrument bereits offen ist, allokiert der Allocator genau `0` Kapital für dieses Instrument, bis die Position geschlossen wird.
*   **Dynamische Kapitalscheiben:** Das verfügbare Gesamtkapital wird gleichmäßig in "Slices" (Scheiben) auf alle Symbole ohne aktive Position aufgeteilt. So passt sich die Positionsgröße an das wachsende oder schrumpfende Universum an.


## Safety & Dry-Run
Beim Starten des Live-Bots gibt es strikte Sicherheitsmechanismen:
*   **`--dry-run` Flag:** Startet die Execution Engine, platziert aber keine echten Orders. HTTP-Requests an eToro werden geblockt. Dies ist der empfohlene Weg, um die Integration zu testen.
*   **Safety Interlock:** Um reale Orders zu platzieren, muss in der `config/setups.py` `environment == 'real'` gesetzt sein, das Skript muss ohne `--dry-run` gestartet werden, UND die Umgebungsvariable `ETORO_CONFIRM_LIVE=1` muss zwingend in der `.env` gesetzt sein. Fehlt diese Variable, bricht der Bot sofort mit `sys.exit(1)` ab.

## Interpreting Tournament Output
The tournament outputs both a JSON file and a printed console table:
- **Sortino**: Primary ranking metric.
- **Calmar**: Secondary tie-breaker metric.
- **PF (Profit Factor)**: Strategies with a PF <= 1.5 are automatically disqualified. Wenn **alle** Strategien für ein Symbol durchfallen, wird dieses Symbol nicht gehandelt.
- **Win?**: Checked (`✓`) if the strategy is the absolute winner for that specific symbol. The live runner will automatically configure the bot using this strategy.
Das JSON-Output (`logs/tournament_*.json`) enthält das genaue Parameter-Dictionary für jede Gewinner-Strategie.

## Adding a New Instrument
Wenn das eToro Smart Portfolio ein neues Asset hinzufügt:
1. Das neue Asset wird vom Skript in Schritt 1 möglicherweise nicht direkt erkannt.
2. Führe `python3 dev_scripts/auto_map_insturments.py` aus. Dieses Skript entdeckt fehlende IDs und fügt sie automatisch zu `adapters/instrument_map.py` hinzu.
3. Führe Schritt 1 (`momentum_ls_universe.py`) erneut aus.
4. Führe Schritt 2b (`momentum_ls_fetch_candles_auto.py`) aus, um die historischen Daten zu laden.
5. Führe das Turnier (Schritt 3) erneut aus.

## Troubleshooting
- **No valid symbols to trade after cross-referencing...**
  - Usually means none of the symbols passed the PF > 1.5 test, OR your parquet data directory is completely empty. Ensure Step 2b was run.
- **Simulation failed for [Symbol]...**
  - Indicates the historical parquet data is malformed or the strategy config is strictly rejecting the default parameters. Check the logs for exact errors. (Nutze `read_parquet.py`).
- **Universe data is stale...**
  - `dev_scripts/momentum_ls_run.py` will warn if the `fetched_at` timestamp is older than 24 hours. Ensure you run Step 1 daily.
- **MomentumLSAllocator: zero allocation...**
  - Dies bedeutet, dass die No-Interference-Regel aktiv ist, da bereits eine Position existiert.
- **Tournament läuft, aber Bot startet nicht...**
  - Prüfe, ob `--dry-run` entfernt wurde und `ETORO_CONFIRM_LIVE=1` in der `.env` gesetzt ist.
- **Connectivity-Fehler...**
  - Führe das Diagnose-Skript aus: `python3 dev_scripts/etoro_connectivity_test.py`.

---
## Weiterführende Dokumente
- `manuals/deployment.md`
- `manuals/TESTING.md`
- `manuals/feature_automation_LS.md`

---
*Zuletzt aktualisiert: 2026-05-17 — Überprüft gegen Repository-Stand vom 2026-05-14*
