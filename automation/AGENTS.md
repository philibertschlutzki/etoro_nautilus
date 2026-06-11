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

- **`automation/optimizer/`**: Paket für Closed-Loop-Hyperparameter-Optimierung. Submodule umfassen `manifest`, `resolve`, `trial_config`, `runner`, `parsing`, `reward`, `spaces`, `confirm`, `run_optimization`.

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
Nach `_close_position()` beim Drehen einer Position MUSS der Signal-State (`current_signal` / `current_position`) auf `None` zurückgesetzt werden, sonst blockiert der Bar-Guard jeden Neueinstieg dauerhaft. Dies muss über den asynchronen Lifecycle-Callback `on_position_closed` erfolgen, da automatische Basisklassen-Exits (ATR/Time) andernfalls die Strategie einfrieren.

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

- **`optimizer.json`**:
  Konfiguriert die Hyperparameter-Optimierung.
  Keys: `n_trials`, `n_startup_trials`, `seed`, `penalty_overfit_weight`, `penalty_dd_weight`, `bonus_coverage_weight`, `penalty_unevaluable_oos`, `sortino_clip_abs`.
  Dynamische Reward-Gewichtung (Zero-Hardcoding): Gewichte (`penalty_overfit_weight`, `penalty_dd_weight`, etc.) werden direkt aus `optimizer.json` gelesen, das `max_drawdown`-Cap (DD-Cap) aus `tournament.json`.

- **`backtest.json` (Erweiterung)**:
  `walk_forward.holdout_days`: Anzahl der Holdout-Tage für Out-of-Sample Validierung nach der Optimierung.

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

Safety-Interlock: Zweistufiges Fail-Closed-Verhalten:
1. **Per-Pair Check:** Es wird zwingend geprüft, ob `fully_eligible_pairs > 0` und `winner_count > 0`. Falls nicht, bricht die Phase hart ab (`LIVE_DEPLOY_ABORTED`).
2. **Aggregat-OOS Evaluierung:** Danach muss der Aggregat-Gewinner ein gültiges und bestandenes OOS-Ergebnis vorweisen (`oos_evaluated` und `oos_eligible` == `True`).
Zusätzlicher Interlock: `environment=='real'` AND `dry_run==False` AND `ETORO_CONFIRM_LIVE=='1'` → sonst `sys.exit(1)`. Stale-Check: Prüft ob Universe-Daten älter als 24 Stunden sind.

---

## 12. Umgebungs-Setup (.env, requirements, systemd)

| Variable | Pflicht | Verwendet von |
|----------|---------|---------------|
| `ETORO_API_KEY` | Ja | alle Dienste |
| `ETORO_USER_KEY` | Ja | alle Dienste |
| `MOMENTUM_LS_USERNAME` | Ja (nur fetch) | universe_fetcher.py |
| `ETORO_CONFIRM_LIVE` | nur Live | momentum_ls_run.py Safety-Interlock |

Installation: `pip install -r automation/requirements.txt` (nautilus_trader>=1.226.0, aiohttp, websockets, pyarrow≥16, pandas, pytest). systemd-Unit für `catalog_service.py` mit `Restart=always`, `RestartSec=5`.

Ausführung:
```bash
python3 -m automation.daily_orchestrator --skip-api-fetch       # täglich (ZIPs vorhanden)
python3 -m automation.daily_orchestrator --dry-run --skip-api-fetch
python3 -m automation.api_backfiller --days 7
python3 -m automation.historical_fetcher --months 12
python3 -m automation.catalog_service                           # systemd
python3 -m automation.optimizer.run_optimization --strategy SmaCrossoverStrategy  # Optimizer Start
```

Hinweis: Wenn systemd-Unit-Files im Repo existieren, müssen die ExecStart-Anweisungen dort ebenfalls auf `python3 -m automation.catalog_service` aktualisiert werden.

---



## 12.5 Sicherheits-Leitplanken (Optimizer)
* Kein Live-Deploy aus dem Optimierer (Runner-Direktaufruf, Phase 5 nie betreten).
* Risiko-Gates eingefroren (tournament.json 1:1 kopiert, nie variiert).
* Holdout unberührt (keine Optimierungs-Auswertung sieht ihn).
* Human-in-the-Loop (Promotion nur per PR; Holdout-Ergebnis + Overfit-Gap im Review).
* Plausibilitäts-Wächter (Trials mit absurden Metriken werden markiert, nicht als Sieger gewertet).
## 13. Testing & Validierung

Tests in `automation/tests/`, Ausführung via `pytest`. Kein Test darf aus `adapters/`/`config/`/`strategies/` (Root) importieren. Naming: immer `_fallback_precisions` (mit Underscore).

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

### Optimizer / `backtest_runner.py` — Config-Contract

- `backtest_runner.py` liest Strategien + Parameter **ausschließlich** aus der via `--config` übergebenen Manifest-Datei. `strategies[].params` sind vollständig aufgelöst und autoritativ; **kein** erneutes Mergen aus `strategy_defaults.json`, sobald `manifest_version` gesetzt ist.

### 🟢 Pitfall #61 — Optimizer Manifest Contract & Catalog Path
**Symptom:** Subprozesse crashen sofort mit `ValueError` ("catalog_path is missing") beim Start von `run_backtest`.
**Root Cause:** Der `backtest_runner.py` verlässt sich im Strict-Manifest-Mode zwingend darauf, dass alle notwendigen Abhängigkeiten in `global_settings` verankert sind. Das dynamisch geschriebene Manifest (`experiment_manifest.json`) von Optuna in `trial_config.py` ließ das Feld `catalog_path` fallen.
**Fix:** Das Feld `catalog_path` wird jetzt in `build_trial` dynamisch aus der `backtest.json` des Config-Sets aufgelöst und mit einem wasserdichten Fallback versehen (z.B. relativ zu `WORK` / Projekt-Root) in die `experiment_manifest.json` übernommen.

### 🟢 Pitfall #62 — Optimizer Trial Log Directory Missing (Issue #346)
**Symptom:** Backtest-Subprozesse im Optimizer protokollieren keine Logs in den jeweiligen Trial-Ordnern oder werfen FileNotFoundError-Fehler im Log-Manager.
**Root Cause:** Die `build_trial` Funktion in `trial_config.py` erstellte zwar den `config`-Ordner, vergaß aber den `logs`-Ordner. Da der Subprozess-Runner (`runner.py`) jedoch strikt `ETORO_LOGS_DIR=trial/logs` erzwingt, liefen die Log-Handler ins Leere.
**Fix:** Das `logs`-Verzeichnis wird nun explizit via `(trial_dir / "logs").mkdir(parents=True, exist_ok=True)` in `build_trial` vor dem Start des Runners initialisiert.
**Betroffen:** `automation/optimizer/trial_config.py`
- `backtest_runner.py` respektiert `ETORO_CONFIG_DIR` (Quelle der eingefrorenen `tournament.json`) und `ETORO_LOGS_DIR`; Ergebnisse ausschließlich nach `--output`.
- Bei `walk_forward.splits > 1` exportiert `aggregate_winner.oos_fold_sortinos` die Pro-Fold-OOS-Sortinos als Liste (Basis des robusten Median-Rewards).
- Param-Auflösung und Fold-Sortino-Sammlung MÜSSEN als reine, testbare Funktionen (`resolve_strategy_params`, `collect_oos_fold_sortinos`) vorliegen.

### 🟢 Pitfall #50 — `--dry-run` entfernt / `--no-deploy` eingeführt
`--dry-run` übersprang Phase 3 und schrieb ein Dummy-Tournament — irreführend, ersatzlos entfernt. Ersatz: `--no-deploy` führt Phase 1–4 vollständig aus und unterbindet ausschließlich Phase 5. Phase 3 läuft ab sofort IMMER real; `_create_dummy_tournament` ist nur noch No-Data-Fallback (`not TOURNAMENT_PATH.exists()`). Operator-Validierung: `python3 automation/daily_orchestrator.py --no-deploy --skip-api-fetch`.

### 🟢 Pitfall #51 — Active/Inactive Filter vs. Defaults Assertion Crash (Issue #311)
**Symptom:** Orchestrator crasht vor dem Backtest mit `AssertionError: Mismatch: 8 defaults loaded but 6 strategies executed.`.
**Root Cause:** Der Guard aus Pitfall #38 nutzte einen strikten Längenvergleich (`len(defaults) == len(strategies)`). Wenn in der `strategies.json` Strategien regulär auf `active: false` gesetzt wurden, schrumpfte die zu exekutierende Liste. Der Guard crashte das System, da er nicht zwischen absichtlich deaktivierten Strategien und echten Mismatches unterschied.
**Fix:** Umstellung auf eine Set-basierte Assertion (`issubset`). Es wird nun nur noch geprüft, ob alle *aktiven* Strategien einen Eintrag in den Defaults besitzen. Überzählige Defaults von inaktiven Strategien werden sicher ignoriert. Zur Prävention wird in der CI-Pipeline (`pytest-gate.yml`) nun standardmäßig `python3 -m automation.backtest_runner --dry-run` ausgeführt.

