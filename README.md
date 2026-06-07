# eToro Nautilus — `automation/` Pipeline

Autonomes, hermetisches Daten- und Ausführungs-Framework für algorithmisches Trading auf eToro, aufgebaut auf [NautilusTrader](https://nautilustrader.io/). Das `automation/`-Paket deckt den vollständigen Zyklus ab — von der Universe-Beschaffung über kontinuierliche Tick-Sammlung und Backtesting bis zum Live-Deployment — **ohne Abhängigkeit vom restlichen Repository**.

> **Eigenständiges Produkt:** Keine Datei in `automation/` importiert aus `adapters/`, `config/` (Root) oder `strategies/` (Root). Diese Verzeichnisse sind Legacy und wurden nach `archive/` verschoben.
>
> **Verbindliche Entwickler-Doku:** [`automation/AGENTS.md`](./automation/AGENTS.md). Dort sind Architektur, Datenfluss, Precision-Logik und alle bekannten offenen Bugs (Pitfalls) dokumentiert. Vor jeder Code-Änderung lesen.

## Architektur

```
automation/universe_fetcher.py ─► data/universe/momentum_ls.json
                                          │
   automation/catalog_service.py (24/7) ─┤   automation/api_backfiller.py (7d)
   stündliche ZIPs in data/import/       │   automation/historical_fetcher.py (12M)
                                          ▼
                          automation/daily_orchestrator.py
                                  │
                    ┌─────────────┼─────────────────────┐
                    ▼             ▼                       ▼
              Phase 1         Phase 2              Phase 3+4
          Universe+Mapping  ZIP-Merge+Backfill  Backtest+Tournament
                                                          │
                                                          ▼
                                             automation/backtest_runner.py
                                             logs/tournament_YYYY-MM-DD.json
                                                          │
                                                          ▼
                                                    Phase 5: Live
                                             automation/momentum_ls_run.py
```

Alle Datenquellen liefern bereits Nautilus-kompatibles `FixedSizeBinary(16)` — im Orchestrator ist keine Typ-Migration nötig.

## Kernkomponenten

| Datei | Zweck |
|-------|-------|
| `automation/daily_orchestrator.py` | 5-Phasen End-to-End-Pipeline (v2.0) |
| `automation/catalog_service.py` | 24/7 WebSocket-Tick-Sammlung → stündliche ZIPs |
| `automation/api_backfiller.py` | 7-Tage-Backfill, dynamische Precision, FSB(16)-nativ |
| `automation/historical_fetcher.py` | Deep Backfill bis 12 Monate |
| `automation/backtest_runner.py` | Matrix-Backtest + Tournament (Sortino/PF/Calmar) |
| `automation/momentum_ls_run.py` | Live-Trading-Orchestrator (Detached Subprocess) |
| `automation/momentum_ls_allocator.py` | Kapital-Allocator (No-Interference, $11-Floor) |
| `automation/universe_fetcher.py` | Smart-Portfolio-Universe-Fetch |
| `automation/log_manager.py` | LLM-optimiertes Logging (JSON-Events) |
| `automation/config/` | `backtest.json`, `instrument_map.json`, `strategies.json`, `strategy_defaults.json`, `tournament.json` |

## Setup

Python 3.10+ erforderlich.

```bash
git clone https://github.com/philibertschlutzki/etoro_nautilus.git
cd etoro_nautilus/

python3 -m venv venv
source venv/bin/activate
pip install -r automation/requirements.txt
```

`.env` im Projekt-Root:

```env
ETORO_API_KEY=dein_api_key
ETORO_USER_KEY=dein_user_key
MOMENTUM_LS_USERNAME=dein_etoro_username   # nur für universe_fetcher
ETORO_CONFIRM_LIVE=1                        # NUR setzen, wenn Live-Trading bewusst aktiviert wird
```

## Ausführung

```bash
# Täglicher Lauf (catalog_service.py hat ZIPs befüllt)
python3 automation/daily_orchestrator.py --skip-api-fetch

# Dry-Run (Backtest + Tournament, kein Bot-Start)
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch

# Mit API-Backfill der letzten 7 Tage
python3 automation/daily_orchestrator.py

# Einzelne Dienste manuell
python3 automation/api_backfiller.py --days 7
python3 automation/historical_fetcher.py --months 12
python3 automation/catalog_service.py        # 24/7, systemd-fähig

# Live-Bot manuell (erfordert ETORO_CONFIRM_LIVE=1 in .env)
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json
```

Der `catalog_service` ist für den Dauerbetrieb via `systemd` mit `Restart=always` ausgelegt (Unit-Template in [`manuals/deployment.md`](./manuals/deployment.md)).

## Tests

```bash
pytest tests/ -v

# Pre-Flight
python3 -c "from automation.backtest_runner import read_precisions_from_parquet; print('OK')"
python3 -c "from automation.universe_fetcher import is_universe_stale; print('OK')"
python3 -c "import json; d=json.load(open('automation/config/instrument_map.json')); print(len(d['instruments']), 'Instrumente')"
```

Alle Tests respektieren das Standalone-Prinzip (geprüft via AST in `test_automation_isolation.py`).

## Precision-Tabelle (v2.0)

| Kategorie | price_precision | size_precision |
|-----------|----------------|----------------|
| SHIB / PEPE | 8 | 8 |
| Krypto (BTC, ETH, …) | 2 | 8 |
| Forex / Rohstoffe | 5 | 5 |
| **Aktien (Default)** | **2** | **2** |

> **Pitfall #14 — GELÖST (v2.0):** `size_precision=2` für Aktien (früher `0`). Fractional Equities werden jetzt korrekt unterstützt — der frühere `ValueError`-Crash bei kleinen Trade-Beträgen ist behoben.

## Wichtiger Hinweis

Dieses System interagiert mit echten Finanzmärkten. Vor produktivem Einsatz [`automation/AGENTS.md`](./automation/AGENTS.md) vollständig lesen — dort sind alle bekannten offenen Bugs mit Status, Root Cause und Fix dokumentiert.

Backtests auf kurzen Datenfenstern (30 Tage) sind anfällig für Overfitting. Eine robuste, risk-adjusted Performance über mehrere unabhängige Zeitfenster ist das tragfähige Ziel.

## Daten- & Log-Verzeichnisse

| Pfad | Inhalt |
|------|--------|
| `data/import/` | ZIP-Drop-Zone (auto-gelöscht nach Merge) |
| `data/nautilus/data/quote_tick/{symbol}/data.parquet` | QuoteTicks (FSB16) |
| `data/state/execution_mapping.json` | eToro-Order-IDs ↔ Nautilus-Mapping |
| `data/state/size_increment_cache.json` | Precision-Cache |
| `data/state/live_bot.pid` | Aktuelle Bot-PID |
| `data/universe/momentum_ls.json` | Universe-Snapshot |
| `logs/orchestrator_YYYYMMDD.log` | Pipeline-Hauptlog |
| `logs/live_bot_YYYYMMDD.log` | Bot-Laufzeit-Log |
| `logs/tournament_YYYY-MM-DD.json` | Tournament-Vollresultat |

## Dokumentation

| Handbuch | Inhalt |
|----------|--------|
| [`manuals/deployment.md`](./manuals/deployment.md) | VM-Setup, systemd-Service, Cron-Job |
| [`manuals/end_to_end_workflow.md`](./manuals/end_to_end_workflow.md) | Vollständige 5-Phasen-Pipeline |
| [`manuals/backtesting_manual.md`](./manuals/backtesting_manual.md) | Backtest-Konfiguration und Auswertung |
| [`manuals/momentum_ls.md`](./manuals/momentum_ls.md) | Momentum-LS Pipeline im Detail |
| [`manuals/new_tickers.md`](./manuals/new_tickers.md) | Neue Instrumente hinzufügen |
| [`manuals/TESTING.md`](./manuals/TESTING.md) | Tests und Verifikation |
| [`manuals/run_bot_manual.md`](./manuals/run_bot_manual.md) | Bot-Betrieb, Log-Diagnose, Notfallmaßnahmen |
| [`manuals/feature_automation_LS.md`](./manuals/feature_automation_LS.md) | Implementierungsstatus (alle Phasen umgesetzt) |
| [`automation/AGENTS.md`](./automation/AGENTS.md) | Autoritative Architektur-Doku (immer aktuell halten) |
