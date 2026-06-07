# automation/AGENTS.md — eToro Nautilus Automation-Paket

> **Zweck:** Diese Datei ist der verbindliche Leitfaden für Jules (und jeden anderen KI-Coding-Agenten), der am **`automation/`-Paket** arbeitet. Sie beschreibt **ausschließlich** das `automation/`-Verzeichnis als eigenständiges, hermetisches Produkt. Sie ist mit dem tatsächlichen Code-Stand abgeglichen — inklusive bekannter, **derzeit offener** Bugs (siehe Abschnitt 16). Halte diese Datei bei jeder strukturellen Änderung aktuell.

> **Geltungsbereich:** Nur `automation/`. Das Root-`adapters/`-, Root-`strategies/`- und `backtesting/`-Verzeichnis sind **Legacy** und wurden nach `archive/` verschoben. Sie sind NICHT Gegenstand dieser Datei. Alle Adapter wurden nach `automation/adapters/` migriert — siehe Pitfall #19.

---

## Inhaltsverzeichnis

1. [Produktübersicht](#1-produktübersicht)
2. [Repository-Struktur (automation/)](#2-repository-struktur-automation)
3. [Architektur & Datenfluss](#3-architektur--datenfluss)
4. [Standalone-Prinzip (hartes Constraint)](#4-standalone-prinzip-hartes-constraint)
5. [Dienste & Komponenten](#5-dienste--komponenten)
6. [Strategy-Layer](#6-strategy-layer)
7. [Konfigurationssystem (automation/config/)](#7-konfigurationssystem-automationconfig)
8. [Precision-Logik (zentral)](#8-precision-logik-zentral)
9. [Daten-Pipeline & FixedSizeBinary(16)](#9-daten-pipeline--fixedsizebinary16)
10. [Backtest & Tournament](#10-backtest--tournament)
11. [Live Deployment (Phase 5)](#11-live-deployment-phase-5)
12. [Umgebungs-Setup (.env, requirements, systemd)](#12-umgebungs-setup-env-requirements-systemd)
13. [Testing & Validierung](#13-testing--validierung)
14. [Error-Handling-Konventionen](#14-error-handling-konventionen)
15. [Code-Style & Conventions](#15-code-style--conventions)
16. [Bekannte Pitfalls & offene Bugs](#16-bekannte-pitfalls--offene-bugs)
17. [Order Management & Async State Machine (Neu)](#17-order-management--async-state-machine-neu)
18. [Conventions für KI-Coding-Agents (Jules)](#18-conventions-für-ki-coding-agents-jules)
19. [Changelog (Agent-Maintained)](#19-changelog-agent-maintained)

---

## 1. Produktübersicht

Das `automation/`-Paket ist ein **vollständig isoliertes, autonomes Daten- und Ausführungs-Framework** für algorithmisches Trading auf eToro, aufgebaut auf [NautilusTrader](https://nautilustrader.io/). Es deckt den kompletten Zyklus ab: Universe-Beschaffung, kontinuierliche Tick-Sammlung, historischer Backfill, Matrix-Backtesting mit Tournament-Selektion und Live-Deployment.

**Kernidee „Shift-Left Data Quality":** Alle Datenquellen (`catalog_service.py`, `api_backfiller.py`, `historical_fetcher.py`) liefern bereits 100 % Nautilus-kompatible Parquet-Daten im `FixedSizeBinary(16)`-Format. Dadurch entfällt jede nachgelagerte Typ-Migration im Orchestrator.

**Kritisches Constraint:** Dieses System interagiert mit echten Finanzmärkten. Fehler in Order-Logik, Positions-State oder Precision-Handling können reale monetäre Verluste verursachen. Jede Änderung an Order-erzeugenden Pfaden ist mit besonderer Sorgfalt zu prüfen.

### Roadmap & bekannte Architektur-Schuld

**Fractional Equities via By-Amount-Endpunkt:** eToro handelt Aktien als CFDs mit By-Amount-Semantik (USD-Betrag statt Stückzahl). Der Backtest simuliert dies über ein `Cfd`-Mock-Instrument; der Live-Pfad nutzt `fractional_trading.build_by_amount_payload()`. Die `size_precision`-Behandlung ist hier die größte Quelle aktiver Bugs (siehe Pitfall #14, #20).

---

## 2. Repository-Struktur (automation/)

```text
automation/
├── __init__.py                 # Public API via lazy __getattr__ (run_backfill, run_fetch, …)
├── api_backfiller.py           # Standalone API-Backfiller — dynamische Precision, FSB(16)-nativ
├── backtest_runner.py          # Matrix-Backtest + Tournament (ersetzt backtesting/run_backtest.py)
├── catalog_service.py          # 24/7 WebSocket-Tick-Sammlung → stündliche ZIPs nach data/import/
├── daily_orchestrator.py       # 5-Phasen End-to-End-Pipeline (v2.0)
├── fractional_trading.py       # By-Amount-USD-Orders (Pitfall-#14-Utilities)
├── historical_fetcher.py       # Deep Backfill (12M), Interval-Kaskade OneHour→OneDay
├── log_manager.py              # LLM-optimiertes Logging (RotatingFileHandler, JSON-Events)
├── momentum_ls_allocator.py    # Kapital-Allocator (No-Interference, dynamisches Slicing)
├── momentum_ls_run.py          # Live-Trading-Orchestrator
├── universe_fetcher.py         # Smart-Portfolio-Universe-Fetch (standalone)
├── utils.py                    # _fallback_precisions() — zentrale Precision-Heuristik
├── config/
│   ├── backtest.json           # start_capital=10000, spread_modeling, min_bars_for_backtest=200
│   ├── instrument_map.json     # {etoro_id: {symbol, asset_class, price/size_precision}}
│   ├── strategies.json         # Aktive Strategie-Liste mit active-Flag
│   ├── strategy_defaults.json  # Per-Strategie-Defaults (1h-Candle-optimiert, trade_amount_pct=15.0)
│   └── tournament.json         # Selektionskriterien (min_trades=20, min_sortino=0.3, min_pf=1.1)
└── strategies/
    ├── __init__.py
    ├── hourly_strategy_base.py # Basisklasse: ATR-Trailing-Stop (1.5×) + 48-Bar-Time-Exit
    ├── sma_crossover.py        # aktiv
    ├── mean_reversion.py       # aktiv
    ├── dynamic_breakout.py     # aktiv (Price-Range, keine Volume-Abhängigkeit)
    ├── flash_crash_reversal.py # aktiv
    ├── volatility_breakout.py  # aktiv
    ├── tesla_combo_strategy.py # aktiv (ComboTrendVwapStrategy)
    ├── vwap_exhaustion.py      # aktiv (Price-Deviation only)
    ├── hourly_mean_reversion.py# aktiv
    ├── trend_pullback.py       # INAKTIV (0 FIFO-Schließungen)
    └── adx_atr_momentum.py     # INAKTIV (ADX-Initialisierungsproblem)
```

**Daten-Verzeichnisse** (relativ zu PROJECT_ROOT, außerhalb von `automation/`):
```text
data/
├── import/                     # ZIP-Drop-Zone von catalog_service.py (auto-gelöscht nach Merge)
├── nautilus/data/quote_tick/{symbol}/data.parquet   # FSB(16) QuoteTicks
├── nautilus/data/cfd/{symbol}/*.parquet             # Cfd-Instrument-Definitionen (size_precision!)
├── state/                      # size_increment_cache.json, execution_mapping.json, live_bot.pid
└── universe/momentum_ls.json   # Universe-Snapshot (fetched_at + universe[])
logs/                           # orchestrator_*.log, backtest_*.log, tournament_*.json
reports/                        # Tearsheets / CSV-Fallbacks
```

---

## 3. Architektur & Datenfluss

```
universe_fetcher.py (Smart-Portfolio)
    │  → data/universe/momentum_ls.json {fetched_at, universe[]}
    ▼
catalog_service.py (24/7 WebSocket)          api_backfiller.py (7-Tage-Lücken)
    │  stündlich                                  │  historical_fetcher.py (12M Deep)
    ▼                                             ▼
data/import/[Timestamp].zip   ───────────►   data/nautilus/data/quote_tick/{symbol}/data.parquet
    │  (FSB(16), Arrow-Metadaten)                 (FSB(16), b"price_precision"/b"size_precision")
    ▼
daily_orchestrator.py — 5 Phasen:
    Phase 1  Universe & Mapping (Stale-Check > 24h)
    Phase 2  Multi-ZIP-Import + Merge (pa.concat_tables + ts_event-Dedup) + 2c API-Backfill + 2d Historical
    Phase 3  Matrix-Backtest (Subprocess → backtest_runner.py, 30-Tage-Midnight-UTC-Fenster, 3600s Timeout)
    Phase 4  Tournament (Sortino/PF/Calmar-Ranking → tournament_YYYY-MM-DD.json)
    Phase 5  Live Deployment (Detached Subprocess → momentum_ls_run.py)
```

**Backtest-Fenster:** Dynamisch. `daily_orchestrator.py` berechnet das Fenster dynamisch anhand der relevanten Variablen aus der `backtest.json` (`is_window_days`, `oos_window_days`, `splits`). Die Berechnung lautet: `total_days = is_window_days + (splits * oos_window_days)` gefolgt von `start = end - timedelta(days=total_days)`.

---

## 4. Standalone-Prinzip (hartes Constraint)

**Keine Datei in `automation/` darf aus `adapters/`, `config/` (Root) oder `strategies/` (Root) importieren.** Geprüft via AST-Parsing in `tests/test_automation_isolation.py`.

- Precisions: ausschließlich via eToro API (dynamisch) mit Fallback `automation/utils._fallback_precisions`.
- Instrument-Map: `automation/config/instrument_map.json` (generiert aus dem alten `adapters/instrument_map.py`).
- `.env`-Pfad-Konvention: `automation/.env` → Fallback `PROJECT_ROOT/.env`.

**Standalone-Prinzip:** Eingehalten. Alle Adapter liegen in `automation/adapters/`.

---

## 5. Dienste & Komponenten

### 5.1 catalog_service.py — 24/7 Tick-Sammlung
WebSocket `wss://ws.etoro.com/ws`. Zwei parallele AsyncIO-Tasks: `_ws_loop()` (Empfang + Puffer) und `_flush_loop()` (stündlicher Flush). Bei jedem WebSocket-Fehler: `os._exit(1)` für systemd-Restart. Flush schreibt pro Instrument `quote_tick/{symbol}/{timestamp}.parquet` als FSB(16) in `data/import/[Timestamp].zip`. Precisions werden beim Start dynamisch via API geladen, Fallback `_fallback_precisions`.

### 5.2 universe_fetcher.py — Smart-Portfolio-Universe
GET `/user-info/people/{username}/portfolio/live`. Mappt eToro-IDs via `instrument_map.json` auf Symbole, schreibt atomar `data/universe/momentum_ls.json` mit `fetched_at`. `is_universe_stale()` prüft > 24h. Benötigt `MOMENTUM_LS_USERNAME`. Bei HTTP 401/403: `sys.exit(1)`.

### 5.3 api_backfiller.py — 7-Tage-Backfill
Holt Candle-History (`OneHour`, count=168), konvertiert **direkt** in PyArrow FSB(16) ohne pandas-Roundtrip (`_candles_to_arrow_table`), injiziert Byte-Key-Metadaten (`_build_arrow_meta`), merged atomar (`_merge_and_save`). Überspringt Symbole mit Datenlücke < 1h. Precisions dynamisch via `fetch_precisions_from_api` + Fallback.

### 5.4 historical_fetcher.py — Deep Backfill
Bis zu 12 Monate. Interval-Kaskade OneHour→OneDay, count=1000 pro Chunk. Rückwärts-Iteration ab dem **ältesten** lokal gespeicherten Timestamp (`_get_oldest_ts_ns`). Erkennt historische Tiefe, wenn die API denselben ältesten Candle zweimal liefert. Wiederverwendet `_candles_to_arrow_table` und `_merge_and_save` aus `api_backfiller.py`. `is_symbol_data_sufficient(min_bars=200)` steuert, ob ein Symbol überhaupt gefetcht wird.

### 5.5 backtest_runner.py — Matrix-Backtest
Siehe Abschnitt 10.

### 5.6 daily_orchestrator.py — End-to-End
Siehe Abschnitt 3. 5 Phasen, JSON-Events (`[JSON_EVENT] {...}`), RotatingFileHandler (1 MB, 5 Backups, 7-Tage-Retention). `--reset-catalog` löscht `data/nautilus/data/quote_tick/` vollständig.

### 5.7 momentum_ls_allocator.py — Kapital-Allocator
Thread-safe. No-Interference-Regel: existiert eine offene Position für das Instrument, Allokation = `0.0`. Dynamisches Slicing: `account_balance / pending_signals`. Floor: < $11.00 → `0.0` (eToro-Mindestbetrag).

### 5.8 fractional_trading.py — By-Amount-Utilities
`build_by_amount_payload()` (USD direkt, InstrumentId/IsBuy/InvestmentAmount/SL/TP), persistenter `size_increment`-Cache.

### 5.9 log_manager.py — LLM-Logging
`setup_bot_logging()`, `emit_execution_event()`, `emit_order_event()`. StructuredFormatter mit eingerückten Stacktraces.

---

## 6. Strategy-Layer

### Basisklasse `HourlyStrategyBase`
Alle aktiven Single-Instrument-Strategien erben hiervon. Liefert automatisch:
- **ATR-Trailing-Stop** (1.5× ATR, `DEFAULT_ATR_TRAILING_MULTIPLIER`)
- **Time-based Exit** (48 Bars, `DEFAULT_MAX_BARS_IN_TRADE`)
- gemeinsames `_compute_quantity()` (nutzt `instrument.make_qty(..., round_down=True)`; siehe Pitfall #20)

`on_bar()` muss in Subklassen `_check_exits_and_update(bar)` als ERSTE Aktion aufrufen und bei Rückgabe `True` sofort `return`.

```python
class MyStrategy(HourlyStrategyBase):
    def on_start(self):
        super().on_start()          # PFLICHT
        self.subscribe_bars(self.bar_type)
    def on_bar(self, bar: Bar):
        if self._check_exits_and_update(bar):
            return                  # Exit ausgelöst — keine neuen Signale diese Bar
        # … Signal-Logik …
```

### Flat-Lock-Vermeidung (Pitfall #17)
Nach `_close_position()` beim Drehen einer Position MUSS der Signal-State (`current_signal` / `current_position`) auf `None` zurückgesetzt werden, sonst blockiert der Bar-Guard jeden Neueinstieg dauerhaft. Alle aktiven Strategien setzen dies korrekt um.

`HourlyStrategyBase.__init__(self, config, allocator=None)` nimmt optional einen `MomentumLSAllocator`.
In `_compute_quantity` greift bei der Bestimmung des Positions-Sizings folgende strikte Priorisierung:
1. **`allocator` (höchste Priorität):** Ist ein Live-Allocator (`MomentumLSAllocator`) vorhanden, wird dieser über `get_allocation()` befragt (nutzt internes `_get_current_balance()`).
2. **`trade_amount_usd`:** Ist in der `config` explizit ein USD-Betrag > 0 hinterlegt (z. B. dynamisch injiziert durch den Backtest-Runner via `params["trade_amount_usd"]` zur Überschreibung), wird dieser absolute Wert genutzt. `_get_current_balance()` wird hierbei **nicht** aufgerufen.
3. **`trade_amount_pct`:** Ist ein Prozentwert gesetzt (und kein USD-Betrag injiziert), wird `_get_current_balance()` aufgerufen, um das Risiko basierend auf der aktuellen Balance dynamisch anzupassen.
4. **`Default`:** Fallback auf feste 100.0 USD.

### Aktive Strategien (`strategies.json` active=true)
| Klasse | Datei | Indikatoren | Default trade_amount_pct |
|--------|-------|-------------|--------------------------|
| SmaCrossoverStrategy | sma_crossover.py | SMA(20) | 15.0 |
| MeanReversionStrategy | mean_reversion.py | Keltner(20,2.0) | 15.0 |
| DynamicBreakoutStrategy | dynamic_breakout.py | Price-Range(10) | 15.0 |
| FlashCrashReversalStrategy | flash_crash_reversal.py | BB(10,2.0)+RSI(7) | 15.0 |
| VolatilityBreakoutPumpStrategy | volatility_breakout.py | BB(10,2.0) | 15.0 |
| ComboTrendVwapStrategy | tesla_combo_strategy.py | SMA+MACD+BB+ATR+VWAP | 15.0 |
| VwapExhaustionStrategy | vwap_exhaustion.py | Custom VWAP-Deviation | 15.0 |

| HourlyMeanReversionStrategy | hourly_mean_reversion.py | Keltner-Channel | 15.0 |

### Inaktive Strategien (active=false)
| Klasse | Grund |
|--------|-------|
| TrendPullbackStrategy | (Status: Inaktiv / Maintenance) 0 FIFO-Schließungen in allen Tests; erbt von HourlyStrategyBase (EMA-Period 200 initialisiert bei kurzen Daten nie) |
| AdxAtrMomentumStrategy | (Status: Inaktiv / Maintenance) ADX-Initialisierungsproblem; erbt von HourlyStrategyBase |

**Wichtig:** Die Config-Klassen in `config_class` müssen exakt zu den Feldern passen, die der Backtest spreizt. Die Konfig-Field-Beschreibungen in der alten Root-AGENTS.md waren teils falsch (z.B. `lookback`/`z_score_threshold` für MeanReversion existieren NICHT — die echte Config nutzt `keltner_period`/`keltner_multiplier`). Maßgeblich ist immer der Code der jeweiligen `*Config`-Klasse.

---

## 7. Konfigurationssystem (automation/config/)

**Merge-Reihenfolge der Strategie-Parameter (niedrig → hoch):**
1. `strategy_defaults.json` (Basis, 1h-optimiert)
2. `params` in `strategies.json` (Override)
3. Vom Backtest-Runner injiziert: `instrument_id`, `bar_type`

`trade_amount_pct` ist der neue Standard-Fallback für dynamisches Sizing basierend auf dem verfügbaren Kapital. Falls ein statischer Betrag gewünscht ist, kann `trade_amount_usd` weiterhin explizit gesetzt werden und überschreibt dann die prozentuale Zuweisung.

`tournament.json`: `eligible_requires_all = [min_trades, min_total_return]`, `eligible_requires_any = [min_sortino, min_profit_factor]`. Score = `sortino·0.4 + pf·0.3 + win_rate·0.2 − max_dd·0.1`.
*Zusatz-Feature:* Mit `tournament_overrides` in `strategies.json` können die globalen Gating-Kriterien aus `tournament.json` für spezifische Strategien individuell überschrieben werden (z. B. geringere `min_trades` für restriktivere Setups).

---

## 8. Precision-Logik (zentral)

**Einzige Quelle:** `automation/utils._fallback_precisions(symbol) -> (price_precision, size_precision)`.

| Kategorie | price_precision | size_precision |
|-----------|-----------------|----------------|
| SHIB/PEPE | 8 | 8 |
| Crypto (BTC, ETH, …) | 2 | 8 |
| Forex/Commodity (NATGAS, PALL, …) | 5 | 5 |
| **Equity (Default)** | **2** | **2** |

Früher erzwang size_precision=0 ganzzahlige Order-Größen und unterdrückte Trades bei Aktienkursen > trade_amount_usd; seit Pitfall #23 schreiben utils._fallback_precisions und instrument_map.json durchgängig size_precision=2 für Equities.

---

## 9. Daten-Pipeline & FixedSizeBinary(16)

Nautilus' Rust-Backend erwartet Preise/Größen intern als i128 im `FixedSizeBinary(16)`-Format: `raw_int64 = round(value · 10^16)`, serialisiert als 16-Byte-LE-int128 (bzw. skaliert mit 10^16). Encoding: `_encode_fsb16()` / `_encode_qty_fsb16()` (identisch in `api_backfiller.py` und `catalog_service.py`). Beachte: Shift-Left Data Quality hat hier vorher nicht gegriffen, da das Binärformat zuvor nie gegen den nativen Nautilus-Reader validiert wurde.

**Pflicht-Spalten:** `bid_price, ask_price, bid_size, ask_size, ts_event, ts_init`.
**Pflicht-Byte-Metadaten:** `b"price_precision"`, `b"size_precision"`, `b"instrument_id"`.

Da alle Quellen bereits FSB(16) liefern, entfällt im Orchestrator jede Typ-Migration (`migrate_catalog_to_fixed_binary` existiert in v2.0 nicht mehr). Merge = `pa.concat_tables` + ts_event-Dedup + atomarer Write.

---

## 10. Backtest & Tournament

`backtest_runner.py` läuft als Subprocess. Multiprocessing via `ProcessPoolExecutor(max_workers=max(1, min(cpu//2, 6)))`, `max_tasks_per_child=1` (Python ≥ 3.11), expliziter `BrokenProcessPool`-Catch mit sequenziellem Fallback.

Bei der Umwandlung von Candle zu Tick wird im Backtest nun Zero-Spread-Modeling (bid=ask=close, Buy@Ask=Sell@Bid=Close) genutzt. Die Live-Ticks im `catalog_service` behalten allerdings den realen Spread.

**Engine-Setup pro Job:** `OmsType.NETTING`, `AccountType.MARGIN`, Spread-Modeling (Buy@Ask, Sell@Bid — NautilusTrader-Default mit QuoteTicks). Mock-Instrument via `create_mock_instrument()` als `Cfd(asset_class=EQUITY)`.

**Metriken** (`extract_metrics`): FIFO-Matching über `generate_fills_report()` (Fallback `generate_order_fills_report()`). Sortino nur ab n ≥ 5 Round-Trips. Tournament-Selektion via `select_winners()`.

🟢 **Behoben:** `create_mock_instrument` und `run_single_backtest_worker` erzwingen nun eine asset-bewusste Normalisierung der `size_precision` via temporärer PyArrow-Schema-Injection und `_fallback_precisions` (Equities fallen auf 2, Crypto auf 8 zurück). (Pitfall #14 gelöst, Schreibseiten-Persistenz #23 behoben).

---

## 11. Live Deployment (Phase 5)

`momentum_ls_run.py` wird als Detached Subprocess (`subprocess.Popen`, `start_new_session=True`) gestartet. Liest `per_symbol_winners` aus dem Tournament-JSON.
- Nutzt eine dynamische `STRATEGY_REGISTRY` (geladen aus `strategies.json`, active=true).
- Die echte Tournament-Gewinner-Strategie pro Symbol wird registriert (Pitfall #22 ist behoben).
- Allocator-Injektion erfolgt via `HourlyStrategyBase`.
- Der Live-`bar_type` ist zwingend `{symbol}-1-HOUR-MID-INTERNAL`, da eToro nur QuoteTicks streamt.

Safety-Interlock: `environment=='real'` AND `dry_run==False` AND `ETORO_CONFIRM_LIVE=='1'` → sonst `sys.exit(1)`. Stale-Check: Prüft ob Universe-Daten älter als 24 Stunden sind.

---

## 12. Umgebungs-Setup (.env, requirements, systemd)

| Variable | Pflicht | Verwendet von |
|----------|---------|---------------|
| `ETORO_API_KEY` | Ja | alle Dienste |
| `ETORO_USER_KEY` | Ja | alle Dienste |
| `MOMENTUM_LS_USERNAME` | Ja (nur fetch) | universe_fetcher.py |
| `ETORO_CONFIRM_LIVE` | nur Live | momentum_ls_run.py Safety-Interlock |

Installation: `pip install -r automation/requirements.txt` (nautilus_trader≥1.200, aiohttp, websockets, pyarrow≥16, pandas, pytest). systemd-Unit für `catalog_service.py` mit `Restart=always`, `RestartSec=5`.

Ausführung:
```bash
python3 -m automation.daily_orchestrator --skip-api-fetch       # täglich (ZIPs vorhanden)
python3 -m automation.daily_orchestrator --dry-run --skip-api-fetch
python3 -m automation.api_backfiller --days 7
python3 -m automation.historical_fetcher --months 12
python3 -m automation.catalog_service                           # systemd
```

Hinweis: Wenn systemd-Unit-Files im Repo existieren, müssen die ExecStart-Anweisungen dort ebenfalls auf `python3 -m automation.catalog_service` aktualisiert werden.

---

## 13. Testing & Validierung

Tests in `tests/` (bzw. `automation/tests/`), Ausführung via `pytest`. Kein Test darf aus `adapters/`/`config/`/`strategies/` (Root) importieren. Naming: immer `_fallback_precisions` (mit Underscore).

Zusätzlich stellen Roundtrip-Tests und Tests auf `total_trades > 0` ab sofort sicher, dass echte Fills generiert werden und nicht nur auf "keinen Crash" geprüft wird.

Pre-Flight:
```bash
python -c "from automation.backtest_runner import read_precisions_from_parquet; print('OK')"
python -c "from automation.universe_fetcher import is_universe_stale; print('OK')"
python -c "import json; d=json.load(open('automation/config/instrument_map.json')); print(len(d['instruments']))"
```

Abgedeckte Suiten (laut Test_report.md): Isolation, fractional_trading, utils (Precisions), api_backfiller (FSB16-Encoding, Merge), catalog_service (os._exit, Flush), daily_orchestrator (Multi-ZIP, Stale, Detached-Start), backtest_runner (Metriken, Tournament), allocator (No-Interference, $11-Floor), log_manager (JSON-Events, Cleanup).

---

## 14. Error-Handling-Konventionen

- WebSocket-Fehler → `os._exit(1)` (systemd-Restart). KEINE In-Process-Reconnection.
- Instrument nicht im Cache → log + `return None` aus `_compute_quantity()`.
- Immer `if qty is None: return` nach `_compute_quantity()`.
- Niemals `raise` in `on_bar()`/`on_quote_tick()`.
- HTTP 429 → `Retry-After` respektieren; Timeout → Retry mit Backoff.
- Worker-Crash im Backtest → `BrokenProcessPool`-Catch → sequenzieller Fallback.

---

## 15. Code-Style & Conventions

- Code: Englisch. Log-Messages: Deutsch. Kommentare: Deutsch in strategies/, Englisch akzeptabel in dev-nahen Skripten.
- Type-Hints: `str | None` (Python 3.10+).
- `StrategyConfig`-Subklassen: `frozen=True` PFLICHT.
- Logging immer `logging.getLogger(__name__)` bzw. `self._log`, nie `print()` (Ausnahme: backtest_runner DualLogger).
- Async: `asyncio.sleep`, nie `time.sleep`; `asyncio.wait_for(..., timeout=...)` für externe Calls.

---

## 16. Bekannte Pitfalls & offene Bugs
### 🟢 #45 — Micro Position Sizing & Flat Equity Curves (Issue #254)
**Symptom:** Strategien haben Plausible Ratios (PF, Sortino), aber generieren ~0% Absolute Return (z.B. -0.04%).
**Root Cause:** Kollabierendes Position Sizing. Wenn das berechnete Notional bei hohen Kursen / kleinen Increments unter 1 Increment fiel, rundete `math.floor` auf 0 ab, oder das System handelte mit Cent-Beträgen. Das führte zu winzigen absoluten PnLs.
**Fix & Constraints (Kritische Architektur-Regeln):**
1. **Arity / 4-Tupel Constraint:** Die Arity (Länge) des `pnls_with_ts` Tupels in `backtest_runner.py` ist ein heiliges Architektur-Konstrukt (4-Tupel). Es darf nicht für neue Metriken (wie Notional) aufgebläht werden, da sonst das Unpacking in Downstream-Systemen oder Tests (Referenz Pitfall #33) unwiderruflich bricht. Zusätzliche Per-Trade-Metadaten sind in separaten, synchron laufenden Listen zu sammeln (z.B. `notionals_with_ts`).
2. **Kein Hebeln durch min/max:** `make_qty` darf niemals das zugewiesene Risiko/Notional künstlich über `max(inc, ...)` nach oben hebeln. Fällt das Kapital unter das Minimal-Instrumenten-Increment, muss hart fail-closed via `return None` reagiert werden.
3. **Konstante Floor:** Der eToro Trade-Floor ($11.00) ist fest über `MIN_TRADE_USD` im Strategy-Layer für alle Sub- und Base-Classes dokumentiert und abzusichern.

- **State/Key Bleed (OOS Gating in `_is_eligible`)**: `oos_metrics` is a sibling key (Geschwister-Key) to `metrics` in the backtest result dictionary. Searching for it inside `metrics` (`metrics.get("oos_metrics")`) will silently fail and return `None`, leading to unexpected rejection in tournament gating. Da `oos_metrics` auf derselben Ebene wie `metrics` liegt und nicht tief verschachtelt ist, muss dieser Fehler bei zukünftigen Aggregations-Modulen von vornherein ausgeschlossen werden. Always parse sibling keys directly from the root result dictionary `r`.

### 🟢 #45 — Aggregate Metric Statistical Artifacts (Issue #255)
**Symptom:** In der Turnierauswertung ergab `win_rate * total_trades` keinen ganzzahligen Wert. Die OOS-Rendite wirkte im Vergleich zu Einzel-Symbolen inkonsistent, und das OOS-Gate wurde durch die aufsummierten Trades ausgehebelt (Trade-Sum Trap).
**Root Cause:** Die Aggregation vermischte arithmetische Mittelwerte (für Trades) mit Medians (für Win-Rates). Dies zerstörte die zugrundeliegende mathematische Identität der Einzel-Backtests und erzeugte "Frankenstein-Metriken". Edge-Cases in Zero-Loss Backtests lieferten zudem `None`, was bei nicht typsicherem Unpacking zu Laufzeitfehlern führen konnte.
**Fix (Architektur-Regel):** Die Metrik-Aggregation (`select_winners`) erzwingt nun streng eine hybride, aber mathematisch konsistente Struktur (`portfolio_sum_for_trades_and_trade_weighted_mean_for_return_and_median_for_ratios`).
1. **Volumen (Trades, Wins):** Werden absolut aufsummiert. Das Unpacking erfolgt typsicher via `(oos.get("total_trades") or 0)`, um TypeErrors bei `None`-Werten auszuschließen.
2. **Rendite:** Wird als kapitalgewichteter (Trade-Weighted) Return berechnet, um das Portfolio-Volumen real abzubilden.
3. **Risiko-Ratios (Sortino, PF):** Werden zwingend als Median (via `get_median()`) beibehalten, da Nenner-Abweichungen eine Aufsummierung verbieten.
4. **OOS-Gating (Trade-Sum Trap):** Um zu verhindern, dass die Portfolio-Trade-Summe die `oos_min_trades`-Schwelle (die eigentlich pro Symbol gilt) trivialerweise überschreitet, wird das aggregierte Dictionary (`avg_oos`) *ausschließlich für das Gate* intern normalisiert (`total_trades / n_res`), bevor es an `_evaluate_oos_eligibility` übergeben wird. In den Logs und im JSON verbleiben die wahren Portfolio-Summen.
**WICHTIG für Agenten:** Dieser hybride Aggregations-Zustand ist gewollt. Versuche nicht, ihn als "Inkonsistenz" zu reparieren, indem du Ratios durchschneidest oder Trades normalisierst (außerhalb des Gates).
**Betroffen:** `automation/backtest_runner.py`, `automation/daily_orchestrator.py`

### 🟢 #42 — Crypto: Degenerated Sortino & Profit Factor Metrics (Issue #232)
**Symptom:** Nahezu alle Crypto-Assets (BTC, ETH, XRP etc.) weisen in Backtests über unterschiedlichste Strategien hinweg stark negative Sortino-Werte (z.B. -12.99) und einen Profit Factor von 0.00 auf, gepaart mit extrem schlechten Win-Rates.
**Root Cause:** Dies ist **kein** Bug im Position Sizing (Precision 8) oder FSB(16) Encoding. Das Sizing (`make_qty`) funktioniert für kleinste Krypto-Bruchstücke fehlerfrei. Das Verhalten ist ein rein legitimer, marktbedingter Effekt (Szenario B). Die Long-only-Ausrichtung der aktuellen Strategien in Kombination mit eToros weiten Crypto-Spreads (15 bps) und hohen Volatilitäten in Down-Märkten führt mathematisch korrekt zu diesem Ausverkauf der Metriken. Da eToro REAL-Shorts ablehnt, filtern wir diese Instrumente auf Basis ihrer Performance ganz natürlich organisch im OOS-Gate heraus.
**Fix:** Kein Fix notwendig, es handelt sich um intendiertes Marktverhalten. Zukünftige Evaluatoren dürfen hierbei nicht auf einen vermeintlichen Precision-Mangel rückschließen.

### 🟢 #41 — Zero-Spread in Synthetic Ticks
**Symptom:** Backtesting führt zu physikalisch unplausiblen Ergebnissen (z.B. Max-Drawdown < 1 % bei hunderten Trades), da die synthetischen 1-Stunden-QuoteTicks einen Zero-Spread aufweisen und somit Ausführungen im Backtest de facto kostenfrei zum Midprice erfolgen. Dies bläht Risk-Metriken (Sortino, PF) artifiziell auf.
**Root Cause:** Beim Umwandeln von stündlichen Kerzen in Ticks entsteht kein natürlicher Bid/Ask Spread, wodurch `fill_model="bid_ask"` ins Leere greift. Ein reiner Order-Fill-Workaround genügt nicht, da die Ticks direkt im Katalog-Ladezyklus die Engine beliefern.
**Fix:** Implementierung einer dynamischen `spread_bps_by_asset_class` Parameter in `backtest.json`. `load_ticks_from_catalog` weitet nun aktiv Bid- und Ask-Preise anhand dieser BPS-Konfiguration direkt beim Lesen aus den Parquet-Metadaten. Ergänzend wurde eine statische FIFO-Kommission (`commission_bps`) im PnL-Matching-Prozess hinzugefügt.
**Betroffen:** `automation/backtest_runner.py`, `automation/config/backtest.json`

### 🟢 Issue #257 — Zweistufige Selektion & OOS-Gating Transparenz
**Symptom:** Diskrepanz zwischen IS-tauglichen Paaren und tatsächlichen Symbol-Gewinnern ohne nachvollziehbares Rejection-Log. Verzerrung der Rank-Normalisierung durch vorzeitigen Ausschluss von OOS-Fails.
**Root Cause:** OOS-Fails wurden vor dem Scoring aus der Grundgesamtheit in `select_winners` entfernt, was die Population für die Normalisierung künstlich verkleinerte.
**Fix:** Umstellung auf "Rank first, Gate second". Die Metrik-Normalisierung findet auf der gesamten IS-tauglichen Population statt. Anschließend wird pro Symbol absteigend nach Score iteriert, bis der erste Kandidat das OOS-Gate besteht. Vollständiges Logging des OOS-Decision-Trails im Terminal (`[OOS-Drop]`) hinzugefügt.
**Betroffen:** `automation/backtest_runner.py`, `automation/tests/*.py`

### 🟢 #39 — TypeError in NautilusTrader balances API (Issue #181)
**Symptom:** Jeder Matrix-Backtest brach beim ersten geschlossenen Bar ab (`TypeError: 'method' object is not iterable`), was zu 0 Trades über alle Symbol/Strategie-Kombinationen führte.
**Root Cause:** Die NautilusTrader-API stellt `account.balances` als Methode (`cpdef dict balances(self)`) bereit, nicht als Eigenschaft/Attribut. Die Backtest-Strategien versuchten, die zurückgegebene Methode zu iterieren (`list(account.balances)`). Gleichzeitig stürzte das Formatieren von Metrics mit `None` (z.B. aus all-win Szenarien) ab, da Formatstrings auf `NoneType` nicht anwendbar sind.
**Fix:** Umstellung auf die typsicheren Methoden `account.balance_total()` und `account.balance_free()` unter Berücksichtigung des `Money`-Rückgabewertes, verpackt in einem try-except Block ohne raises (`AGENTS.md` §14). In den Metriken-Formatstrings `(val or 0.0)` verwendet.
**Betroffen:** `automation/strategies/hourly_strategy_base.py`, `automation/backtest_runner.py`, `automation/tests/test_strategy_duplication.py`

### 🟢 #40 — Datenspanne Toleranz / INSUFFICIENT DATA (Issue #193)
**Symptom:** Etwa 40 % der Backtests wurden wegen geringfügiger Datenunterschreitung (z. B. 149.8 statt 150 Tage) hart verworfen, weil der Data Span Check in `backtest_runner.py` keinen Toleranzpuffer bot.
**Root Cause:** Die Anforderung `_walk_forward_days` wurde strikt ohne Toleranz validiert, was insbesondere bei letzen Candles, die wenige Stunden vor dem berechneten Endpunkt lagen, zum Abbruch mit "[ERROR] INSUFFICIENT DATA" führte.
**Fix:** Die Konfigurationsvariable `span_tolerance_days` (Standard 1.0) wurde in `backtest.json` eingeführt und wird nun im Data Span Check (`check_data_span`) berücksichtigt. Minimale Defizite verursachen nun nur noch ein `WARNING`, lassen den Backtest aber passieren.
**Betroffen:** `automation/backtest_runner.py`, `automation/config/backtest.json`

### 🟢 #38 — Strategy Matrix Execution Mismatch (Issue #152)
**Symptom:** Das System lud Defaults für 8 Strategien, aber führte nur 7 Strategien im Backtest-Matrix-Loop aus. Die Gesamtanzahl der Jobs lag bei 343 statt den erwarteten 392.
**Root Cause:** `HourlyMeanReversionStrategy` war in `strategy_defaults.json` definiert, fehlte jedoch in der aktiven `strategies.json` als Ausführungsziel. Zudem fehlte eine Validierung, die sicherstellt, dass definierte Defaults auch tatsächlich als aktive Strategien registriert sind.
**Fix:** `HourlyMeanReversionStrategy` wurde mit `active=true` zur `strategies.json` hinzugefügt. Zusätzlich wurde eine Laufzeit-Assertion in `automation/backtest_runner.py` implementiert, die das Backend hart abstürzen lässt, wenn die Anzahl der geladenen Defaults nicht exakt mit der Anzahl der auszuführenden aktiven Strategien übereinstimmt. *Jede Strategie, die in den Defaults geladen wird, MUSS zwingend im Execution Loop enthalten sein, es sei denn, sie wird explizit per Bypass übersprungen.*
**Betroffen:** `automation/backtest_runner.py`, `automation/config/strategies.json`, `automation/AGENTS.md`

### 🟢 #37 — Sortino Ratio Explosion & Tournament Artefakte (Issue #151)
**Symptom:** Unrealistisch hohe Sortino-Werte (> 200) führen zu einer fehlerhaften Selektion von Gewinnern im Tournament, wobei oft Low-Yield-Strategien mit minimaler absoluter Rendite bevorzugt werden.
**Root Cause:** Bei einer geringen Anzahl von Trades oder fehlenden Verlusten tendiert die Downside-Deviation (`dd_dev`) gegen null. Die Division im Sortino-Nenner erzeugt mathematisch explodierende Werte (Artefakte), die keine echte Performance widerspiegeln.
**Fix:** Einführung eines harten Caps (Winsorizing auf max. 50.0), einer Absicherung des Nenners (`max(dd_dev, 1e-6)`), eines Sanity-Gates (Cap auf max. 2.0 bei `total_return < 0.5%`) und eines Minimum-Downside-Gates (Cap auf max. 2.0 bei weniger als 2 echten Verlust-Trades und unter 50 Gesamt-Trades).
**Betroffen:** `automation/backtest_runner.py`

### 🟢 #36 — Precision API Parsing & Datenkorruption (Issue #146)
* **Hinweis (Issue #179 / Issue #231):** Die eToro API liefert beim Endpoint `instruments` derzeit oftmals **keine Precision-Felder für reine Equities**. Dies ist ein bekanntes Limit der API, weshalb das fehlende Feld für Equities kein Fehler ist. Der in `_fallback_precisions` verwendete Standard-Fallback auf `(2,2)` ist funktional korrekt und fängt dieses Verhalten sicher ab. False-Positive-Warnungen in `api_backfiller.py` bei Batch-Requests, die ausschließlich aus Equities bestehen, wurden zu `DEBUG` herabgestuft, um irrelevante Warnungen im `historical_fetcher` zu vermeiden.
**Root Cause:** eToro änderte die API-Struktur (`instruments` -> `instrumentDisplayDatas`). Der alte Parser lieferte 0 Treffer. Das System fiel stumm auf einen blinden `(2, 2)`-Fallback für alle Instrumente zurück (inklusive Krypto/Fractional).
**Symptom:** Krypto-Parquets wurden mit `size_prec=2` geschrieben. Der Mismatch zwischen Arrow-Metadaten und den generierten i128-Ticks führte unweigerlich zu `RuntimeError`-Abbrüchen im Nautilus-Matrix-Backtest.
**Lösung & Architektur-Regeln:**
1. **Strict Partial Fail Drop:** Partielle API-Ausfälle bei der Precision-Abfrage dürfen niemals in einen generischen `=2`-Fallback rutschen. Fehlt die Precision für ein Instrument in der `instrumentDisplayDatas`-Struktur (oder schlägt der int-Cast fehl), muss dieses Instrument zwingend aus dem aktuellen Backfill-Batch ausgeschlossen (geskippt) werden.
   > **Update (Issue #171, 2026-06-04):** Die „Strict Partial Fail Drop"-Regel gilt weiterhin für **explizite** API-Mismatches (API liefert z. B. `size_prec=2` für ein Non-Equity). Der reine **None-Fall** (API liefert gar keine Precision) wird im vorvalidierten Universe NICHT mehr gedroppt, sondern über `_fallback_precisions` aufgefüllt — inklusive Standard-Equities `(2,2)`.
2. **Sanity Check Enforcement (Equities vs. Non-Equities):** Ein Mismatch zwischen dynamisch geparster Precision (z. B. `size_prec=2`) und der durch `_fallback_precisions` erwarteten Precision für Non-Equities (z.B. Krypto, Fractional) ist keine bloße Warnung, sondern führt zum sofortigen Drop des Instruments aus der Schleife (`continue`), bzw. Hard-Fail bei `STRICT_PRECISION_FAIL`.
3. **Tick & Arrow Meta Synchronization:** Unsaubere Instrument-Precisions führen unmittelbar zu `RuntimeError`-Crashes im Matrix-Backtest. Die Parameterübergabe an `_candles_to_arrow_table` und `_build_arrow_meta` muss 100 % synchron laufen, weshalb falsche oder ratende Metadaten das System nie passieren dürfen.

> **Legende:** 🔴 OFFEN (im aktuellen Code aktiv) · 🟡 TEILWEISE · 🟢 BEHOBEN/dokumentiert

### 🟢 #14 — `create_mock_instrument` übergeht den eigenen Docstring (Haupt-Bug: 0 Trades)
**Symptom:** Backtest liefert über alle Symbole × Strategien `Trades=0`, `0 eligibel`, `0 Gewinner`. Logs zeigen `size=0 (parquet meta)`.
**Root Cause:** `backtest_runner.py:create_mock_instrument` dokumentiert „`size_precision`: Ignoriert — immer 8", implementiert aber `sp = size_precision if size_precision is not None else 8`. Der aktive Aufrufer (`run_single_backtest_worker`) übergibt `size_precision=sp_parquet`, wobei `sp_parquet=0` aus den Parquet-Metadaten stammt (`read_precisions_from_parquet`). Da `0 is not None`, schlägt die 0 durch → `size_increment=1.0` → ganzzahlige Order-Größe → bei Aktienkursen > `trade_amount_usd` rundet `make_qty()` auf 0 → jedes Signal verworfen.
**Fix:** `sp = size_precision if (size_precision is not None and size_precision > 0) else 8`.
**Betroffen:** `automation/backtest_runner.py`.

### 🟢 #20 — Drei divergierende `_compute_quantity`-Implementierungen
**Symptom:** Inkonsistentes Verhalten je nach Strategie-Basisklasse; teils stille Signal-Verwerfung.
**Root Cause:** Drei unterschiedliche Implementierungen:
1. `hourly_strategy_base.py` nutzt `instrument.make_qty(units, round_down=True)` und greift nicht auf `lot_size` zu.
2. `momentum_ls_base.py`, `adx_atr_momentum.py`, `trend_pullback.py` nutzen `float(instrument.size_increment)` — bei `size_increment=1` und `units<1` → `return None`.
3. `fractional_trading.safe_compute_quantity` nutzt `size_increment`, wird aber nirgends aufgerufen.
**Fix:** Eine einzige Implementierung in `HourlyStrategyBase`, die `make_qty` selbst über `size_precision` entscheiden lässt, statt manuell gegen `lot_size`/`size_increment` zu prüfen. Doppelimplementierungen entfernen.
**Betroffen:** `automation/strategies/hourly_strategy_base.py`, `momentum_ls_base.py`, `adx_atr_momentum.py`, `trend_pullback.py`.

### 🟢 #21 — `safe_compute_quantity` ist toter Code
**Symptom:** Die als Pitfall-#14-Fix gedachte Funktion in `fractional_trading.py` wird von keiner Strategie aufgerufen; der dokumentierte Schutz greift im Backtest nicht.
**Fix:** Die ungenutzte Funktion `safe_compute_quantity` wurde vollständig aus der Codebasis und den Tests entfernt. Die Logik ist nun alleinig in `HourlyStrategyBase.make_qty` konsolidiert (siehe #20).
**Betroffen:** `automation/fractional_trading.py`.

### 🟢 #19 — `momentum_ls_run.py` verletzt das Standalone-Prinzip
**Symptom:** Live-Pfad ist nicht hermetisch.
**Root Cause:** `from archive.adapters.etoro_data import …` und `from archive.adapters.etoro_config import …`. `automation/` soll laut Abschnitt 4 keine externen Importe haben.
**Fix:** Adapter in `automation/adapters/` migriert und Isolations-Test entsprechend angepasst. Keine dokumentierte Standalone-Ausnahme mehr.
**Betroffen:** `automation/momentum_ls_run.py`.

### 🟢 #25 — Tournament-OOS-Kriterien Validation Warning
**Symptom:** Startup-Validierung meldet, dass `oos_min_trades` und `oos_min_total_return` definiert, aber nicht referenziert sind.
**Fix:** `load_tournament_config` streicht den `oos_` Prefix bei der Validierung. OOS-Kriterien werden vom Evaluator im `check_oos=True` Zweig genutzt, die Warnung war ein False-Positive.
**Betroffen:** `automation/backtest_runner.py`.

### 🟢 #22 — Alle Tournament-Gewinner werden auf MomentumLSSmaStrategy reduziert

**Symptom:** Egal welche Strategie das Tournament pro Symbol gewinnt, live läuft immer SMA(5).
**Fix:** Dynamische Registry aus `strategies.json`, echte Gewinner-Strategie wird live registriert, Allocator-Hook in `HourlyStrategyBase`, PoC-Dateien entfernt, Live-`bar_type` auf 1h umgestellt (MID-INTERNAL), QuoteTick-Subscription in allen aktiven Strategien.
**Betroffen:** `automation/momentum_ls_run.py`, `automation/strategies/*.py`.

### 🟢 #23 — `size_precision=0` wird an der Quelle persistiert (Live + Catalog)
**Symptom:** Selbst nach Fix von #14 bleiben Live-Metadaten kaputt.
**Root Cause:** `catalog_service.py` (ZIP-Metadaten), `api_backfiller._build_arrow_meta` und `utils._fallback_precisions` schreiben für Equities `size_precision=0`. `daily_orchestrator._ensure_metadata` übernimmt diese.
**Fix:** Schreibseite auf `size_precision=2` für Equities angehoben. `regenerate_precision.py` wurde ersatzlos gelöscht. Korrupte Katalogdaten aus der Quelle müssen komplett neu aufgebaut werden (`--reset-catalog`).
**Betroffen:** `automation/utils.py`, `automation/api_backfiller.py`, `automation/catalog_service.py`, `automation/daily_orchestrator.py`.

### 🟢 #24 — Datendichte vs. Indikator-Warmup (Walk-Forward OOS-Guard)
**Symptom:** Backtests rechneten mit 30 Tagen Historie ein 90d+30d Walk-Forward-Fenster, emittierten verzerrte OOS-Metriken und gewannen Turniere ohne Warnung (Issue #105). Strategien mit langen Perioden initialisierten bei kurzen Datenfenstern spät oder gar nicht.
**Fix:** Guard im `backtest_runner.py` implementiert (überspringt, wenn `span_days < required_days * 0.95`). Die Beschaffungstiefe für den `historical_fetcher` im `daily_orchestrator.py` dynamisch an das Walk-Forward-Fenster (inklusive Puffer) gekoppelt statt fix `min_bars=200`. Die Zuweisung von `_walk_forward_days` wurde in Issue #121 gefixt, um den Guard erfolgreich zu triggern.
**Betroffen:** `automation/backtest_runner.py`, `automation/daily_orchestrator.py`.

### 🟢 #15 — `BrokenProcessPool` durch OOM
Worker auf `cpu//2` (max 6) begrenzt, `max_tasks_per_child=1`, expliziter Catch + sequenzieller Fallback. **Behoben** in `backtest_runner.py`.

### 🟢 #16 — PyArrow 24+ `BinaryView` → Nautilus Rust-Panic
Alle Quellen schreiben jetzt `pa.binary(16)` (= FixedSizeBinary(16)) nativ; Migration entfällt. **Behoben** (Shift-Left).

### 🟢 #17 — Flat-Lock nach Reverse Entry
Signal-State wird nach `_close_position()` auf `None` zurückgesetzt. **Behoben** in allen aktiven Strategien.

### 🟢 #18 — `make_qty` ValueError bei Equities
`round_down=True` verhindert den ValueError NICHT. Zweistufige Absicherung (Pre-Check + try/except) dokumentiert. **Teilweise** umgesetzt — siehe #20/#21 für die verbleibende Inkonsistenz.


### 🟢 #44 — Asynchrone Speicherung (Deferred Flush Bug)
**Symptom:** Datenverlust oder Inkonsistenzen beim Schreiben der Puffer.
**Fix:** Korrektes Flush-Handling implementiert (dokumentiert in `Test_report.md` via `test_do_flush`).
**Betroffen:** `automation/catalog_service.py`.

### 🟢 #26 — Fehlendes Cleanup temporärer Verzeichnisse
**Symptom:** Mögliche Dateisystem-Müllansammlung bei fehlerhaftem Backtest-Abbruch.
**Fix:** Absicherung des `temp_catalog_dir` Cleanups durch einen harten `try/finally`-Block (via `patch_try_finally.py`).
**Betroffen:** `automation/backtest_runner.py`.

### 🟢 #27 — Divergierende size_precision-Heuristiken
**Symptom:** Es existieren drei widersprüchliche Heuristiken für Equity `size_precision`: `automation/utils._fallback_precisions` liefert 2, während `automation/adapters/instrument_utils.get_size_precision` und `automation/fractional_trading._get_size_precision` fälschlicherweise 0 liefern.
**Fix:** Konsolidiert. `adapters/instrument_utils` und `fractional_trading` nutzen nun ausschließlich `automation/utils._fallback_precisions(symbol)[1]`. Redundante Implementierungen und Sets wurden entfernt.

### 🟢 KeltnerChannel `atr_period` Mismatch
**Symptom:** `mean_reversion.py` und `hourly_mean_reversion.py` führten `keltner_atr_period` in der Config, übergaben sie aber nicht an `KeltnerChannel(period=…, k_multiplier=…)`.
**Fix:** Parameter korrekt übergeben. Die `MeanReversionStrategy` wurde standardisiert (gemäß Changelog Issue #213 vom 2026-06-05).

### 🟢 #28 — Backtest BarType Diskrepanz (0 Trades / 0 Gewinner)
**Symptom:** Backtest liefert `Trades=0` für alle Strategien/Symbole, obwohl der Live-Pfad läuft.
**Root Cause:** Die Backtest-Engine nutzte hardcoded `1-MINUTE-MID-INTERNAL`, während historische Daten stündlich (1-HOUR) gestreamt werden und die Strategien (`HourlyStrategyBase`) für Stunden-Bars konfiguriert sind. Minuten-Bars wurden zwar aggregiert, aber bei stündlichen Quelldaten entstehen kaum bewegte Bars; zudem feuerte der 48-Bar-Time-Exit nach 48 Minuten statt 48 Stunden.
**Fix:** Hardcoded `1-MINUTE-MID-INTERNAL` in `backtest_runner.py` durch `1-HOUR-MID-INTERNAL` ersetzt.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 #29 — FSB(16) Encoding Error (0 Trades)
**Symptom:** 0 Trades im Backtest.
**Root Cause:** Falsche `10^precision`-Skalierung beim FSB16-Encoding statt `10^16`. Unentdeckt geblieben, weil der custom-Encoder nie von Nautilus nativ gelesen wurde.
**Fix:** Encoder in `_serde.py` auf `10^16` Skalierung geändert.
**Betroffen:** `automation/_serde.py`

### Daten-/API-Pitfalls (aus dem Adapter-Erbe, relevant für Live)
- **PnL-Envelope:** Reale PnL wrappt in `clientPortfolio` → immer `data.get("clientPortfolio", data)`.
- **`content` als JSON-String:** WebSocket-`content` ist meist String → `json.loads` falls `isinstance(str)`.
- **`IsMarketOpen`/`AllowBuy` sind Strings** (`"true"`/`"false"`), keine Booleans.
- **SHORT auf REAL stillschweigend verworfen** (IsBuy:False) für bestimmte Instrumente → Strategien als LONG-only validieren.
- **eToro verdoppelt StopLossRate intern** (gesendet 5 % → gespeichert ~10 % vom openRate).
- **PnL-Latenz 30–90s** für neue Positionen.
- **`min_notional`/`min_quantity`** in der CFD-Mock-Definition sind `None` → Backtest füllt Mini-Positionen, die live (eToro-Mindestbetrag ~$10–50) abgelehnt würden. Diskrepanz Backtest↔Live.

---


### Walk-Forward Evaluation & OOS Gate (Phase 5)
Die Backtest-Orchestrierung unterstützt nun eine Walk-Forward-Validierung mit Out-of-Sample (OOS) Gating.
- Das erweiterte historische Datenfenster wird dynamisch über `automation/config/backtest.json` definiert (z.B. `is_window_days=60`, `oos_window_days=7`).
- Während des Backtests generiert Nautilus PnLs über das gesamte Fenster. Die `extract_metrics` Funktion teilt die PnLs via Zeitstempel (`split_oos_start_ns` berechnet aus `_walk_forward_dict`) in `is_pnls` und `oos_pnls` ohne den Nautilus Rust Core zu beeinträchtigen.
- `daily_orchestrator.py` wertet in Phase 5 die `aggregate_winner` Performance aus. Wenn die OOS-Rendite negativ ist (Gate failed), wird das Live-Deployment des Bots gestoppt.



### 🟢 #30 — Rust Engine FFI Abort bei Signaturänderungen
**Symptom:** Subprozesse crashen mit `Fatal Python error: Aborted` aus `nautilus_trader.system.kernel.py __init__`.
**Root Cause:** Die Nautilus Rust-Engine crasht unweigerlich, wenn ein Python-Worker aufgrund unhandled Exceptions (wie `TypeError` bei inkonsistenten Funktionssignaturen) unsauber stirbt, bevor `engine.dispose()` gerufen wird. Dies trat auf, als `run_single_backtest_worker` ein neues Positionsargument erhielt, das in Multiprocessing-Pools und Tests nicht überall übergeben wurde.
**Fix:** Niemals die Signatur des Workers für Backtest-Konfigurationen ändern. Externe Variablen (wie Walk-Forward Splits) werden stattdessen in das `strat`-Dictionary injiziert (z.B. `strat["_walk_forward_dict"]`) und im Worker dynamisch per `.get()` ausgelesen.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 #31 — Metrik-Dictionary Nesting Bug
**Symptom:** Tests schlagen fehl, weil keine Trades ausgewiesen werden (`total_trades = 0`), obwohl Trades stattfinden.
**Root Cause:** `extract_metrics` wurde erweitert, um `{"metrics": {...}, "oos_metrics": {...}}` zurückzugeben. Der Worker hat diese Struktur ungeprüft weitergegeben, was zu einer doppelten Verschachtelung führte (z.B. `res["metrics"]["metrics"]["total_trades"]`). Tests suchten auf der falschen Ebene.
**Fix:** Explizites Unpacking im Worker via `extracted_data.get("metrics")` und Fail-Safe-Fallback auf flache Dictionaries, falls die Extraktion fehlschlägt. Assertions für Trades müssen zwingend auf `> 0` bleiben, um stille Logik-Fehler sofort abzufangen.
**Betroffen:** `automation/backtest_runner.py`, `automation/tests/test_backtest_runner_bar_type.py`

### 🟢 #32 — Queue-Korruption bei Zeitfenster-Splits
**Symptom:** PnL-Werte sind in einem bestimmten Zeitfenster fehlerhaft oder leer.
**Root Cause:** Die FIFO-Position-Matching-Schleife wurde aufgrund eines Zeitstempel-Cutoffs vorzeitig unterbrochen.
**Fix:** FIFO-Logik muss immer über das gesamte Datenset (`IS + OOS`) unangetastet iterieren, da sonst offene Queues korrumpieren. Erst *nach* dem FIFO-Matching werden die generierten PnL-Tupel (`pnl, ts_event`) anhand des Cutoffs separiert.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 #33 — Tuple Unpacking Regression in extract_metrics
**Symptom:** `total_trades=0` in allen Strategien, leere Backtest-Metriken, keine Tournament-Gewinner.
**Root Cause:** Regression durch Mismatch der Tuple-Arity. `pnls_with_ts.append` generierte ein flaches 4-Tupel, aber die nachfolgende Loop versuchte in 3 Ziele zu entpacken.
**Fix:** Unpacking Loop wurde auf `for pnl, ts, ht, m_qty in pnls_with_ts:` korrigiert (4 Variablen entpackt). Referenz: Issue #132.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 #35 — `compute_tournament_score` konstanter Wert und fehlerhafte Gewinner-Reihenfolge
**Symptom:** Die Auswahl des Gewinners pro Symbol in `select_winners` hing nur von der Iterationsreihenfolge ab. Alle Strategien erhielten einen Score von `0.0`.
**Root Cause:** `compute_tournament_score` wurde mit `norm_metrics` aufgerufen, berechnete den Score jedoch auf Basis von `total_return` und `avg_holding_time_s`, welche in `norm_metrics` nicht existierten. Zudem wich die Implementierung von der dokumentierten Spezifikation ab (Composite-Score vs. Haltedauer-Rendite).
**Fix:** `compute_tournament_score` wurde so umgeschrieben, dass es die Metriken `sortino_ratio`, `profit_factor`, `win_rate` und `max_drawdown` gemäß den Gewichten aus `tournament.json` zu einem Composite-Score aggregiert.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 #43 — Profit Factor / Sortino NoneType Artefakte bei Zero-Loss (Issue #150 / #209 / #227)
**Symptom:** In Backtests mit 100% Win Rate generieren bestimmte Metriken mathematische Artefakte (z.B. Sortino Ratio = 50.0 oder Profit Factor = 50.0). Dies verzerrt die Aggregat-Mediane und Auswertung extrem, wenn die Werte durch künstliche Caps (`MAX_CAP = 50.0`, `CALMAR_CAP = 100.0`) künstlich hoch gehalten werden. Andererseits explodieren die Werte ohne Caps bei minimalen Verlusten ins Unendliche.
**Root Cause:** Fallback bei undefinierten Nennern (z.B. `gross_loss == 0`) waren hardcodierte `MAX_CAP`-Werte, die die Scores nach oben verzerrten und echte Resultate verfälschten. Das komplette Entfernen der Caps in #209 führte stattdessen bei minimalen Nennern zu Explosionen.
**Fix:** Undefinierte finanzmathematische Zustände (All-Win-Szenarien oder <2 Losses bei <50 Trades) erzeugen konsequent `None`. Extreme Werte bei validen Samples werden hart gekappt (Sortino/PF auf 50.0, Calmar auf 100.0). Dies verhindert Median-Verfälschungen bei der Aggregation. Im CLI-Output rendern die None-Werte distinkt als `n/a(win)` oder `n/a(<min)`. Eine dedizierte Filter-Gating-Logik in `_is_eligible` wirft diese None-Kandidaten proaktiv ab.
**Wichtige Architektur-Regel:** Downstream-Systeme in Evaluationen und Formatting müssen stets typensicher entwickelt werden, da Metrik-Extraktionen immer `None`-safe verarbeitet werden müssen! Die Rankings in `select_winners` nutzen nun `(m.get('metric') or 0.0)`, um die Metrik zu normalisieren.
**Betroffen:** `automation/backtest_runner.py`

## 17. Order Management & Async State Machine (Neu)
Alle stündlichen Strategien in `automation/strategies/` müssen für Exit-Bedingungen zwingend die Methoden der `HourlyStrategyBase` nutzen, um Event-Loop-Blockaden und Orphaned Orders zu vermeiden.
Limit-Exits (wie z.B. das native Profit-Target) werden **asynchron** verwaltet.
* Wenn eine Markt-Order (z.B. durch Time-Exit oder Mean-Reversion) platziert werden soll, **müssen** offene Limit-Orders über `self._pending_cancels` getrackt und asynchron storniert werden.
* Erst wenn die Callbacks (`on_order_canceled`, `on_order_filled`, `on_order_rejected`) das Set `self._pending_cancels` komplett geleert haben und die Position noch teilweise offen ist, feuert die Base-Class `self._execute_market_close()`.
* Strategien dürfen diesen asynchronen Fluss niemals durch blockierende While-Loops oder eigene Callback-Überschreibungen stören (ausgenommen via `super().on_...`).

## 18. Conventions für KI-Coding-Agents (Jules)

- **Standalone-Constraint** (Abschnitt 4) strikt einhalten — Ausnahme nur `momentum_ls_run.py` (Pitfall #19, behoben).
- Neue Strategien → `automation/strategies/`, von `HourlyStrategyBase` erben, in `strategies.json` registrieren.
- Neue Instrumente → `automation/config/instrument_map.json`.
- Precisions IMMER über `automation/utils._fallback_precisions` bzw. API — keine zweite Heuristik einführen.
- `os._exit(1)`-Konvention für WebSocket-Fehler beibehalten.
- Subprocess-stdout/stderr immer in eine Log-Datei umleiten.
- Vor jedem Commit: Pre-Flight-Checks (Abschnitt 13) und `pytest` laufen lassen.
- **Bugfixes chirurgisch halten:** Pitfalls #14, #20, #21 hängen zusammen (alle `size_precision`/`_compute_quantity`), sollten aber in nachvollziehbaren, einzeln testbaren Commits behoben werden. Bestehende CFD-Parquet-Metadaten nach einem #23-Fix regenerieren.
- **Test-Gates bei extract_metrics/FIFO:** Nach jeder Modifikation an `extract_metrics`, der FIFO-Matching-Schleife, der IS/OOS-Aufteilungsschleife oder der reportbasierten Datenextraktion MUSS `pytest automation/tests/test_backtest_runner.py -v` lokal fehlerfrei durchlaufen.
- **Tupel-Arity Koppelung:** Erzeugung (`pnls_with_ts.append(...)`) und Konsum (`for ... in pnls_with_ts`) der Trade-Tupel sind als gekoppeltes Paar zu behandeln: Ändert sich die Arity der erzeugten Tupel, MUSS die Entpackung im selben Commit angepasst werden.
- **total_trades Guards:** Assertions auf `total_trades > 0` in den Test-Suites dürfen unter keinen Umständen gelockert oder entfernt werden.

---

## 19. Changelog (Agent-Maintained)
> **Anweisung für Jules:** Bei jeder Änderung am `automation/`-Paket hier einen Eintrag (Datum, Beschreibung, Dateien) anhängen.

| Datum | Änderung | Dateien |
|-------|----------|---------|
| 2026-06-07 | **Issue #257 (Zweistufige Selektion & OOS-Gating Transparenz):** "Rank first, Gate second" Logik in `select_winners` implementiert. OOS-Fails werden nicht mehr vorzeitig aus der IS-Population für die Rank-Normalisierung gefiltert. Die Selektion iteriert nun pro Symbol über die sortierten Scores und bewertet OOS On-the-Fly. Klarer Logging-Trail (`[OOS-Drop]`) im Terminal ergänzt. Neue Return-Werte für `is_eligible_pairs` und `fully_eligible_pairs` in die JSON-Ausgabe und in den Test-Files übernommen. | `automation/backtest_runner.py`, `automation/tests/*.py`, `automation/AGENTS.md` |
| 2026-06-07 | **Issue #232 (Crypto Precision vs. Market Dynamics):** Untersuchung der extrem negativen Sortino-Werte bei Crypto-Assets (BTC, ETH) abgeschlossen. Es handelt sich *nicht* um einen Bug in der 8-Dezimalstellen-Quantisierung oder im FSB(16)-Encoding. Die negativen Werte (Szenario B) resultieren aus der Kombination von High-Frequency Long-only-Strategien, hohen Krypto-Spreads auf eToro (15 bps) und dem Unvermögen, Shorts zu handeln. Dokumentiert, um künftige Fehlinterpretationen zu vermeiden. | `automation/AGENTS.md` |
| 2026-06-06 | **Issue #205 (Fix Zero-Spread Artifacts via Dynamic Spreads):** Backtest-Ticks weisen nun einen Asset-Class-spezifischen Spread in Basis-Punkten (`spread_bps_by_asset_class`) auf, der direkt in `load_ticks_from_catalog` rekonstruiert wird, um artifiziell hohe Sortino/PF Metriken zu dämpfen. Zudem wurde `commission_bps` für eine netto FIFO-PnL Extraktion eingebaut. | `automation/backtest_runner.py`, `automation/config/backtest.json`, `automation/AGENTS.md` |
| 2026-06-05 | **Issue #210 (Fix Bimodal Strategy Distribution):** `VwapExhaustionStrategy` von täglichem VWAP-Reset auf Rolling VWAP (deque, 24 Bars) umgestellt und `deviation_threshold` auf 1.5% gesenkt. `ComboTrendVwapStrategy` um einen 12-Bar Cooldown-Guard erweitert und das BB-Touch-Fenster auf 10 Bars entspannt. Verhindert Tournament-Ausschlüsse durch < min_trades sowie massives Overtrading. | `automation/strategies/vwap_exhaustion.py`, `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json` |
| 2026-06-05 | **Issue #212 (Log-Spam & Duplicate Rejection Logs):** Per-Bar-Logs (Zustands-Outputs mit `BAR | Close`) in allen aktiven Strategien von INFO auf DEBUG herabgestuft, um das Log-Volumen signifikant zu reduzieren (Signale & Orders bleiben auf INFO). Doppelter Aufruf von `select_winners` im `backtest_runner.py` behoben, indem `write_tournament_json` nun die vorberechneten Gewinner und Rejections als Argumente übernimmt. Dies verhindert doppelte Konsolen-Outputs der 'Rejected'-Liste. | `automation/strategies/*.py`, `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-04 | **Issue #193 (Datenspanne Toleranz):** Einführung von `span_tolerance_days` (Standard: 1.0) in `backtest.json` und Implementierung eines Toleranz-Fensters beim Data Span Check in `backtest_runner.py`. Dies verhindert, dass Backtests (z. B. 149.8 statt 150 Tage) aufgrund minimal fehlender Stunden hart verworfen werden. | `automation/backtest_runner.py`, `automation/config/backtest.json`, `automation/tests/test_backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-04 | **Issue #181 (TypeError in NautilusTrader balances API):** `account.balances` in `_get_current_balance` als Iteration entfernt, da es eine gebundene Methode ist. Umgestellt auf die typsicheren `account.balance_total()` / `account.balance_free()` unter Berücksichtigung des `Money`-Typs. Sanity Check in Formatstrings (für `None`-Metriken) in `backtest_runner.py` eingebaut. Pitfall #39 dokumentiert. | `automation/strategies/hourly_strategy_base.py`, `automation/backtest_runner.py`, `automation/tests/test_strategy_duplication.py`, `automation/AGENTS.md` |
| 2026-06-04 | **Issue #152 (Strategy Matrix Execution Mismatch):** Hinzufügen der fehlenden `HourlyMeanReversionStrategy` in `strategies.json` und Implementierung eines Assertions-Guards in `backtest_runner.py`, um Inkonsistenzen zwischen konfigurierten Defaults und ausgeführten aktiven Strategien im Matrix-Backtest zukünftig hart abzufangen. | `automation/config/strategies.json`, `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-04 | **Issue #151 (Sortino Ratio Winsorizing & Sanity Gates):** Überarbeitung von `_calculate_stats` zur Dämpfung explodierender Sortino-Ratios. Enforces `dd_dev >= 1e-6`, caps Sortino at 50.0, introduces a Sanity-Gate for low returns (`< 0.5%`) and a Minimum Downside Gate (`neg_trades < 2` under 50 trades) unter Beibehaltung der Methodensignatur. | `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-03 | **Issue #133 (Regression-Guard):** Test-Härtung (Guard `total_trades > 0` nach Metriken-Entpackung) und PR-Gate für `extract_metrics` eingebaut, um stumme Fehler bei Tuple-Arity-Bugs frühzeitig abzufangen. Konventionserweiterungen eingefügt. | `automation/tests/test_backtest_runner.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-03 | **Issue #121 (Walk-Forward-Datenguard Toter Code):** Zuweisung von `_walk_forward_days` in `backtest_runner.py` hinzugefügt, da dieser Wert nicht gesetzt wurde und der Guard in `run_single_backtest_worker` nie getriggert hat. Pitfall #24 auf 🟡 gesetzt bis Live-Deploy. | `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-02 | **Issue #105 / Pitfall #24 (Walk-Forward OOS-Guard & Historical Fetcher Integration):** Guard im `backtest_runner.py` implementiert, der sicherstellt, dass die Datenspanne der geladenen Ticks das geforderte Walk-Forward-Fenster (IS+OOS) abdeckt. Die Beschaffungstiefe des `historical_fetcher.py` wurde im `daily_orchestrator.py` dynamisch an die konfigurierte Walk-Forward-Spanne (inkl. Puffer) gekoppelt. | `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/AGENTS.md` |
| 2026-06-01 | **Issue #84 (FlashCrashReversalStrategy Haltedauer optimieren):** `HourlyStrategyConfig` mit optionalem `profit_target_pct` eingeführt. Exit bei Rückkehr zum Mean (bb.middle) hinzugefügt. Event-Loop Blockaden in Nautilus durch Order-Spamming behoben (`self.cache.orders_open`). Optimierte Default-Parameter für `max_bars_in_trade=16` und `atr_trailing_multiplier=0.75` in `strategy_defaults.json` validiert und dokumentiert. | `automation/strategies/flash_crash_reversal.py`, `automation/strategies/hourly_strategy_base.py`, `automation/config/strategy_defaults.json`, `automation/tests/test_flash_crash_exits.py` |
| 2026-05-31 | **Issue #80 (KeyError: 'median_sortino' verhindert Tournament-JSON und Live-Deploy):** Konsistente Implementierung der Median-Berechnung (get_median) für die Tournament-Gewinner. Ersetzte den Key `mean_sortino` durch `median_sortino` in `aggregate_winner` (`automation/backtest_runner.py`), um KeyErrors beim Parsen (`daily_orchestrator.py`) in Phase 4 zu beheben. Der Standalone-Grundsatz wurde bewahrt. | `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/AGENTS.md` |
| 2026-05-31 | **Issue #88 (Overtrading Fix):** Behebung von exzessivem Overtrading in `DynamicBreakout` und `SmaCrossover` Strategien durch Einführung einer `cooldown_bars` (12 Bars) Debounce-Logik in den `on_bar` Methoden. Fehlerhafte Zustandsverwaltung bei Positionswechseln behoben, indem `self.current_signal` gezielt auf den neuen Status (`"BUY"`/`"SELL"`) gesetzt wird statt auf `None`. | `automation/strategies/dynamic_breakout.py`, `automation/strategies/sma_crossover.py`, `automation/tests/test_backtest_trades_generated.py`, `automation/tests/test_precision_mismatch.py`, `automation/AGENTS.md` |
| 2026-05-31 | **Issue #73 (Walk-Forward & OOS-Gate abgeschlossen):** Konfigurierbarer Split in `backtest.json`. `backtest_runner.py` trennt IS/OOS *nach* dem FIFO-Matching via Tuple-Filterung (`ts >= split_oos_start_ns`). Parameterübergabe erfolgt signatursicher über das `strat`-Dict (Pitfall #30). Explizites Dictionary-Unpacking im Worker verhindert Metrik-Verlust (Pitfall #31). `daily_orchestrator.py` erzwingt in Phase 5 das OOS-Gate (Fail-Closed). Tests konsolidiert (`total_trades > 0`). | `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/config/backtest.json`, `automation/AGENTS.md` |
| 2026-05-29 | **Fix FSB(16)-Encoding (Pitfall #29) & Zero-Spread:** Encoder in `_serde.py` auf Nautilus i128 High-Precision (`10^16`) umgestellt. `api_backfiller` auf Zero-Spread-Kerzen (`bid=ask=close`) umgestellt. `regenerate_precision.py` gelöscht, Katalog muss neu aufgebaut werden. Roundtrip/Trade-Tests hinzugefügt und bestehende Tests (precision_mismatch und bar_type) auf nativen Custom-Encoder und total_trades > 0 assertions umgebaut. | `automation/_serde.py`, `automation/api_backfiller.py`, `automation/catalog_service.py`, `automation/regenerate_precision.py`, `automation/tests/test_fsb16_roundtrip.py`, `automation/tests/test_backtest_trades_generated.py`, `automation/tests/test_precision_mismatch.py`, `automation/tests/test_backtest_runner_bar_type.py`, `automation/AGENTS.md`, `automation/Test_report.md` |
| 2026-05-29 | **Fix Backtest BarType Mismatch:** `run_backtest` und Worker nutzten hardcoded `1-MINUTE-MID-INTERNAL`, während Live-Pfad und Fetcher 1h nutzen (0 Trades Resultat). Auf `1-HOUR-MID-INTERNAL` umgestellt. | `automation/backtest_runner.py`, `automation/tests/test_precision_mismatch.py`, `automation/tests/test_backtest_runner_bar_type.py`, `AGENTS.md` |
| 2026-05-29 | **PR #64 final:** §6/§11 auf Ist-Stand synchronisiert (make_qty statt lot_size, QuoteTick-Subscription im Beispiel, allocator-Parameter, korrekte Vererbung inaktiver Strategien, Live-Deployment-Beschreibung), Fail-Fast bei 0 Registrierungen | `automation/momentum_ls_run.py`, `AGENTS.md` |
| 2026-05-29 | **Hotfix PR #64:** Config-Felder als Strings übergeben (Crash-Fix), §8-Tabelle/Absatz auf size_precision=2 vervollständigt, Instanziierungs-Smoke-Test ergänzt. | `automation/momentum_ls_run.py`, `automation/tests/test_live_strategy_mapping.py`, `AGENTS.md`, diverse Strategien |
| 2026-05-29 | **Fix Pitfall #22 (Live-Strategie-Reduktion)** — Dynamische Registry in `momentum_ls_run.py`, Allocator in `HourlyStrategyBase`, PoC-Dateien entfernt, 1h-bar_type (MID), QuoteTick-Subscriptions in allen Strategien ergänzt. Dokumentations-Korrekturen (C2-C6). | `automation/momentum_ls_run.py`, `automation/strategies/hourly_strategy_base.py`, `automation/strategies/sma_crossover.py`, diverse Strategien, AGENTS.md, Test_report.md |
| 2026-05-29 | **Synchronisation von Code und Dokumentation:** Auflösung der `safe_compute_quantity`-Diskrepanz (Pitfall #21), Korrektur des Regenerations-Status (Pitfall #23) und Nachtrag der Fixes für Deferred Flush (#25) sowie Try/Finally-Cleanup (#26). | `automation/AGENTS.md`, `automation/Test_report.md`, `automation/fractional_trading.py` |
| 2026-05-28 | **Fix size_precision Bug Chain (#14, #20, #21, #23)** — Angepasst an eToro by-amount Semantik (size_precision=2 für Equities), konsolidierte quantity Berechnung auf make_qty in HourlyStrategyBase, tote Methode safe_compute_quantity entfernt. (Pitfall #14 war bereits teilweise behoben, Ticks normalisiert). Bestehende CFD-Parquet-Metadaten müssen später nach einem separaten Task regeneriert werden. | `automation/utils.py`, `automation/api_backfiller.py`, `automation/catalog_service.py`, `automation/backtest_runner.py`, `automation/strategies/hourly_strategy_base.py`, `automation/strategies/momentum_ls_base.py`, `automation/strategies/adx_atr_momentum.py`, `automation/strategies/trend_pullback.py`, `automation/fractional_trading.py` |
| 2026-05-28 | **automation/AGENTS.md neu erstellt** — vollständig auf `automation/` abgeglichen, alle offenen Bugs als Pitfalls #14–#24 mit STATUS-Kennzeichnung dokumentiert (size_precision-Kette, divergierende _compute_quantity, toter safe_compute_quantity, archive.adapters-Import, SMA-PoC-Reduktion). | `automation/AGENTS.md` |
| 2026-05-28 | #19: Adapter in `automation/adapters` migriert (Hermetisches Standalone). #23: Schreibseite `size_precision=2`. | `automation/AGENTS.md`, `automation/momentum_ls_run.py`, `automation/adapters/*`, `automation/api_backfiller.py`, `automation/utils.py`, `automation/catalog_service.py` |
| 2026-05-28 | size_precision=8 für Mock-Instrumente *intendiert* (Cfd(EQUITY)); im Code jedoch durch Parameter-Durchschlag von sp_parquet=0 unwirksam — siehe Pitfall #14. (Update: Seit #135 wird asset-bewusst fallback 2 für Equities und 8 für Crypto verwendet). | `automation/backtest_runner.py` |
| 2026-05-28 | HourlyStrategyBase (ATR-Trailing 1.5× + 48-Bar-Time-Exit); alle aktiven Strategien erben davon. | `automation/strategies/hourly_strategy_base.py`, `automation/strategies/*.py` |
| 2026-05-28 | historical_fetcher.py (Deep Backfill 12M, Kaskade OneHour→OneDay); Phase 2d im Orchestrator; 30-Tage-Backtest-Fenster; --reset-catalog. | `automation/historical_fetcher.py`, `automation/daily_orchestrator.py` |
| 2026-05-28 | dynamic_breakout.py (Price-Range), vwap_exhaustion.py (Price-Deviation only) — Volume-Abhängigkeit entfernt (synthetische Bars volume=1.0). | `automation/strategies/dynamic_breakout.py`, `automation/strategies/vwap_exhaustion.py` |
| 2026-05-27 | `automation/` als eigenständiges Produkt etabliert — kein adapters/-Import (Ausnahme momentum_ls_run.py). | alle automation/*.py |

| 2026-05-28 | size_precision=8 Fix via PyArrow Schema-Injection implementiert (Pitfall #14). | `automation/backtest_runner.py` |
| 2026-05-29 | **Issue #72 (`min_trades` Erhöhung):** Die Schwelle für `min_trades` in der Tournament-Config und den Default-Werten wurde von 4 auf 20 angehoben, um robustere Ratios (Sortino, Profit-Factor) auf Basis einer statistisch tragfähigeren Stichprobe zu gewährleisten. | `automation/config/tournament.json`, `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-01 | **Issue #102 (Divergierende size_precision-Heuristiken behoben - Pitfall #27):** `get_size_precision` in `adapters/instrument_utils.py` und `fractional_trading.py` entfernt/angepasst, um ausschließlich `automation/utils._fallback_precisions` zu nutzen. Equity size_precision ist nun über Backtest und Live-Adapter konsistent (2). | `automation/adapters/instrument_utils.py`, `automation/fractional_trading.py`, `automation/tests/test_size_precision_fixes.py` |
| 2026-05-31 | **Refactored HourlyStrategyBase to use HourlyStrategyConfig for optimizable exit parameters (Issue #4):** Replaced hardcoded constants for `atr_period`, `atr_trailing_multiplier`, and `max_bars_in_trade` with a dedicated `HourlyStrategyConfig` class inheriting from `StrategyConfig`. Refactored all active strategies to inherit from `HourlyStrategyConfig` and dynamically utilize these exit parameters from `self.config` to enable algorithmic optimization of holding periods. | `automation/strategies/hourly_strategy_base.py`, `automation/strategies/*.py`, `automation/AGENTS.md` |
| 2026-06-02 | **Issue #103 & Backtest Bug Fixes:** Korrektur der `msgspec.Struct` Vererbungshierarchie (`HourlyStrategyConfig` / `HourlyMeanReversionConfig`) via `kw_only=True` und Verschiebung der Strategie in den inaktiven Block der Dokumentation. Sowie Behebung des Tuple-Unpacking-Fehlers `(holding_time_ns, match_qty)` und der NameErrors (`is_holding_times`) in `extract_metrics` im `backtest_runner`. | `automation/strategies/hourly_strategy_base.py`, `automation/strategies/hourly_mean_reversion.py`, `automation/strategies/*.py`, `automation/AGENTS.md`, `automation/backtest_runner.py` |
| 2026-06-04 | **Issue #135 (Inkonsistenz Mock-Instrumente):** `_normalize_size_precision` in `backtest_runner.py` ist nun asset-bewusst und nutzt `_fallback_precisions(symbol)`. Equities fallen nun korrekterweise auf `2` zurück anstatt pauschal auf `8`. Das behebt die Inkonsistenz zwischen Live-Pfad, Parquet-Katalog und Backtest-Mock für Equities. Da alte Parquet-Daten im Katalog ggf. noch `size_precision=0` tragen, ist nach diesem PR ein `--reset-catalog` Lauf erforderlich, um einen sauberen Zustand zu erzwingen. | `automation/backtest_runner.py`, `automation/tests/test_size_precision_fixes.py`, `automation/AGENTS.md` |
| 2026-06-03 | **Issue #132 (Pitfall #33):** Behebung der Tuple-Arity-Regression in `extract_metrics` (flaches 4-Tupel-`append` vs. 3-Ziel-Entpackung). Der Fehler wurde explizit als Regression von Pitfall #31/#103 gekennzeichnet. | `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-03 | **Issue #134 (Pitfall #35):** Behebung des konstanten 0.0-Scores in `compute_tournament_score` und der reihenfolgeabhängigen Gewinnerauswahl. Die Funktion nutzt nun die dokumentierten Composite-Gewichte. Ein Test zur Sicherstellung der korrekten Sortierung wurde ergänzt. | `automation/backtest_runner.py`, `automation/tests/test_backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-03 | **Issue #147 (Fix Overfitting & OOS Gating):** Einführung der harten Train/Test (IS/OOS) Separierung mit verbessertem Logging und striktem OOS-Gating gegen fehlende Trades. Einführung eines `min_expectancy` Thresholds in `tournament.json`, um Sortino-Artefakte ohne Return (wie FlashCrashReversal) auszuschließen. Walk-Forward Architektur (State Bleed) in Phase 5 korrigiert (siehe Architektur-Dokumentation weiter unten). | `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/config/tournament.json`, `automation/AGENTS.md` |
| 2026-06-05 | **Issue #153 (Fix Position Sizing & Tournament Return Gating):** `trade_amount_pct` zur `HourlyStrategyConfig` hinzugefügt, um Position Sizing dynamisch (prozentual) relativ zur Equity im Backtest und Live-Modus zu berechnen. Statische Beträge (`trade_amount_usd`) wurden abgelöst. Total Return als `Compounded Equity-Normalized Return` dokumentiert. Harter Gate in `tournament.json` implementiert (`min_total_return: 0.005`), um unprofitable Strategien mit hohem Sortino sicher abzuweisen. Regression-Tests mit `total_return`-Guard ergänzt. | `automation/strategies/hourly_strategy_base.py`, `automation/config/strategy_defaults.json`, `automation/config/tournament.json`, `automation/backtest_runner.py`, `manuals/backtesting_manual.md`, `automation/tests/test_tournament_validation.py`, `automation/AGENTS.md` |
| 2026-06-04 | **Issue #171 (Strict (2,2)-Precision-Reject entfernt):** Der harte `(2,2)`-Reject im `None`-Pfad von `fetch_precisions_from_api` (`api_backfiller.py`) wurde entfernt (kein `log.error` + `continue` mehr). Fehlt die API-Precision, füllt der Symbol-Fallback `_fallback_precisions` Standard-Equities (TSLA, GOOG, NVDA) jetzt nahtlos mit `(2,2)` auf, statt sie aus dem Backfill zu werfen. Eliminiert das ERROR-Log-Spam in Phase 2 des `daily_orchestrator` für das vorvalidierte Universe. Der Mismatch-Guard für explizit gelieferte `size_prec=2` bei Non-Equities (Pitfall #36) bleibt unangetastet. Regressions-Test für `(2,2)`-Mapping ergänzt. | `automation/api_backfiller.py`, `automation/AGENTS.md`, `automation/tests/test_api_precisions.py` |
| 2026-06-05 | **Issue #179 (False Alarm Bug Data-Pipeline):** Log-Spam für fehlende API-Precisions bei korrekten Equities behoben. Es wurde dokumentiert, dass die eToro API derzeit beim Endpoint `instruments` keine Precision-Felder für Equities zurückgibt und der Fallback auf `(2,2)` funktional korrekt ist. Statt `ERROR`-Logs werden nun `DEBUG`-Meldungen mit einem Response-Dump zur weiteren Analyse generiert. Pitfall #36 Guard für Non-Equities bleibt weiterhin aktiv. | `automation/api_backfiller.py`, `automation/catalog_service.py`, `automation/AGENTS.md` |
| 2026-06-06 | **Issue #206 (Real Rolling Walk-Forward):** Implementierung des echten Rolling Walk-Forward und OOS-Gating-Fixes. Strikte Evaluierung der benötigten Datenspanne und retrospektiver rollierender Split in `extract_metrics` integriert, während das 'State Bleed'-Paradigma zur Performanceoptimierung erhalten bleibt. Fehlerhafter Fallback im Orchestrator entfernt. | `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/AGENTS.md` |
| 2026-06-07 | **Issue #231 (False Positive API Warning):** Herabstufung der 'Keine Felder'-Warnung auf DEBUG in `api_backfiller.py`, wenn der Batch ausschließlich aus erwarteten Equities besteht. Log-Ausgabe in `historical_fetcher.py` für bessere Transparenz angepasst. Pitfall #36 Guards bleiben strikt erhalten. | `automation/api_backfiller.py`, `automation/historical_fetcher.py`, `automation/AGENTS.md` |
| 2026-06-07 | **Issue #256 (Fix Tournament Observability & Config Logging):** Vollständige Offenlegung aller Tournament-Gates (inklusive hidden gates wie min_win_rate, min_total_return, min_expectancy) im Backtest-Startup-Log implementiert. Dies verhindert irre führende Rejection-Logs und verbessert das Debugging von Sizing-Bugs. | `automation/backtest_runner.py`, `automation/AGENTS.md` |

## Architektonische Methodik: IS/OOS Split und "State Bleed"

Der `daily_orchestrator.py` und der `backtest_runner.py` nutzen nun ein echtes, rollierendes Walk-Forward (`walk_forward_active: true`). Das Train/Test (IS/OOS) Splitting basiert aus Performancegründen weiterhin auf einem einzigen, durchgehenden Engine-Run des `backtest_runner.py`. Die Aufteilung in `n=splits` rollierende Fenster erfolgt jedoch *retrospektiv* anhand der Timestamp-Filterung über die gesamte Spanne während der Metrik-Extraktion in `extract_metrics`.

**Wichtige Limitationen für Agenten (State Bleed):**
- **Kein Hard-Reset:** An der IS/OOS-Grenze findet kein Zurücksetzen der Engine statt. Das bedeutet, dass laufende offene Positionen, das angesammelte Account-Guthaben sowie die Historie aller Indikatoren (z.B. aufgewärmte EMAs, RSI-Werte) ungefiltert aus der In-Sample Phase in den Out-of-Sample Zeitraum überfließen ("State Bleed").
- **Gültigkeit der OOS-Metriken:** OOS-Ergebnisse sind somit methodisch nicht 100% "rein" oder vollständig unabhängig vom In-Sample Lauf. Dieser Kompromiss wird derzeit bewusst akzeptiert, um Backtesting-Overhead und Laufzeiten zu minimieren.
- Zukünftige Code-Änderungen an Strategien oder Evaluierungs-Metriken müssen diese architektonische Gegebenheit berücksichtigen.

---

*Zuletzt aktualisiert: 2026-06-05. Datum und Changelog bei jeder Änderung an dieser Datei aktualisieren.*

## Known Pitfalls & Architecture Notes
* **Pitfall #41 (State-Mutation vor Early-Returns / Signal-Desync):** Strategien müssen ihren Signal-State (`current_signal`, `bars_since_last_signal`) zwingend erst NACH allen Guard-Checks und direkt vor `submit_order` setzen. Wenn der State vor einem Early-Return gesetzt wird, entsteht ein Desync (Agent blockiert Folge-Signale, obwohl keine Order ausgeführt wurde). Siehe Issue #211.
* **Type Casting in API Payloads:** Document the strict necessity of casting variables (e.g., str() vs int()) when matching eToro IDs from the API against the local JSON configurations, as implicit typing will cause silent drop-outs during universe construction.
* **Zero-Signal Metric Structures:** Note that extract_metrics must always return the explicit format {"metrics": None, "oos_metrics": None} (or similarly nested dicts) for empty signal generations, otherwise the daily orchestrator aggregation will fail.
* **Precision Mismatch Handling:** Explicitly warn that instrument-only parameter fixes for precision bugs are insufficient. Any precision adjustments must perfectly align with the actual tick precision of the underlying data. Failure to address this root cause will result in RuntimeError crashes that silently abort the matrix backtest loops.
* **Log Management:** Local backtest .log and .json files must be kept out of Git tracking to avoid repository bloat and blocked pushes. Always use `git checkout origin/main -- logs/` or explicitly unstage modified log files.
* **Pitfall #37 (Orphaned Limit Orders via Custom Exits):** Das Überschreiben von Exits in Child-Klassen ohne Beachtung von `orders_open` und dem Async-State der Base-Class führt zu Order-Spamming, Orphaned Limit-Orders und extrem asymmetrischen FIFO-Legs. Strategien müssen auf `self.cache.orders_open` nach Base Exits prüfen und sollten `self._close_position` oder `super()._close_position_base()` richtig delegieren, um Base-Code zu erhalten. (Referenz: Issue #149).
* **Pitfall #46 (Observability & Hidden Gates):**
  * **Symptom:** Rejection-Logs (z. B. `min_win_rate failed`) verweisen auf Schwellen, die im Startup-Header nie erwähnt wurden.
  * **Root Cause:** Hardcodierte Print-Statements für die Konfiguration, die neu hinzugefügte Parameter (wie Expectancy oder Drawdown) verschwiegen haben.
  * **Architektur-Regel:** Konfigurationen, die das Gating oder den Control-Flow steuern (speziell Tournament- und OOS-Thresholds), müssen beim Systemstart lückenlos (`req_all` und `req_any`) und transparent an stdout/Logger ausgegeben werden. "Hidden Gates" sind streng untersagt.

| 2026-06-04 | **Issue #172 (OOS-Gate Propagation & Transparenz):** `aggregate_winner` trägt nun deterministisch `oos_evaluated`/`oos_eligible`/`oos_metrics`/`oos_rejection_reasons`. Phase 5 dreistufig (eligible/failed/not-evaluable) mit präziser Begründung statt „Status unbekannt". Walk-Forward Ableitung vereindeutigt + OOS-Span-Logging. Fail-Closed unverändert, keine Schwellen-Absenkung. Tests für alle drei OOS-Zustände ergänzt. | `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/tests/test_backtest_runner.py`, `automation/tests/test_oos_gate.py`, `automation/AGENTS.md` |
| 2026-06-05 | **Issue #182 (Sizing-Precedence Bug im Backtest):** Behebung der unbeabsichtigten Priorisierung von `%` über `USD` bei gesetzten Defaults. `_compute_quantity` enforcing Hierarchy: `allocator > trade_amount_usd > trade_amount_pct > Default`. Regression-Test ergänzt. §6 `AGENTS.md` aktualisiert. | `automation/strategies/hourly_strategy_base.py`, `automation/tests/test_sizing_precedence.py`, `automation/AGENTS.md` |
| 2026-06-05 | **Issue #213 (Crypto Precision, Strategy Divergence & Trend Filter):** Fixed `_normalize_size_precision` falling back to `2` for crypto. Standardized `MeanReversionStrategy` to use `KeltnerChannel` and removed manual `_close_position` overrides to fix divergence. Added `trend_filter_period` to `HourlyStrategyConfig` with a `_warmup_trend_filter` in `HourlyStrategyBase` that loads and resamples parquet data to pre-warm the SMA filter locally, enforcing fail-closed gatekeeping via `can_go_long` before generating BUY signals. | `automation/strategies/hourly_strategy_base.py`, `automation/strategies/mean_reversion.py`, `automation/strategies/hourly_mean_reversion.py`, `automation/backtest_runner.py`, `automation/tests/test_api_precisions.py`, `automation/tests/test_live_execution_defaults.py`, `automation/AGENTS.md` |
| 2026-06-06 | **Issue #194 (Sizing Bug & Impossible Risk Metrics Guards):** Fixed sizing calculation bug causing microscopic returns by strictly quantizing quantity to align with tick precision and size_increment in `HourlyStrategyBase._compute_quantity`. Introduced `EPSILON = 1e-9` in risk metric denominators. Enforced sample size minimums for near-all-win situations (`losses_count < 2 and n < 50`) to return `None` in `backtest_runner.py._calculate_stats`. | `automation/strategies/hourly_strategy_base.py`, `automation/backtest_runner.py`, `automation/tests/test_backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-06 | **Issue #207 (Fix Tournament Expectancy Gating & IS Transparenz):** Absenkung von `min_expectancy` auf `0.00005`, neues Logging der Ablehnungsgründe in `_is_eligible()` sowie Cooldown-Logik für Mean-Reversion. Bugfix in `test_oos_aggregation.py` für `total_trades` Average-Calculation. | `automation/config/tournament.json`, `automation/backtest_runner.py`, `automation/strategies/mean_reversion.py`, `automation/strategies/hourly_mean_reversion.py`, `automation/AGENTS.md` |
* **Live-Trading Safety Rule:** Zero OOS-eligible pairs MUST strictly prevent any live deployment. The aggregate OOS pass cannot override a per-pair failure.
* **Data Structure Rule:** The `oos_metrics` object is a sibling key to `metrics` in backtest results, never nested. Always access via `r.get('oos_metrics')`.