### 🟢 Issue #276 — Verfälschung von Risk-Metriken (Sortino-Caps & Drawdown-Basis) und Spread-Nachkalibrierung
**Symptom:** Strategien mit exakt einem Verlust und einer sehr geringen Tradeanzahl (<50) ruinierten die Turniermathematik durch ungedeckelte Sortino-Ratios, während gleichzeitig Drawdowns systematisch unterschätzt wurden.
**Root Cause:** `_calculate_stats` wertete Szenarien mit `losses_count < 2` pauschal zu `None` aus, was downstream (in #263) zu perfekten 50.0-Ausreißern hochskaliert wurde. Zudem basierte der berechnete Drawdown rein auf der realisierten FIFO-PnL-Kurve der geschlossenen Trades (ohne Berücksichtigung intra-trade Exkursionen). Der Equity-Spread in eToro war mit 3 bps zu optimistisch angesetzt.
**Fix:** Sortino-Capping auf hart `2.0` für 1-Loss Low-Sample Szenarien (<50 Trades). Drawdown-Basis (Realized FIFO PnL) explizit im Code als architektonische Entscheidung dokumentiert. Der eToro Equity Spread in `backtest.json` wurde auf konservativere 8.0 bps nachkalibriert, um artifizielle Profitabilität zu mindern.
**Betroffen:** `automation/backtest_runner.py`, `automation/config/backtest.json`

### 🟢 Issue #263 — Sentinel Metric Distortion in Cross-Sectional Aggregations
**Symptom:** VwapExhaustion oder andere restriktive Setups zeigen ein Sortino Ratio von exakt 50.00 (Cap-/Sentinel-Wert). Diese Werte fließen in die Median-Berechnung ein und verfälschen die Auswahl des `aggregate_winner` im Turniersystem.
**Root Cause:** Winsorizing-Caps (50.0) für Sortino und Profit Factor schützten zwar vor Division-by-Zero, fungierten in Folgesystemen jedoch als extrem verzerrende Ausreißer im Median-Pool.
**Fix:** Die statistische Funktion `get_median` filtert Sentinel-Werte (exakt 50.0) proaktiv heraus, sofern alternative, organische Ratios im Population-Sample existieren. Ratios werden dadurch rein von realen Markt-Kopien angetrieben.
**Betroffen:** `automation/backtest_runner.py`
### 🟢 Pitfall #45 — Micro Position Sizing & Flat Equity Curves (Issue #254)
**Symptom:** Strategien haben Plausible Ratios (PF, Sortino), aber generieren ~0% Absolute Return (z.B. -0.04%).
**Root Cause:** Kollabierendes Position Sizing. Wenn das berechnete Notional bei hohen Kursen / kleinen Increments unter 1 Increment fiel, rundete `math.floor` auf 0 ab, oder das System handelte mit Cent-Beträgen. Das führte zu winzigen absoluten PnLs.
**Fix & Constraints (Kritische Architektur-Regeln):**
1. **Arity / 4-Tupel Constraint:** Die Arity (Länge) des `pnls_with_ts` Tupels in `backtest_runner.py` ist ein heiliges Architektur-Konstrukt (4-Tupel). Es darf nicht für neue Metriken (wie Notional) aufgebläht werden, da sonst das Unpacking in Downstream-Systemen oder Tests (Referenz Pitfall #33) unwiderruflich bricht. Zusätzliche Per-Trade-Metadaten sind in separaten, synchron laufenden Listen zu sammeln (z.B. `notionals_with_ts`).
2. **Kein Hebeln durch min/max:** `make_qty` darf niemals das zugewiesene Risiko/Notional künstlich über `max(inc, ...)` nach oben hebeln. Fällt das Kapital unter das Minimal-Instrumenten-Increment, muss hart fail-closed via `return None` reagiert werden.
3. **Konstante Floor:** Der eToro Trade-Floor ($11.00) ist fest über `MIN_TRADE_USD` im Strategy-Layer für alle Sub- und Base-Classes dokumentiert und abzusichern.

- **State/Key Bleed (OOS Gating in `_is_eligible`)**: `oos_metrics` is a sibling key (Geschwister-Key) to `metrics` in the backtest result dictionary. Searching for it inside `metrics` (`metrics.get("oos_metrics")`) will silently fail and return `None`, leading to unexpected rejection in tournament gating. Da `oos_metrics` auf derselben Ebene wie `metrics` liegt und nicht tief verschachtelt ist, muss dieser Fehler bei zukünftigen Aggregations-Modulen von vornherein ausgeschlossen werden. Always parse sibling keys directly from the root result dictionary `r`.

### 🟢 Pitfall #52 — Aggregate Metric Statistical Artifacts (Issue #255)
**Symptom:** In der Turnierauswertung ergab `win_rate * total_trades` keinen ganzzahligen Wert. Die OOS-Rendite wirkte im Vergleich zu Einzel-Symbolen inkonsistent, und das OOS-Gate wurde durch die aufsummierten Trades ausgehebelt (Trade-Sum Trap).
**Root Cause:** Die Aggregation vermischte arithmetische Mittelwerte (für Trades) mit Medians (für Win-Rates). Dies zerstörte die zugrundeliegende mathematische Identität der Einzel-Backtests und erzeugte "Frankenstein-Metriken". Edge-Cases in Zero-Loss Backtests lieferten zudem `None`, was bei nicht typsicherem Unpacking zu Laufzeitfehlern führen konnte.
**Fix (Architektur-Regel):** Die Metrik-Aggregation (`select_winners`) erzwingt nun streng eine hybride, aber mathematisch konsistente Struktur (`portfolio_sum_for_trades_and_count_ratio_for_win_rate_and_trade_weighted_mean_for_return_and_median_for_risk_ratios`).
1. **Volumen (Trades, Wins):** Werden absolut aufsummiert. Das Unpacking erfolgt typsicher via `(oos.get("total_trades") or 0)`, um TypeErrors bei `None`-Werten auszuschließen.
   - `total_trades`: Absolute Summe aller Trades über das Portfolio.
   - `win_rate`: Absolute Portfolio-Wins dividiert durch absolute Portfolio-Trades (Count-Ratio).
2. **Rendite:** Wird als kapitalgewichteter (Trade-Weighted) Return berechnet, um das Portfolio-Volumen real abzubilden.
   - `total_return`: Kapitalgewichteter (Trade-Weighted) Mittelwert.
3. **Risiko-Ratios (Sortino, PF) & Max Drawdown:**
   - `sortino_ratio` / `profit_factor`: Werden zwingend als Median (via `get_median()`) beibehalten, da Nenner-Abweichungen eine Aufsummierung verbieten.
   - `max_drawdown`: Wird **nicht** als Median ermittelt, sondern exakt aus der gemergten OOS-Equity-Kurve (chronologisch gemergte OOS-Einzeltrades des `aggregate_winner`) berechnet, um Drawdown-Glättung zu vermeiden.
4. **OOS-Gating (Trade-Sum Trap):** Um zu verhindern, dass die Portfolio-Trade-Summe die `oos_min_trades`-Schwelle (die eigentlich pro Symbol gilt) trivialerweise überschreitet, wird das aggregierte Dictionary (`avg_oos`) *ausschließlich für das Gate* intern normalisiert (`total_trades / n_res`), bevor es an `_evaluate_oos_eligibility` übergeben wird. In den Logs und im JSON verbleiben die wahren Portfolio-Summen.
**Schutz-Klausel:** Der Datenfluss über `_oos_trade_records` (temporäre Weitergabe zur Equity-Kurven-Berechnung, `.pop()` vor dem Export) ist ein kritisch geschützter Pfad. Er darf unter keinen Umständen bei zukünftigen Refactorings als "Bloat" wegoptimiert werden.
**WICHTIG für Agenten:** Dieser hybride Aggregations-Zustand (Volumen-Summe, Trade-gewichtete Rendite, Ratio-Median für Sortino/PF, echte Equity-Kurve für Max Drawdown) ist gewollt. Versuche nicht, diese restliche hybride Struktur als "Inkonsistenz" zu reparieren.
**Betroffen:** `automation/backtest_runner.py`, `automation/daily_orchestrator.py`

### 🟢 Pitfall #56 — Portfolio OOS Capital Scaling Mismatch (Issue #312)
**Symptom:** Der `total_return` und `max_drawdown` des `aggregate_winner` im Turniersystem weichen drastisch (Faktor 10) von den kumulierten Metriken der Einzel-Paare ab. Der Bot bricht in Phase 5 fälschlicherweise im OOS-Gate mit `OOS_GATE_FAILED` ab, weil Renditen künstlich verkleinert werden.
**Root Cause:** `select_winners` berechnet die aggregierte Portfolio-Equity-Kurve über `_calculate_stats`. Die Funktion versuchte, das `starting_capital` fälschlicherweise aus `strat_params` zu extrahieren, wo es nicht existiert (da es sich um ein globales Setting handelt). Dies führte zu einem permanenten, stummen Fallback auf `100_000.0`, während die Engine real mit `10_000.0` initialisiert wurde.
**Fix:** 1. `run_single_backtest_worker` reicht das tatsächliche `start_capital` nun explizit auf Root-Ebene des Ergebnis-Dictionarys an das Turniersystem weiter.
2. `select_winners` extrahiert diesen Wert primär vom Worker-Ergebnis und nutzt als sekundären Fallback das direkte Laden über `config_dir() / "backtest.json"`, wodurch die mathematische Identität von absoluten Trades und prozentualem Portfolio-Drawdown wiederhergestellt wird.
**Betroffen:** `automation/backtest_runner.py`, `automation/tests/test_oos_aggregation.py`

### 🟢 Pitfall #42 — Crypto: Degenerated Sortino & Profit Factor Metrics (Issue #232)
**Symptom:** Nahezu alle Crypto-Assets (BTC, ETH, XRP etc.) weisen in Backtests über unterschiedlichste Strategien hinweg stark negative Sortino-Werte (z.B. -12.99) und einen Profit Factor von 0.00 auf, gepaart mit extrem schlechten Win-Rates.
**Root Cause:** Dies ist **kein** Bug im Position Sizing (Precision 8) oder FSB(16) Encoding. Das Sizing (`make_qty`) funktioniert für kleinste Krypto-Bruchstücke fehlerfrei. Das Verhalten ist ein rein legitimer, marktbedingter Effekt (Szenario B). Die Long-only-Ausrichtung der aktuellen Strategien in Kombination mit eToros weiten Crypto-Spreads (15 bps) und hohen Volatilitäten in Down-Märkten führt mathematisch korrekt zu diesem Ausverkauf der Metriken. Da eToro REAL-Shorts ablehnt, filtern wir diese Instrumente auf Basis ihrer Performance ganz natürlich organisch im OOS-Gate heraus.
**Fix:** Kein Fix notwendig, es handelt sich um intendiertes Marktverhalten. Zukünftige Evaluatoren dürfen hierbei nicht auf einen vermeintlichen Precision-Mangel rückschließen.

### 🟢 Pitfall #41 — Zero-Spread in Synthetic Ticks
**Symptom:** Backtesting führt zu physikalisch unplausiblen Ergebnissen (z.B. Max-Drawdown < 1 % bei hunderten Trades), da die synthetischen 1-Stunden-QuoteTicks einen Zero-Spread aufweisen und somit Ausführungen im Backtest de facto kostenfrei zum Midprice erfolgen. Dies bläht Risk-Metriken (Sortino, PF) artifiziell auf.
**Root Cause:** Beim Umwandeln von stündlichen Kerzen in Ticks entsteht kein natürlicher Bid/Ask Spread, wodurch `fill_model="bid_ask"` ins Leere greift. Ein reiner Order-Fill-Workaround genügt nicht, da die Ticks direkt im Katalog-Ladezyklus die Engine beliefern.
**Fix:** Implementierung einer dynamischen `spread_bps_by_asset_class` Parameter in `backtest.json`. `load_ticks_from_catalog` weitet nun aktiv Bid- und Ask-Preise anhand dieser BPS-Konfiguration direkt beim Lesen aus den Parquet-Metadaten. Ergänzend wurde eine statische FIFO-Kommission (`commission_bps`) im PnL-Matching-Prozess hinzugefügt.
**Betroffen:** `automation/backtest_runner.py`, `automation/config/backtest.json`

### 🟢 Issue #257 — Zweistufige Selektion & OOS-Gating Transparenz
**Symptom:** Diskrepanz zwischen IS-tauglichen Paaren und tatsächlichen Symbol-Gewinnern ohne nachvollziehbares Rejection-Log. Verzerrung der Rank-Normalisierung durch vorzeitigen Ausschluss von OOS-Fails.
**Root Cause:** OOS-Fails wurden vor dem Scoring aus der Grundgesamtheit in `select_winners` entfernt, was die Population für die Normalisierung künstlich verkleinerte.
**Fix:** Umstellung auf "Rank first, Gate second". Die Metrik-Normalisierung findet auf der gesamten IS-tauglichen Population statt. Anschließend wird pro Symbol absteigend nach Score iteriert, bis der erste Kandidat das OOS-Gate besteht. Vollständiges Logging des OOS-Decision-Trails im Terminal (`[OOS-Drop]`) hinzugefügt.
**Betroffen:** `automation/backtest_runner.py`, `automation/tests/*.py`

### 🟢 Pitfall #39 — TypeError in NautilusTrader balances API (Issue #181)
**Symptom:** Jeder Matrix-Backtest brach beim ersten geschlossenen Bar ab (`TypeError: 'method' object is not iterable`), was zu 0 Trades über alle Symbol/Strategie-Kombinationen führte.
**Root Cause:** Die NautilusTrader-API stellt `account.balances` als Methode (`cpdef dict balances(self)`) bereit, nicht als Eigenschaft/Attribut. Die Backtest-Strategien versuchten, die zurückgegebene Methode zu iterieren (`list(account.balances)`). Gleichzeitig stürzte das Formatieren von Metrics mit `None` (z.B. aus all-win Szenarien) ab, da Formatstrings auf `NoneType` nicht anwendbar sind.
**Fix:** Umstellung auf die typsicheren Methoden `account.balance_total()` und `account.balance_free()` unter Berücksichtigung des `Money`-Rückgabewertes, verpackt in einem try-except Block ohne raises (`AGENTS.md` §14). In den Metriken-Formatstrings `(val or 0.0)` verwendet.
**Betroffen:** `automation/strategies/hourly_strategy_base.py`, `automation/backtest_runner.py`, `automation/tests/test_strategy_duplication.py`

### 🟢 Pitfall #40 — Datenspanne Toleranz / INSUFFICIENT DATA (Issue #193, Issue #271)
**Symptom:** Etwa 40 % der Backtests wurden wegen geringfügiger Datenunterschreitung (z. B. 149.8 statt 150 Tage) hart verworfen. An Wochenenden führte dies bei Equity/Forex trotz Toleranz zum Abbruch (z.B. 148.3 statt 150), da die Toleranz durch einen Hardcoded-Guard überschrieben wurde.
**Root Cause:** Die Anforderung `_walk_forward_days` wurde initial strikt ohne Toleranz validiert. Später wurde ein widersprüchlicher Hard-Guard (`required_days * 0.95`) eingeführt, der die `span_tolerance_days` stumm überschrieb. Zudem zog der Orchestrator an Wochenenden das Fenster bis Sonntag auf, obwohl freitags die letzten Equity-Daten vorliegen.
**Fix:** Die Konfigurationsvariable `span_tolerance_days` (Standard 3.0) ist nun die absolute "Single Source of Truth" in `check_data_span`. Der 0.95-Hard-Guard wurde entfernt. Der Orchestrator rollt nun am Wochenende den Endpunkt mathematisch korrekt auf Freitag Mitternacht zurück.
**Betroffen:** `automation/backtest_runner.py`, `automation/config/backtest.json`, `automation/daily_orchestrator.py`

### 🟢 Pitfall #38 — Strategy Matrix Execution Mismatch (Issue #152)
**Symptom:** Das System lud Defaults für 8 Strategien, aber führte nur 7 Strategien im Backtest-Matrix-Loop aus. Die Gesamtanzahl der Jobs lag bei 343 statt den erwarteten 392.
**Root Cause:** `HourlyMeanReversionStrategy` war in `strategy_defaults.json` definiert, fehlte jedoch in der aktiven `strategies.json` als Ausführungsziel. Zudem fehlte eine Validierung, die sicherstellt, dass definierte Defaults auch tatsächlich als aktive Strategien registriert sind.
**Fix:** `HourlyMeanReversionStrategy` wurde mit `active=true` zur `strategies.json` hinzugefügt. Zusätzlich wurde eine Laufzeit-Assertion in `automation/backtest_runner.py` implementiert, die das Backend hart abstürzen lässt, wenn die Anzahl der geladenen Defaults nicht exakt mit der Anzahl der auszuführenden aktiven Strategien übereinstimmt. *Jede Strategie, die in den Defaults geladen wird, MUSS zwingend im Execution Loop enthalten sein, es sei denn, sie wird explizit per Bypass übersprungen.*
**Betroffen:** `automation/backtest_runner.py`, `automation/config/strategies.json`, `automation/AGENTS.md`

### 🟢 Pitfall #37 — Sortino Ratio Explosion & Tournament Artefakte (Issue #151)
**Symptom:** Unrealistisch hohe Sortino-Werte (> 200) führen zu einer fehlerhaften Selektion von Gewinnern im Tournament, wobei oft Low-Yield-Strategien mit minimaler absoluter Rendite bevorzugt werden.
**Root Cause:** Bei einer geringen Anzahl von Trades oder fehlenden Verlusten tendiert die Downside-Deviation (`dd_dev`) gegen null. Die Division im Sortino-Nenner erzeugt mathematisch explodierende Werte (Artefakte), die keine echte Performance widerspiegeln.
**Fix:** Einführung eines harten Caps (Winsorizing auf max. 50.0), einer Absicherung des Nenners (`max(dd_dev, 1e-6)`), eines Sanity-Gates (Cap auf max. 2.0 bei `total_return < 0.5%`) und eines Minimum-Downside-Gates (Cap auf max. 2.0 bei weniger als 2 echten Verlust-Trades und unter 50 Gesamt-Trades).
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #36 — Precision API Parsing & Datenkorruption (Issue #146)
* **Hinweis (Issue #179 / Issue #231):** Die eToro API liefert beim Endpoint `instruments` derzeit oftmals **keine Precision-Felder für reine Equities**. Dies ist ein bekanntes Limit der API, weshalb das fehlende Feld für Equities kein Fehler ist. Der in `_fallback_precisions` verwendete Standard-Fallback auf `(2,2)` ist funktional korrekt und fängt dieses Verhalten sicher ab. False-Positive-Warnungen in `api_backfiller.py` bei Batch-Requests, die ausschließlich aus Equities bestehen, wurden zu `DEBUG` herabgestuft, um irrelevante Warnungen im `historical_fetcher` zu vermeiden.
**Root Cause:** eToro änderte die API-Struktur (`instruments` -> `instrumentDisplayDatas`). Der alte Parser lieferte 0 Treffer. Das System fiel stumm auf einen blinden `(2, 2)`-Fallback für alle Instrumente zurück (inklusive Krypto/Fractional).
**Symptom:** Krypto-Parquets wurden mit `size_prec=2` geschrieben. Der Mismatch zwischen Arrow-Metadaten und den generierten i128-Ticks führte unweigerlich zu `RuntimeError`-Abbrüchen im Nautilus-Matrix-Backtest.
**Lösung & Architektur-Regeln:**
1. **Strict Partial Fail Drop:** Partielle API-Ausfälle bei der Precision-Abfrage dürfen niemals in einen generischen `=2`-Fallback rutschen. Fehlt die Precision für ein Instrument in der `instrumentDisplayDatas`-Struktur (oder schlägt der int-Cast fehl), muss dieses Instrument zwingend aus dem aktuellen Backfill-Batch ausgeschlossen (geskippt) werden.
   > **Update (Issue #171, 2026-06-04):** Die „Strict Partial Fail Drop"-Regel gilt weiterhin für **explizite** API-Mismatches (API liefert z. B. `size_prec=2` für ein Non-Equity). Der reine **None-Fall** (API liefert gar keine Precision) wird im vorvalidierten Universe NICHT mehr gedroppt, sondern über `_fallback_precisions` aufgefüllt — inklusive Standard-Equities `(2,2)`.
2. **Sanity Check Enforcement (Equities vs. Non-Equities):** Ein Mismatch zwischen dynamisch geparster Precision (z. B. `size_prec=2`) und der durch `_fallback_precisions` erwarteten Precision für Non-Equities (z.B. Krypto, Fractional) ist keine bloße Warnung, sondern führt zum sofortigen Drop des Instruments aus der Schleife (`continue`), bzw. Hard-Fail bei `STRICT_PRECISION_FAIL`.
3. **Tick & Arrow Meta Synchronization:** Unsaubere Instrument-Precisions führen unmittelbar zu `RuntimeError`-Crashes im Matrix-Backtest. Die Parameterübergabe an `_candles_to_arrow_table` und `_build_arrow_meta` muss 100 % synchron laufen, weshalb falsche oder ratende Metadaten das System nie passieren dürfen.

> **Legende:** 🔴 OFFEN (im aktuellen Code aktiv) · 🟡 TEILWEISE · 🟢 BEHOBEN/dokumentiert

### 🟢 Pitfall #14 — `create_mock_instrument` übergeht den eigenen Docstring (Haupt-Bug: 0 Trades)
**Symptom:** Backtest liefert über alle Symbole × Strategien `Trades=0`, `0 eligibel`, `0 Gewinner`. Logs zeigen `size=0 (parquet meta)`.
**Root Cause:** `backtest_runner.py:create_mock_instrument` dokumentiert „`size_precision`: Ignoriert — immer 8", implementiert aber `sp = size_precision if size_precision is not None else 8`. Der aktive Aufrufer (`run_single_backtest_worker`) übergibt `size_precision=sp_parquet`, wobei `sp_parquet=0` aus den Parquet-Metadaten stammt (`read_precisions_from_parquet`). Da `0 is not None`, schlägt die 0 durch → `size_increment=1.0` → ganzzahlige Order-Größe → bei Aktienkursen > `trade_amount_usd` rundet `make_qty()` auf 0 → jedes Signal verworfen.
**Fix:** `sp = size_precision if (size_precision is not None and size_precision > 0) else 8`.
**Betroffen:** `automation/backtest_runner.py`.

### 🟢 Pitfall #20 — Drei divergierende `_compute_quantity`-Implementierungen
**Symptom:** Inkonsistentes Verhalten je nach Strategie-Basisklasse; teils stille Signal-Verwerfung.
**Root Cause:** Drei unterschiedliche Implementierungen:
1. `hourly_strategy_base.py` nutzt `instrument.make_qty(units, round_down=True)` und greift nicht auf `lot_size` zu.
2. `momentum_ls_base.py`, `adx_atr_momentum.py`, `trend_pullback.py` nutzen `float(instrument.size_increment)` — bei `size_increment=1` und `units<1` → `return None`.
3. `fractional_trading.safe_compute_quantity` nutzt `size_increment`, wird aber nirgends aufgerufen.
**Fix:** Eine einzige Implementierung in `HourlyStrategyBase`, die `make_qty` selbst über `size_precision` entscheiden lässt, statt manuell gegen `lot_size`/`size_increment` zu prüfen. Doppelimplementierungen entfernen.
**Betroffen:** `automation/strategies/hourly_strategy_base.py`, `momentum_ls_base.py`, `adx_atr_momentum.py`, `trend_pullback.py`.

### 🟢 Pitfall #21 — `safe_compute_quantity` ist toter Code
**Symptom:** Die als Pitfall-#14-Fix gedachte Funktion in `fractional_trading.py` wird von keiner Strategie aufgerufen; der dokumentierte Schutz greift im Backtest nicht.
**Fix:** Die ungenutzte Funktion `safe_compute_quantity` wurde vollständig aus der Codebasis und den Tests entfernt. Die Logik ist nun alleinig in `HourlyStrategyBase.make_qty` konsolidiert (siehe #20).
**Betroffen:** `automation/fractional_trading.py`.

### 🟢 Pitfall #19 — `momentum_ls_run.py` verletzt das Standalone-Prinzip
**Symptom:** Live-Pfad ist nicht hermetisch.
**Root Cause:** `from archive.adapters.etoro_data import …` und `from archive.adapters.etoro_config import …`. `automation/` soll laut Abschnitt 4 keine externen Importe haben.
**Fix:** Adapter in `automation/adapters/` migriert und Isolations-Test entsprechend angepasst. Keine dokumentierte Standalone-Ausnahme mehr.
**Betroffen:** `automation/momentum_ls_run.py`.

### 🟢 Pitfall #25 — Tournament-OOS-Kriterien Validation Warning
**Symptom:** Startup-Validierung meldet, dass `oos_min_trades` und `oos_min_total_return` definiert, aber nicht referenziert sind.
**Fix:** `load_tournament_config` streicht den `oos_` Prefix bei der Validierung. OOS-Kriterien werden vom Evaluator im `check_oos=True` Zweig genutzt, die Warnung war ein False-Positive.
**Betroffen:** `automation/backtest_runner.py`.

### 🟢 Pitfall #22 — Alle Tournament-Gewinner werden auf MomentumLSSmaStrategy reduziert

**Symptom:** Egal welche Strategie das Tournament pro Symbol gewinnt, live läuft immer SMA(5).
**Fix:** Dynamische Registry aus `strategies.json`, echte Gewinner-Strategie wird live registriert, Allocator-Hook in `HourlyStrategyBase`, PoC-Dateien entfernt, Live-`bar_type` auf 1h umgestellt (MID-INTERNAL), QuoteTick-Subscription in allen aktiven Strategien.
**Betroffen:** `automation/momentum_ls_run.py`, `automation/strategies/*.py`.

### 🟢 Pitfall #23 — `size_precision=0` wird an der Quelle persistiert (Live + Catalog)
**Symptom:** Selbst nach Fix von #14 bleiben Live-Metadaten kaputt.
**Root Cause:** `catalog_service.py` (ZIP-Metadaten), `api_backfiller._build_arrow_meta` und `utils._fallback_precisions` schreiben für Equities `size_precision=0`. `daily_orchestrator._ensure_metadata` übernimmt diese.
**Fix:** Schreibseite auf `size_precision=2` für Equities angehoben. `regenerate_precision.py` wurde ersatzlos gelöscht. Korrupte Katalogdaten aus der Quelle müssen komplett neu aufgebaut werden (`--reset-catalog`).
**Betroffen:** `automation/utils.py`, `automation/api_backfiller.py`, `automation/catalog_service.py`, `automation/daily_orchestrator.py`.

### 🟢 Pitfall #24 — Datendichte vs. Indikator-Warmup (Walk-Forward OOS-Guard)
**Symptom:** Backtests rechneten mit 30 Tagen Historie ein 90d+30d Walk-Forward-Fenster, emittierten verzerrte OOS-Metriken und gewannen Turniere ohne Warnung (Issue #105). Strategien mit langen Perioden initialisierten bei kurzen Datenfenstern spät oder gar nicht.
**Fix:** Guard im `backtest_runner.py` implementiert (überspringt, wenn `span_days < required_days * 0.95`). Die Beschaffungstiefe für den `historical_fetcher` im `daily_orchestrator.py` dynamisch an das Walk-Forward-Fenster (inklusive Puffer) gekoppelt statt fix `min_bars=200`. Die Zuweisung von `_walk_forward_days` wurde in Issue #121 gefixt, um den Guard erfolgreich zu triggern.
**Betroffen:** `automation/backtest_runner.py`, `automation/daily_orchestrator.py`.

### 🟢 Pitfall #15 — `BrokenProcessPool` durch OOM
Worker auf `cpu//2` (max 6) begrenzt, `max_tasks_per_child=1`, expliziter Catch + sequenzieller Fallback. **Behoben** in `backtest_runner.py`.

### 🟢 Pitfall #16 — PyArrow 24+ `BinaryView` → Nautilus Rust-Panic
Alle Quellen schreiben jetzt `pa.binary(16)` (= FixedSizeBinary(16)) nativ; Migration entfällt. **Behoben** (Shift-Left).

### 🟢 Pitfall #17 — Flat-Lock nach Reverse Entry
Signal-State wird nach `_close_position()` auf `None` zurückgesetzt. **Behoben** in allen aktiven Strategien.

### 🟢 Pitfall #18 — `make_qty` ValueError bei Equities
`round_down=True` verhindert den ValueError NICHT. Zweistufige Absicherung (Pre-Check + try/except) dokumentiert. **Teilweise** umgesetzt — siehe #20/#21 für die verbleibende Inkonsistenz.


### 🟢 Pitfall #44 — Asynchrone Speicherung (Deferred Flush Bug)
**Symptom:** Datenverlust oder Inkonsistenzen beim Schreiben der Puffer.
**Fix:** Korrektes Flush-Handling implementiert (dokumentiert in `Test_report.md` via `test_do_flush`).
**Betroffen:** `automation/catalog_service.py`.

### 🟢 Pitfall #26 — Fehlendes Cleanup temporärer Verzeichnisse
**Symptom:** Mögliche Dateisystem-Müllansammlung bei fehlerhaftem Backtest-Abbruch.
**Fix:** Absicherung des `temp_catalog_dir` Cleanups durch einen harten `try/finally`-Block (via `patch_try_finally.py`).
**Betroffen:** `automation/backtest_runner.py`.

### 🟢 Pitfall #27 — Divergierende size_precision-Heuristiken
**Symptom:** Es existieren drei widersprüchliche Heuristiken für Equity `size_precision`: `automation/utils._fallback_precisions` liefert 2, während `automation/adapters/instrument_utils.get_size_precision` und `automation/fractional_trading._get_size_precision` fälschlicherweise 0 liefern.
**Fix:** Konsolidiert. `adapters/instrument_utils` und `fractional_trading` nutzen nun ausschließlich `automation/utils._fallback_precisions(symbol)[1]`. Redundante Implementierungen und Sets wurden entfernt.

### 🟢 KeltnerChannel `atr_period` Mismatch
**Symptom:** `mean_reversion.py` und `hourly_mean_reversion.py` führten `keltner_atr_period` in der Config, übergaben sie aber nicht an `KeltnerChannel(period=…, k_multiplier=…)`.
**Fix:** Parameter korrekt übergeben. Die `MeanReversionStrategy` wurde standardisiert (gemäß Changelog Issue #213 vom 2026-06-05).

### 🟢 Pitfall #28 — Backtest BarType Diskrepanz (0 Trades / 0 Gewinner)
**Symptom:** Backtest liefert `Trades=0` für alle Strategien/Symbole, obwohl der Live-Pfad läuft.
**Root Cause:** Die Backtest-Engine nutzte hardcoded `1-MINUTE-MID-INTERNAL`, während historische Daten stündlich (1-HOUR) gestreamt werden und die Strategien (`HourlyStrategyBase`) für Stunden-Bars konfiguriert sind. Minuten-Bars wurden zwar aggregiert, aber bei stündlichen Quelldaten entstehen kaum bewegte Bars; zudem feuerte der 48-Bar-Time-Exit nach 48 Minuten statt 48 Stunden.
**Fix:** Hardcoded `1-MINUTE-MID-INTERNAL` in `backtest_runner.py` durch `1-HOUR-MID-INTERNAL` ersetzt.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #29 — FSB(16) Encoding Error (0 Trades)
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



### 🟢 Pitfall #30 — Rust Engine FFI Abort bei Signaturänderungen
**Symptom:** Subprozesse crashen mit `Fatal Python error: Aborted` aus `nautilus_trader.system.kernel.py __init__`.
**Root Cause:** Die Nautilus Rust-Engine crasht unweigerlich, wenn ein Python-Worker aufgrund unhandled Exceptions (wie `TypeError` bei inkonsistenten Funktionssignaturen) unsauber stirbt, bevor `engine.dispose()` gerufen wird. Dies trat auf, als `run_single_backtest_worker` ein neues Positionsargument erhielt, das in Multiprocessing-Pools und Tests nicht überall übergeben wurde.
**Fix:** Niemals die Signatur des Workers für Backtest-Konfigurationen ändern. Externe Variablen (wie Walk-Forward Splits) werden stattdessen in das `strat`-Dictionary injiziert (z.B. `strat["_walk_forward_dict"]`) und im Worker dynamisch per `.get()` ausgelesen.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #31 — Metrik-Dictionary Nesting Bug
**Symptom:** Tests schlagen fehl, weil keine Trades ausgewiesen werden (`total_trades = 0`), obwohl Trades stattfinden.
**Root Cause:** `extract_metrics` wurde erweitert, um `{"metrics": {...}, "oos_metrics": {...}}` zurückzugeben. Der Worker hat diese Struktur ungeprüft weitergegeben, was zu einer doppelten Verschachtelung führte (z.B. `res["metrics"]["metrics"]["total_trades"]`). Tests suchten auf der falschen Ebene.
**Fix:** Explizites Unpacking im Worker via `extracted_data.get("metrics")` und Fail-Safe-Fallback auf flache Dictionaries, falls die Extraktion fehlschlägt. Assertions für Trades müssen zwingend auf `> 0` bleiben, um stille Logik-Fehler sofort abzufangen.
**Betroffen:** `automation/backtest_runner.py`, `automation/tests/test_backtest_runner_bar_type.py`

### 🟢 Pitfall #32 — Queue-Korruption bei Zeitfenster-Splits
**Symptom:** PnL-Werte sind in einem bestimmten Zeitfenster fehlerhaft oder leer.
**Root Cause:** Die FIFO-Position-Matching-Schleife wurde aufgrund eines Zeitstempel-Cutoffs vorzeitig unterbrochen.
**Fix:** FIFO-Logik muss immer über das gesamte Datenset (`IS + OOS`) unangetastet iterieren, da sonst offene Queues korrumpieren. Erst *nach* dem FIFO-Matching werden die generierten PnL-Tupel (`pnl, ts_event`) anhand des Cutoffs separiert.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #33 — Tuple Unpacking Regression in extract_metrics
**Symptom:** `total_trades=0` in allen Strategien, leere Backtest-Metriken, keine Tournament-Gewinner.
**Root Cause:** Regression durch Mismatch der Tuple-Arity. `pnls_with_ts.append` generierte ein flaches 4-Tupel, aber die nachfolgende Loop versuchte in 3 Ziele zu entpacken.
**Fix:** Unpacking Loop wurde auf `for pnl, ts, ht, m_qty in pnls_with_ts:` korrigiert (4 Variablen entpackt). Referenz: Issue #132.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #35 — `compute_tournament_score` konstanter Wert und fehlerhafte Gewinner-Reihenfolge
**Symptom:** Die Auswahl des Gewinners pro Symbol in `select_winners` hing nur von der Iterationsreihenfolge ab. Alle Strategien erhielten einen Score von `0.0`.
**Root Cause:** `compute_tournament_score` wurde mit `norm_metrics` aufgerufen, berechnete den Score jedoch auf Basis von `total_return` und `avg_holding_time_s`, welche in `norm_metrics` nicht existierten. Zudem wich die Implementierung von der dokumentierten Spezifikation ab (Composite-Score vs. Haltedauer-Rendite).
**Fix:** `compute_tournament_score` wurde so umgeschrieben, dass es die Metriken `sortino_ratio`, `profit_factor`, `win_rate` und `max_drawdown` gemäß den Gewichten aus `tournament.json` zu einem Composite-Score aggregiert.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #43 — Profit Factor / Sortino NoneType Artefakte bei Zero-Loss (Issue #150 / #209 / #227)
**Symptom:** In Backtests mit 100% Win Rate generieren bestimmte Metriken mathematische Artefakte (z.B. Sortino Ratio = 50.0 oder Profit Factor = 50.0). Dies verzerrt die Aggregat-Mediane und Auswertung extrem, wenn die Werte durch künstliche Caps (`MAX_CAP = 50.0`, `CALMAR_CAP = 100.0`) künstlich hoch gehalten werden. Andererseits explodieren die Werte ohne Caps bei minimalen Verlusten ins Unendliche.
**Root Cause:** Fallback bei undefinierten Nennern (z.B. `gross_loss == 0`) waren hardcodierte `MAX_CAP`-Werte, die die Scores nach oben verzerrten und echte Resultate verfälschten. Das komplette Entfernen der Caps in #209 führte stattdessen bei minimalen Nennern zu Explosionen.
**Fix:** Undefinierte finanzmathematische Zustände (All-Win-Szenarien oder <2 Losses bei <50 Trades) erzeugen konsequent `None`. Extreme Werte bei validen Samples werden hart gekappt (Sortino/PF auf 50.0, Calmar auf 100.0). Dies verhindert Median-Verfälschungen bei der Aggregation. Im CLI-Output rendern die None-Werte distinkt als `n/a(win)` oder `n/a(<min)`. Eine dedizierte Filter-Gating-Logik in `_is_eligible` wirft diese None-Kandidaten proaktiv ab.
**Wichtige Architektur-Regel:** Downstream-Systeme in Evaluationen und Formatting müssen stets typensicher entwickelt werden, da Metrik-Extraktionen immer `None`-safe verarbeitet werden müssen! Die Rankings in `select_winners` nutzen nun `(m.get('metric') or 0.0)`, um die Metrik zu normalisieren.
**Betroffen:** `automation/backtest_runner.py`





### Pitfall #53: Optimizer-Storage ausschließlich SQLite
**Symptom:** Unklarheit über Datenhaltung und fehlende PR-Promotion.
**Ursache/Lösung:** Optimizer-Storage ausschließlich SQLite. Optimizer verändert tournament.json NIE und startet NIE Phase 5; Promotion nur per PR.
## 17. Order Management & Async State Machine (Neu)
Alle stündlichen Strategien in `automation/strategies/` müssen für Exit-Bedingungen zwingend die Methoden der `HourlyStrategyBase` nutzen, um Event-Loop-Blockaden und Orphaned Orders zu vermeiden.
Limit-Exits (wie z.B. das native Profit-Target) werden **asynchron** verwaltet.
* Wenn eine Markt-Order (z.B. durch Time-Exit oder Mean-Reversion) platziert werden soll, **müssen** offene Limit-Orders über `self._pending_cancels` getrackt und asynchron storniert werden.
* Erst wenn die Callbacks (`on_order_canceled`, `on_order_filled`, `on_order_rejected`) das Set `self._pending_cancels` komplett geleert haben und die Position noch teilweise offen ist, feuert die Base-Class `self._execute_market_close()`.
* Strategien dürfen diesen asynchronen Fluss niemals durch blockierende While-Loops oder eigene Callback-Überschreibungen stören (ausgenommen via `super().on_...`).

**⚠️ WARNUNG (Issue #307):** Child-Strategien dürfen **niemals** die native Methode `self.close_position()` von NautilusTrader direkt aufrufen oder `def _close_position(self, pos)` überschreiben. Ein lokales Override mit `super()._close_position(pos)` crasht das System, da diese Methode in der Basisklasse nicht existiert. Für manuelle Exits und Reversals ist in Signal-Handlern ausschließlich `self._close_position_base(pos)` zu verwenden, um offene Take-Profit-Limits asynchron korrekt zu stornieren. Ein synchroner Reset des Zustands in Signal-Handlern (z.B. `self.current_signal = None`) bricht die State Machine; dieser muss zwingend erst im Lifecycle-Callback `on_position_closed(self, event)` erfolgen.

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
- **Observability Gate-Regel:** Jeder PR, der neue Gating-Parameter (z. B. in `tournament.json` oder `strategies.json`) einführt, MUSS zwingend die Startup-Log-Ausgabe in `backtest_runner.py` um diese Parameter erweitern. PRs ohne Header-Update für neue Gates werden als 'unvollständig' abgelehnt. Dies gilt als strikte Vorgabe zur Vermeidung von "Hidden Gates" und als Blocker für Merges.

---

## 19. Changelog
| 2026-06-10 | **1c:** Optuna-Loop (SQLite, TPE, Warm-Start), Holdout-Confirmation, PR-Proposal-Export. Autotuner V2 abgeschlossen. | `automation/optimizer/` |

- **Phase 0b:** ETORO_CONFIG_DIR/ETORO_LOGS_DIR env isolation implemented; Manifest-Contract (no re-merge if manifest_version is set); oos_fold_sortinos export added for aggregate winners.
 (Agent-Maintained)
> **Anweisung für Jules:** Bei jeder Änderung am `automation/`-Paket hier einen Eintrag (Datum, Beschreibung, Dateien) anhängen.

| Datum | Änderung | Dateien |
|-------|----------|---------|
| 2026-06-11 | **Issue #346 (Pitfall #62 - Befund B5):** Fehlendes `logs`-Verzeichnis in `build_trial` ergänzt, um FileNotFoundError im Subprozess-Logging des Optimizers zu beheben, wenn `ETORO_LOGS_DIR` gesetzt wird. | `automation/optimizer/trial_config.py`, `automation/AGENTS.md` |
| 2026-06-10 | **Fix Bugfix-Sprint Optimizer (B1, B2, B3):** B1: Manifest Contract in `trial_config.py` repariert (dynamischer `catalog_path` Fallback implementiert) plus Doku Pitfall #61; B2: CLI Entry in `run_optimization.py` via `argparse` hinzugefügt; B3: Suchräume für alle in `AGENTS.md` gelisteten aktiven Strategien in `spaces.py` hinzugefügt. | `automation/optimizer/trial_config.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/spaces.py`, `automation/AGENTS.md` |
| 2026-06-10 | **Doku-Nachtrag 0b (Verifikations-Finding P1):** Config-Contract-Block in Kap. 16 wörtlich ergänzt; Verhalten von `oos_fold_sortinos` in Kap. 10 dokumentiert (wann gesetzt, Reihenfolge, None-Handling). | `automation/AGENTS.md` |
| 2026-06-10 | **Pitfall-Nummern-Bereinigung (Verifikations-Finding P2):** Duplikate (insb. #50) entkoppelt — erste Instanz behält die Nummer, distincte Folge-Pitfalls auf #54+ umnummeriert, exakte Dubletten entfernt. Rein redaktionell, Pitfall-Inhalte unverändert; Querverweise verifiziert. | `automation/AGENTS.md` |
| 2026-06-10 | **Auftrag 1b:** runner.py (Subprozess-Aufruf, Env-Isolation, timeout=10800), parsing.py (Fold-Median, None-safe), reward.py (vollständig konfiguriert). | `automation/optimizer/runner.py`, `automation/optimizer/parsing.py`, `automation/optimizer/reward.py`, `automation/AGENTS.md` |
| 2026-06-10 | **Auftrag 1a:** `holdout_days` in `backtest.json`; `optimizer.json`; Optimizer-Paket (`manifest`/`resolve`/`trial_config`) mit injizierbarem `now` für deterministische Window-Berechnung. | `automation/config/backtest.json`, `automation/config/optimizer.json`, `automation/optimizer/`, `automation/AGENTS.md` |
| 2026-06-09 | `0a: --dry-run restlos entfernt; --no-deploy eingeführt; Phase 3 läuft immer real; Event LIVE_DEPLOY_SKIPPED_NO_DEPLOY.` | `automation/daily_orchestrator.py`, `automation/tests/test_orchestrator_cli.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-09 | **Issue #311 (Pitfall #51 - Active/Inactive Config Crash):** Längen-Assertion in `backtest_runner.py` durch Set-Prüfung ersetzt, um Abstürze bei `active: false` gesetzten Strategien zu verhindern. `--dry-run` in GitHub Actions Workflow integriert, um Config-Mismatches direkt in der CI abzufangen. | `automation/backtest_runner.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-09 | **Issue #308 (0-Trade Micro-Sizing Artifact Fix):** Hard Short-Circuit in `_is_eligible` bei 0 Trades implementiert, um kaskadierende Rejection-Artefakte und irreführendes Logging zu stoppen. | `automation/backtest_runner.py`, `automation/tests/test_tournament_validation.py` |
| 2026-06-08 | **Issue #293 (Gate-Scope vs. Deployment-Scope & Observability):** Behobene Ambiguität bei Sortino-Metriken im `daily_orchestrator.py` durch explizite Log- und Event-Namen (In-Sample Median vs. Aggregate OOS). `OOS-DEPLOY-REJECT` Check in `momentum_ls_run.py` eingebaut, der nicht OOS-evaluierte oder gescheiterte Strategie-Zuweisungen auf Symbol-Ebene hart aussortiert. | `automation/daily_orchestrator.py`, `automation/momentum_ls_run.py`, `automation/AGENTS.md` |
| 2026-06-08 | **Issue #261 (Inception-Bounds für junge Instrumente):** Caching-System für `inception_bounds.json` im `historical_fetcher.py` integriert, um Endlosschleifen bei jungen Instrumenten zu verhindern. `is_backtest_range_covered` bypass-logik bei voller Historie ergänzt. Design-Notiz in AGENTS.md hinzugefügt. | `automation/historical_fetcher.py`, `automation/AGENTS.md`, `automation/tests/test_historical_fetcher.py` |
| 2026-06-08 | **Issue #275:** Präzisierung der 'aggregation_basis'-Beschreibung in backtest_runner.py und AGENTS.md zur Beseitigung missverständlicher Ratio-Mischungen. Count-Ratio-Konsistenztest in test_oos_aggregation.py integriert. | `automation/backtest_runner.py`, `automation/AGENTS.md`, `automation/tests/test_oos_aggregation.py` |
| 2026-06-07 | **Issue #263 (Eliminierung von Sentinel-Verzerrungen):** Modifikation von `get_median` in `select_winners` zum Ausschluss von gecappten Sentinel-Werten (50.0) aus der Berechnung der Aggregat-Mediane und Tie-Breaker. Verhindert die Verzerrung von Portfolio-Ratios durch All-Win-Artefakte. Update von `AGENTS.md` §16. | `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-07 | **Issue #273:** Hard Per-Pair Safety Gate in Phase 5 implementiert. Prüft und loggt fully_eligible_pairs und winner_count vor Bot-Start, um stumme Aggregat-Bypasses bei leeren Turnieren zu verhindern. JSON-Events erweitert. | `automation/daily_orchestrator.py`, `automation/AGENTS.md` |
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
| 2026-06-07 | **Issue #272 (Systematischer Trade-Generierungs-Bug / Base-Exit Signal State Lock):** Behebung eines permanenten State-Locks in `MeanReversionStrategy` und `ComboTrendVwapStrategy`. Das Inkrementieren von `self.bars_since_last_signal` wurde in `MeanReversionStrategy.on_bar` ergänzt. In beiden Strategien wurde `on_position_closed` überschrieben, um den Base-Class-Exit korrekt aufzurufen und `self.current_signal` auf `None` zurückzusetzen. Ein Testfall in `test_backtest_trades_generated.py` sichert nun eine Ausführung von > 20 Trades für beide ab. | `automation/strategies/mean_reversion.py`, `automation/strategies/tesla_combo_strategy.py`, `automation/tests/test_backtest_trades_generated.py`, `automation/AGENTS.md` |

## Architektonische Methodik: IS/OOS Split und "State Bleed"

Der `daily_orchestrator.py` und der `backtest_runner.py` nutzen nun ein echtes, rollierendes Walk-Forward (`walk_forward_active: true`). Das Train/Test (IS/OOS) Splitting basiert aus Performancegründen weiterhin auf einem einzigen, durchgehenden Engine-Run des `backtest_runner.py`. Die Aufteilung in `n=splits` rollierende Fenster erfolgt jedoch *retrospektiv* anhand der Timestamp-Filterung über die gesamte Spanne während der Metrik-Extraktion in `extract_metrics`.

**Wichtige Limitationen für Agenten (State Bleed):**
- **Kein Hard-Reset:** An der IS/OOS-Grenze findet kein Zurücksetzen der Engine statt. Das bedeutet, dass laufende offene Positionen, das angesammelte Account-Guthaben sowie die Historie aller Indikatoren (z.B. aufgewärmte EMAs, RSI-Werte) ungefiltert aus der In-Sample Phase in den Out-of-Sample Zeitraum überfließen ("State Bleed").
- **Gültigkeit der OOS-Metriken:** OOS-Ergebnisse sind somit methodisch nicht 100% "rein" oder vollständig unabhängig vom In-Sample Lauf. Dieser Kompromiss wird derzeit bewusst akzeptiert, um Backtesting-Overhead und Laufzeiten zu minimieren.
- Zukünftige Code-Änderungen an Strategien oder Evaluierungs-Metriken müssen diese architektonische Gegebenheit berücksichtigen.

---

*Zuletzt aktualisiert: 2026-06-08. Datum und Changelog bei jeder Änderung an dieser Datei aktualisieren.*

## Known Pitfalls & Architecture Notes
* **Pitfall #54 (Metrics Rendering und Rejection Reasons):** Die String-Repräsentationen `"all-loss"`, `"all-win (no losses)"`, `"insufficient sample (n=...)"` und `"insufficient loss data"` sind feste Bestandteile der Systemarchitektur und werden verwendet, um OOS-/IS-Abweisungen granulär zu differenzieren. Fließkommawerte im OOS-Log werden mit hoher Präzision (`:.5f`) formatiert, um logische Paradoxa bei Rundungsfehlern (z. B. `0.0050 < 0.0050`) zu vermeiden. Bei zu geringen Trade-Anzahlen wird die Metrik-Rückgabe (`n/a(<min)`) priorisiert vor `None`-Checks gerendert.
* **Pitfall #55 (State-Mutation vor Early-Returns / Signal-Desync):** Strategien müssen ihren Signal-State (`current_signal`, `bars_since_last_signal`) zwingend erst NACH allen Guard-Checks und direkt vor `submit_order` setzen. Wenn der State vor einem Early-Return gesetzt wird, entsteht ein Desync (Agent blockiert Folge-Signale, obwohl keine Order ausgeführt wurde). Siehe Issue #211.
* **Type Casting in API Payloads:** Document the strict necessity of casting variables (e.g., str() vs int()) when matching eToro IDs from the API against the local JSON configurations, as implicit typing will cause silent drop-outs during universe construction.
* **Zero-Signal Metric Structures:** Note that extract_metrics must always return the explicit format {"metrics": None, "oos_metrics": None} (or similarly nested dicts) for empty signal generations, otherwise the daily orchestrator aggregation will fail.
* **Precision Mismatch Handling:** Explicitly warn that instrument-only parameter fixes for precision bugs are insufficient. Any precision adjustments must perfectly align with the actual tick precision of the underlying data. Failure to address this root cause will result in RuntimeError crashes that silently abort the matrix backtest loops.
* **Log Management:** Local backtest .log and .json files must be kept out of Git tracking to avoid repository bloat and blocked pushes. Always use `git checkout origin/main -- logs/` or explicitly unstage modified log files.
* **Pitfall #56 (Orphaned Limit Orders & State Locks via Custom Exits - Issue #307):** Das Überschreiben von Exits (z.B. `def _close_position`) in Child-Klassen ohne Beachtung von `orders_open` und dem Async-State der Base-Class führt zu Order-Spamming, Orphaned Limit-Orders und AttributeError-Crashes bei Aufrufen wie `super()._close_position(pos)`. Strategien dürfen `_close_position` nicht überschreiben, sondern müssen `self._close_position_base()` aus der Basisklasse nutzen, um den korrekten asynchronen Bereinigungsfluss beizubehalten. Zusätzlich führt ein asynchroner State-Lock (Einfrieren der Strategie) auf, wenn `current_signal` nicht im asynchronen `on_position_closed`-Callback bereinigt wird, sondern synchron im Signal-Handler. (Referenz: Issue #149, #290, #307).
* **Pitfall #46 (Observability & Hidden Gates):**
  * **Symptom:** Rejection-Logs (z. B. `min_win_rate failed`) verweisen auf Schwellen, die im Startup-Header nie erwähnt wurden. Backtests brechen mit Warnungen ab, deren Werte mathematisch nicht zum deklarierten Limit passen.
  * **Root Cause:** Hardcodierte Print-Statements für die Konfiguration, die neu hinzugefügte Parameter (wie Expectancy oder Drawdown) verschwiegen haben. Oder hardcodierte Guards im Code (wie `0.95 * required_days`), die konfigurierte Toleranzen (wie `span_tolerance_days`) stumm überstimmen.
  * **Architektur-Regel:** Konfigurationen, die das Gating oder den Control-Flow steuern (speziell Tournament- und OOS-Thresholds, sowie Data-Span-Requirements), müssen beim Systemstart lückenlos (`req_all`, `req_any`, `Effective Data Span`) und transparent an stdout/Logger ausgegeben werden. "Hidden Gates" sind streng untersagt. Die im Log geprinteten Constraints müssen mit der exekutierten Logik exakt deckungsgleich sein.
  * **Single Source of Truth Constraint:** Gemäß **Issue #304** ist `span_tolerance_days` aus `automation/config/backtest.json` (aktuell 3.0) die absolute "Single Source of Truth" für Datenspannen-Defizite. Jegliche lokale Überschreibung, unsaubere Fallback-Zuweisungen in den Logging-Blöcken (z.B. Fallback auf 1.0 in `backtest_runner.py`), oder Default-Parameter in Worker-Funktionssignaturen sind strikt untersagt, um Diskrepanzen zwischen Header-Logs und tatsächlichen Validierungs-Guards zu verhindern.
* **Pitfall #47 (Breakout Lookback Contamination & Alternation Lock Trap - Issue #260):**
  * **Symptom:** Exzessives Overtrading (hunderte Trades) kombiniert mit einer kollabierenden WinRate (ca. 1%) bei Ausbruchsstrategien wie `DynamicBreakoutStrategy`.
  * **Root Cause:** (A) History-Befüllung VOR Min/Max-Kanalberechnung führt dazu, dass der aktuelle Bar selbst der Breakout-Level wird. Dadurch kauft das System unweigerlich das absolute Top der Kerze und shortet den absoluten Bottom (lokale Extreme). (B) Fehlender Signal-Reset bei Base-Class Stopouts zwingt das System in einen "Alternation Lock" (ein Kauf kann nur auf einen Verkauf folgen, niemals auf einen gestoppten Kauf), wodurch massiv Churn beim Versuch des Reversals auf ein- und demselben Bar entsteht.
  * **Lösung:** Historische Daten-Pipelines (z.B. Deques) dürfen *immer erst nach* der Evaluierung des aktuellen Bars aktualisiert werden (Ende von `on_bar`). Der Signalzustand muss dynamisch über den nautilus cache (`positions_open`) abgefragt werden, statt über eine flüchtige String-Statusvariable.

* **Pitfall #49 (Infinite Historical Fetch Loop bei jungen Instrumenten / Inception Bounds - Issue #261):**
  * **Symptom:** Der `daily_orchestrator.py` ruft jeden Tag den `historical_fetcher.py` für Instrumente auf, die jünger sind als die benötigte In-Sample Spanne (z.B. Listing am 20.11., gefordert ab 09.11.). Dies verlangsamt die Pipeline drastisch und erzeugt Endlosschleifen.
  * **Root Cause:** `is_backtest_range_covered` prüft strikt gegen das errechnete Datum (`start_ns`), unabhängig davon, ob die API überhaupt ältere Ticks liefert. Da die ältesten Ticks > `start_ns` sind, schlägt die Validierung ewig fehl.
  * **Lösung:** Implementierung eines Caches (`inception_bounds.json`) in `data/state/`. Stößt der Fetcher auf sein historisches Limit (API liefert keine neuen Candles mehr, aber Ziel nicht erreicht), speichert er den ältesten bekannten Timestamp als Inception Bound atomar ab. Zukünftige Aufrufe von `is_backtest_range_covered` erkennen dies und melden "vollständige Abdeckung" für das Symbol.
* **Pitfall #57 — Alternation Lock & Cooldown Bypass Trap in Mean Reversion/Breakout:**
  * **Symptom:** Extremes Overtrading und unkontrollierte Whipsaw-Re-Entries in `DynamicBreakoutStrategy` und `MeanReversionStrategy`, die zu defizitären Kaskaden führen, sowie eine 10×-Trade-Diskrepanz zwischen `MeanReversionStrategy` und `HourlyMeanReversionStrategy`.
  * **Root Cause:** Ein Logik-Fehler im `can_signal` Guard (ein `or`-Bypass) hebelte in der Keltner-Strategie den Cooldown nach Exits aus. Die `HourlyMeanReversionStrategy` implementierte zudem kein `on_position_closed`, wodurch der State `current_signal` einfror und künstlich eine Alternierung erzwang. Bei der Breakout-Strategie setzte `on_position_closed` den Cooldown-Zähler falsch zurück, sodass sofort wieder eingestiegen werden konnte.
  * **Lösung:** Cooldowns als harte AND-Bedingungen umgesetzt. `on_position_closed` in allen Klassen synchronisiert, sodass `bars_since_last_signal` auf 0 zurückgesetzt wird und ein echter Cooldown nach Stop-Outs/Exits erzwungen wird.

* **Pitfall #58 (Zero-Trade Statistical Cascades / Micro-Sizing Illusion):**
  Wenn ein Symbol-Strategie-Paar im Backtest 0 Trades generiert, dürfen nachgelagerte Trade-Metrik-Guards (wie der Median-Notional-Floor von $10) nicht evaluiert werden. Architektur-Regel: 0-Trade-Ergebnisse müssen in den Evaluierungsfunktionen (`_is_eligible`, `_evaluate_oos_eligibility`) sofort via Early-Return hart ausgeschlossen werden. Downstream-Validierungen von Dummy-Werten (wie `0.0` bei fehlenden Trades) führen unweigerlich zu systematischem Log-Spam (z. B. "Micro-Sizing"-Ablehnungsgründen) und verdecken das eigentliche Problem (keine Signal-Auslösung).

### 🟢 Pitfall #59 — Restriktive Strategiefrequenzen vs. OOS-Gating-Fenster (Issue #289)
**Symptom:** Strategien mit exzellenten IS-Werten fallen im OOS-Gate reihenweise wegen Trade-Mangel durch (Rejection: `oos_not_evaluable`).
**Root Cause:** Bei engmaschigen Out-of-Sample-Testfenstern (z. B. 30 Tage OOS bei 1h-Bars) sind hoch-restriktive Indikator-Schwellenwerte (wie ein `deviation_threshold` von 1.5 % bei der VwapExhaustionStrategy) zu träge. Die statistische Mindestanzahl von Trades (`oos_min_trades`) wird strukturell nie erreicht.
**Fix:** Herabsetzung des `deviation_threshold` auf 0.8 % in den Defaults, um die Signal-Frequenz stabil auf das OOS-Validierungsfenster zu kalibrieren. Zudem Einführung einer strikten statistischen Trennung zwischen echten OOS-Fails (`oos_failed_pairs`) und strukturell nicht-evaluierbaren Paaren (`oos_not_evaluable_pairs`) im Root-JSON der Turnierauswertung.
**Betroffen:** `automation/strategies/vwap_exhaustion.py`, `automation/config/strategy_defaults.json`, `automation/backtest_runner.py`

| 2026-06-08 | **Issue #289 (OOS-Drop & Statistik-Trennung):** Rekalibrierung des `deviation_threshold` der VwapExhaustionStrategy auf 0.8 % zur Absicherung der Mindest-Trade-Frequenz im 30d-OOS-Fenster. Implementierung der deklarativen Trennung von `oos_not_evaluable_pairs` und `oos_failed_pairs` im Turniersystem und Terminal-Output unter Beibehaltung aller Core-Signaturen. | `automation/strategies/vwap_exhaustion.py`, `automation/config/strategy_defaults.json`, `automation/backtest_runner.py`, `automation/AGENTS.md` |
  * **Symptom:** Extremes Overtrading und unkontrollierte Whipsaw-Re-Entries in `DynamicBreakoutStrategy` und `MeanReversionStrategy`, die zu defizitären Kaskaden führen, sowie eine 10×-Trade-Diskrepanz zwischen `MeanReversionStrategy` und `HourlyMeanReversionStrategy`.
  * **Root Cause:** Ein Logik-Fehler im `can_signal` Guard (ein `or`-Bypass) hebelte in der Keltner-Strategie den Cooldown nach Exits aus. Die `HourlyMeanReversionStrategy` implementierte zudem kein `on_position_closed`, wodurch der State `current_signal` einfror und künstlich eine Alternierung erzwang. Bei der Breakout-Strategie setzte `on_position_closed` den Cooldown-Zähler falsch zurück, sodass sofort wieder eingestiegen werden konnte.
  * **Lösung:** Cooldowns als harte AND-Bedingungen umgesetzt. `on_position_closed` in allen Klassen synchronisiert, sodass `bars_since_last_signal` auf 0 zurückgesetzt wird und ein echter Cooldown nach Stop-Outs/Exits erzwungen wird.
* **🟢 Issue #286, Issue #303 — OOS-Portfolio-max_drawdown Portfolio-Equity-Aggregation**
  * **Problem:** Der max_drawdown des aggregate_winner wurde historisch als Median der Pair-Drawdowns aggregiert, was das wahre Portfolio-Risiko und zeitgleiche/zeitversetzte Drawdown-Tiefpunkte drastisch unterschätzte.
  * **Lösung:** Behoben durch die Einführung chronologischer Trade-PnL-Merges ('_oos_trade_records') vor der statistischen Evaluierung. Das Phase-5-Gate in 'daily_orchestrator.py' referenziert nun auch den echten Portfolio-DD. Um JSON Bloat zu verhindern, wird `_oos_trade_records` über `.pop()` vor dem Export gelöscht. Es ist extrem wichtig, dass `extract_metrics` diese rohen Daten temporär weiterreicht, da nur so ein chronologischer Portfolio-Merge in `select_winners` möglich ist. Zukünftige Refactorings dürfen diesen Datenfluss nicht als 'unnötigen Bloat' wegoptimieren.

| 2026-06-08 | **Issue #292 Fixed:** Cooldown-Bypass nach Stop-Outs in DynamicBreakout eliminiert. can_signal Logik-Fehler (or-Bypass) in Keltner-Strategien behoben und HourlyMeanReversion State-Reset via on_position_closed synchronisiert. | `automation/strategies/dynamic_breakout.py`, `automation/strategies/mean_reversion.py`, `automation/strategies/hourly_mean_reversion.py`, `automation/AGENTS.md` |
| 2026-06-07 | **Issue #271 (Knappe Datenspanne & Toleranz-Inkonsistenz):** Kalender-Awareness für Wochenendläufe in daily_orchestrator.py integriert. Hardcoded 0.95-Guard in backtest_runner.py eliminiert; span_tolerance_days in backtest.json auf 3.0 angehoben und als Single Source of Truth in check_data_span verankert. Transparentes Logging des effektiven Schwellenwerts im Startup-Header implementiert. | `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/config/backtest.json`, `automation/AGENTS.md` |
| 2026-06-04 | **Issue #172 (OOS-Gate Propagation & Transparenz):** `aggregate_winner` trägt nun deterministisch `oos_evaluated`/`oos_eligible`/`oos_metrics`/`oos_rejection_reasons`. Phase 5 dreistufig (eligible/failed/not-evaluable) mit präziser Begründung statt „Status unbekannt". Walk-Forward Ableitung vereindeutigt + OOS-Span-Logging. Fail-Closed unverändert, keine Schwellen-Absenkung. Tests für alle drei OOS-Zustände ergänzt. | `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/tests/test_backtest_runner.py`, `automation/tests/test_oos_gate.py`, `automation/AGENTS.md` |
| 2026-06-05 | **Issue #182 (Sizing-Precedence Bug im Backtest):** Behebung der unbeabsichtigten Priorisierung von `%` über `USD` bei gesetzten Defaults. `_compute_quantity` enforcing Hierarchy: `allocator > trade_amount_usd > trade_amount_pct > Default`. Regression-Test ergänzt. §6 `AGENTS.md` aktualisiert. | `automation/strategies/hourly_strategy_base.py`, `automation/tests/test_sizing_precedence.py`, `automation/AGENTS.md` |
| 2026-06-05 | **Issue #213 (Crypto Precision, Strategy Divergence & Trend Filter):** Fixed `_normalize_size_precision` falling back to `2` for crypto. Standardized `MeanReversionStrategy` to use `KeltnerChannel` and removed manual `_close_position` overrides to fix divergence. Added `trend_filter_period` to `HourlyStrategyConfig` with a `_warmup_trend_filter` in `HourlyStrategyBase` that loads and resamples parquet data to pre-warm the SMA filter locally, enforcing fail-closed gatekeeping via `can_go_long` before generating BUY signals. | `automation/strategies/hourly_strategy_base.py`, `automation/strategies/mean_reversion.py`, `automation/strategies/hourly_mean_reversion.py`, `automation/backtest_runner.py`, `automation/tests/test_api_precisions.py`, `automation/tests/test_live_execution_defaults.py`, `automation/AGENTS.md` |
| 2026-06-06 | **Issue #194 (Sizing Bug & Impossible Risk Metrics Guards):** Fixed sizing calculation bug causing microscopic returns by strictly quantizing quantity to align with tick precision and size_increment in `HourlyStrategyBase._compute_quantity`. Introduced `EPSILON = 1e-9` in risk metric denominators. Enforced sample size minimums for near-all-win situations (`losses_count < 2 and n < 50`) to return `None` in `backtest_runner.py._calculate_stats`. | `automation/strategies/hourly_strategy_base.py`, `automation/backtest_runner.py`, `automation/tests/test_backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-06 | **Issue #207 (Fix Tournament Expectancy Gating & IS Transparenz):** Absenkung von `min_expectancy` auf `0.00005`, neues Logging der Ablehnungsgründe in `_is_eligible()` sowie Cooldown-Logik für Mean-Reversion. Bugfix in `test_oos_aggregation.py` für `total_trades` Average-Calculation. | `automation/config/tournament.json`, `automation/backtest_runner.py`, `automation/strategies/mean_reversion.py`, `automation/strategies/hourly_mean_reversion.py`, `automation/AGENTS.md` |
| 2026-06-07 | **Issue #274 (Metrics Rendering, Precise Rejection Reasons & OOS Log Precision):** Fix für `_is_eligible` eingeführt, um bei fehlenden Metriken genaue Ablehnungsgründe (z.B. `"all-loss"`, `"all-win"`) zu ermitteln anstatt pauschalem Text. In `format_metric` der Tabellenausgaben wird jetzt die Limitprüfung für Trade-Anzahl zuerst ausgeführt, um bei sehr wenigen Trades das korrekte `"n/a(<min)"` anstelle irreführender Float-Werte auszugeben. In `_evaluate_oos_eligibility` wird das OOS-Log für Fließkommazahlen auf 5 Nachkommastellen (`:.5f`) erhöht, um bei Fehlermeldungen logische Rundungsparadoxien zu vermeiden. Pitfall #43 dokumentiert. | `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-08 | **Issue #287 (Fix Datenspannen-Toleranz-Logging):** Vereinheitlichung der `span_tolerance_days` Zuweisung im Startup-Header von `backtest_runner.py`. Beseitigung des redundanten Fallbacks auf `1.0` durch Kopplung an die zentrale `backtest.json` (3.0d). Grenzwerttests hinzugefügt. | `automation/backtest_runner.py`, `automation/AGENTS.md`, `automation/tests/test_backtest_runner.py` |
| 2026-06-08 | **Issue #276 (Verfälschung von Risk-Metriken & Spread-Nachkalibrierung):** Behebung eines OOS-Gate-Leaks, bei dem Low-Sample Strategien mit exakt 1 Verlust falsche (unendliche) Sortino-Werte erhielten. Einführung eines harten Caps (2.0) für diese Ausreißer. Dokumentation der Realized FIFO-PnL-Basis für Drawdown-Berechnungen (vs. MtM). Erhöhung des eToro Equity-Spreads in `backtest.json` von 3.0 auf 8.0 bps zur Eindämmung künstlicher Renditen. | `automation/backtest_runner.py`, `automation/config/backtest.json`, `automation/AGENTS.md`, `automation/tests/test_backtest_runner.py` |
| 2026-06-08 | **Issue #291 (0-Trade Micro-Sizing Artifact Fix):** Micro-Sizing-Check in `_is_eligible` wird nun blockiert, wenn `n_trades == 0`. Verhindert irreführende Kaskaden-Rejections (`Median notional < 10.0`) bei Paaren ohne Trade-Aktivität. | `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-08 | **Issue #288 (Low-Sample Tournament Dominance Fix):** Harmonisiertes Ratio-Capping auf 50.0 in `_calculate_stats`. Logistisches Sample-Size Shrinkage via `k_shrinkage` auf den finalen Score angewendet. Dynamische, stichprobenbasierte All-Win Sentinel-Skalierung mit Schutzdeckel gegen organische Outrankings implementiert. `k_shrinkage` aus Code in `tournament.json` extrahiert und transparentes Startup-Logging hinzugefügt. **Constraint:** Zukünftige Agent-Runs dürfen diese mathematischen Shrinkage- und Capping-Gatekeeper unter keinen Umständen entfernen oder überschreiben. | `automation/backtest_runner.py`, `automation/config/tournament.json`, `automation/AGENTS.md` |
| 2026-06-08 | **Issue #290 (Behebung fehlerhafter Positions-Exits und State-Locks):** Behebung des fehlerhaften `_close_position`-Overrides und Verhinderung von State-Locks bei automatischen Exits (ATR/Time-Exits) in `vwap_exhaustion.py` und `flash_crash_reversal.py` durch Einführung standardisierter `on_position_closed`-Lifecycle-Resets und Umstellung auf `_close_position_base()`. | `automation/strategies/vwap_exhaustion.py`, `automation/strategies/flash_crash_reversal.py`, `automation/AGENTS.md` |
| 2026-06-09 | **Whipsaw-Bug & Performance-Optimierung:** Cooldown-Logik in `DynamicBreakoutStrategy` und `MeanReversionStrategy` dokumentiert (`bars_since_last_signal = 0` via `on_position_closed`). Diskrepanz bei `MeanReversion` (Defaults 20/20/2.0 vs. Hourly 10/10/1.5) per Docstring geklärt. Beide Strategien in `strategies.json` als Default deaktiviert, um Overtrading/Rauschen zu reduzieren. Implementierung eines globalen `max_daily_trades` Whipsaw-Detektors in `HourlyStrategyBase` (`_daily_trades` Zähler, Reset bei Tageswechsel, Blockierung via `_compute_quantity`), um irrelevantes HFT-Whipsawing auf Base-Class-Level abzufangen. | `automation/strategies/hourly_strategy_base.py`, `automation/strategies/mean_reversion.py`, `automation/config/strategies.json`, `automation/AGENTS.md` |
* **Live-Trading Safety Rule:** Zero OOS-eligible pairs MUST strictly prevent any live deployment. The aggregate OOS pass cannot override a per-pair failure. No individual symbol-strategy pair may be deployed live unless its corresponding strategy has been verified as OOS-eligible within the tournament execution matrix.
| 2026-06-08 | **Issue #286 / PR #294 (Portfolio-Equity-Aggregation für aggregate_winner):** Refactoring der Turnier-Aggregation. 'aggregate_winner' berechnet Risiko- und Performancemetriken (max_drawdown, Sortino, PF) nun aus chronologisch gemergten OOS-Einzeltrades anstelle von Pair-Medians. Regressions-Tests in 'test_oos_aggregation.py' integriert. | `automation/backtest_runner.py`, `automation/AGENTS.md`, `automation/tests/test_oos_aggregation.py` |
| 2026-06-08 | **Issue #304 (Data Span Tolerance Logging Mismatch):** Beseitigung inkonsistenter Toleranz-Werte im Startup-Logging. Single Source of Truth (`span_tolerance_days` = 3.0) über alle Ausgabequellen, Worker-Funktionssignaturen und Guards hinweg erzwungen. Tests für Toleranzgrenzen-Akzeptanz verifiziert. | `automation/backtest_runner.py`, `automation/AGENTS.md`, `automation/tests/test_backtest_runner.py` |
| 2026-06-08 | **Issue #303 (Portfolio-Equity-Aggregation für OOS-Gate / Max DD):** Korrektur der OOS-Trade-Aggregation für das Phase 5 Gate in `daily_orchestrator.py`. Rohe PnLs werden via `_oos_trade_records` exportiert, chronologisch sortiert und als Portfolio-Equity-Kurve für einen korrekten `max_drawdown` ausgewertet, um Drawdown-Glättung durch Mediane zu vermeiden. | `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/tests/test_oos_aggregation.py` |
| 2026-06-09 | **Issue #307 (Toter Code in `_close_position` & fehlerhaftes Lifecycle-State-Handling):** Tote `_close_position` Overrides in VwapExhaustion und FlashCrashReversal entfernt. Reversal-Pfade strikt auf `_close_position_base` umgestellt und `current_signal` Reset in `on_position_closed` garantiert. | `automation/strategies/vwap_exhaustion.py`, `automation/strategies/flash_crash_reversal.py`, `automation/AGENTS.md` |
* **Live-Trading Safety Rule:** Zero OOS-eligible pairs MUST strictly prevent any live deployment. The aggregate OOS pass cannot override a per-pair failure.
* **Data Structure Rule:** The `oos_metrics` object is a sibling key to `metrics` in backtest results, never nested. Always access via `r.get('oos_metrics')`.

| 2026-06-08 | **Issue #306 (OOS-Drop & Statistik-Trennung):** Rekalibrierung des `deviation_threshold` der VwapExhaustionStrategy auf 0.008 (0.8 %) und `cooldown_bars` auf 3 zur Absicherung der Mindest-Trade-Frequenz im 30d-OOS-Fenster. Implementierung der deklarativen Trennung von `oos_not_evaluable_pairs` und `oos_failed_pairs` im Turniersystem (`write_tournament_json`) und im Orchestrator Phase 5 Logging, um Daten-Mangel-Abbrüche (insufficient trades) strikt von Performance-Abbrüchen zu trennen. | `automation/strategies/vwap_exhaustion.py`, `automation/config/strategy_defaults.json`, `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/AGENTS.md` |
| 2026-06-09 | **Issue #310 (Gate-Scope vs. Deployment-Scope):** Behebung eines Architektur-Fehlers in Phase 5 des Orchestrators. Die Logik wurde so umgestellt, dass eine explizite `whitelist_tournament.json` erzeugt wird, die per-symbol OOS-Verlierer aussortiert. `momentum_ls_run.py` nutzt nun diese Whitelist als einzige Source of Truth. | `automation/daily_orchestrator.py`, `automation/AGENTS.md` |
| 2026-06-10 | **Optimizer Fixes (Manifest, Universe, Isolierung & Spaces):** Erweiterung des Autotuner/Optimizer-Moduls um wichtige Standalone-Constraints. 1) Manifest-Bug behoben: `catalog_path` wird zwingend in `build_trial` injiziert. 2) Universe-Größe wird dynamisch aus `momentum_ls.json` aufgelöst, statt die Winners-Länge als Proxy zu missbrauchen (verhindert Coverage-Exploits). 3) Invarianten `n_folds=4` und `holdout_days=45` wurden hardcodiert in `run_optimization.py` erzwungen. 4) Dynamische Pfad-Isolation (`config_dir()`) in `reward.py` verankert, um Konflikte bei parallelen Worker-Instanzen zu vermeiden. 5) Suchräume für 4 neue Strategien (`ComboTrendVwapStrategy`, `FlashCrashReversalStrategy`, `VolatilityBreakoutPumpStrategy`, `VwapExhaustionStrategy`) implementiert. **Wichtig:** Sämtliche Änderungen verletzen die Standalone-Constraint von `/automation/optimizer` nicht! | `automation/optimizer/trial_config.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/reward.py`, `automation/optimizer/spaces.py`, `automation/AGENTS.md` |
| 2026-06-11 | **Bugfix Issue #339:** `TypeError` in `backtest_runner.py` behoben. Bei der Pfad-Erzeugung für Worker-Logs wird nun die Variable `logs_dir_str` anstatt der Funktion `logs_dir` genutzt. | `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-10 | **Issue #312 (Portfolio OOS Capital Mismatch Fix):** `start_capital` wird nun vom Worker an das Turniersystem durchgereicht. Behebt den Scaling-Faktor-Bug (10x) bei der portfoliobasierten Drawdown- und Return-Aggregation für das Phase-5-Gate. | `automation/backtest_runner.py`, `automation/tests/test_oos_aggregation.py`, `automation/AGENTS.md` |
| 2026-06-11 | **Bugfix `KeyError: 'strategy_module'` und Optuna FileNotFoundError:** 1) In `backtest_runner.py` wird nun `.get()` verwendet und ein `ValueError` ausgelöst, wenn `strategy_module` fehlt. 2) In `trial_config.py` liest `build_trial` nun das `strategy_module` sowie `config_class` aus `strategies.json` aus und speichert diese in das erzeugte Manifest. 3) In `runner.py` prüft `run_backtest` nun den Subprozess-Returncode und fängt Abstürze mit `optuna.TrialPruned` statt `FileNotFoundError` ab. | `automation/backtest_runner.py`, `automation/optimizer/trial_config.py`, `automation/optimizer/runner.py`, `automation/tests/test_optimizer_runner.py`, `automation/AGENTS.md` |

### Architectural Dependency: Strategy Parameters and OOS Gating
* **Trade-off Constraint:** Configurations in `strategy_defaults.json` (such as `deviation_threshold` for mean-reversion strategies) MUST be strictly calibrated against the `oos_min_trades` tournament gating requirement relative to the Out-of-Sample evaluation window (e.g., 30 days). If thresholds are too tight (e.g., 0.015 instead of 0.008 for VWAP), the mathematical possibility of passing the OOS gate falls to zero because the strategy naturally produces too few signals within the OOS span to be statistically evaluable. This results in false-positive "fail" states.
| 2026-06-08 | **Issue #305 (Consistent Capping Policy & Raw Ratio Sample-Size Shrinkage):** Vereinheitlichte das Capping für alle Ratio-Metriken (Sortino, Profit Factor, Calmar) in `_calculate_stats` auf exakt `50.0`. Um Division-by-Zero zu verhindern, wurde für alle Nenner (Downside-Dev, Gross Loss, Max Drawdown) ein `DENOMINATOR_FLOOR = 1e-6` eingeführt. Zusätzlich wird nun in `select_winners` eine asymptotische Dämpfungsfunktion (Shrinkage) auf die Raw-Ratios basierend auf `n_trades` und `k_shrinkage` angewendet, *bevor* die Rankings berechnet werden. Dabei wird die Sortino Ratio in Richtung Baseline `0.0` und der Profit Factor in Richtung Baseline `1.0` gedämpft: `Damped_Ratio = Baseline + (Raw_Ratio - Baseline) * (n_trades / (n_trades + k_shrinkage))`. Dies verhindert, dass kleine Sample-Sizes durch Rauschen ungedämpfte Outlier produzieren und die Turnier-Selektion dominieren. | `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-11 | **Optimizer Handbuch Bugfix:** Aktualisierung von manuals/run_optimizer.md. Die veraltete Warnung (Abschnitt 0) und Empfehlungen für manuelle Code-Änderungen (Abschnitt 5.4, 9.1) wurden entfernt, da Bugs B1-B4 im Code bereits behoben wurden. Die Fehler-Referenzen im Fehlerkatalog und in der Diskrepanz-Tabelle (Abschnitt 10) wurden auf 'Behoben' gesetzt. | `manuals/run_optimizer.md`, `automation/AGENTS.md` |

### Pitfall #60: Gate-Scope vs. Deployment-Scope (Mismatch Prevention)
- **Symptom:** Ein notorischer Einzel-Verlierer könnte fälschlicherweise live geschaltet werden, weil er innerhalb seines isolierten Symbols als lokaler Gewinner hervorging oder Teil eines Turniers war, das auf Aggregat-Ebene (Portfolio-Level) das OOS-Gate bestand, obwohl die spezifische Strategie das OOS-Gate nie individuell bestanden hat.
- **Root Cause:** Der Orchestrator (Phase 5) validierte standardmäßig das *aggregierte* OOS-Gate (Portfolio-Level). `momentum_ls_run.py` deployt jedoch die *individuellen per-Symbol-Gewinner*. Wenn das Aggregat-Gate bestanden wurde, wurde bisher die gesamte State-Datei übergeben, ohne isolierte OOS-Verlierer auf Symbol-Ebene strikt herauszufiltern.
- **Fix/Rule:**
  1. Die `daily_orchestrator.py` muss zwingend eine explizite Whitelist generieren (`whitelist_tournament.json`), bevor der Live-Run angestoßen wird. Dies wird über einen `OOS-DEPLOY-REJECT`-Filter in `_build_bots_config` hart blockiert.
  2. In dieser Whitelist werden nur diejenigen Symbole aus `per_symbol_winners` behalten, die **individuell** das OOS-Gate bestanden haben (`oos_eligible == True` UND `oos_evaluated == True`). Alle anderen werden gedroppt und geloggt.
  3. JSON-Events und Logausgaben im Orchestrator müssen zur Vermeidung von Mehrdeutigkeiten zwischen IS und OOS strikt qualifiziert werden (z. B. `median_is_sortino` vs. `aggregate_oos_sortino`).
  4. Diese Whitelist ist die *einzige* Source of Truth für das tatsächliche Deployment (`--tournament whitelist_tournament.json`). Das übergeordnete Aggregat-Gate ist lediglich ein Vorfilter für den generellen Start, qualifiziert aber keinen individuellen Ausreißer.
