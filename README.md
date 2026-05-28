# 🚀 eToro Nautilus — `automation/` Pipeline

Autonomes, hermetisches Daten- und Ausführungs-Framework für algorithmisches Trading auf eToro, aufgebaut auf [NautilusTrader](https://nautilustrader.io/). Das `automation/`-Paket deckt den vollständigen Zyklus ab — von der Universe-Beschaffung über kontinuierliche Tick-Sammlung und Backtesting bis zum Live-Deployment — **ohne Abhängigkeit vom restlichen Repository**.

> **Eigenständiges Produkt:** Keine Datei in `automation/` importiert aus `adapters/`, `config/` (Root) oder `strategies/` (Root). Diese Verzeichnisse sind Legacy und wurden nach `archive/` verschoben. Die einzige bekannte Ausnahme ist `momentum_ls_run.py` (siehe `automation/AGENTS.md`, Pitfall #19).
>
> **Verbindliche Entwickler-Doku:** [`automation/AGENTS.md`](./automation/AGENTS.md). Dort sind Architektur, Datenfluss, Precision-Logik und **alle bekannten offenen Bugs** (Pitfalls #14–#24) dokumentiert. Vor jeder Code-Änderung lesen.

## 🏗️ Architektur

```
universe_fetcher.py ─► data/universe/momentum_ls.json
                              │
   catalog_service.py (24/7) ─┤   api_backfiller.py (7d) / historical_fetcher.py (12M)
   stündliche ZIPs            ▼
   data/import/*.zip ──► daily_orchestrator.py (5 Phasen)
                              │  1 Universe → 2 Daten-Merge → 3 Backtest → 4 Tournament → 5 Live
                              ▼
                    backtest_runner.py ──► tournament_YYYY-MM-DD.json ──► momentum_ls_run.py (Live-Bot)
```

Alle Datenquellen liefern bereits Nautilus-kompatibles `FixedSizeBinary(16)` (Shift-Left Data Quality) — im Orchestrator ist keine Typ-Migration nötig.

## 📦 Kernkomponenten

| Datei | Zweck |
|-------|-------|
| `daily_orchestrator.py` | 5-Phasen End-to-End-Pipeline (v2.0) |
| `catalog_service.py` | 24/7 WebSocket-Tick-Sammlung → stündliche ZIPs |
| `api_backfiller.py` | 7-Tage-Backfill, dynamische Precision, FSB(16)-nativ |
| `historical_fetcher.py` | Deep Backfill bis 12 Monate (Kaskade OneHour→OneDay) |
| `backtest_runner.py` | Matrix-Backtest + Tournament (Sortino/PF/Calmar) |
| `momentum_ls_run.py` | Live-Trading-Orchestrator (Detached Subprocess) |
| `momentum_ls_allocator.py` | Kapital-Allocator (No-Interference, $11-Floor) |
| `fractional_trading.py` | By-Amount-USD-Order-Utilities |
| `universe_fetcher.py` | Smart-Portfolio-Universe-Fetch |
| `log_manager.py` | LLM-optimiertes Logging (JSON-Events) |
| `utils.py` | `_fallback_precisions()` — zentrale Precision-Heuristik |
| `strategies/` | `HourlyStrategyBase` + 8 aktive Strategien (ATR-Trailing-Stop, 48-Bar-Time-Exit) |
| `config/` | `backtest.json`, `instrument_map.json`, `strategies.json`, `strategy_defaults.json`, `tournament.json` |

## ⚙️ Setup

Python 3.10+ erforderlich.

```bash
git clone https://github.com/philibertschlutzki/etoro_nautilus.git
cd etoro_nautilus/

python3 -m venv venv
source venv/bin/activate
pip install -r automation/requirements.txt
```

`.env` im Projekt-Root (oder `automation/.env`):

```env
ETORO_API_KEY=dein_api_key
ETORO_USER_KEY=dein_user_key
MOMENTUM_LS_USERNAME=dein_etoro_username   # nur für universe_fetcher
ETORO_CONFIRM_LIVE=1                        # NUR setzen, wenn Live-Trading bewusst aktiviert wird
```

## 🚀 Ausführung

```bash
# Täglicher Lauf (catalog_service.py hat ZIPs befüllt)
python3 automation/daily_orchestrator.py --skip-api-fetch

# Dry-Run (Phase 1+2, kein Backtest/Bot)
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch

# Mit API-Backfill der letzten 7 Tage
python3 automation/daily_orchestrator.py

# Einzelne Dienste
python3 automation/api_backfiller.py --days 7
python3 automation/historical_fetcher.py --months 12
python3 automation/catalog_service.py        # 24/7, systemd-fähig
```

Der `catalog_service` ist für den Dauerbetrieb via `systemd` mit `Restart=always` ausgelegt (Unit-Template in `automation/AGENTS.md`, Abschnitt 12).

## 🧪 Tests

```bash
pytest tests/ -v

# Pre-Flight
python -c "from automation.backtest_runner import read_precisions_from_parquet; print('OK')"
python -c "from automation.universe_fetcher import is_universe_stale; print('OK')"
python -c "import json; d=json.load(open('automation/config/instrument_map.json')); print(len(d['instruments']))"
```

Alle Tests respektieren das Standalone-Prinzip (geprüft via AST in `test_automation_isolation.py`).

## ⚠️ Wichtiger Hinweis

Dieses System interagiert mit echten Finanzmärkten. Es enthält derzeit **offene Bugs**, die u.a. dazu führen, dass der Backtest 0 Trades produziert (`size_precision`-Kette, Pitfalls #14/#20/#21). Diese sind in [`automation/AGENTS.md`, Abschnitt 16](./automation/AGENTS.md) mit Status, Root Cause und Fix dokumentiert. Vor produktivem Einsatz lesen und beheben.

Backtests auf kurzen Datenfenstern (30 Tage) sind anfällig für Overfitting. Eine „auf Maximalgewinn optimierte" Parametrisierung hält im Live-Betrieb selten — robuste, risk-adjusted Performance über mehrere unabhängige Zeitfenster ist das tragfähige Ziel.

## 📂 Daten- & Log-Verzeichnisse

| Pfad | Inhalt |
|------|--------|
| `data/import/` | ZIP-Drop-Zone (auto-gelöscht nach Merge) |
| `data/nautilus/data/quote_tick/{symbol}/data.parquet` | QuoteTicks (FSB(16)) |
| `data/nautilus/data/cfd/{symbol}/*.parquet` | Cfd-Instrument-Definitionen |
| `data/state/` | `execution_mapping.json`, `size_increment_cache.json`, `live_bot.pid` |
| `data/universe/momentum_ls.json` | Universe-Snapshot |
| `logs/` | Orchestrator-/Backtest-Logs, `tournament_*.json` |
| `reports/` | Tearsheets / CSV-Fallbacks |