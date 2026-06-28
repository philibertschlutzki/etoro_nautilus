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
18.5. [Agent-Rollen, System-Prompts & Interaktionsprotokolle (Enterprise / Security-Audit-Grade)](#185-agent-rollen-system-prompts--interaktionsprotokolle-enterprise--security-audit-grade)
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

- **`automation/optimizer/`**: Paket für Closed-Loop-Hyperparameter-Optimierung. Submodule umfassen `manifest`, `resolve`, `trial_config`, `runner`, `parsing`, `reward`, `spaces`, `confirm`, `run_optimization`, `bounds`, `gate`, `sweep`. `gate.py` (A4.4) ist Gate 1 (Daten-Suffizienz); `sweep.py` (A4.6) ist der Per-Symbol-Meta-Orchestrator (Enumeration → Gate 1 → Dispatch von `optimize_symbol`/`confirm_per_symbol_promotion`). `bounds.py` (A4.0) extrahiert die numerischen Suchraum-Grenzen deklarativ aus `spaces.sample_params` per aufzeichnendem Trial-Double (`_RecordingTrial`) — einzige Quelle der Wahrheit für die Normierung in `param_pen` (A4.3); kategoriale (`require_*`) und abgeleitete (`macd_slow`) Parameter werden bewusst ausgeschlossen.

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

### Positions-Sizing-Priorisierung (`HourlyStrategyBase`)
*(Flat-Lock nach Reverse Entry — Signal-State-Reset nach `_close_position()` via Lifecycle-Callback `on_position_closed` — ist kanonisch als Pitfall #17 in §16 dokumentiert; hier bewusst nicht doppeln.)*

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
  Keys: `n_trials`, `n_startup_trials`, `seed`, `penalty_overfit_weight`, `penalty_dd_weight`, `bonus_coverage_weight`, `penalty_unevaluable_oos`, `sortino_clip_abs`, `unevaluable_shaping_span`, `evaluable_floor_epsilon`, `shaping_trade_target`, `per_symbol_shaping_trade_target` (#406), `shaping_return_target` (#407), `shaping_winrate_target` (#407), `lambda_reg`, `promotion_margin`, `reward_mode`, `oos_sortino_fallback`, `reward_semantics_version` (#410).
  Dynamische Reward-Gewichtung (Zero-Hardcoding): Gewichte (`penalty_overfit_weight`, `penalty_dd_weight`, etc.) werden direkt aus `optimizer.json` gelesen, das `max_drawdown`-Cap (DD-Cap) aus `tournament.json`.
  **Reward-Invariante (Pitfall #75, Issues #404–#410):** Im Per-Symbol-Pfad ist die **Evaluierbarkeit (`oos_evaluated`) vom Gewinner-/Aggregat-Status entkoppelt** (`single_symbol_oos`-Block). Die **Anti-Gate-Gaming-Invariante** bleibt erhalten: das Unevaluable-Shaping (Trade-/IS-Aktivität, Gate-Proximity) ist auf `[0,1]·unevaluable_shaping_span` gebunden, also gilt strikt **Unevaluable < Evaluable-Floor** — evaluierbare Trials werden IMMER besser bewertet als unevaluable.
  **Per-Symbol-Reward (A4.3):** Bei `universe_size == 1` (oder `reward_mode == 'per_symbol'`) entfällt der Coverage-Term (`win_count/universe_size` degeneriert) und stattdessen greift eine Shrinkage-Strafe `param_pen = lambda_reg · normalized_param_distance(sampled, global_params, bounds)` Richtung globalem Optimum (Gate 2 im Reward-Raum). Der Coverage-Pfad (`universe_size > 1`, `reward_mode='auto'`) bleibt **bit-identisch**. Die Floor-/Ordnungsinvariante bleibt strikt: `floor = penalty_unevaluable_oos + unevaluable_shaping_span + evaluable_floor_epsilon`, `return max(reward, floor)` ⇒ jeder evaluierbare Trial `>= floor >` jeder nicht-evaluierbare. **Kalibrierungs-Hinweis:** `lambda_reg` konservativ wählen — sehr große `param_pen` kollabieren Trials auf den Floor (TPE unterscheidet geflorte Trials nicht); nahe dem Optimum bleibt der Gradient erhalten. `param_pen` greift nur, wenn `sampled`, `global_params` und `strategy` übergeben werden (sonst 0.0).
  **`shaping_trade_target` (Zero-Hardcoding, ISSUE-OPT-375):** Summe der IS-Trades über das Universum, bei der das IS-Aktivitäts-Shaping nicht-evaluierbarer Trials saturiert. Solange kein Symbol IS-eligibel wird, ist `oos_total_trades = 0` und der Reward flächig `penalty_unevaluable_oos` — ohne Gradient. `compute_reward` koppelt das Unevaluable-Shaping daher an `max(trade_progress_oos, min(1, is_total_trades / shaping_trade_target))`, sodass „fast eligibel" von „nie eligibel" unterscheidbar wird. **Floor-Invariante bleibt strikt:** `progress ∈ [0,1] ⇒ shaping ≤ unevaluable_shaping_span`, jeder nicht-evaluierbare Trial bleibt `≤ penalty + span` und damit unter dem Evaluable-Floor (`penalty + span + evaluable_floor_epsilon`). Die IS-Aktivität (`is_total_trades`/`is_max_trades`) wird in `parsing.TournamentMetrics` aus `full_results[].metrics.total_trades` abgeleitet (von `backtest_runner.write_tournament_json` exportiert). Fehlt `shaping_trade_target` in den (injizierten) Weights, gilt der Legacy-OOS-only-Pfad. Konservativ kalibrieren — zu kleine Werte saturieren den Gradienten vorzeitig.
  **Gate 1 — Daten-Suffizienz (A4.4, `optimizer/gate.py`):** Schwellen `gate1_buffer_days` (30), `min_bars_per_param` (200), `min_oos_bars_per_fold` (500). `is_symbol_tunable(symbol, n_params, *, available_bars, config)` gibt `(ok, reason)` zurück und ist **rein/I-O-frei** — die Bar-Zahl wird per Injektion geliefert (der Sweep A4.6 zählt sie über `historical_fetcher`). Geprüft wird in dieser Reihenfolge: (a) `available_bars >= required_bars((is + splits*oos + holdout + gate1_buffer_days) * 24)` → sonst `INSUFFICIENT_HISTORY`; (b) `available_bars / max(1, n_params) >= min_bars_per_param` → sonst `PARAM_DATA_RATIO_TOO_LOW` (Anti-Overfit-Heuristik); (c) `oos_window_days * 24 >= min_oos_bars_per_fold` → sonst `OOS_FOLD_TOO_SHORT`. **Finding (Recon):** Ein `historical_fetcher.is_symbol_data_sufficient` existiert **nicht** (vorhanden ist nur das range-basierte `is_backtest_range_covered`); Gate 1 nutzt daher bewusst eine injizierte Bar-Zahl statt jener Funktion.
  **Single-Symbol-Study + Gate 2 — Warm-Start (A4.5a, `run_optimization.optimize_symbol`):** `optimize_symbol(strategy, symbol, n_trials=None, *, storage=None)` legt eine **eigene benannte SQLite-Study** unter `{WORK}/sweep/study_{strategy}_{_sanitize(symbol)}.db` an (per-Study-Isolation ⇒ keine Lock-Contention, EP-2), erzeugt Manifeste mit `instruments=[symbol]` (⇒ `universe_size==1`, Per-Symbol-Reward) und **erzwingt `n_jobs=1`** (SQLite-Reproduzierbarkeit, Pitfall #68). **Gate 2** ist der Warm-Start: `study.enqueue_trial(load_global_best(strategy, …))` setzt den ersten Trial auf das globale Optimum. `load_global_best` bevorzugt `proposal_{strategy}.json['proposed_params_override']` **nur** bei status `READY_FOR_PR`, sonst `strategies.json[strategy].params`, sonst `{}`. Das globale `optimize`/`make_objective` bleibt unverändert.
  **Gate 3 — Promotion-Marge gegen Global (A4.5b, `confirm.confirm_per_symbol_promotion`):** Das **entscheidende** Per-Symbol-Gate. Ein `instrument_override` wird nur promotet, wenn der symbol-getunte Vektor auf dem **ungesehenen Holdout** (a) das Holdout-Gate selbst besteht (`oos_evaluated ∧ oos_eligible ∧ oos_sortino>0 ∧ oos_max_drawdown ≤ risk_dd_cap`) **und** (b) das globale Baseline um `promotion_margin` (optimizer.json) schlägt: `promote = holdout_passed ∧ (R_symbol > R_global + promotion_margin)`. `_holdout_metrics_for_params` baut je Vektor einen single-symbol-Holdout (`holdout_days=0`, `n_folds=1`, `oos_window_days_override=holdout_days`). **Verbindlich:** Der Vergleichs-Score ist die **rohe** risikoadjustierte Performance — `compute_reward(..., universe_size=1)` **ohne** `sampled`/`global_params` ⇒ `param_pen=0` (param_pen ist ein Such-Regularisierer, kein Performance-Maß, und würde den fairen Edge-Test verzerren). **Status-Werte:** `READY_FOR_PR` (promote), `REJECTED_NO_EDGE_OVER_GLOBAL` (Holdout bestanden, aber Marge nicht erreicht), `REJECTED_ON_HOLDOUT` (symbol-Lauf besteht das Holdout-Gate nicht). `export_symbol_proposal` schreibt nur `data/optimizer/proposal_{strategy}_{symbol}.json` — **niemals** `strategies.json` (HI-3); Promotion ausschließlich per menschlichem PR. Der Holdout wird nur hier angefasst, nie im Such-Korridor (HI-5).
  **Sweep-Meta-Orchestrator (A4.6, `optimizer/sweep.py`):** `enumerate_tunable_pairs(strategies, symbols, *, tier, available_bars, config)` bildet (strategy, symbol)-Paare nach Tier (`deployable` = Tier-A-Gewinner aus `per_symbol_winners`; `refine` = P3-Platzhalter; `all` = Kreuzprodukt) und filtert sie durch Gate 1. `run_per_symbol_sweep(...)` dispatcht je Paar `optimize_symbol → confirm_per_symbol_promotion → export_symbol_proposal` und gibt die Proposal-Pfade zurück. **Der Sweep betritt NIEMALS Phase 5** (kein Live-Deploy, kein `subprocess.Popen`) und schreibt **nie** `strategies.json` — er erzeugt ausschließlich Proposal-JSONs (HI-3); Promotion bleibt ein menschlich freigegebener PR. Parallelität läuft ausschließlich über **getrennte Studies** (je eigene SQLite-Datei), nie `n_jobs>1` innerhalb einer Study (Reproduzierbarkeit, Pitfall #68). CLI: `python -m automation.optimizer.sweep --strategies all --symbols all --tier deployable --n-jobs 4` (`all` ⇒ aktive Strategien aus `strategies.json` bzw. das volle Universum). Die Bar-Zählung für Gate 1 liefert `count_available_bars` (Zeitspanne aus dem Parquet-Katalog; im CI gemockt).

- **`backtest.json` (Erweiterung)**:
  `walk_forward.holdout_days`: Anzahl der Holdout-Tage für Out-of-Sample Validierung nach der Optimierung.
  `walk_forward.data_history_days` (Issue #445): dokumentierte/erwartete Mindest-Datenhistorie pro Symbol in Tagen — **Single Source of Truth**, deckungsgleich mit `strategy_defaults.json._schema` (~15 Monate / 450 Tage). **Invariante:** `is_window_days + splits·oos_window_days + holdout_days ≤ data_history_days`; `build_trial` erzwingt dies fail-loud (sonst zehrt das Holdout-Fenster die Reserve auf und der Spätstarter-Filter kollabiert spät & still in „Keine Instrumente"). Die aktive Geometrie 180 + 4·45 + 45 = 405 Tage liegt unter 450 (plus Gate-1-Puffer 30 = 435). Die validierte 4-Fold-Geometrie wurde bewusst **nicht** verkleinert (Walk-Forward-Validierung unberührt); stattdessen wurde die früher widersprüchliche „12 Monate"-Doku auf den realen Bedarf korrigiert (`catalog_service` akkumuliert Ticks 24/7 über den initialen Backfill hinaus).

**Merge-Reihenfolge der Strategie-Parameter (niedrig → hoch):**
1. `strategy_defaults.json` (Basis, 1h-optimiert)
2. `params` in `strategies.json` (Override)
3. Vom Backtest-Runner injiziert: `instrument_id`, `bar_type`

**`instrument_overrides` — symbol-spezifische Parameter (A4.1):** `strategies.json[strategy]` darf optional ein Feld `instrument_overrides = { "<SYMBOL.ETORO>": { "<param>": <value> } }` tragen (pro Symbol nur die zu überschreibenden Keys). Auflösungs-Precedence: `strategy_defaults < strategies.json[params] < instrument_overrides[symbol] < sampled`. Die Auflösung ist **additiv und strikt rückwärtskompatibel**: ohne `instrument`-Argument (bzw. ohne das Feld) ist das Ergebnis bit-identisch zum Ist-Zustand (HI-2). Reine Resolver: `optimizer/resolve.resolve_params(..., *, instrument=None)` (Such-Basis) und `backtest_runner.resolve_strategy_params(..., *, is_manifest, instrument=None)` (Legacy/Matrix). **`is_manifest=True` ignoriert Overrides immer** (Manifest-Pfad bleibt verbatim, Pitfall #61). Promotion eines Overrides erfolgt ausschließlich per menschlich freigegebenem PR (A4.5b). **Live-/Matrix-Wiring (A4.8):** (1) Der Daily-Orchestrator-**Matrix-Backtest** (`backtest_runner`, Legacy-Pfad) löst pro (symbol, strategy) an der Dispatch-Call-Site via `resolve_strategy_params(..., instrument=symbol)` auf; (2) `momentum_ls_run._build_bots_config` instanziiert den per-Symbol-Gewinner mit override-aufgelösten Parametern. **`daily_orchestrator.py` bleibt unverändert** (HI-1) — das Wiring sitzt ausschließlich in `backtest_runner.py` und `momentum_ls_run.py`. **Gate-Scope vor Deployment-Scope:** Overrides werden erst im Deployment-Scope aktiv, NACHDEM das OOS-Gating passiert wurde — ein per-Symbol-OOS-Verlierer bleibt auch *mit* Override ausgeschlossen (Pitfall #60, `OOS-DEPLOY-REJECT`-Filter bleibt intakt). Ohne Override ist beides bit-identisch zum Ist-Zustand (HI-2).

**`global_settings.instruments` — manifest-getriebene Universum-Restriktion (A4.2):** Das Manifest darf optional `global_settings.instruments = ["<SYMBOL.ETORO>", …]` tragen. `build_trial(..., instruments=…)` schreibt das Feld nur, wenn `instruments` gesetzt ist (sonst NICHT — volles Universum, bit-identisch, HI-2). `backtest_runner` filtert das aus dem Katalog entdeckte Universum am Seam via `restrict_universe(universe, global_settings.get("instruments"))`: leere/fehlende Liste ⇒ unverändert; sonst Schnittmenge **unter Beibehaltung der Universum-Reihenfolge**. **Unbekannte Symbole** (nicht im Katalog) werden **still gedroppt** — der Backtest crasht NICHT sofort (ein leeres Restuniversum wird erst durch den bestehenden „keine Instrumente"-Guard abgefangen). Steuerung erfolgt **rein über das Manifest**, kein `--instrument`-CLI-Flag.

`trade_amount_pct` ist der neue Standard-Fallback für dynamisches Sizing basierend auf dem verfügbaren Kapital. Falls ein statischer Betrag gewünscht ist, kann `trade_amount_usd` weiterhin explizit gesetzt werden und überschreibt dann die prozentuale Zuweisung.

`tournament.json`: `eligible_requires_all = [min_trades, min_total_return]`, `eligible_requires_any = [min_sortino, min_profit_factor]`. Score = `sortino·0.4 + pf·0.3 + win_rate·0.2 − max_dd·0.1`.
*Zusatz-Feature:* Mit `tournament_overrides` in `strategies.json` können die globalen Gating-Kriterien aus `tournament.json` für spezifische Strategien individuell überschrieben werden (z. B. geringere `min_trades` für restriktivere Setups).

**`ComboTrendVwapConfig` — Konjunktions-Schalter (ISSUE-OPT-373; Kontext OPT-02/03):**
- `require_vwap_confirmation: bool = True` — wenn `False`, entfällt die VWAP-Bestätigungs-Bedingung aus dem Entry-Gate.
- `require_bb_touch: bool = True` — wenn `False`, entfällt die BB-Touch-Fenster-Bedingung (`bars_since_bb_touch <= bb_touch_window`).
- **Default `True`** → verhaltensneutral gegenüber dem Zustand vor Einführung dieser Schalter (Regressionssicherheit).
- Beide Schalter werden in `spaces.py` via `suggest_categorical(..., [True, False])` durch Optuna kategorial gesucht. Damit kann der Optimierer die Konjunktionsstruktur selbst auflockern — ohne dass `tournament.json`-Gates verändert werden (Gate-Gaming-Verbot gem. §12).
- `trend_tolerance_pct: float = 0.02` (Issue #446, Pitfall #81) — Toleranzband um die SMA für die Trend-Gates (`close > sma·(1−tol)` bullish, `close < sma·(1+tol)` bearish). Zuvor gesampelt, aber weder Config-Feld noch verdrahtet (hart 0.98/1.02); Default 0.02 ist verhaltensneutral. Wird in `spaces.py` (0.0–0.10) gesucht.

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

**Metriken** (`extract_metrics`): FIFO-Matching über `generate_fills_report()` (Fallback `generate_order_fills_report()`). Sortino nur ab n ≥ `sortino_min_trades` Round-Trips (deklarativ in `tournament.json`, Default 5, ausgeliefert mit 2 — Issue #401; vormals hartcodiert `n < 5`). Tournament-Selektion via `select_winners()`.

**Entry-Frequenz `ComboTrendVwapStrategy`:** Mit `require_vwap_confirmation=False` und/oder `require_bb_touch=False` steigt `fully_eligible_pairs` messbar, da die 4-fach-UND-Konjunktion auf 2–3 Bedingungen reduziert wird (Diagnose ISSUE-OPT-01). Die `tournament.json`-Gates (`min_trades`) bleiben unverändert (Gate-Gaming-Verbot).

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

### 🟢 Pitfall #69 — Config Fallbacks via kwargs (Issue #OPT-01)
**Symptom:** Strategie-Instanzen, die über `strategies.json` geladen werden (oder im Live-Betrieb via Allocator), stürzen beim Start ab, wenn neue Parameter (wie `require_vwap_confirmation`) nicht explizit in der Datei stehen und die Pydantic/Dataclass-Validierung fehlschlägt.
**Root Cause:** Config-Klassen wie `ComboTrendVwapConfig` definieren zwar Typen, aber wenn das zugrundeliegende Framework (Nautilus `StrategyConfig`) oder das Parsing keine Defaults für fehlende Keys liefert, kommt es zum Absturz.
**Fix (Architektur-Regel):** Die `__struct_fields__` bzw. Pydantic-ähnliche Validierung wird abgesichert, indem bei Konfigurationsklassen stets `kw_only=True` und explizite Typ-Defaults (z.B. `= True`) direkt in der Klassen-Definition verankert werden (wie in `ComboTrendVwapConfig`). Der `backtest_runner.py` füllt über `apply_strategy_defaults` fehlende Werte auf. Das ist durch `test_live_execution_defaults.py` abgedeckt.

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
* **Reward-Shaping-Guardrail (Issue #357/#358-Folge):** Das Shaping nicht-evaluierbarer Trials darf ausschließlich `oos_total_trades` (Volumen-Proxy) verwenden — niemals den Holdout, niemals die OOS-Sortino-Magnitude. Die Invariante `penalty_unevaluable_oos + unevaluable_shaping_span < evaluable_reward_floor` MUSS per Test erzwungen bleiben: kein nicht-passierender Trial darf je als `best_trial` selektierbar sein. Dies verhindert Gate-Gaming (das OOS-Gate bleibt Pflicht für `evaluable`) und Meta-Overfitting (Confirm-/Holdout-Phase bleibt unberührt). DO NOT REMOVE die Floor-Clamp oder die Ordnungsinvariante.

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
- Beim Auslösen von JSON-Execution-Events MUSS `emit_execution_event` aus `automation.log_manager` verwendet werden, wie in Pitfall #63 beschrieben. Lazy Imports (z.B. in Workern) müssen zudem explizit durch Execution-Tests abgedeckt sein.
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

### 🟢 Pitfall #66 — Silent Worker Crash Swallowing (Fail-Fast vs Resilience, Issue #355)
**Symptom:** Der Orchestrator meldet "[Phase 3] Backtest beendet (Exit-Code: 0)" und startet das Live-Deployment, obwohl im Hintergrund Worker-Prozesse aufgrund fundamentaler Python-Fehler (z.B. `ImportError`) abgestürzt sind.
**Root Cause:** In `automation/backtest_runner.py` fing eine pauschale `try/except Exception`-Resilience-Schleife während der Future-Auswertung (`future.result()`) alle Fehler stumm ab, um marktbedingte Einzelfehler abzufangen. Dadurch wurde ein systemischer Fehler maskiert, was zu einer leeren Metrik und einem unberechtigten Exit-Code 0 führte.
**Regel:** KI-Agenten dürfen niemals globale `try...except Exception:` Blöcke um die Multiprocessing-Worker oder Core-Logik legen. Marktdaten-Fehler oder fehlende Ticks dürfen ignoriert werden (Resilience). Fundamentale Code-Fehler (Imports, Syntax, Typisierung) müssen zwingend zu einem sofortigen Crash (`sys.exit(1)`) führen (Fail-Fast), um toxische Live-Deployments in Phase 5 zu verhindern. Es ist außerdem essenziell, vor dem Exit den `ProcessPoolExecutor` ordnungsgemäß herunterzufahren (`executor.shutdown(wait=False, cancel_futures=True)`), um Zombie-Prozesse zu vermeiden. Phase 5 (Live-Trading) darf nur nach einem validen Exit-Code `0` aus Phase 3 (Backtesting/Tournament) angetriggert werden.
**Betroffen:** `automation/backtest_runner.py`

### Optimizer / `backtest_runner.py` — Config-Contract

- `backtest_runner.py` liest Strategien + Parameter **ausschließlich** aus der via `--config` übergebenen Manifest-Datei. `strategies[].params` sind vollständig aufgelöst und autoritativ; **kein** erneutes Mergen aus `strategy_defaults.json`, sobald `manifest_version` gesetzt ist.
- **Self-describing Manifest (ISSUE-OPT-374):** Die `global_settings` des Manifests tragen zusätzlich `walk_forward` (`is_window_days`, `oos_window_days`, `splits == n_folds`, `holdout_days`) **und** `start_capital`. `backtest_runner.py` liest Walk-Forward-Geometrie und Start-Kapital **autoritativ aus dem Manifest** (`global_settings`); fehlen sie (Legacy/Direkt-Lauf), fällt es auf die trial-lokale `backtest.json` zurück. Der Startup-Header loggt die effektive Quelle (`Walk-Forward Quelle: manifest|backtest.json`). Damit hängt die IS/OOS-Aufteilung nicht mehr an einem Side-Channel — Manifest und Sizing/Splitting sind deckungsgleich.
- **Korridor-Geometrie vs. 12-Monats-History (ISSUE-OPT-374):** Der benötigte Korridor = `is_window_days + n_folds·oos_window_days + holdout_days` = `180 + 4·45 + 45` = **405 Tage** vor heute. `historical_fetcher --months 12` liefert ~365 Tage ⇒ am frühen Rand fehlen ~40 Tage; die frühesten Folds können datenarm sein. **Bewusst beibehalten** (keine Geometrie-Änderung ohne frischen Baseline-Lauf, Typ S): Der `span_tolerance_days`-Guard und die Trennung `oos_not_evaluable_pairs` fangen datenarme Folds deklarativ ab; der Holdout wird aus den jüngsten, dichtesten 45 Tagen geschnitten. Wer alle Folds voll abdecken will, erhöht die Beschaffungstiefe (z. B. `historical_fetcher --months 14`) statt die Fenster zu verkleinern.

### 🟢 Pitfall #64 — parse_tournament NoneType bei null aggregate_winner (Issue #357/#358)
**Symptom:** Reihenweise `AttributeError("'NoneType' object has no attribute 'get'")` in `parsing.py`; die gesamte Optuna-Study stirbt beim ersten Trial.
**Root Cause:** `data.get("aggregate_winner", {})` liefert bei vorhandenem Key mit JSON-Wert `null` ein `None` (Default greift nur bei fehlendem Key). `backtest_runner.py` schreibt `aggregate_winner: null`, wenn die optimierte Single-Strategie keinen OOS-tauglichen Gewinner produziert — der Normalfall während HPO.
**Fix/Regel:** In `parse_tournament` strikt `data.get(k) or {}` / `... or []` verwenden. Ein leeres/`null`-Aggregat MUSS als kanonischer „unevaluable"-Record (`oos_evaluated=False`, `oos_sortino=None`) zurückgegeben werden, damit `compute_reward` die Penalty vergibt. JSON-Parser im Optimizer dürfen `null`-Werte niemals als „Key fehlt" behandeln.
**Betroffen:** `automation/optimizer/parsing.py`

### 🟢 Pitfall #61 — Optimizer Manifest Contract & Catalog Path
**Symptom:** Optuna Trials crashen reihenweise mit `FileNotFoundError: Output file .../tournament_result.json not generated by backtest_runner.py`. Im Worker-Log steht oft die Warnung: `0 Ticks im Zeitraum — überspringe.`
**Root Cause:** In `trial_config.py` wurde der `catalog_path` falsch konstruiert (es zeigte z.B. fälschlicherweise auf `automation/data/nautilus` statt auf das Projekt-Root). Da das Verzeichnis leer war, fand der Subprozess-Runner keine Ticks, übersprang den Backtest gracefully und schrieb keine Ergebnis-JSON. Der aufrufende Optimizer erwartete diese Datei jedoch und crashte die gesamte Optuna-Study. Zudem war das Feld `catalog_path` doppelt (als Objekt und als String) in den `global_settings` der Manifest-Payload vorhanden.
**Fix:** Der `catalog_path` wird nun sauber relativ zum absoluten Projekt-Root aufgelöst (`(WORK.parent.parent / raw_catalog_path).resolve()`). In der `experiment_manifest.json` wird dieser Pfad strikt nur noch einmal als serialisierter String übergeben.
**Betroffen:** `automation/optimizer/trial_config.py`

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

### 🟢 Pitfall #63 — Lazy Import Crash im Worker-Prozess (Issue #354)
**Symptom:** In Worker-Prozessen (z. B. `run_single_backtest_worker` im Matrix-Backtest) kommt es vereinzelt zu `ImportError` oder `NameError` Abstürzen, wenn Edge-Cases ausgelöst werden (z. B. bei unzureichenden Datenspannen / WALK_FORWARD_INSUFFICIENT_DATA).
**Root Cause:** Ein Lazy Import (z. B. `from automation.utils import emit_json_event`) schlug fehl, weil die Methode nach einem Refactoring verlegt wurde, dieser Codepfad jedoch nie auf Worker-Ebene durch AST-Isolation-Tests abgedeckt war, da Lazy Imports erst zur Laufzeit aufgelöst werden.
**Fix/Rule:**
1. Lazy Imports in Multiprozess-Workern müssen zwingend durch gezielte Execution-Tests (End-to-End) abgedeckt sein (wie in `test_worker_lazy_imports.py`).
2. Jeder Pfad, der einen Lazy Import enthält, muss per Mocking durchlaufen und evaluiert werden. Jeder Lazy Import innerhalb einer Subprozess-Worker-Funktion MUSS zwingend durch einen Testpfad abgedeckt sein, der die Laufzeit-Exekution dieser Zeile erzwingt und die tatsächliche Ausführung des Loggings mockt und verifiziert.

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
**Diagnostic Artifact:** Wenn durch das Aufweichen von Konjunktionsschaltern (wie `require_vwap_confirmation=False`) die Frequenz getestet wird, kann dies in Low-Volume-Fenstern (Zero-Loss) dazu führen, dass der PF als `999` (bzw. gecappt auf max float) ausgegeben wird. Dies darf nicht als statistischer Outlier verworfen werden, sondern ist ein erwartetes Artefakt eines Zero-Loss-Frequenztests.
**Wichtige Architektur-Regel:** Downstream-Systeme in Evaluationen und Formatting müssen stets typensicher entwickelt werden, da Metrik-Extraktionen immer `None`-safe verarbeitet werden müssen! Die Rankings in `select_winners` nutzen nun `(m.get('metric') or 0.0)`, um die Metrik zu normalisieren.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #71 — Flat Reward Landscape (-9.75) durch hartcodiertes `n < 5` Sortino-Limit (Issue #401)
**Symptom:** Im Optimizer-Sweep enden sehr viele Trials parameter-unabhängig mit exakt `-9.75` (`Best is trial 0 with value: -9.75`). Die Reward-Landschaft ist flach (Zero-Gradient), TPE kann nicht mehr optimieren.
**Root Cause (toxische Kette über drei Dateien):** (1) `_calculate_stats` setzte `sortino = None` bei hartcodiertem `n < 5` ODER `losses_count == 0`. (2) `parse_tournament` propagiert das zu `oos_sortino = None`. (3) `compute_reward` stuft jedes `oos_sortino is None` als *unevaluable* ein → `penalty_unevaluable_oos (-10.0) + unevaluable_shaping_span (0.25) = -9.75`. Hourly-Strategien erzeugen in 30-Tage-OOS-Fenstern oft < 5 Trades oder 0 Verluste und fallen massenhaft in die Falle — obwohl sie laut `tournament.json` (`oos_min_trades: 1`) eligible wären. Das `n < 5` war zudem ein Zero-Hardcoding-Verstoß und stand im Widerspruch zu `oos_min_trades` (ISSUE-06).
**Fix (zwei Wurzeln, beide chirurgisch):**
1. **Sub-Threshold (deklarativ):** Das `n < 5`-Limit ist jetzt `tournament.json['sortino_min_trades']` (Default 5, ausgeliefert mit 2 — statistischer Boden für eine definierbare Downside-Deviation). `_calculate_stats` liest es via `_read_sortino_min_trades()` (gecached, je Worker-Subprozess konstant) oder per `min_trades_for_sortino`-kwarg (Tests, deterministisch).
2. **Zero-Loss (Reward-Fallback):** `_calculate_stats` liefert für Zero-Loss weiterhin `None` (Issue #209/#43 unangetastet — KEIN fabrizierter Sortino). Stattdessen fängt `compute_reward` den Fall ab: Ist ein Sample `oos_evaluated ∧ oos_eligible` mit `oos_sortino is None`, wird — gegated über `optimizer.json['oos_sortino_fallback'] == 'total_return'` — der geclippte `oos_total_return` als evaluable Base genutzt (statt des -9.75-Floors). `TournamentMetrics` trägt dafür das neue Feld `oos_total_return` (Default 0.0, rückwärtskompatibel).
**Invarianten:** Der Reward-WERT bleibt performance-basiert (`oos_total_return`), nie das Gate-Flag → kein Gate-Gaming (Falle 2). Die Eligibility-Bedingung erbt alle eingefrorenen Risiko-Gates inkl. Micro-Sizing (Pitfall #58). Ohne den Config-Key bleibt der exakte Legacy-Penalty-Pfad erhalten. Die Ordnungsinvarianten (`evaluable ≥ floor > unevaluable`; echter Sortino-Sieger > Zero-Loss-Fallback) sind test-gesichert (`test_issue_401_flat_reward.py`).
**Betroffen:** `automation/backtest_runner.py`, `automation/optimizer/parsing.py`, `automation/optimizer/reward.py`, `automation/config/tournament.json`, `automation/config/optimizer.json`

### 🟢 Pitfall #72 — `--n-jobs` im Sweep ignoriert / Sweep strikt sequenziell (Issue #400)
**Symptom:** `python -m automation.optimizer.sweep --strategies all --tier all --n-jobs 6` lief trotz `--n-jobs 6` strikt sequenziell — keine Parallelisierung über die Symbole, ~6× zu langsam. Der CLI-Kontrakt war still gebrochen.
**Root Cause:** `run_per_symbol_sweep` nahm `n_jobs` entgegen, benutzte es aber nie — der Funktionskörper iterierte synchron in einer `for`-Schleife.
**Fix:** `n_jobs > 1` verteilt die (strategy, symbol)-Paare jetzt über einen `ThreadPoolExecutor` (Ansatz 4: jedes Paar ist eine eigene Study mit eigener SQLite-Datei; `optimize_symbol` erzwingt intern weiterhin `n_jobs=1`, Pitfall #68 — also kein `n_jobs>1` innerhalb *einer* Study). **ThreadPool statt ProcessPool**, weil (1) der eigentliche Backtest als Subprozess läuft (`run_backtest`) und die GIL freigibt → echte Nebenläufigkeit für diesen IO-/Subprozess-gebundenen Workload, und (2) die injizierbaren `optimize_symbol`/`confirm` (HI-7) ohne Pickling testbar bleiben (lokale Mocks). `executor.map` bewahrt die Eingabereihenfolge → deterministische Proposal-Reihenfolge; `n_jobs <= 1` bleibt bit-identisch sequenziell. Kein globales `try/except Exception` im Worker — fundamentale Fehler propagieren (Fail-Fast; test-gesichert). Nebenläufigkeit deterministisch via `threading.Barrier` verifiziert (`test_issue_400_sweep_parallel.py`).
> ⚠️ **Vorbedingung der Per-Study-Parallelität (→ #411/#412, Pitfall #76/#77):** „je Paar eine eigene SQLite-Datei" ist **nur** race-frei, wenn (1) die Paarmenge **eindeutig** ist (die Enumeration dedupliziert derzeit NICHT — doppelte Paare kollabieren mehrere Worker auf dieselbe Datei, #412) und (2) der Schema-Bootstrap (`create_all`) pro Datei **einmal/serialisiert** läuft (sonst DDL-Race `table studies already exists`, #411). `test_issue_400` ist vollständig gemockt und prüft KEINEN dieser beiden realen Storage-Aspekte.
**Betroffen:** `automation/optimizer/sweep.py`

### 🟢 Pitfall #73 — Fehlende Backtest-Zeitfenster & Config-Quellen in den Logs (Issue #403)
**Symptom:** Bei Optimierungen/Sweeps zeigte das Terminal weder (1) über welchen Zeitraum (Start/Ende/Dauer) der Backtest lief, noch (2) aus welchen Dateien die Configs geladen wurden — man sah nur rohe Optuna-Trial-Ergebnisse.
**Root Cause (drei Stellen):** (1) `runner.run_backtest` ruft den Subprozess mit `capture_output=True` und verwirft bei `returncode==0` den gesamten stdout (alle Worker-Logs zu Quellen/Startdaten erreichen das Terminal nie). (2) Der Worker (`run_single_backtest_worker`) loggte nur den *ersten* Tick, nie Enddatum/Dauer. (3) Weder `sweep.main` noch der Optimizer gaben beim Start eine Übersicht der genutzten Config-Dateien/Schwellen aus.
**Fix:** (1) Worker loggt jetzt das tatsächliche Daten-Zeitfenster (Start–Ende + Spanne in Tagen) via reinem, testbarem `_format_backtest_window`. (2) Gemeinsamer Startup-Header `log_active_config()` (in `run_optimization.py`) legt Config-Verzeichnis, die vier JSON-Pfade (optimizer/tournament/strategies/backtest) und Kern-Schwellen (`n_trials`, `seed`, `oos_sortino_fallback`, `oos_min_trades`, `sortino_min_trades`, `max_drawdown`) offen — einmalig im Hauptthread, BEVOR der Lauf in die (subprocess-stummen) Trials übergeht. Aufgerufen aus `sweep.main()` (Sweep) und `run()` (globale Optimierung). Defensiv gegen fehlende/kaputte JSONs.
**Bezug:** Erfüllt die Observability-Regel (Pitfall #46, „Hidden Gates untersagt") und erweitert die Startup-Transparenz aus Issue #256 auf den Optimizer/Sweep-Layer.
**Betroffen:** `automation/backtest_runner.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/sweep.py`

### 🟢 Pitfall #74 — Optuna ExperimentalWarning-Spam (Issue #402)
**Symptom:** Beim Optimizer-Start flutet `ExperimentalWarning: Argument multivariate/group is an experimental feature` den Terminal — pro Sampler-Instanziierung, im Sweep also pro Symbol.
**Root Cause:** Der `TPESampler` wird bewusst mit `multivariate=True`/`group=True` erzeugt (`run_optimization.py`, `optimize` und `optimize_symbol`); Optuna warnt für diese (intendierten) experimentellen Argumente bei jeder Instanziierung.
**Fix:** Modul-Level `warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)` in `run_optimization.py`. **Bewusst gezielt:** nur diese Warn-Kategorie wird unterdrückt; **kein** globales `optuna.logging.set_verbosity(ERROR)`, da Optunas native Per-Trial-INFO-Logs (Reward-Werte) im Sweep (`make_symbol_objective` emittiert kein strukturiertes Event) die einzige Per-Trial-Rückmeldung sind und für die Diagnose (Issue #401) gebraucht werden — ein ERROR-Silencing würde die Observability aus Issue #403 untergraben. Test-gesichert via `importlib.reload` in einem isolierten `catch_warnings`-Kontext (pytest verwaltet `warnings.filters` pro Test).
**Betroffen:** `automation/optimizer/run_optimization.py`

### 🟢 Pitfall #75 — Per-Symbol-Sweep: Unevaluable-Floor-Kollaps [BEHOBEN — Plateau (#404–#410), Floor-Guard v3 (#413), **Defekt A: stiller Fill-`ts`-Fallback (#448, GH-#448) → Pitfall #80**]
**Symptom:** `python -m automation.optimizer.sweep --strategies all --tier all --n-jobs 6` liefert für JEDEN Trial exakt `value: -9.75`, über alle Parameter (`sma_period` 5–53, `cooldown_bars` 2–36) und über 576+ akkumulierte Trials hinweg. `Best is trial 0` bewegt sich nie. TPE hat keinen Gradienten; der Sweep ist effektiv ein teurer Zufallsgenerator.
**Abgrenzung zu Pitfall #71/#401:** NICHT der Zero-Loss/Sub-Threshold-Sortino-Fall. −9.75 ist mathematisch exakt `penalty_unevaluable_oos (−10.0) + unevaluable_shaping_span (0.25) × progress (1.0)` — der **Unevaluable-Floor mit gesättigtem Shaping**, NICHT der Evaluable-Floor (`−10.0 + 0.25 + evaluable_floor_epsilon = −9.749`). Der `max(reward, floor)`-Evaluable-Pfad kann −9.75 niemals erzeugen (sein Minimum ist −9.749). Folglich landet jeder Trial im Unevaluable-Zweig `not m.oos_evaluated or base_source is None`, und der #401-`total_return`-Fallback greift NICHT (er verlangt `oos_evaluated ∧ oos_eligible`).
**Root Cause (zwei kompoundierende Defekte):**
1. **Evaluierbarkeit an Aggregate-Winner-Status gekoppelt:** `parse_tournament` leitet `oos_evaluated` aus `data['aggregate_winner']` ab. `aggregate_winner` bleibt `None`, solange das Symbol kein VOLLSTÄNDIGER Turnier-Gewinner wird (IS-eligible ∧ OOS-eligible; `backtest_runner.py` Z. 1309 `if win_counts:` und Z. 1289 `if oos_eval.get("oos_eligible")`). Im Single-Symbol-Sweep (`universe_size==1`) spiegelt `oos_evaluated` damit den GEWINNER-Status, nicht die OOS-Evaluierbarkeit. Klärt das Paar (z. B. CPRT+SMA) das volle Gate-Stack (`eligible_requires_all` + `_evaluate_oos_eligibility`) für KEINE Parametrisierung, ist `aggregate_winner` immer `None` → `oos_evaluated` parst zu `False` → Unevaluable-Floor für jeden Trial. Die Pro-Symbol-OOS-Resultate (`r['_oos_eval']`, `r['oos_metrics']`) existieren, werden im Reward aber verworfen.
2. **Shaping-Sättigung (Anti-Flatness defekt genau im Bedarfsfall):** Das Gradienten-Shaping (ISSUE-OPT-375) nutzt `progress = max(trade_progress, activity)` mit `activity = min(1.0, is_total_trades / shaping_trade_target)`. `shaping_trade_target=50` ist für die UNIVERSE-weite IS-Trade-Summe (~70 Symbole) kalibriert. Im Per-Symbol-Pfad ist `is_total_trades` die Fold-Summe EINES Symbols (Hourly-SMA über 180d × 4 Folds ≫ 50) → `activity` sättigt sofort auf 1.0 → `shaping = 0.25 × 1.0` konstant. Das Shaping, das den Gradienten Richtung Eligibility liefern soll, ist exakt in dem Modus tot, in dem es gebraucht wird.
**Präzise Kette:** `m.oos_total_trades = 0` (aus null `aggregate_winner`) ⇒ `trade_progress = 0`; `m.is_total_trades ≥ 50` (IS-Folds handeln reichlich) ⇒ `activity = 1.0`; `progress = max(0, 1.0) = 1.0` ⇒ Reward `= −10.0 + 0.25×1.0 = −9.75` konstant für jeden Trial.
**Nicht-Ursache (Klarstellung):** Die `Subprocess crashed with return code 1/-2` + `KeyboardInterrupt`-Tracebacks am Log-Ende sind ausschließlich der manuelle `^C`-Abbruch (SIGINT unterbricht den `nautilus_trader.backtest`-Import im Subprozess). KEIN Bug — die −9.75 treten in den sauberen Trials 576–614 VOR jedem Interrupt auf.
**Behebung (Issues #404–#410, sequenziell — ALLE ERLEDIGT):**
- ✅ **#404 (P0) Per-Symbol-Telemetrie** — `make_symbol_objective` emittiert nach `compute_reward` ein `optimizer_trial_completed`-Event (`symbol`, `oos_evaluated`, `oos_eligible` [trennt IS-Drop von OOS-Drop], `oos_total_trades`, `oos_total_return`, `is_total_trades`, `is_max_trades`, `outcome`). Der Floor-Kollaps ist nun forensisch sichtbar.
- ✅ **#405 (P0) Evaluierbarkeit von Gewinner-Status entkoppeln** — `write_tournament_json` schreibt im Single-Symbol-Pfad einen `single_symbol_oos`-Block aus `r['_oos_eval']`/`r['oos_metrics']` (ungeachtet des Gewinner-Status); `parse_tournament` nutzt ihn als Fallback, wenn `aggregate_winner` fehlt. Multi-Symbol bleibt bit-identisch. **Behebt Defekt 1.**
- ✅ **#406 (P1) `shaping_trade_target` kontextsensitiv** — neuer `per_symbol_shaping_trade_target` (400); im Unevaluable-Zweig wird bei `universe_size==1`/`reward_mode=='per_symbol'` der dedizierte, nicht-saturierende Target genutzt. **Behebt Defekt 2.**
- ✅ **#407 (P1) kontinuierlicher Eligibility-Gradient** — `_gate_proximity(m, weights)` ∈ [0,1] aus `is_best_total_return`/`is_best_win_rate` gegen `shaping_return_target`/`shaping_winrate_target`; additiv und gebunden (`progress = max(progress, _gate_proximity(...))`), hart durch `unevaluable_shaping_span` gedeckelt.
- ✅ **#408 (P2) modale Drop-Reason** — pro Trial `trial.set_user_attr("rejection_reason", ...)`; `confirm._dominant_rejection` aggregiert (Counter) in `proposal["dominant_rejection"]`.
- ✅ **#409 (P2) Fail-Loud-Guard — im v3-Regime durch evaluable-basierten Guard ERSETZT (Issue #413, BEHOBEN):** Das alte Wert-Gleichheits-Prädikat `all(abs(value − (−9.75)) < 1e-6)` war im geshapeten Regime strukturell unerfüllbar (#406/#407 verteilen unevaluable Trials *absichtlich* unter −9.75 → der Guard feuerte nie, der `test_issue_409`-Test maskierte die Blindheit mit exakt −9.75). `floor_plateau_callback` nutzt jetzt PRIMÄR `oos_evaluated` (Per-Trial-User-Attr aus `make_symbol_objective`): warnt, wenn KEIN abgeschlossener Trial je evaluable war — verifiziert mit Sub-Floor-Werten (−9.85…−9.93), `test_issue_413_floor_guard_v3.py`. Der Legacy-Wert-Guard bleibt als Fallback für alte Studies / den globalen `make_objective`-Pfad (kein False-Positive). **Das ist Defekt B aus #413; Defekt A bleibt offen (s. u.).**
- ✅ **#410 (P3) Reward-Semantik-Versionierung** — `reward_semantics_version: 3`; `_check_reward_semantics_version` stempelt frische Studies und warnt bei Versions-Diskrepanz (alte Floor-Trials nicht vergleichbar ⇒ DBs löschen).

**Fehlerquelle behoben:** Die zwei kompoundierenden Defekte — (1) **Gewinner-Status vs. Evaluierbarkeit** (Evaluierbarkeit war an `aggregate_winner` gekoppelt) und (2) **Shaping-Sättigung** (universe-skalierter Target saturierte sofort) — sind entkoppelt bzw. kontextsensitiv gemacht. TPE hat im Per-Symbol-Sweep wieder einen Gradienten.
**Empirischer Befund 2026-06-24 (Reklassifizierung auf 🟡 TEILWEISE, → #413):** Ein erneuter Lauf (`--tier all --n-jobs 6`) zeigt: das **konstante** −9.75-Plateau ist weg — die Werte variieren jetzt (−9.85…−9.93), d. h. #406/#407 (Shaping-Desaturation) **wirken** und TPE hat einen Gradienten. ABER: −9.85…−9.93 liegen ausnahmslos **im Unevaluable-Band** ([−10.0, −9.75), strikt unter der Decke −9.75; der Evaluable-Boden ist −9.749). ⇒ **0 evaluable Trials, 0 promotbare Vektoren** für `SmaCrossoverStrategy`. „Gradient repariert ≠ Evaluierbarkeit erreicht": der Gradient wird von IS-Aktivität/Gate-Proximity getrieben (ein Proxy), nie von OOS-Performance — denn kein Trial wird je evaluable. Die Wurzel (OOS-0-Trades vs. #405-Fallback greift nicht vs. Gate-1-zu-großzügig) ist **noch nicht diagnostizierbar**, weil die #404-Telemetrie aktuell verworfen wird (Sweep-Entrypoint ohne Logging-Handler, **#414**) — #414 ist Vorbedingung der Wurzeldiagnose. Status: TPE-Gradient hergestellt, Per-Symbol-Tuning praktisch weiterhin No-Op.
**Update 2026-06-24 (Implementierung #413/#414):** Defekt B ist BEHOBEN — `floor_plateau_callback` ist jetzt evaluable-basiert (`oos_evaluated`) und feuert im realen Sub-Floor-Band laut (test-gesichert), und der Sweep-Entrypoint initialisiert Logging (#414), sodass die `optimizer_trial_completed`-Telemetrie (`oos_evaluated`/`oos_eligible`/`oos_total_trades`/`is_total_trades`/`backtest_ms` + neue `optimizer_study_completed`/`sweep_completed`-Summaries) jetzt in `logs/optimizer_*.log` landet.

**Update 2026-06-25 (Defekt A BEHOBEN — GH-#448, Vorschlag #424):** Die strukturelle Wurzel A1 (`OOS=0-Trades`) ist als **stiller Fill-`ts`-Fallback** identifiziert und behoben → eigene **Pitfall #80**. Kern: `extract_metrics` las den Fill-Exit-Zeitstempel via `getattr(f, 'ts_event', getattr(f, 'ts_init', 0))`. (a) Der `0`-Default klassifizierte bei fehlendem Feld JEDEN Round-Trip als In-Sample (`ts=0 < start_ns+is_window`) ⇒ `OOS=0` über alle Symbole/Strategien; (b) der Fallback-Report `generate_order_fills_report()` trägt **kein** `ts_event`, sondern `ts_last` — wurde also gar nicht erst korrekt gelesen. Fix: `_fill_ts_ns(f)` liest robust `ts_event → ts_last → ts_init`, konvertiert `pd.Timestamp.value` und **wirft fail-loud**, statt still auf 0 zu defaulten; zusätzlich eine Plausibilitäts-Assertion (`fill_ts_max < start_ns` ∨ `fill_ts_min ≤ 0` ⇒ `ValueError`). Der Reproduktionsfall (142 uniforme Exits über `[2025-05-15, 2026-05-10]`) liefert jetzt deterministisch `IS=72 / OOS=70` (`test_issue_448_oos_split.py`). Die Telemetrie-Lücke A (Forensik-Blindheit) ist mit dem `data_window`-Block (#444, inkl. `fill_ts_min/max`) geschlossen, sodass eine künftige Domänen-Divergenz ohne Ad-hoc-Logzeile sichtbar wird. **Verbleibende Restklassen A2/A3** (single_symbol_oos-Fallback / Gate-1-Kalibrierung) sind durch den Fail-Loud-Guard (#413) + die neue Plausibilitäts-Assertion nun **laut** statt still — ein erneuter struktureller Kollaps fällt sofort auf, statt 100 Trials lang Rauschen zu optimieren. Pitfall #75 ist damit auf 🟢 gehoben: alle bekannten Kollaps-Mechanismen sind code-seitig geschlossen oder fail-loud.
**Invariante (nach Fix, dauerhaft einzuhalten):**
- **Per-Symbol-Evaluierbarkeit ≠ Gewinner-Status:** Im Per-Symbol-Reward gilt `oos_evaluated` ⇔ „das Symbol hat im OOS-Fenster evaluierbare Trades erzeugt", strikt entkoppelt von „das Symbol hat das volle Tournament-Gate bestanden / ist Aggregat-Gewinner".
- **Anti-Gate-Gaming-Invariante bleibt erhalten (Unevaluable < Evaluable-Floor):** Der Reward-WERT bleibt rein performance-basiert (nie ein Gate-Flag) → kein Gate-Gaming (Falle 2). Das gesamte Unevaluable-Shaping (Trade-Aktivität, IS-Aktivität, Gate-Proximity) ist auf `[0,1]·unevaluable_shaping_span` gebunden, sodass jeder unevaluable Trial strikt unter dem Evaluable-Floor (`penalty_unevaluable_oos + unevaluable_shaping_span + evaluable_floor_epsilon`) bleibt. Evaluierbare Trials werden IMMER besser bewertet als unevaluable.
**Betroffen (Fix):** `automation/optimizer/reward.py`, `automation/optimizer/parsing.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/confirm.py`, `automation/backtest_runner.py`, `automation/config/optimizer.json`; Tests `test_issue_404_symbol_telemetry.py`, `test_issue_405_single_symbol_evaluability.py`, `test_issue_406_per_symbol_shaping.py`, `test_issue_407_gate_proximity.py`, `test_issue_408_rejection_surfacing.py`, `test_issue_409_floor_guard.py`, `test_issue_410_reward_versioning.py`

### 🟢 Pitfall #76 — Optuna/SQLite `create_all`-DDL-Race bei parallelen Per-Symbol-Studies [BEHOBEN: Issues #411/#416]
**Symptom:** `--n-jobs>1` crasht nach ~500 Trials mit `sqlite3.OperationalError: table studies already exists` im `optimize_symbol → optuna.create_study`-Pfad (sauberer Stacktrace, KEIN `^C`).
**Root Cause:** `RDBStorage.__init__` ruft `models.BaseModel.metadata.create_all(self.engine)`. `create_all(checkfirst=True)` ist Check-then-Create → zwei Worker, die dieselbe **frische** Datei quasi-gleichzeitig öffnen, setzen beide `CREATE TABLE studies` ab; der zweite scheitert. **`load_if_exists=True` schützt NICHT** — es greift erst auf Study-Row-Ebene, NACH dem Schema-Bootstrap. Tritt nur auf, wenn zwei Worker dieselbe Datei (= denselben `study_name`) anfassen → Auslöser ist die Paar-Duplikation #77.
**Fix/Regel:** (1) prozessweiter `_study_lock` + `_create_study_with_retry` serialisieren den `create_study`-Aufruf und retrien GENAU EINMAL auf die exakte Race-Signatur (`"already exists"` in `sqlite3`/`sqlalchemy` `OperationalError`) — **kein** blindes `except Exception` (Fail-Fast, Pitfall #66; jeder andere Fehler propagiert). (2) `_preinit_study_storage(study_name)` erzwingt den Schema-Bootstrap einmal/seriell im Hauptthread VOR dem `executor.map` (jeder Worker trifft danach „exists"). `optimize_symbol` nutzt `_create_study_with_retry`; `run_per_symbol_sweep` ruft `_preinit_study_storage` pro eindeutigem `study_name` (nur im echten Storage-Pfad).
**Betroffen:** `automation/optimizer/run_optimization.py` (`_study_lock`, `_create_study_with_retry`, `_preinit_study_storage`, `optimize_symbol`), `automation/optimizer/sweep.py`; Test `automation/tests/test_issue_411_storage_ddl_race.py` (≥8 Threads gegen EINE frische `tmp_path`-DB, echtes SQLite ⇒ kein Crash, genau eine Study; Retry-/Fail-Fast-/Idempotenz-Fälle).

### 🟢 Pitfall #77 — Doppelte `(strategy, symbol)`-Paare kollabieren Worker auf eine Study [BEHOBEN: Issue #412/#415]
**Symptom:** Im Startup `1× "A new study created" + N× "Using an existing study"` für **dasselbe** Symbol; ein Trial-Zähler übersteigt `n_trials` massiv (Log 2026-06-24: `study_…_WDAY_ETORO` erreicht Trial 499 bei `n_trials=100`).
**Root Cause:** `load_symbol_universe` (Listen-Quelle) und `enumerate_tunable_pairs` deduplizieren NICHT; doppelte Universe-Einträge erzeugen doppelte Paare; `executor.map` verteilt sie 1:1 → mehrere Worker gegen **eine** per-Study-Datei. Folgen: (a) Reproduzierbarkeit zerstört (effektiv `n_jobs=N` auf Study-Ebene trotz internem `n_jobs=1`, bricht Pitfall #68); (b) löst den DDL-Crash #76 aus; (c) Parallelität „verklumpt" auf ein Symbol, der Lauf verbrennt redundante Trials und stürzt ab, bevor andere Strategien drankommen.
**Fix/Regel:** order-preserving Dedup in `load_symbol_universe` (`dict.fromkeys`) UND `enumerate_tunable_pairs` (`seen`-Set über `(strategy, symbol)`); harte Fail-Fast-Assertion (`collections.Counter`) in `run_per_symbol_sweep`, die mit `ValueError` abbricht, falls zwei Paare denselben `study_name` ergäben (Pitfall #66). Daten-Hygiene: `data/universe/momentum_ls.json` auf Duplikate prüfen (gitignored, außerhalb des Repos). **Eindeutigkeit ist eine harte Vorbedingung der Per-Study-Parallelität, kein Implementierungsdetail.**
**Betroffen:** `automation/optimizer/sweep.py` (`load_symbol_universe`, `enumerate_tunable_pairs`, `run_per_symbol_sweep`); Test `automation/tests/test_issue_412_pair_dedup.py` (Universe-Dedup, Paar-Eindeutigkeit, Reihenfolge-Erhalt, Fail-Fast bei Duplikat).

### 🟢 Pitfall #78 — Sweep-Entrypoint ohne Logging-Init ⇒ `[JSON_EVENT]`-Telemetrie verworfen [BEHOBEN: Issue #414]
**Symptom:** `python -m automation.optimizer.sweep` produziert KEIN `[JSON_EVENT] optimizer_trial_completed` — obwohl `make_symbol_objective` es pro Trial emittiert (#404). Der Operator sieht nur Optunas native Trial-Zeilen, also weder `oos_evaluated`/`oos_eligible` noch `oos_total_trades`/`is_total_trades`.
**Root Cause:** `sweep.py` konfiguriert kein Logging (kein `basicConfig`/`setup_bot_logging`). `getLogger("optimizer")` hat im Standalone-Pfad keinen Handler; `emit_execution_event` loggt auf INFO → Pythons `lastResort` gibt nur WARNING+ aus ⇒ alle INFO-Events fallen weg. (Im Orchestrator-Pfad rettet `daily_orchestrator.basicConfig` die Events; der direkte Sweep-Aufruf nie.)
**Fix/Regel:** `setup_bot_logging("optimizer")` als ERSTE Anweisung in `sweep.main()` (File-Handler DEBUG → rotierende JSONL, Stream INFO, `propagate=False`; kollidiert nicht mit Optunas Logger — KEIN `set_verbosity`, Pitfall #74). Per-Trial-Events in die Datei, Konsole für Header/WARNINGs/Timing-Summaries. **Vorbedingung für die Wurzeldiagnose von #413.**
**Betroffen:** `automation/optimizer/sweep.py` (`main`); Test `automation/tests/test_issue_414_sweep_logging.py` (Handler-Assertion, `[JSON_EVENT]`-Datei-Roundtrip, `main`-Init-Aufruf, Optuna-Logger unangetastet).

### 🟢 Pitfall #79 — Fehlende Backtest-Zeitdauer & verschluckter Subprozess-Output [BEHOBEN: Issues #415/#416]
**Symptom:** Die Logs weisen KEINE Lauf-Dauer aus (weder Per-Backtest noch Per-Study/Per-Sweep); zudem verwirft `run_backtest` im Erfolgsfall stdout/stderr → Per-Trial-Fehleranalyse unmöglich.
**Root Cause:** `run_backtest` umschließt `subprocess.run(... capture_output=True)` ohne `perf_counter`; das `optimizer_trial_completed`-Event trägt kein `backtest_ms`; stdout/stderr werden nur im Crash-Fall ausgegeben. (Pitfall #73/#403 führte nur das **Daten**-Zeitfenster ein, nicht die **Lauf**-Dauer.)
**Fix/Regel:** Wall-Clock in `run_backtest` (beide Modi; via optionalem `timings`-Out-Param → **keine** Signatur-Änderung, Pitfall #33). `backtest_ms` ins Per-Trial-Event (im Objective per `perf_counter` gemessen, damit alle bestehenden `run_backtest`-Mocks weiter funktionieren). Neue Events `optimizer_study_completed` (n_trials, evaluable_trials, best_value, backtest_ms_total/median, wallclock_s) und `sweep_completed` (wallclock_s) + menschenlesbare Konsolen-Schlusszeile. Subprozess-stdout/stderr pro Trial via `_persist_subprocess_logs` nach `trial_dir/logs/` (auch im Erfolgsfall); Daten-Fenster (`data_window_*`) + `rejection_reason` zusätzlich ins Per-Trial-Event (aus der `tournament_result.json`, None-safe — der menschenlesbare 📅-Fensterstring steht ohnehin in `backtest_stdout.log`).
**Betroffen:** `automation/optimizer/runner.py` (`run_backtest`, `_record_timing`, `_persist_subprocess_logs`), `automation/optimizer/run_optimization.py` (`make_symbol_objective`, `make_objective`, `_emit_study_summary`, `optimize_symbol`), `automation/optimizer/sweep.py` (`run_per_symbol_sweep`), `automation/optimizer/parsing.py` (`data_window_*`); Tests `automation/tests/test_issue_415_backtest_timing.py`, `automation/tests/test_issue_416_subprocess_logs.py`.

### 🟢 Pitfall #80 — Stiller Fill-`ts`-Fallback ⇒ struktureller OOS=0-Kollaps [BEHOBEN: GH-#448 (Vorschlag #424)]
**Symptom:** In **jedem** Trial, für **jedes** Symbol und über **beide** geprüften Strategien (`SmaCrossoverStrategy`, `FlashCrashReversalStrategy`) zeigt die Per-Trial-Telemetrie `oos_evaluated: false`, `oos_total_trades: 0`, während `is_total_trades` 100–160 beträgt — der In-Sample-Backtest läuft, die Out-of-Sample-Auswertung liefert aber **null** Round-Trips. Jeder Trial fällt auf den Unevaluable-Floor (−9.85…−9.93), Optuna optimiert nur Rauschen. Dies ist **Defekt A** aus Pitfall #75.
**Root Cause:** `extract_metrics` las den Fill-Exit-Zeitstempel via `getattr(f, 'ts_event', getattr(f, 'ts_init', 0))` (an **fünf** Stellen: Sortier-Key + je zwei Exit-/Entry-Reads der BUY-/SELL-Seite). Zwei kompoundierende Defekte: **(a)** der stille `0`-Default — fehlt das Feld, ist `ts=0`, und wegen `0 < start_ns + is_window` landet **jeder** Round-Trip im In-Sample-Zweig ⇒ `OOS=0` strikt über alle Symbole; **(b)** der Fallback-Report `generate_order_fills_report()` (genutzt, wenn `generate_fills_report()` leer ist) trägt **kein** `ts_event`, sondern `ts_last` — das `getattr` traf also gar nicht den richtigen Schlüssel und fiel auf `ts_init` (Order-Init statt Fill-Zeit) oder den `0`-Default zurück. Statisch sagt jede Stelle der Kette `OOS ≈ 70` voraus (142 uniforme Exits → IS=72/OOS=70); der Defekt war die einzige nicht rein statisch verifizierbare Kante: die Laufzeit-Domäne der Fill-`ts`.
**Fix/Regel:** `_fill_ts_ns(f)` ist die EINZIGE Lesestelle: liest robust `ts_event → ts_last → ts_init`, konvertiert `pd.Timestamp.value` → ns, überspringt `NaN`/`NaT`, und **wirft fail-loud** (`ValueError`), wenn ALLE drei Felder fehlen — **niemals** still auf `0`. Zusätzlich eine Plausibilitäts-Assertion nach der FIFO-Extraktion: `fill_ts_max < start_ns` (alle Fills vor Fensterbeginn) ∨ `fill_ts_min ≤ 0` ⇒ `ValueError` (für valide Daten unmöglich, fängt jede künftige Domänen-Divergenz). Die beobachtete Fill-ts-Spanne wird in den `data_window`-Block (#444, `fill_ts_min/max`) gehoben — eine erneute Domänen-Divergenz ist damit ohne Ad-hoc-Diagnose telemetrie-sichtbar. **Regel: Fill-Zeitstempel NIE per stillem `getattr(..., 0)` lesen — immer `_fill_ts_ns` (fail-loud).**
**Invariante:** Im Walk-Forward-Modus MÜSSEN alle Fill-`ts` in der absoluten Epoch-ns-Domäne von `[start_ns, end_ns]` liegen. Ein `ts=0`/Out-of-Window-Fill ist ein Bug und MUSS laut scheitern, nicht still als IS klassifiziert werden.
**Betroffen:** `automation/backtest_runner.py` (`_fill_ts_ns`, `extract_metrics` — Sortier-Key + 4 ts-Reads, Plausibilitäts-Assertion, `_fill_ts_min/max`-Export; `write_tournament_json`/`run_single_backtest_worker` für `data_window`), `automation/optimizer/parsing.py` (`fill_ts_min/max`); Tests `automation/tests/test_issue_448_oos_split.py` (Fail-Loud-Kontrakt, OOS>0, Reproduktion IS=72/OOS=70), `automation/tests/test_issue_444_data_window.py`.

### 🟢 Pitfall #81 — Sampling↔Config-Bindung: gesampelte Phantom-Parameter werden still verworfen [BEHOBEN: GH-#446 (Vorschlag #426)]
**Symptom:** Bei `FlashCrashReversalStrategy` bleibt `is_total_trades` über Trials mit stark unterschiedlichen gesampelten Parametern nahezu **konstant** — der Optimizer „tunt", aber die Backtest-Ergebnisse bewegen sich nicht.
**Root Cause:** Mehrere in `spaces.py` gesampelte Schlüssel existierten im zugehörigen `*Config`-Struct **nicht** (`frozen=True, kw_only=True` msgspec). Der Worker filtert defensiv (`dropped = {k for k in params if k not in valid_keys}`) und verwirft sie **still** (`_dropped_params`); die Strategie nutzt ihren Default, Optuna optimiert einen wirkungslosen Parameter. Drei Fehlerklassen: **(1) Namens-Mismatch** (`bb_std`→`bb_std_dev`, `vwap_window`→`vwap_period`); **(2) fehlendes Config-Feld** für einen real genutzten Parameter (`trend_tolerance_pct` in Combo war hart 0.98/1.02 kodiert); **(3) totes Sampling** ohne Strategie-Nutzung (`vol_surge_multiplier`, `vol_window`, `vol_threshold`, `rsi_period`/`rsi_extreme` in VwapExhaustion).
**Architektur-Realität (für die Entscheide entscheidend):** Synthetische 1h-Bars tragen **konstant `volume=1.0`** (`hourly_strategy_base.py:174`). Jeder volumenbasierte Filter ist daher tot — solche Phantom-Keys werden **entfernt, nicht verdrahtet** (gleiche Entscheidung wie historisch in `vwap_exhaustion`/`dynamic_breakout`). VwapExhaustion ist bewusst „Price-Deviation only" (kein RSI). `trend_tolerance_pct` hingegen WIRD verdrahtet (echtes Toleranzband, Default 0.02 = verhaltensneutral).
**Fix/Regel:** Single Source of Truth = `*Config`-Felder. **Ein Parameter darf nur gesampelt werden, wenn er als Config-Feld existiert.** Renames in `spaces.py`; Phantom-Volumen-/RSI-Keys entfernt; `trend_tolerance_pct` als Feld ergänzt + im Trend-Gate verdrahtet; FlashCrash sampelt jetzt die echten Entry-Felder `bb_period`/`bb_std_dev`. Die zentrale Regressions-Assertion `test_search_space_binding.py::test_sampled_params_bind_to_config` prüft für JEDE aktive Strategie `set(sample_params(s)) ⊆ set(Config.__struct_fields__)` und fängt jeden künftigen Drift fail-fast ab. Die vollständige Soll-Vorgabe steht in `automation/OPTIMIZER_PARAMETER_REFERENZ.md`.
**Betroffen:** `automation/optimizer/spaces.py`, `automation/strategies/tesla_combo_strategy.py` (`trend_tolerance_pct`-Feld + Verdrahtung); Tests `automation/tests/test_search_space_binding.py`, `automation/tests/test_combo_conjunction_switches.py` (veraltete „dropped"-Assertion korrigiert); Doku `automation/OPTIMIZER_PARAMETER_REFERENZ.md`.

### 🟢 Pitfall #82 — OOS-Abdeckungs-Blindstelle: Katalog erreicht das OOS-Fenster nicht ⇒ struktureller OOS=0 [BEHOBEN: GH-#455 (P0)]
**Symptom:** Nach dem `_fill_ts_ns`-Fix (#448/#80) läuft der In-Sample-Backtest sauber (100+ Round-Trips), trotzdem ist `oos_evaluated: false` / `oos_total_trades: 0` über **alle** Strategien für ein Symbol (z. B. `TSLA.ETORO`); Reward klebt am Unevaluable-Floor (−9.90…−9.93), „Floor-Plateau erkannt" nach 16 Trials, `Best is trial 0` bewegt sich nie. Sechs strukturell verschiedene Strategien können nicht zufällig alle exakt am Tag 180 aufhören zu handeln.
**Root Cause:** **Daten-/telemetrieseitig, NICHT logisch** — IS/OOS-Split (`extract_metrics`) und `check_data_span` sind beide korrekt. Die Walk-Forward-Geometrie verankert die früheste OOS-Sub-Fenster-Grenze (fold=0) bei `start_ns + is_window_ns`. Reichen die Katalogdaten in der zweiten Fensterhälfte nur als dünner/staler Endpunkt (typisch nach `catalog_service`-Ausfall mit partiellem Backfill, der nur Randticks hinterlässt), liegen ALLE realen Fills in `[start, start+is_window]` ⇒ jedes OOS-Sub-Fenster erhält **null** Fills ⇒ `oos_total_trades=0` strukturell, parameter-unabhängig. **Zwei bestehende Guards verfehlen das:** (1) die #448-Plausibilitäts-Assertion prüft nur die UNTERE Kante (`fill_ts_max < start_ns`), nicht die OOS-Abdeckungs-Kante (`fill_ts_max < start_ns + is_window_ns`); (2) `check_data_span` validiert nur die Spannweite (`last − first ≥ required`), nicht Dichte/Aktualität in H2. Entscheidend: die einzige diagnostisch relevante Zahl — `fill_ts_max` gegen die OOS-Grenze — wurde vor der Operator-Konsole verworfen.
**Fix/Regel:** (1) **Telemetrie (die eigentliche Entblockung):** `extract_metrics` berechnet `oos_window_start_ns = start_ns + is_window_ns` und `oos_covered = (fill_ts_max ≥ oos_window_start_ns)`; durchgereicht über `run_single_backtest_worker` → `write_tournament_json` (`data_window`: `oos_window_start_ns`, `oos_window_start` ISO, `oos_covered`, `oos_coverage_gap_days`) → `parse_tournament`/`TournamentMetrics` → BEIDE `optimizer_trial_completed`-Events. Bei `oos_covered=false` ist der Floor-Grund auf einen Blick **datenseitig**. (2) **WARN statt `raise`** in `extract_metrics`: die Abdeckungs-Verletzung wird als sichtbare Logzeile gemeldet — ein `raise` würde über die NULL-Rückgabe genau diese Telemetrie verschlucken. (3) **Gate-1-Preflight:** reine Funktion `gate.data_reaches_oos_window(newest_ns, oos_window_start_ns)` + `sweep.latest_ts_by_symbol` (jüngster `ts_event` je Symbol aus Parquet-Row-Group-Statistiken); `enumerate_tunable_pairs` überspringt ein Symbol, dessen jüngster Tick die früheste OOS-Grenze nicht erreicht (`OOS_WINDOW_UNREACHABLE` + WARN), VOR dem Sweep statt 100 nutzlose Trials. **Vollständig fail-open:** fehlt Tick-Telemetrie oder Geometrie, bleibt das Preflight aus (bit-identisch).
**Invariante:** Die OOS-Abdeckungs-Telemetrie (`oos_covered`) ist reine Diagnose und ändert **NIE** eine Reward-/Promotion-Entscheidung. Das Preflight ist fail-open (kein stiller Skip bei fehlender Telemetrie). Bei `oos_covered=false` ist die Ursache der H2-Katalog (Backfill 2025-11 → heute nötig), nicht die Parametrisierung.
**Betroffen:** `automation/backtest_runner.py` (`extract_metrics`, `run_single_backtest_worker`, `write_tournament_json`), `automation/optimizer/parsing.py`, `automation/optimizer/gate.py` (`data_reaches_oos_window`), `automation/optimizer/sweep.py` (`latest_ts_by_symbol`, `compute_oos_window_start_ns`, Preflight), `automation/optimizer/run_optimization.py` (beide Trial-Events); Tests `automation/tests/test_issue_449_oos_coverage.py`.

### 🟢 Pitfall #83 — Floor-Plateau-Guard warnt nur, stoppt die aussichtslose Study nicht ⇒ verschwendete Compute [BEHOBEN: GH-#456 (P1)]
**Symptom:** Nach „🚨 Floor-Plateau erkannt" (alle Trials unevaluable, #75/#82-Klasse) läuft die Study dennoch bis `n_trials=100` weiter. Der TPE-Sampler hat keinen Gradienten (jeder Trial unevaluable) und erzeugt nur teures Rauschen; pro Symbol verfallen ~84 Trials nutzlos (~30 min pro Floor-Symbol über einen `--symbols all`-Sweep).
**Root Cause:** `floor_plateau_callback` (#409, evaluable-basiert seit #413) war **reine Observability**: setzt das User-Attr `floor_plateau_warned` und loggt eine WARN-Zeile, ruft aber **nie** `study.stop()` — obwohl bereits nach `n_startup_trials` feststeht, dass nichts Promotbares mehr kommt.
**Fix/Regel:** Opt-in-Parameter `stop_on_plateau: bool = False`. Bei `True` ruft der Guard in **beiden** Plateau-Zweigen (evaluable-basiert UND Legacy-Wert) `study.stop()` — crash-sicher über `getattr(study, "stop", None)` + `try/except` (eine Study außerhalb eines `optimize()`-Kontexts crasht nicht). Die **Produktion** bindet `stop_on_plateau=True` in beiden `partial(floor_plateau_callback, …)`-Stellen (`optimize`, `optimize_symbol`); die **Default-Signatur bleibt `False`**, sodass alle Bestands-Tests mit Fake-Study (ohne `.stop()`) unverändert grün bleiben.
**Invariante:** Die **Observability-Invariante bleibt erhalten** — der Guard ändert weiterhin NIE eine Reward- oder Promotion-Entscheidung; er beendet lediglich eine bereits als aussichtslos erkannte Suche früher. Ist ≥1 Trial evaluable, wird NIE gestoppt.
**Betroffen:** `automation/optimizer/run_optimization.py` (`floor_plateau_callback`-Signatur + `_stop_study_safely` + zwei `study.stop()`-Zweige; zwei `partial`-Bindungen); Tests `automation/tests/test_issue_449_oos_coverage.py` (`test_plateau_*`).

### 🟢 Pitfall #84 — Walk-Forward-Fenster-Arithmetik dupliziert ⇒ Divergenz-Footgun [BEHOBEN: GH-#457 (P2)]
**Symptom/Risiko:** Latentes Risiko, **kein** akutes Fehlverhalten. Die Fenster-Berechnung (`end = Mitternacht(now)`; Sonntag → −1 Tag; `− holdout_days`; `start = end − (is_window + splits·oos)`) lebte ausschließlich inline in `build_trial`. Das #455-OOS-Preflight braucht **exakt dieselbe** OOS-Grenze (`start + is_window`); ein Nachbau erzeugte genau die Divergenz-Klasse zwischen „`start_ns` fürs Daten-Laden" und „`start_ns` für den Split", die der Wurzel der OOS=0-Bug-Familie entspricht (#80/#82).
**Fix/Regel:** Neue reine Funktion `trial_config.compute_walk_forward_window(*, now, holdout_days, is_window_days, oos_window_days, n_folds) -> (start, end)` als **EINZIGE** Quelle der Fenster-Arithmetik. `build_trial` delegiert (bit-identisch); das Sweep-Preflight (`sweep.compute_oos_window_start_ns`) bezieht die OOS-Grenze aus derselben Funktion. **Regel: Fenster-Grenzen NIE inline nachbauen — immer `compute_walk_forward_window`.** Verifiziert gegen das real beobachtete Fenster (`now=2026-06-25` ⇒ `start=2025-05-16`, `end=2026-05-11`), inkl. Sonntag-Rollback.
**Invariante:** Es existiert genau EINE Implementierung der Walk-Forward-Fenster-Grenzen. Jede neue Stelle, die die Grenze braucht, MUSS `compute_walk_forward_window` aufrufen statt die Arithmetik zu kopieren.
**Betroffen:** `automation/optimizer/trial_config.py` (`compute_walk_forward_window`; `build_trial` delegiert), `automation/optimizer/sweep.py` (`compute_oos_window_start_ns`); Tests `automation/tests/test_issue_449_oos_coverage.py` (`test_window_*`, `test_build_trial_uses_shared_window`).

### 🟢 Pitfall #85 — Stiller Holdout-Kollaps bei stalem Katalog / Wanduhr-Anker (Issue #460)

**Symptom:** Alle Holdout-Proposals scheitern mit `status="REJECTED_ON_HOLDOUT"` und `is_rejection_detail="REJECT_OOS_INACTIVE"`, weil der Katalog hinter der Wanduhr (`now`) zurückbleibt und der Holdout-Slice somit in einer "Zukunfts-Datenlücke" evaluiert wird (0 Trades).
**Root Cause:** `compute_walk_forward_window` ankert strikt an der echten Wanduhr (`now`), ignoriert aber den tatsächlichen Katalog-Endstand. Dadurch gleitet das Sweep- und Holdout-Fenster in den leeren Raum, wenn der Katalog stale ist.
**Fix:** Anchor-Clamp in `compute_walk_forward_window` implementiert. Falls `catalog_newest_ns` übergeben wird, wird das Fenster-`end` vor Subtraktion und Wochenend-Rollback an den jüngsten Tick geclampt. Zusätzlich schützt ein `REJECT_HOLDOUT_UNREACHABLE` Short-Circuit in Gate 3 (`confirm_per_symbol_promotion`) vor sinnloser Evaluierung im leeren Raum, wenn die OOS-Holdout-Startgrenze komplett in der Zukunft des Katalogs liegt.

### Pitfall #53: Optimizer-Storage — SQLite-Default, Postgres-Opt-in (A4.7)
**Symptom:** Unklarheit über Datenhaltung und fehlende PR-Promotion.
**Ursache/Lösung:** **SQLite für Single-Node** ist der strikte Default (per-Study-Datei `{WORK}/sweep/{study}.db`). **Postgres (o. ä.) nur für explizite parallele Sweeps** über mehrere Maschinen gegen *eine* Study — reines Opt-In via `optimizer.json['storage_url']` oder ENV `ETORO_OPTUNA_STORAGE` (ENV hat Vorrang), aufgelöst durch `run_optimization.resolve_storage`. Diese Aufweichung der „ausschließlich SQLite"-Leitplanke ist **bewusst, dokumentiert und begrenzt**: bei non-SQLite-URL ist Determinismus pro Study nur bei `n_jobs=1` garantiert (Warnung wird geloggt; Pitfall #68 bleibt für SQLite gültig). Eine ENV-URL wird verbatim genutzt (Fail-Fast bei ungültiger URI statt stillem SQLite-Fallback). Der Optimizer verändert `tournament.json` NIE und startet NIE Phase 5; Promotion nur per PR.
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
- Lazy Imports in isolierten Prozessen müssen per Execution-Tests gedeckt sein (vgl. Pitfall #63 und `test_worker_lazy_imports.py`).
- `os._exit(1)`-Konvention für WebSocket-Fehler beibehalten.
- Subprocess-stdout/stderr immer in eine Log-Datei umleiten.
- Vor jedem Commit: Pre-Flight-Checks (Abschnitt 13) und `pytest` laufen lassen.
- **Bugfixes chirurgisch halten:** Pitfalls #14, #20, #21 hängen zusammen (alle `size_precision`/`_compute_quantity`), sollten aber in nachvollziehbaren, einzeln testbaren Commits behoben werden. Bestehende CFD-Parquet-Metadaten nach einem #23-Fix regenerieren.
- **Test-Gates bei extract_metrics/FIFO:** Nach jeder Modifikation an `extract_metrics`, der FIFO-Matching-Schleife, der IS/OOS-Aufteilungsschleife oder der reportbasierten Datenextraktion MUSS `pytest automation/tests/test_backtest_runner.py -v` lokal fehlerfrei durchlaufen.
- **Tupel-Arity Koppelung:** Erzeugung (`pnls_with_ts.append(...)`) und Konsum (`for ... in pnls_with_ts`) der Trade-Tupel sind als gekoppeltes Paar zu behandeln: Ändert sich die Arity der erzeugten Tupel, MUSS die Entpackung im selben Commit angepasst werden.
- **total_trades Guards:** Assertions auf `total_trades > 0` in den Test-Suites dürfen unter keinen Umständen gelockert oder entfernt werden.
- **Observability Gate-Regel:** Jeder PR, der neue Gating-Parameter (z. B. in `tournament.json` oder `strategies.json`) einführt, MUSS zwingend die Startup-Log-Ausgabe in `backtest_runner.py` um diese Parameter erweitern. PRs ohne Header-Update für neue Gates werden als 'unvollständig' abgelehnt. Dies gilt als strikte Vorgabe zur Vermeidung von "Hidden Gates" und als Blocker für Merges.
- **Zeitdauer-Pflicht (Issue #415):** Jeder Lauf-Pfad (Backtest, Trial, Study, Sweep) MUSS seine Wall-Clock-Dauer als strukturiertes Event ausweisen (`backtest_ms`, `optimizer_study_completed.wallclock_s`, `sweep_completed.wallclock_s`). PRs, die Lauf-Pfade einführen oder ändern, ohne die Dauer zu instrumentieren, gelten als unvollständig. Subprozess-stdout/stderr sind pro Trial nach `trial_dir/logs/` zu persistieren (auch im Erfolgsfall, Issue #416) — nicht nur im Crash-Fall ausgeben.
- **Logging-Init-Pflicht am Entrypoint (Issue #414):** Jeder ausführbare Entrypoint, der `emit_execution_event`/`getLogger("optimizer")` nutzt, MUSS Logging EINMALIG initialisieren (`setup_bot_logging(...)`), sonst werden INFO-`[JSON_EVENT]` ohne Handler verworfen. Strukturierte Events ohne konfigurierten Handler am Eintrittspunkt sind wertlos (vgl. Pitfall #78).
- **Eindeutigkeits-Vorbedingung der Per-Study-Parallelität (Issues #411/#412):** Die Aussage „Parallelität über getrennte Studies (je eigene SQLite-Datei)" ist NUR korrekt, wenn die `(strategy, symbol)`-Paarmenge eindeutig ist UND der Schema-Bootstrap pro Datei einmal/serialisiert läuft. Enumeration MUSS deduplizieren; Sweep MUSS bei kollidierenden `study_name` fail-fast abbrechen. `n_jobs>1`-Tests dürfen nicht ausschließlich gemockt sein — mindestens ein Test MUSS echtes SQLite unter Nebenläufigkeit prüfen.
- **Konjunktions-Schalter in `ComboTrendVwapConfig`:** `require_vwap_confirmation` und `require_bb_touch` sind boolesche Flags, die Optuna erlauben, einzelne Entry-Bedingungen kategorial abzuwählen (Werte: `[True, False]`). Default `True` = verhaltensneutral. Neue Strategieparameter dieser Art MÜSSEN als Klassenfeld mit Default `True` in der Config-Klasse eingeführt werden, in `strategy_defaults.json` mit dem Default dokumentiert und in `spaces.py` via `suggest_categorical` freigegeben werden. Die `tournament.json`-Gates dürfen zur Frequenz-Erhöhung NICHT verändert werden (Gate-Gaming-Verbot §12).
- **Gate-Gaming vs. Statistische Signifikanz (§12):** Wenn Entry-Bedingungen (wie VWAP oder BB-Touch) über Schalter aufgeweicht werden, steigt die Frequenz, aber die statistische Qualität (Sortino, Profit Factor) kann sinken. Der Optimierer darf dies nicht ausnutzen, um schwache Strategien durch das Frequenz-Gate zu schmuggeln. In solchen Fällen MUSS künftig (in `tournament.json` oder via `tournament_overrides`) ein strengeres `min_sortino` oder `min_profit_factor` angelegt werden, um das Rauschen der minderwertigeren Entries abzufangen.
- **Optuna Study Invalidation:** Da `bb_touch_window` nun scharfgeschaltet ist, werden alte Studien zu `ComboTrendVwapStrategy` inkonsistent (Typ S Strategie-Logik-Änderung). Vor einer erneuten HPO-Runde MUSS die alte SQLite-Datenbank gelöscht werden (`rm -f data/optimizer/studies.db`). Dies ist im Changelog deklariert.
- **Fill-`ts` Fail-Loud-Pflicht (Issue #448, Pitfall #80):** Fill-Zeitstempel werden AUSSCHLIESSLICH über `_fill_ts_ns(f)` gelesen (Reihenfolge `ts_event → ts_last → ts_init`, `pd.Timestamp.value`→ns, fail-loud bei Fehlen). Ein stiller `getattr(f, 'ts_event', getattr(f, 'ts_init', 0))`-Default ist VERBOTEN — er klassifiziert bei fehlendem Feld jeden Round-Trip als In-Sample (struktureller OOS=0-Kollaps). Im Walk-Forward-Modus MÜSSEN alle Fill-`ts` in `[start_ns, end_ns]` liegen; die Plausibilitäts-Assertion in `extract_metrics` darf nicht entfernt werden.
- **Sampling↔Config-Bindungs-Pflicht (Issue #446, Pitfall #81):** Ein in `spaces.py` gesampelter Parameter MUSS als Feld im zugehörigen `*Config`-Struct existieren (Single Source of Truth). Phantom-Keys werden vom Worker still verworfen. Neue/geänderte Suchräume MÜSSEN `test_search_space_binding.py` grün halten; volumenbasierte Filter sind im Backtest tot (`volume=1.0`) und werden NICHT verdrahtet, sondern entfernt. `OPTIMIZER_PARAMETER_REFERENZ.md` ist mitzupflegen.
- **Kein Loop-Variablen-Shadowing (Issue #443):** Innere Fold-Schleifen in `extract_metrics` heißen `fold` (nicht `i`/`j`), damit sie die äußere `enumerate`-Variable nicht überschreiben. Neue Schleifen in dieser Funktion MÜSSEN diese Konvention einhalten.
- **Walk-Forward-Geometrie ≤ Datenhistorie (Issue #445):** `is_window + splits×oos + holdout` MUSS ≤ `backtest.json.walk_forward.data_history_days` sein (= dieselbe Annahme wie `strategy_defaults.json._schema`). `build_trial` erzwingt dies fail-loud. Wer die Geometrie ändert, MUSS `data_history_days` und die `_schema`-Beschreibung konsistent mitführen.
- **Fenster-Arithmetik Single Source of Truth (Issue #457, Pitfall #84):** Die Walk-Forward-Fenster-Grenzen (`start`, `end`, OOS-Grenze `start + is_window`) werden AUSSCHLIESSLICH über `trial_config.compute_walk_forward_window(...)` berechnet. Inline-Nachbau der Datums-Arithmetik (`now − holdout`, Sonntag-Rollback, `start = end − (is_window + splits·oos)`) ist VERBOTEN — er erzeugt die Divergenz-Klasse zwischen „start fürs Laden" und „start für den Split" (#80/#82-Wurzel). Jede neue Stelle, die eine Fenster-Grenze braucht (Sweep-Preflight, Backfill-Heuristiken), MUSS diese Funktion aufrufen.
## ARCHITECTURAL INVARIANT: Walk-Forward Boundary Anchoring (Issue #463)

1. **Single Source of Truth (SSOT):**
   Sämtliche In-Sample (IS) und Out-of-Sample (OOS) Split-Boundaries für Worker und Aggregate MÜSSEN aus dem deterministischen Output von `compute_walk_forward_window` (spezifisch `start_ns`) abgeleitet werden.

2. **Strict Prohibition of Dynamic Re-Anchoring:**
   OOS-Boundaries dürfen UNTER KEINEN UMSTÄNDEN dynamisch aus Runtime-Daten (z.B. `_first_tick_ns`, `_last_tick_ns` der Instrumente) neu berechnet oder verschoben werden.

3. **Telemetry & Validation Contract:**
   Die Invariante `oos_covered ∧ (fill_ts_max ∈ OOS-Union) ⇒ (oos_total_trades ≥ 1)` ist permanent durchzusetzen. Jede Verletzung muss das Flag `oos_anchor_divergence=True` im `optimizer_trial_completed` Payload forcieren. PRs, die Boundary-Logik aus Tick-Timestamps re-derivieren, sind zwingend abzulehnen.
- **OOS-Abdeckungs-Telemetrie & WARN-statt-`raise` (Issue #455, Pitfall #82):** Eine OOS-Abdeckungs-Verletzung (`fill_ts_max < start_ns + is_window_ns`) wird in `extract_metrics` als WARN-Logzeile gemeldet und als `oos_covered=false`-Telemetrie durchgereicht (`data_window` → `TournamentMetrics` → BEIDE `optimizer_trial_completed`-Events) — **niemals** als `raise` (das verschluckt über die NULL-Rückgabe genau die Diagnose). Die harte Vorab-Abweisung gehört ausschließlich ins Gate-1-Preflight (`gate.data_reaches_oos_window`, fail-open). Wer die OOS-Telemetrie-Kette ändert, MUSS alle vier Felder (`fill_ts_max`, `oos_window_start_ns`, `oos_covered`, `oos_coverage_gap_days`) in BEIDEN Events erhalten.
- **Floor-Plateau-Stop nur Opt-in & Observability-neutral (Issue #456, Pitfall #83):** `floor_plateau_callback(stop_on_plateau=True)` darf `study.stop()` aufrufen, aber NUR crash-sicher (`getattr` + `try/except`) und NUR als Compute-Ersparnis — es ändert NIE eine Reward-/Promotion-Entscheidung. Die Default-Signatur bleibt `stop_on_plateau=False` (Bestands-Tests mit Fake-Study ohne `.stop()` müssen grün bleiben). Die Produktion bindet `True` in beiden `partial`-Stellen.
- **Granulare Rejection-Observability (Issue #453):** Der Catch-All `oos_not_evaluated` wird in dezidierte, aggregierbare Kategorien aufgelöst (`run_optimization._classify_is_rejection_detail` / `_map_oos_reason`): `REJECT_OOS_WINDOW_UNREACHABLE` (datenseitig, #455) vs. `REJECT_OOS_INACTIVE` (strategieseitig) vs. konkretes Gate (`REJECT_OOS_MAX_DRAWDOWN`, `REJECT_OOS_MIN_TRADES`, …). Pro Trial als `is_rejection_detail`-User-Attr persistieren, ins Event heben, modal ins Proposal (`confirm._dominant_is_rejection_detail` → `proposal["is_rejection_detail"]`) schreiben. Reine Observability — ändert KEINE Entscheidung. Hinweis: Die Proposal-Serialisierung liegt in `confirm.export_symbol_proposal` (NICHT in `_serde.py`, das ausschließlich die Nautilus-FSB16-Encodierung kapselt).

---

## 18.5 Agent-Rollen, System-Prompts & Interaktionsprotokolle (Enterprise / Security-Audit-Grade)

> **Zweck.** Diese Sektion ist die **eine unmissverständliche Referenz** für *welcher Agent darf was,
> auf Basis welchen System-Prompts, mit welcher Autoritäts-Grenze, und über welches exakte Protokoll
> er mit dem Trading-System interagiert.* Sie ist so geschrieben, dass ein Security-Audit jede
> Zuständigkeit, jede Vertrauensgrenze und jeden Datenfluss-Vertrag gegen den Code verifizieren kann.
> **Begründende Quellen** (`.agents/`, normativ): `JULES_SYSTEM_PROMPT.md` (System-Prompt des
> Coding-Agents), `Integration_Guide.md` (eToro-API-Integration), `API_docs_etoro.md` (API-Vertrag,
> Rate-Limits, Auth), `testing.md` (Test-/Verifikations-Protokoll).

### 18.5.1 Agent-Taxonomie — zwei disjunkte Klassen

Im Repository existieren **zwei** klar getrennte Agenten-Klassen. Sie dürfen **nie** vermischt werden:

1. **Coding-Agent** (Jules / Claude Code) — der KI-Software-Engineer, der die Codebasis pflegt.
   Kein Laufzeit-Bestandteil des Trading-Systems. Autoritativer System-Prompt:
   `.agents/JULES_SYSTEM_PROMPT.md`.
2. **Automatisierungs-Agenten** — die autonomen Laufzeit-Komponenten der Trading-Pipeline. Jede ist
   ein Prozess mit **fest begrenzter Autorität** (bounded authority). Genau **eine** davon
   (`momentum_ls_run`, Phase 5) berührt jemals den Broker.

### 18.5.2 Rollen, Zuständigkeiten & Autoritäts-Grenzen (verbindliche Matrix)

| Agent | Rolle / Zuständigkeit | Input (liest) | Output (schreibt) | Autoritäts-Grenze — DARF NIEMALS |
|-------|----------------------|---------------|-------------------|----------------------------------|
| **Coding-Agent (Jules)** | Verify/Improve/Enforce/Document der Codebasis; chirurgische, test-gesicherte Fixes | AGENTS.md, Code, Issues | Code-/Config-/Doku-PRs, Changelog-Eintrag | Live deployen; Holdout sehen/ändern; Risiko-Gates lockern; `strategies.json` direkt promoten; Hardcodings einführen |
| **`universe_fetcher`** | Smart-Portfolio-Universum bestimmen | eToro-Smart-Portfolio-API | `data/universe/momentum_ls.json` | Handeln; Katalog schreiben |
| **`catalog_service`** | 24/7-Marktdaten-Ingestion (WebSocket) | eToro-Quote-Stream | `data/nautilus/.../*.parquet` (FSB16) | Handeln; Strategien werten |
| **`daily_orchestrator`** | 5-Phasen-Dirigent (Universe→Import→Backtest→Tournament→Deploy) | Configs, Katalog | Tournament-JSON, Detached-Phase-5-Start | Phase 5 starten OHNE bestandenes OOS-Gate (fail-closed, Pitfall #45) |
| **`backtest_runner` (Worker, Subprozess)** | Isolierter Backtest + Tournament-Evaluierung pro `(strategy, symbol)` | `experiment_manifest.json`, Katalog | `tournament_result.json` | Andere Trials/Prozesse beeinflussen (Fault-Isolation); Live-Orders |
| **`optimizer/sweep` + `run_optimization`** | Per-Symbol-HPO (Ansatz 4), TPE über getrennte SQLite-Studies | Configs, Worker-Output | Optuna-Study (SQLite), Trial-Events | Phase 5 betreten; `n_jobs>1` *innerhalb* einer Study; `strategies.json` schreiben; `tournament.json` variieren |
| **`confirm`** | Gate 3 — Holdout-Edge-Test gegen globalen Baseline | best_trial, Holdout-Backtest | `proposal_{strategy}_{symbol}.json` | `strategies.json` schreiben; Promotion ohne bestandenes Holdout |
| **`momentum_ls_run` (Phase 5)** | Live-Execution gegen den Broker | freigegebene `strategies.json`/`instrument_overrides` | Broker-Orders, Live-Logs | Parameter ohne menschlich gemergten PR übernehmen |

**Invariante (Gewaltenteilung):** Der gesamte Optimierungs-Stack (`sweep`/`run_optimization`/`confirm`)
schreibt **ausschließlich** `proposal_*.json` und betritt **nie** Phase 5 (HARD INVARIANT in `sweep.py`:
kein `subprocess.Popen`, kein `strategies.json`-Write). Der einzige Schreibpfad in die Produktiv-Config
ist ein **menschlich gemergter PR** (HI-3, §12.5 „Human-in-the-Loop").

### 18.5.3 System-Prompt-Vertrag des Coding-Agents (`.agents/JULES_SYSTEM_PROMPT.md`)

Der Coding-Agent operiert unter einem **nicht verhandelbaren** Protokoll (Auszug, normativ in der Datei):
- **AGENTS.md ist Single Source of Truth.** Vor jeder Aufgabe die relevanten Sektionen lesen; bei
  Diskrepanz zuerst AGENTS.md korrigieren, dann coden.
- **Verify → Improve → Enforce → Document.** Jede `automation/`-Änderung erhält einen Changelog-Eintrag
  (Datum, Beschreibung, Dateien) in §19.
- **Chirurgische Fixes, test-gesichert.** Vor jedem Commit: Pre-Flight (§13) + `pytest`.
- **Autoritäts-Grenze = die der Tabelle oben:** nur Code/Doku/Config; nie Live, nie Holdout, nie
  Gate-Lockerung, nie Hardcoding.

### 18.5.4 Interaktionsprotokoll — die Promotion-Pipeline als Kette unveränderlicher Artefakte

Jeder Pfeil ist ein **Datenfluss-Vertrag** (wer schreibt, wer liest, welches Artefakt). Jedes Artefakt
ist selbstbeschreibend und provenienz-gestempelt, sodass jeder Schritt unabhängig auditierbar ist:

```
universe_fetcher ──► data/universe/momentum_ls.json {fetched_at, universe[]}
catalog_service  ──► data/nautilus/.../*.parquet     (FSB16, b"price_precision"/b"size_precision")
config (optimizer/tournament/backtest/strategies.json)
        │  eingefroren pro Study (ETORO_CONFIG_DIR)
        ▼
build_trial      ──► experiment_manifest.json {manifest_version:"1.0",
        │              provenance:{git_commit, data_snapshot_sha256, frozen_tournament_sha256},
        │              global_settings:{start_time,end_time,seed,walk_forward,…}, strategies:[…]}
        ▼
run_backtest (Subprozess, isoliert)
        ▼
tournament_result.json {full_results[], single_symbol_oos, aggregate_winner,
        │   data_window:{start,end,fill_ts_min/max, oos_window_start_ns, oos_covered, …}}  ← #444/#455
        ▼
parse_tournament ──► TournamentMetrics ──► compute_reward ──► Optuna-Study (SQLite, per Symbol)
        ▼
confirm_per_symbol_promotion (Gate 3: Holdout-Edge > global + margin)
        ▼
proposal_{strategy}_{symbol}.json {status ∈ READY_FOR_PR | REJECTED_NO_EDGE_OVER_GLOBAL |
        │   REJECTED_ON_HOLDOUT, dominant_rejection, is_rejection_detail, R_symbol, R_global}
        ▼
  ── MENSCHLICHER PR-REVIEW (HI-3) ──►  strategies.json / instrument_overrides
        ▼
momentum_ls_run (Phase 5) ──► Broker (live)
```

**Das 3-Gate-Modell** (jede Promotion durchläuft alle drei, in Reihenfolge):
- **Gate 1 — Daten-Suffizienz & OOS-Erreichbarkeit** (`gate.is_symbol_tunable` + `gate.data_reaches_oos_window`,
  #455): genug Historie für das ganze Walk-Forward-Korridor UND jüngster Tick erreicht die früheste
  OOS-Grenze (`compute_walk_forward_window` → `start + is_window`). Fail-open bei fehlender Telemetrie.
- **Gate 2 — Warm-Start am globalen Optimum** (`load_global_best` → `study.enqueue_trial`): der
  symbol-getunte Vektor startet beim globalen Baseline (Anti-Overfit-Anker, Shrinkage `lambda_reg`).
- **Gate 3 — Holdout-Edge** (`confirm_per_symbol_promotion`): der symbol-getunte Vektor MUSS auf dem
  nie-optimierten Holdout (a) das Holdout-Gate selbst bestehen UND (b) den globalen Vektor um
  `promotion_margin` schlagen. Sonst `REJECTED_*`. Der Holdout bleibt von der Optimierung unberührt.

### 18.5.5 Observability-Vertrag (strukturierte Events)

Alle Laufzeit-Agenten emittieren LLM-/Audit-parsbare Events über `log_manager.emit_execution_event`
(`[JSON_EVENT] {…}`-Hülle). **Invariante: Observability ändert NIE eine Entscheidung** (kein Event-Feld
fließt je in Reward/Promotion zurück). Zentrale Events:
- `optimizer_trial_completed` — pro Trial; trägt seit #404 die Per-Symbol-Telemetrie, seit #455 die
  OOS-Abdeckung (`fill_ts_max`, `oos_window_start_ns`, `oos_covered`, `oos_coverage_gap_days`), seit
  #453 die granulare Kategorie (`is_rejection_detail`, `oos_rejection_reasons`).
- `optimizer_study_completed` / `sweep_completed` — Timing (`wallclock_s`) + Evaluierbarkeit
  (`evaluable_trials`). Zeitdauer-Pflicht §18.
- Logging MUSS am Entrypoint einmalig initialisiert sein (`setup_bot_logging`, Pitfall #78), sonst
  werden INFO-`[JSON_EVENT]` verworfen.

### 18.5.6 Security-Audit-Checkliste (jede Leitplanke → ihr erzwingender Test/Code)

| Leitplanke (Zusicherung) | Erzwungen durch | Verifizierbar via |
|--------------------------|-----------------|-------------------|
| Kein Live-Deploy aus dem Optimizer | HARD INVARIANT in `sweep.py` (kein `subprocess.Popen`) | Code-Review + `test_automation_isolation.py` |
| Risiko-Gates eingefroren (`tournament.json` nie variiert) | Optimizer liest, schreibt nie | §12.5 + Code-Review |
| Holdout unberührt | `confirm` nutzt separaten `holdout_days=0`-Lauf | `test_holdout_window.py`, `test_per_symbol_promotion.py` |
| Human-in-the-Loop (Promotion nur per PR) | `confirm` schreibt nur `proposal_*.json` | `test_issue_408/451`, Code-Review |
| Reward-Floor-Ordnungsinvariante (unevaluable < evaluable) | `compute_reward`-Floor-Clamp | `test_issue_447_floor_separation.py`, `test_issue_452_reward_distance.py` |
| Zero-Hardcoding (Schwellen aus Config) | Config-derivierte Werte (HI-6) | `CODE_AUDIT_ZERO_HARDCODING.md` + Review |
| Fill-`ts` fail-loud (kein stiller OOS=0) | `_fill_ts_ns` + Plausibilitäts-Assertion | `test_issue_448_oos_split.py` |
| OOS-Abdeckung sichtbar (kein stiller Floor-Kollaps) | `oos_covered`-Telemetrie + Preflight | `test_issue_449_oos_coverage.py` |
| Rate-Limit-Konformität (eToro-API) | `adapters/etoro_rate_limiter.py` | `.agents/API_docs_etoro.md`, `Integration_Guide.md` |
| Per-Study-Parallelität reproduzierbar | Paar-Dedup + Schema-Pre-Init + Fail-Fast | `test_issue_411/412`, Pitfall #76/#77 |

---

## 19. Changelog
| 2026-06-10 | **1c:** Optuna-Loop (SQLite, TPE, Warm-Start), Holdout-Confirmation, PR-Proposal-Export. Autotuner V2 abgeschlossen. | `automation/optimizer/` |

- **Phase 0b:** ETORO_CONFIG_DIR/ETORO_LOGS_DIR env isolation implemented; Manifest-Contract (no re-merge if manifest_version is set); oos_fold_sortinos export added for aggregate winners.
 (Agent-Maintained)
> **Anweisung für Jules:** Bei jeder Änderung am `automation/`-Paket hier einen Eintrag (Datum, Beschreibung, Dateien) anhängen.

| Datum | Änderung | Dateien |
|-------|----------|---------|
| 2026-06-27 | **Implementierung Issue #460: Pitfall #85 Holdout Reachability & Anchor-Clamp** | `automation/optimizer/trial_config.py`, `automation/optimizer/confirm.py`, `automation/optimizer/sweep.py`, `automation/optimizer/run_optimization.py`, `automation/AGENTS.md` |
| 2026-06-26 | **IMPLEMENTIERUNG GitHub-Issues #451–#457 (OOS-Abdeckung, Reward-Gradient, Observability, Plateau-Stop, Fenster-SSOT).** Sieben verzahnte Optimizer-Fixes, alle test-gesichert (volle automation-Suite lokal grün: 386 passed). **#455 (P0, Pitfall #82) — OOS-Abdeckungs-Blindstelle:** Wurzel = der Katalog erreicht die früheste OOS-Sub-Fenster-Grenze (`start+is_window`) nicht (dünner/staler H2 nach `catalog_service`-Ausfall) ⇒ `oos_total_trades=0` strukturell über alle Strategien (TSLA-Signatur). Fix: `extract_metrics` berechnet `oos_window_start_ns`/`oos_covered` (WARN statt `raise`), durchgereicht über `write_tournament_json` (`data_window`) → `parse_tournament` → BEIDE Trial-Events; reine fail-open `gate.data_reaches_oos_window` + `sweep.latest_ts_by_symbol`/`compute_oos_window_start_ns` + Preflight-Skip (`OOS_WINDOW_UNREACHABLE`) VOR dem Sweep. **#452/#454 (Bug) — Reward-Gradient:** evaluiert-aber-nicht-eligible OOS-Trials nicht mehr auf den Flat-Floor clampen, sondern kontinuierliche, quadratische, config-gewichtete Distanz-Penalty (`constraint_distance_penalty_weight`, neue OOS-`win_rate`/`profit_factor` in `TournamentMetrics`) — near-miss > katastrophal, aber strikt unter dem Evaluable-Floor (Anti-Gate-Gaming bleibt). **#453 (Feature) — granulare Rejection-Observability:** Catch-All `oos_not_evaluated` aufgelöst in dezidierte Kategorien (`_classify_is_rejection_detail`/`_map_oos_reason`: `REJECT_OOS_WINDOW_UNREACHABLE` datenseitig vs. `REJECT_OOS_INACTIVE` strategieseitig vs. konkretes Gate); pro Trial als `is_rejection_detail`-User-Attr + Event, modal ins Proposal (`confirm._dominant_is_rejection_detail`). **#451 (Bug) — 100%-Rejection-Diagnose:** Root-Cause ist NICHT ein zu strenges IS-Gate (Boolean-Logik in `gate.py` korrekt, separat gepinnt), sondern die OOS-Abdeckung (#455); der 502-IS-Trades/OOS=0-Fall wird jetzt eindeutig als `REJECT_OOS_WINDOW_UNREACHABLE` diagnostiziert. **#456 (P1, Pitfall #83) — Plateau-Stop:** `floor_plateau_callback(stop_on_plateau=True)` ruft `study.stop()` (beide Zweige, crash-sicher via `getattr`+`try/except`); Produktion bindet `True`, Default bleibt `False` (Bestands-Tests grün); Observability-Invariante erhalten. **#457 (P2, Pitfall #84) — Fenster-SSOT:** reine `trial_config.compute_walk_forward_window` als EINZIGE Quelle der Fenster-Arithmetik; `build_trial` delegiert (bit-identisch); Sweep-Preflight nutzt dieselbe Grenze (verifiziert `now=2026-06-25` ⇒ `start=2025-05-16`/`end=2026-05-11`). **Hinweis #453:** Proposal-Serialisierung liegt in `confirm.py` (nicht `_serde.py`, FSB16-only) — Intent statt fehlerhafter Datei-Referenz umgesetzt. AGENTS.md: Pitfalls #82/#83/#84, §18-Konventionen (Fenster-SSOT, OOS-WARN, Plateau-Opt-in, Rejection-Granularität), neue §18.5 (Agent-Rollen/System-Prompts/Interaktionsprotokolle, Security-Audit-Checkliste). | `automation/optimizer/reward.py`, `automation/optimizer/parsing.py`, `automation/optimizer/gate.py`, `automation/optimizer/sweep.py`, `automation/optimizer/trial_config.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/confirm.py`, `automation/backtest_runner.py`, `automation/config/optimizer.json`, `automation/tests/test_issue_449_oos_coverage.py`, `automation/tests/test_issue_451_gate_diagnosis.py`, `automation/tests/test_issue_452_reward_distance.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-25 | **IMPLEMENTIERUNG GitHub-Issues #441–#448 (Per-Symbol-Optimizer-Forensik 2026-06-24).** Sechs Code-/Config-Defekte + zwei Doku-Deliverables, alle test-gesichert (volle automation-Suite lokal grün: 351 passed). **#448 (P0, Pitfall #80) — struktureller OOS=0:** Wurzel = stiller Fill-`ts`-Fallback `getattr(f,'ts_event',getattr(f,'ts_init',0))` (a) `0`-Default ⇒ jeder Round-Trip als In-Sample; (b) Fallback-Report `generate_order_fills_report` hat `ts_last`, kein `ts_event`. Fix: `_fill_ts_ns` (fail-loud, `ts_event→ts_last→ts_init`) als einzige Lesestelle + Plausibilitäts-Assertion (`fill_ts_max<start_ns ∨ fill_ts_min≤0 ⇒ ValueError`). Reproduktion 142 uniforme Exits ⇒ IS=72/OOS=70. Pitfall #75 → 🟢. **#447 (P1) — Reward-Floor:** Floor-Separations- (`unevaluable_max=−9.75 < evaluable_min=−9.749`) und Saturations-Invariante (`reward(is=target)==reward(is=10·target)`) formal test-fixiert (Code war bereits korrekt). **#446 (P1, Pitfall #81) — Sampling↔Config:** Renames `bb_std→bb_std_dev`/`vwap_window→vwap_period`; Phantom-Volumen-/RSI-Keys entfernt (1h-Bars haben `volume=1.0`, VwapExhaustion hat kein RSI); `trend_tolerance_pct` in Combo verdrahtet; FlashCrash sampelt jetzt `bb_period`/`bb_std_dev`; zentrale Bindungs-Assertion `test_search_space_binding.py`. **#445 (P1) — Walk-Forward-Historie:** validierte Geometrie (180/45/4/45=405) behalten, ehrliche `data_history_days=450` (~15 Monate) deklariert (SSOT mit `strategy_defaults._schema`), Fail-Loud-Startup-Assertion in `build_trial`. **#444 (P2) — data_window:** Schreib-Seite ergänzt (`write_tournament_json` schreibt `data_window` inkl. `fill_ts_min/max`; Worker/`extract_metrics` liefern die Spanne); Round-Trip-Test. **#443 (P3) — Loop-Var-Shadowing:** innere Fold-Schleifen `i/j`→`fold` (verhaltensneutral). **#442/#441 (Doku):** `OPTIMIZER_PARAMETER_REFERENZ.md` (korrigierte Suchräume aller aktiven Strategien) neu; AGENTS.md: Pitfall #75 → 🟢, neue Pitfalls #80/#81, §18-Konventionen (Fill-`ts` fail-loud, Bindungs-Pflicht, kein Loop-Shadowing, Geometrie≤Historie). | `automation/backtest_runner.py`, `automation/optimizer/spaces.py`, `automation/optimizer/parsing.py`, `automation/optimizer/trial_config.py`, `automation/strategies/tesla_combo_strategy.py`, `automation/config/backtest.json`, `automation/config/strategy_defaults.json`, `automation/OPTIMIZER_PARAMETER_REFERENZ.md`, `automation/tests/test_issue_448_oos_split.py`, `automation/tests/test_issue_447_floor_separation.py`, `automation/tests/test_issue_445_walkforward_history.py`, `automation/tests/test_issue_444_data_window.py`, `automation/tests/test_search_space_binding.py`, `automation/tests/test_combo_conjunction_switches.py`, `automation/AGENTS.md` |
| 2026-06-24 | **IMPLEMENTIERUNG GitHub-Issues #415–#423 (= interne #411–#416 Code + #417 Doku); Pitfalls #76–#79 → 🟢 BEHOBEN.** Die in der Forensik (Eintrag unten) als OFFEN dokumentierten Defekte sind jetzt chirurgisch implementiert UND test-gesichert (lokal grün: optimizer/sweep/runner-Suite 154 passed). **#411/#416-GH-#417 (DDL-Race, Pitfall #76):** prozessweiter `_study_lock` + `_create_study_with_retry` (genau EIN Retry nur auf `"already exists"`, sonst Fail-Fast Pitfall #66) + serielles `_preinit_study_storage` vor dem Pool; Test mit echtem SQLite & 8 Threads. **#412/GH-#415+#418 (Paar-Dedup, Pitfall #77):** order-preserving Dedup in `load_symbol_universe` & `enumerate_tunable_pairs` + Fail-Fast-`ValueError` bei kollidierendem `study_name`. **#413/GH-#419 (Floor-Guard v3):** `floor_plateau_callback` evaluable-basiert (`oos_evaluated`-User-Attr) statt Wert-Gleichheit; Legacy-Wert-Guard als Fallback; `make_symbol_objective` setzt `oos_evaluated`-Attr. **Defekt A (0 evaluable Trials) bleibt offen** (erfordert realen Katalog-Sweep, `data/` gitignored) ⇒ Pitfall #75 bleibt 🟡. **#414/GH-#420 (Logging-Init, Pitfall #78):** `setup_bot_logging("optimizer")` als erste Anweisung in `sweep.main()`. **#415/GH-#421 (Zeitdauer, Pitfall #79):** Wall-Clock in `run_backtest` (beide Modi, optionaler `timings`-Out-Param ⇒ signatur-kompat); `backtest_ms` im Per-Trial-Event; neue `optimizer_study_completed`/`sweep_completed`-Events + Konsolen-Schlusszeile. **#416/GH-#422 (Subprozess-Logs, Pitfall #79):** `_persist_subprocess_logs` schreibt stdout/stderr pro Trial nach `trial_dir/logs/` (auch im Erfolgsfall); `data_window_*`/`rejection_reason` zusätzlich im Event. CI-Gate um die neuen Tests erweitert. | `automation/optimizer/run_optimization.py`, `automation/optimizer/runner.py`, `automation/optimizer/sweep.py`, `automation/optimizer/parsing.py`, `automation/tests/test_issue_411_storage_ddl_race.py`, `automation/tests/test_issue_412_pair_dedup.py`, `automation/tests/test_issue_413_floor_guard_v3.py`, `automation/tests/test_issue_414_sweep_logging.py`, `automation/tests/test_issue_415_backtest_timing.py`, `automation/tests/test_issue_416_subprocess_logs.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-24 | **Forensik Sweep-Log (`--tier all --n-jobs 6`) + Issues #411–#417.** Sechs Defekte + AGENTS.md-Härtung aus der Analyse zweier Sweep-Logs. **#411 (P0)** Optuna/SQLite `create_all`-DDL-Race (`table studies already exists`) crasht den Sweep — `load_if_exists` schützt den Schema-Bootstrap NICHT; Fix: serialisiertes `_preinit_study_storage` + Retry (Pitfall #76). **#412 (P0)** doppelte `(strategy,symbol)`-Paare kollabieren N Worker auf eine Study (`…_WDAY_ETORO` Trial 499 bei n_trials=100), zerstören Reproduzierbarkeit (Pitfall #68) und lösen #411 aus; Fix: order-preserving Dedup in `load_symbol_universe`/`enumerate_tunable_pairs` + Fail-Fast-Assertion (Pitfall #77). **#413 (P1)** Floor-Kollaps besteht empirisch fort: alle Trials unevaluable (−9.85…−9.93 ⊂ [−10.0,−9.75)), 0 evaluable; `floor_plateau_callback` (#409) ist im v3-Shaping-Regime toter Code (Wert-Gleichheits-Prädikat trifft geshapete Sub-Floor-Werte nie) → Ersatz durch evaluable-basierten Guard; Pitfall #75 auf 🟡 TEILWEISE reklassifiziert. **#414 (P1)** Sweep-Entrypoint initialisiert kein Logging ⇒ #404-`[JSON_EVENT]`-Telemetrie (INFO) wird stumm verworfen; Fix: `setup_bot_logging("optimizer")` in `sweep.main()` (Pitfall #78). **#415 (P1)** Backtest-Zeitdauer wird nirgends ausgewiesen; Fix: Wall-Clock in `run_backtest` (beide Modi) + `backtest_ms` + `optimizer_study_completed`/`sweep_completed`-Summaries (Pitfall #79). **#416 (P2)** `run_backtest` verschluckt stdout/stderr im Erfolgsfall; Fix: pro Trial nach `trial_dir/logs/` persistieren (Pitfall #79). **#417 (P2)** AGENTS.md wasserdicht: #75 reklassifiziert + empirischer Nachtrag, #409-Bullet als ⚠️ ineffektiv markiert, #72-Parallel-Safety-Vorbedingung ergänzt, Pitfalls #76–#79 angelegt, §18 um Zeitdauer-/Logging-Init-/Eindeutigkeits-Pflicht erweitert. **Pitfall-Nummern kollisionsfrei (höchste #79).** | `automation/optimizer/run_optimization.py`, `automation/optimizer/runner.py`, `automation/optimizer/sweep.py`, `automation/config/` (Universe-Hygiene), `automation/tests/test_issue_411_storage_ddl_race.py`, `…_412_pair_dedup.py`, `…_413_floor_guard_v3.py`, `…_414_sweep_logging.py`, `…_415_backtest_timing.py`, `…_416_subprocess_logs.py`, `automation/AGENTS.md` |
| 2026-06-24 | **Behebung Pitfall #75 (Per-Symbol-Sweep konstanter Reward −9.75 — Unevaluable-Floor-Kollaps): Issues #404–#410.** Die zwei kompoundierenden Defekte (Gewinner-Status vs. Evaluierbarkeit; Shaping-Sättigung) sind behoben — TPE hat im Per-Symbol-Sweep wieder einen Gradienten. **#404 (P0)** Per-Symbol-Telemetrie: `make_symbol_objective` emittiert `optimizer_trial_completed` (`symbol`, `oos_evaluated`, `oos_eligible` [trennt IS-/OOS-Drop], `oos_total_trades`, `oos_total_return`, `is_total_trades`, `is_max_trades`, `outcome`). **#405 (P0)** Evaluierbarkeit entkoppelt: `write_tournament_json` schreibt im Single-Symbol-Pfad einen `single_symbol_oos`-Block aus `r['_oos_eval']`/`r['oos_metrics']` (ungeachtet Gewinner-Status); `parse_tournament` nutzt ihn als Fallback bei fehlendem `aggregate_winner` (Multi-Symbol bit-identisch). **#406 (P1)** `per_symbol_shaping_trade_target` (400) verhindert die sofortige Shaping-Sättigung im Per-Symbol-Pfad. **#407 (P1)** `_gate_proximity` (aus `is_best_total_return`/`is_best_win_rate` gegen `shaping_return_target`/`shaping_winrate_target`) liefert einen kontinuierlichen, hart auf `unevaluable_shaping_span` gedeckelten Eligibility-Gradienten. **#408 (P2)** modale Rejection-Reason: `trial.set_user_attr("rejection_reason")` + `confirm._dominant_rejection` → `proposal["dominant_rejection"]`. **#409 (P2)** `floor_plateau_callback` warnt nach `n_startup_trials`, wenn alle Trials am Unevaluable-Floor kleben. **#410 (P3)** `reward_semantics_version: 3` + `_check_reward_semantics_version` (Study-Hygiene gegen alte Floor-Trials). **Invarianten:** Per-Symbol-Evaluierbarkeit ≠ Gewinner-Status; Anti-Gate-Gaming bleibt erhalten (Unevaluable < Evaluable-Floor — evaluierbare Trials werden IMMER besser bewertet). | `automation/optimizer/reward.py`, `automation/optimizer/parsing.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/confirm.py`, `automation/backtest_runner.py`, `automation/config/optimizer.json`, `automation/tests/test_issue_404_symbol_telemetry.py`, `automation/tests/test_issue_405_single_symbol_evaluability.py`, `automation/tests/test_issue_406_per_symbol_shaping.py`, `automation/tests/test_issue_407_gate_proximity.py`, `automation/tests/test_issue_408_rejection_surfacing.py`, `automation/tests/test_issue_409_floor_guard.py`, `automation/tests/test_issue_410_reward_versioning.py`, `automation/AGENTS.md` |
| 2026-06-23 | **Diagnose Pitfall #75 (Per-Symbol-Sweep konstanter Reward −9.75 — Unevaluable-Floor-Kollaps):** Forensische Analyse des `sweep`-Logs + Optimizer-Module. −9.75 = `penalty_unevaluable_oos (−10.0) + unevaluable_shaping_span (0.25) × progress (1.0)` = Unevaluable-Floor mit gesättigtem Shaping (NICHT der Evaluable-Floor −9.749; NICHT der #401-Fall — der `total_return`-Fallback greift nicht, weil `oos_evaluated=False`). Zwei kompoundierende Defekte: (1) `oos_evaluated` ist via `aggregate_winner` an den vollen Tournament-Gewinner-Status gekoppelt → im Single-Symbol-Pfad strukturell `False`, wenn das Paar das Gate für keine Parametrisierung klärt; (2) `shaping_trade_target=50` ist universe-skaliert und sättigt im Per-Symbol-Pfad sofort (`activity=1.0`) → Zero-Gradient, `Best is trial 0` bewegt sich nie. Fix-Katalog Issues #404–#410 erstellt. Status: DIAGNOSTIZIERT, Implementierung offen. | `automation/AGENTS.md` (Diagnose); Fix folgt in #404–#410 |
| 2026-06-23 | **Issue #402 (Pitfall #74 — Optuna ExperimentalWarning-Spam):** Modul-Level `warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)` in `run_optimization.py` unterdrückt die pro Sampler-Instanziierung wiederholten `multivariate`/`group`-Warnungen. Bewusst gezielt: KEIN globales `set_verbosity(ERROR)`, damit Optunas native Per-Trial-INFO-Logs (Reward-Werte; im Sweep via `make_symbol_objective` die einzige Rückmeldung) erhalten bleiben und die Observability (#403) nicht untergraben wird. | `automation/optimizer/run_optimization.py`, `automation/tests/test_issue_402_warning_filter.py`, `automation/AGENTS.md` |
| 2026-06-23 | **Issue #403 (Pitfall #73 — Observability: Zeitfenster & Config-Quellen):** (1) Der Worker loggt jetzt das tatsächliche Daten-Zeitfenster (Start–Ende + Spanne in Tagen) via reinem `_format_backtest_window` (vorher nur erster Tick). (2) Neuer gemeinsamer Startup-Header `log_active_config()` legt Config-Verzeichnis, die vier JSON-Pfade und Kern-Schwellen (`n_trials`, `seed`, `oos_sortino_fallback`, `oos_min_trades`, `sortino_min_trades`, `max_drawdown`) einmalig offen, bevor der Lauf in die (bei `capture_output=True` stummen) Trials übergeht — aufgerufen aus `sweep.main()` und `run()`. Erfüllt die Observability-Regel (Pitfall #46) und erweitert Issue #256 auf den Optimizer/Sweep-Layer. | `automation/backtest_runner.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/sweep.py`, `automation/tests/test_issue_403_observability.py`, `automation/AGENTS.md` |
| 2026-06-23 | **Issue #400 (Pitfall #72 — `--n-jobs` ignoriert):** `run_per_symbol_sweep` nahm `n_jobs` entgegen, iterierte aber synchron — der Sweep lief trotz `--n-jobs N` strikt sequenziell. Fix: `n_jobs > 1` verteilt die (strategy, symbol)-Paare über einen `ThreadPoolExecutor` (Ansatz 4, je Paar eigene SQLite-Study; `optimize_symbol` bleibt intern `n_jobs=1`, Pitfall #68). ThreadPool statt ProcessPool, da der Backtest-Subprozess die GIL freigibt und die injizierbaren `optimize_symbol`/`confirm` (HI-7) ohne Pickling testbar bleiben. `executor.map` bewahrt die Reihenfolge; `n_jobs <= 1` bleibt bit-identisch sequenziell; Worker sind Fail-Fast (kein `try/except Exception`). | `automation/optimizer/sweep.py`, `automation/tests/test_issue_400_sweep_parallel.py`, `automation/AGENTS.md` |
| 2026-06-23 | **Issue #401 (Pitfall #71 — Flat Reward Landscape -9.75):** Das hartcodierte `n < 5`-Sortino-Limit in `_calculate_stats` ist jetzt deklarativ (`tournament.json['sortino_min_trades']`, Default 5, ausgeliefert mit 2; Zero-Hardcoding/ISSUE-06) und an `oos_min_trades` ausgerichtet. Zero-Loss-Samples liefern weiterhin `None` (Issue #209/#43 unangetastet), werden aber im Reward aufgefangen: `compute_reward` nutzt für `oos_evaluated ∧ oos_eligible ∧ oos_sortino is None` den geclippten `oos_total_return` als evaluable Base (gegated über `optimizer.json['oos_sortino_fallback']`), statt auf den `-9.75`-Unevaluable-Floor zu kollabieren. Reward bleibt performance-basiert (kein Gate-Gaming); Micro-Sizing-/Risiko-Gates bleiben über `oos_eligible` wirksam. `TournamentMetrics` um `oos_total_return` erweitert. | `automation/backtest_runner.py`, `automation/optimizer/parsing.py`, `automation/optimizer/reward.py`, `automation/config/tournament.json`, `automation/config/optimizer.json`, `automation/tests/test_issue_401_flat_reward.py`, `automation/AGENTS.md` |
| 2026-06-16 | **Konjunktions-Schalter zur Combo-Strategie (ISSUE-OPT-01):** `require_vwap_confirmation` und `require_bb_touch` als boolesche Config-Felder (Default: `True`) in `ComboTrendVwapConfig` eingeführt. `on_bar` (Long- und Short-Zweig) nutzt jetzt `vwap_ok`/`vwap_bearish_ok`/`bb_ok`-Guards statt fest verdrahteter UND-Glieder. `bb_touch_window` als explizites Config-Feld (war hardcoded `24`). Beide Schalter in `spaces.py` via `suggest_categorical([True, False])` für Optuna freigegeben. `strategy_defaults.json` und AGENTS.md §7/§10/§18 aktualisiert. **Achtung: Dies ist eine Typ S Änderung. Bisherige Optuna Studien für ComboTrendVwapStrategy sind nun invalide und müssen gelöscht werden.** | `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json`, `automation/optimizer/spaces.py`, `automation/AGENTS.md` |
| 2026-06-16 | **Konjunktions-Schalter zur Combo-Strategie (ISSUE-OPT-01):** `require_vwap_confirmation` und `require_bb_touch` als boolesche Config-Felder (Default: `True`) in `ComboTrendVwapConfig` eingeführt. `on_bar` (Long- und Short-Zweig) nutzt jetzt `vwap_ok`/`vwap_bearish_ok`/`bb_ok`-Guards statt fest verdrahteter UND-Glieder. `bb_touch_window` als explizites Config-Feld (war hardcoded `24`). Beide Schalter in `spaces.py` via `suggest_categorical([True, False])` für Optuna freigegeben. `strategy_defaults.json` und AGENTS.md §7/§10/§18 aktualisiert. | `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json`, `automation/optimizer/spaces.py`, `automation/AGENTS.md` |
| 2026-06-11 | **Issue #355 (Pitfall #66 - Silent Worker Crash Swallowing):** Fail-Fast in `backtest_runner.py` implementiert, damit fundamentale systemische Fehler (z.B. ImportError) nicht stumm verschluckt werden und das Live-Deployment hart abbrechen. | `automation/backtest_runner.py`, `automation/tests/test_backtest_fatal_worker_crash.py`, `automation/AGENTS.md` |
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

| 2026-06-11 | **Optimizer Fault-Isolation (Pitfall #65):** `runner.run_backtest` wirft `BacktestRunError` statt `optuna.TrialPruned` (kein Optuna-Leak; Holdout-Pfad crasht nicht mehr). `objective` übersetzt `BacktestRunError`→`TrialPruned` an genau einer Stelle; `study.optimize` mit engem `catch=(json.JSONDecodeError, OSError)` (kein bare-Exception). `run()` exportiert `NO_VIABLE_TRIAL` statt `study.best_trial`-`ValueError`, wenn alle Trials prunen. Per-Trial-JSON-Logging (outcome/oos_total_trades/fully_eligible_pairs) für Diagnose. | `automation/optimizer/runner.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/confirm.py`, `automation/tests/test_optimizer_fault_isolation.py`, `automation/AGENTS.md` |
## Architektonische Methodik: IS/OOS Split und "State Bleed"

Der `daily_orchestrator.py` und der `backtest_runner.py` nutzen nun ein echtes, rollierendes Walk-Forward (`walk_forward_active: true`). Das Train/Test (IS/OOS) Splitting basiert aus Performancegründen weiterhin auf einem einzigen, durchgehenden Engine-Run des `backtest_runner.py`. Die Aufteilung in `n=splits` rollierende Fenster erfolgt jedoch *retrospektiv* anhand der Timestamp-Filterung über die gesamte Spanne während der Metrik-Extraktion in `extract_metrics`.

**Wichtige Limitationen für Agenten (State Bleed):**
- **Kein Hard-Reset:** An der IS/OOS-Grenze findet kein Zurücksetzen der Engine statt. Das bedeutet, dass laufende offene Positionen, das angesammelte Account-Guthaben sowie die Historie aller Indikatoren (z.B. aufgewärmte EMAs, RSI-Werte) ungefiltert aus der In-Sample Phase in den Out-of-Sample Zeitraum überfließen ("State Bleed").
- **Gültigkeit der OOS-Metriken:** OOS-Ergebnisse sind somit methodisch nicht 100% "rein" oder vollständig unabhängig vom In-Sample Lauf. Dieser Kompromiss wird derzeit bewusst akzeptiert, um Backtesting-Overhead und Laufzeiten zu minimieren.
- Zukünftige Code-Änderungen an Strategien oder Evaluierungs-Metriken müssen diese architektonische Gegebenheit berücksichtigen.

---

*Zuletzt aktualisiert: 2026-06-25. Datum und Changelog bei jeder Änderung an dieser Datei aktualisieren.*

## Known Pitfalls & Architecture Notes
### 🟢 Pitfall #65 — Optimizer Fault-Isolation (Study-/Holdout-Crash)
**Symptom:** Ein einzelner fehlerhafter Trial (Subprocess-Crash, korruptes JSON) oder ein Lauf ohne verwertbaren Trial reißt die gesamte Optuna-Study bzw. `run()` mit in den Absturz.
**Root Cause:** (1) `runner.run_backtest` warf `optuna.TrialPruned` direkt — außerhalb der Optimize-Schleife (Holdout-Confirm) eskaliert das ungefangen. (2) `study.optimize` hatte kein `catch`. (3) `study.best_trial` ohne Guard bei 0 `COMPLETE`-Trials.
**Fix/Regeln:**
1. Low-Level-Runner ist Optuna-frei: `BacktestRunError` statt `TrialPruned`. Übersetzung in `TrialPruned` ausschließlich im `objective`.
2. `study.optimize(..., catch=(json.JSONDecodeError, OSError))` — eng gefasst. `catch=(Exception,)` ist untersagt (maskiert Code-Bugs).
3. `run()` prüft auf ≥1 `COMPLETE`-Trial; sonst `export_no_viable_proposal` (`status="NO_VIABLE_TRIAL"`). `confirm_on_holdout` fängt `BacktestRunError` und liefert `passed=False, reason="holdout_subprocess_failed"`.
4. Diese Regel erweitert PR #353 („Trial Pruning over Hard Crashes") auf Parse-/IO-Pfade und den Confirm-/Export-Pfad. DO NOT REVERT.
5. **Ausnahme (A4.9):** Der **In-Process-Modus** (`mode='inprocess'`) wirft `optuna.TrialPruned` direkt im Runner (siehe Pitfall #70). Das ist bewusst und auf den Optimize-Loop begrenzt — der **Subprozess-Default bleibt Optuna-frei** (`BacktestRunError`), und der Confirm-/Holdout-Pfad MUSS den Subprozess-Default nutzen, damit Regel 1 dort gilt.

### 🟢 Pitfall #70 — In-Process-Backtest-Entry: Fault-Isolation-Trade-off (A4.9)
**Symptom/Kontext:** Der naive Sweep zahlt pro Trial den Overhead eines `python automation/backtest_runner.py`-Subprozess-Spawns + Importkosten. Der optionale In-Process-Entry (`backtest_runner.run_backtest_inprocess`, via `runner.run_backtest(mode='inprocess')`) eliminiert den **äußeren** Spawn.
**Trade-off:** Die Fault-Isolation des äußeren Prozesses entfällt — fachliche Trial-Fehler werden zu `optuna.TrialPruned` (Study läuft weiter), **fundamentale Fehler** (`ImportError`/`ModuleNotFoundError`/`SyntaxError`) propagieren hart (Fail-Fast, crasht die Study bewusst). Der **Subprozess-Modus bleibt Default** und voll funktionsfähig; das Backtest-Ergebnis ändert sich durch den Moduswechsel nicht (Operator-Smoke: identische Rewards).
**State-Leak-Mitigation (Rückfrage #396):** Die eigentlichen Per-(Symbol,Strategie)-Backtests laufen auch im In-Process-Modus in einem internen `ProcessPoolExecutor` mit `max_tasks_per_child=1` (frische Worker je Job). Per-Job-State-Isolation (Log-Handler, Caches, Modul-Level-Dicts) bleibt also erhalten; nur globaler State des *äußeren* Hauptprozesses wird je `run_backtest()`-Aufruf neu initialisiert (kein expliziter Tear-Down nötig, da `run_backtest()` seine Engines/Pools pro Aufruf frisch aufbaut). **Regel:** In-Process-Modus NUR im Optimize-Loop verwenden, nie im Confirm-/Holdout-Pfad (Pitfall #65, Regel 5).
**Config-Sharing:** `build_trial(copy_config=False)` überspringt die Pro-Trial-Config-Kopie (Manifest ist seit ISSUE-OPT-374 self-describing); der Aufrufer stellt eine eingefrorene Study-`config/` via `ETORO_CONFIG_DIR` bereit. Default `True` ⇒ bit-identisch (Kopie pro Trial).
**Betroffen:** `automation/backtest_runner.py`, `automation/optimizer/runner.py`, `automation/optimizer/trial_config.py`
**Betroffen:** `automation/optimizer/runner.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/confirm.py`

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
> ### PR #353: Optuna Subprocess Isolation & Metadata Injection
>
>
> **Date:** 2026-06-11
> **Context:** Optuna optimizations crashed entirely when the underlying `backtest_runner.py` subprocess failed (e.g., due to missing `strategy_module` keys).
> **Architectural Decisions (DO NOT REVERT):**
> 1. **Trial Pruning over Hard Crashes:** In `automation/optimizer/runner.py`, if a subprocess exits with `returncode != 0` or fails to generate `tournament_result.json`, the exception is caught and `optuna.TrialPruned` is raised. Optuna must *never* crash entirely due to a single bad trial configuration.
> 2. **Explicit Manifest Injection:** The trial config generator (`trial_config.py`) MUST explicitly inject `strategy_module` and `config_class` from the base `strategies.json` into the generated trial manifest. The backtest runner requires these for dynamic `importlib` loading.
> 3. **Defensive Runner Instantiation:** `backtest_runner.py` uses `.get()` for meta-keys and explicitly returns `_empty_result()` instead of throwing `TypeErrors` if import parameters are missing.
>
>

### Architectural Dependency: Strategy Parameters and OOS Gating
* **Trade-off Constraint:** Configurations in `strategy_defaults.json` (such as `deviation_threshold` for mean-reversion strategies) MUST be strictly calibrated against the `oos_min_trades` tournament gating requirement relative to the Out-of-Sample evaluation window (e.g., 30 days). If thresholds are too tight (e.g., 0.015 instead of 0.008 for VWAP), the mathematical possibility of passing the OOS gate falls to zero because the strategy naturally produces too few signals within the OOS span to be statistically evaluable. This results in false-positive "fail" states.
| 2026-06-08 | **Issue #305 (Consistent Capping Policy & Raw Ratio Sample-Size Shrinkage):** Vereinheitlichte das Capping für alle Ratio-Metriken (Sortino, Profit Factor, Calmar) in `_calculate_stats` auf exakt `50.0`. Um Division-by-Zero zu verhindern, wurde für alle Nenner (Downside-Dev, Gross Loss, Max Drawdown) ein `DENOMINATOR_FLOOR = 1e-6` eingeführt. Zusätzlich wird nun in `select_winners` eine asymptotische Dämpfungsfunktion (Shrinkage) auf die Raw-Ratios basierend auf `n_trades` und `k_shrinkage` angewendet, *bevor* die Rankings berechnet werden. Dabei wird die Sortino Ratio in Richtung Baseline `0.0` und der Profit Factor in Richtung Baseline `1.0` gedämpft: `Damped_Ratio = Baseline + (Raw_Ratio - Baseline) * (n_trades / (n_trades + k_shrinkage))`. Dies verhindert, dass kleine Sample-Sizes durch Rauschen ungedämpfte Outlier produzieren und die Turnier-Selektion dominieren. | `automation/backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-11 | **Optimizer Handbuch Bugfix:** Aktualisierung von manuals/run_optimizer.md. Die veraltete Warnung (Abschnitt 0) und Empfehlungen für manuelle Code-Änderungen (Abschnitt 5.4, 9.1) wurden entfernt, da Bugs B1-B4 im Code bereits behoben wurden. Die Fehler-Referenzen im Fehlerkatalog und in der Diskrepanz-Tabelle (Abschnitt 10) wurden auf 'Behoben' gesetzt. | `manuals/run_optimizer.md`, `automation/AGENTS.md` |
| 2026-06-11 | **Issue #331 (P1 Defect / Pitfall Fix):** Ergänzung fehlender Argumente (`span_tolerance_days`, `commission_bps`, `spread_bps_by_asset_class`) im `_run_remaining_sequentially` Fallback bei einem `BrokenProcessPool` Absturz. Robuster Signatur-Regressionstest ergänzt. | `automation/backtest_runner.py`, `automation/tests/test_backtest_runner.py`, `automation/AGENTS.md` |
| 2026-06-11 | **Bugfix Issue #354 / Pitfall #63 (Lazy Import Crash):** Behebung eines `ImportError` Absturzes im Backtest-Worker-Prozess durch fehlerhaften Lazy Import von `emit_json_event` in `run_single_backtest_worker`. Korrektur des Pfades auf `automation.log_manager.emit_execution_event` und Einführung eines gezielten Execution-Tests (`automation/tests/test_worker_lazy_imports.py`) inklusive CI-Integration (Tier 3), um sicherzustellen, dass in Edge-Cases angestoßene Lazy Imports in Workern fehlerfrei aufgelöst werden. Pitfall #63 dokumentiert. | `automation/backtest_runner.py`, `automation/tests/test_worker_lazy_imports.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-12 | **Issue #357/#358 (Pitfall #64 — parse_tournament NoneType):** `parse_tournament` crashte bei `"aggregate_winner": null` (häufigster HPO-Fall: kein Gewinner). Umstellung auf `or {}`/`or []`-Pattern für `aggregate_winner`, `oos_metrics`, `oos_fold_sortinos`, `fully_eligible_pairs`. Liefert nun deterministisch den kanonischen „unevaluable"-Record → Penalty statt Study-Crash. | `automation/optimizer/parsing.py`, `automation/tests/test_parsing_null_winner.py`, `automation/AGENTS.md` |
| 2026-06-12 | **Optimizer Parallel-Safety (Pitfall #68):** `build_storage` mit SQLite-Busy-Timeout (60s, `pool_pre_ping`) gegen `database is locked` bei `n_jobs>1`. `build_sampler` unterdrückt gezielt die `ExperimentalWarning`s (multivariate/group bleiben). Einmalige Determinismus-Warnung bei `seed`+`n_jobs>1`; optional `--deterministic` (erzwingt n_jobs=1). Helfer ausgelagert (DI/Unit-testbar). | `automation/optimizer/run_optimization.py`, `automation/tests/test_optimizer_parallel.py`, `manuals/run_optimizer.md`, `automation/AGENTS.md` |
| 2026-06-13 | **Issue #372 / PR #380 (Rollierendes VWAP in ComboTrendVwapStrategy):** Unendliche VWAP-Akkumulierung behoben. Logik wurde durch ein rollierendes Fenster (`collections.deque` mit `vwap_period`) in `tesla_combo_strategy.py` ersetzt. `vwap_period` in JSON-Defaults und Optuna-Suchraum ergänzt, um Pfadabhängigkeit zu unterbinden und den Indikator ordnungsgemäß optimierbar zu machen. Architektur-Constraint in Sektion 10 der AGENTS.md für VWAP in synthetischen Bars dokumentiert. | `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json`, `automation/optimizer/spaces.py`, `automation/AGENTS.md` |
| 2026-06-11 | **Holdout-Anti-Kontamination (Pitfall #67):** Holdout-Confirm bewertete OOS auf `[now-30, now]` (nur `oos_window_days`) statt der carved-out 45 Tage — IS-Anteil überlappte den Optimierungs-Envelope um ~105 Tage. `build_trial` erhält `oos_window_days_override`; `confirm_on_holdout` setzt OOS=`holdout_days` ⇒ bewerteter OOS = exakt der nie-optimierte Slice. Zusätzlich `walk_forward` (is/oos/splits/holdout) in die Trial-Config geschrieben ⇒ Sizing (build_trial) und Splitting (backtest_runner) sind harmonisiert (Fold-Desync behoben). | `automation/optimizer/trial_config.py`, `automation/optimizer/confirm.py`, `automation/tests/test_holdout_window.py`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.9 / Issue #396 (Overhead-Reduktion: In-Process-Backtest-Entry + Config-Sharing):** Optionale Performance-Erweiterung. `backtest_runner.run_backtest_inprocess(manifest_path, output_path)` ist ein importierbarer In-Process-Entry (reuse von `run_backtest()` via argv, kein äußerer Subprozess-Spawn). `runner.run_backtest(..., *, mode='subprocess'|'inprocess')`: Subprozess bleibt Default (unverändert, `BacktestRunError`); inprocess wandelt fachliche Fehler in `optuna.TrialPruned`, propagiert `ImportError` (Fail-Fast). `build_trial(copy_config=True)` ⇒ Config-Sharing-Flag (False überspringt Pro-Trial-Kopie, Manifest ist self-describing). Fault-Isolation-Trade-off als **Pitfall #70** dokumentiert (Per-Job-Isolation via interner ProcessPool bleibt; Inkonsistenz zu Pitfall #65 aufgelöst: inprocess ist bewusste, auf den Optimize-Loop begrenzte Ausnahme). Subprozess-Default-Regression + Fault-Isolation getestet (HI-7). `daily_orchestrator.py` unberührt (HI-1). | `automation/backtest_runner.py`, `automation/optimizer/runner.py`, `automation/optimizer/trial_config.py`, `automation/tests/test_inprocess_backtest_entry.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.8 / Issue #395 (Live-Integration: Matrix-Wiring + Momentum-LS, `touches-prod`):** Die in A4.1 erstellten reinen Resolver werden im Realbetrieb verdrahtet: (1) `backtest_runner` Matrix-Dispatch löst pro (symbol, strategy) im `is_manifest=False`-Zweig via `resolve_strategy_params(..., instrument=symbol)` auf (Log bei aktivem Override); (2) `momentum_ls_run._build_bots_config` wendet `instrument_overrides[symbol]` additiv auf die Gewinner-Params an (reine Funktion bleibt rein). **`daily_orchestrator.py` unverändert** (HI-1, `git diff` leer). Ohne Override bit-identisch (HI-2, Regressionstest). OOS-Gating/Whitelist/Fail-Closed (Pitfall #60) unangetastet — OOS-Verlierer bleibt trotz Override ausgeschlossen (Test). Mocked-Tests (HI-7). **Typ S-nah (Produktionspfad): vor Aktivierung echter Overrides Operator-Smoke `daily_orchestrator --no-deploy` fahren.** | `automation/backtest_runner.py`, `automation/momentum_ls_run.py`, `automation/tests/test_live_instrument_override.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.7 / Issue #394 (Konfigurierbare Storage-URL):** `run_optimization.resolve_storage(*, study_name, base_cfg=None)` löst die Optuna-Storage-URL auf: ENV `ETORO_OPTUNA_STORAGE` > `optimizer.json['storage_url']` (null ⇒ ignoriert) > SQLite-Default `{WORK}/sweep/{study}.db`. `optimize_symbol` nutzt es statt fester SQLite-Konstruktion. **SQLite bleibt strikter Default** (`storage_url: null`); Postgres ist reines Opt-In. Warnung bei non-SQLite (Determinismus nur bei n_jobs=1); ENV-URL verbatim (Fail-Fast statt stillem Fallback). Leitplanke Pitfall #53 präzisiert („SQLite für Single-Node; Postgres nur für explizite parallele Sweeps"). Tests prüfen nur URL-Auflösung (keine echte Postgres-Verbindung, HI-7). | `automation/optimizer/run_optimization.py`, `automation/config/optimizer.json`, `automation/tests/test_storage_url_resolution.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.6 / Issue #393 (`sweep.py` Meta-Orchestrator):** Neues Modul `automation/optimizer/sweep.py`. `enumerate_tunable_pairs` (Tier `deployable`/`refine`/`all` + Gate-1-Filter), `run_per_symbol_sweep` (Dispatch `optimize_symbol → confirm_per_symbol_promotion → export_symbol_proposal`, injizierbar), `load_symbol_universe`, `load_tier_a_winners` (aus `per_symbol_winners`), `n_params_for`, `count_available_bars` (Zeitspanne-Adapter) + CLI (`--strategies/--symbols/--tier/--n-jobs`, `all`-Auflösung). **Betritt NIE Phase 5** (Test beweist: kein `subprocess.Popen`), schreibt **nie** `strategies.json` (HI-3). Parallelität nur über getrennte Studies. Vollständig gemockte Tier-10-Tests (HI-7). `daily_orchestrator.py` unberührt (HI-1). | `automation/optimizer/sweep.py`, `automation/tests/test_sweep_enumeration.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.5b / Issue #392 (Gate 3 — `confirm_per_symbol_promotion`):** Kern-Verteidigungs-Gate (P0). `confirm.confirm_per_symbol_promotion` promotet ein Symbol-Tuning nur, wenn es das globale Baseline auf dem ungesehenen Holdout um `promotion_margin` schlägt **und** der symbol-getunte Lauf das Holdout-Gate selbst besteht. `_holdout_metrics_for_params` (single-symbol Holdout, `holdout_days=0`/`n_folds=1`/`oos_window_days_override`) liefert die Metriken; Vergleichs-Score ist die **rohe** Performance (`compute_reward(universe_size=1)` **ohne** `param_pen`). Status `READY_FOR_PR` / `REJECTED_NO_EDGE_OVER_GLOBAL` / `REJECTED_ON_HOLDOUT` (alle drei getestet). `export_symbol_proposal` schreibt `proposal_{strategy}_{symbol}.json`, **nie** `strategies.json` (HI-3); Holdout nur hier angefasst (HI-5). E2E mit params-abhängigem Mock (HI-7). | `automation/optimizer/confirm.py`, `automation/tests/test_per_symbol_promotion.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.5a / Issue #391 (`optimize_symbol` + Gate 2 Warm-Start):** Single-Symbol-Variante von `optimize` in `run_optimization.py` (additiv; globales `optimize`/`make_objective` unverändert). `optimize_symbol` legt eine eigene SQLite-Study unter `{WORK}/sweep/study_{strategy}_{_sanitize(symbol)}.db` an, baut Manifeste mit `instruments=[symbol]` (universe_size==1 ⇒ Per-Symbol-Reward) und erzwingt `n_jobs=1` (Pitfall #68). Gate 2: `study.enqueue_trial(load_global_best(...))`. `load_global_best` nutzt `proposal_{strategy}.json` nur bei status READY_FOR_PR (sonst Fallback `strategies.json[strategy].params`, dann `{}`). `make_symbol_objective` setzt `sampled_params` als user_attr (für Gate 3). E2E-Tests mit gemocktem Backtest (HI-7); `daily_orchestrator.py` unberührt (HI-1). | `automation/optimizer/run_optimization.py`, `automation/tests/test_optimize_symbol.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.4 / Issue #390 (Gate 1 — Daten-Suffizienz `is_symbol_tunable`):** Neues reines, I/O-freies Modul `automation/optimizer/gate.py`. `required_bars(...)` und `is_symbol_tunable(symbol, n_params, *, available_bars, config)` (Bar-Zahl per Injektion) liefern `(ok, reason)` mit `reason ∈ {OK, INSUFFICIENT_HISTORY, PARAM_DATA_RATIO_TOO_LOW, OOS_FOLD_TOO_SHORT}`. Schwellen `gate1_buffer_days` (30), `min_bars_per_param` (200), `min_oos_bars_per_fold` (500) in `optimizer.json` (+ `_schema`, HI-6). Tests (Tier 10) decken alle drei Reject-Gründe + Boundary-Werte ab, Schwellen aus JSON berechnet. **Finding:** `historical_fetcher.is_symbol_data_sufficient` existiert nicht (nur `is_backtest_range_covered`) — Gate 1 nutzt bewusst injizierte Bars. | `automation/optimizer/gate.py`, `automation/config/optimizer.json`, `automation/tests/test_symbol_tunable_gate.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.3 / Issue #389 (Per-Symbol-Reward — `reward_mode`, `param_pen`, Coverage-Drop):** `compute_reward` um optionale `*, sampled, global_params, strategy` erweitert. Bei `universe_size == 1` (oder `reward_mode == 'per_symbol'`) entfällt der Coverage-Term und eine Shrinkage-Strafe `param_pen = lambda_reg · normalized_param_distance(sampled, global_params, bounds.extract_numeric_bounds(strategy))` kommt hinzu (0.0 falls Inputs fehlen). Coverage-Pfad (`universe_size > 1`, `reward_mode='auto'`) **bit-identisch** (Regressionstest). Floor-/Ordnungsinvariante strikt erhalten. Neue Zero-Hardcoding-Keys `lambda_reg` (0.25), `promotion_margin` (0.10, erst A4.5b), `reward_mode` ('auto') in `optimizer.json` (+ `_schema`, inkl. Kalibrierungs-Hinweis). Tests lesen Werte aus JSON (HI-6). | `automation/optimizer/reward.py`, `automation/config/optimizer.json`, `automation/tests/test_reward_per_symbol.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.2 / Issue #388 (`global_settings.instruments`-Filter):** Reproduzierbarer, manifest-getriebener Single-/Multi-Symbol-Backtest. `build_trial(..., instruments=None)` schreibt `global_settings.instruments` nur bei gesetztem Wert (sonst Schlüssel weggelassen ⇒ volles Universum, HI-2). Neue reine Funktion `backtest_runner.restrict_universe(universe, instruments)` (Schnittmenge, Universum-Reihenfolge erhalten, unbekannte Symbole still gedroppt) am Universum-Seam (`discover_instruments_from_catalog`) eingebunden. Kein `--instrument`-CLI-Flag. Tests: Filter in Tier 3, Manifest in Tier 10. `daily_orchestrator.py` unberührt (HI-1). | `automation/optimizer/trial_config.py`, `automation/backtest_runner.py`, `automation/tests/test_runner_instrument_filter.py`, `automation/tests/test_trial_instruments_manifest.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.1 / Issue #387 (`instrument_overrides`-Schema + Resolution):** Datenmodell + reine Auflösungslogik für symbol-spezifische Parameter, additiv und rückwärtskompatibel. `resolve.resolve_params(..., *, instrument=None)` und `backtest_runner.resolve_strategy_params(..., *, is_manifest, instrument=None)` erweitert: Precedence `defaults < params < instrument_overrides[symbol] < sampled`. `is_manifest=True` ignoriert Overrides strikt (Pitfall #61). `strategies.json._schema` dokumentiert das optionale Feld (keine realen Overrides committet). Ohne `instrument` bit-identisches Legacy-Verhalten (HI-2, Regressionstests). Kein Call-Site-Wiring (das ist A4.8). `daily_orchestrator.py` unberührt (HI-1). | `automation/optimizer/resolve.py`, `automation/backtest_runner.py`, `automation/config/strategies.json`, `automation/tests/test_resolve_instrument_override.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **A4.0 / Issue #386 (Deklarativer Suchraum-Bounds-Extractor):** Neues Modul `automation/optimizer/bounds.py` extrahiert die numerischen `(low, high)`-Grenzen aus `spaces.sample_params` per `_RecordingTrial`-Introspektion (DRY, `spaces.py` bit-identisch). `extract_numeric_bounds(strategy)` (ValueError bei unbekannter Strategie; kategoriale/abgeleitete Parameter exkludiert) und `normalized_param_distance(sampled, reference, bounds)` (mittlere quadrierte, auf [0,1] normierte Abweichung) als reine Bausteine für `param_pen` (A4.3). Tests in Tier 10; kein Backtest/I/O. | `automation/optimizer/bounds.py`, `automation/tests/test_optimizer_bounds.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |
| 2026-06-23 | **ISSUE-OPT-377 (Suchraum-/Betriebs-Diskrepanzen):** `spaces.py` für `ComboTrendVwapStrategy` an Konzept §4 angeglichen: `macd_fast` 8–20 → **3–14**, `macd_gap` 6–20 → **4–26** (`macd_slow = fast + gap`). TPE-Sampler läuft fehlerfrei über die neuen Ranges an. **Hinweis:** Geänderte Optuna-Distributions ⇒ alte `ComboTrendVwapStrategy`-Studies sind inkonsistent; vor erneuter HPO-Runde `data/optimizer/studies.db` löschen. AGENTS.md: Ausführungs-/Storage-Empfehlung (Konzept §10) an Pitfall #68 ergänzt — SQLite mit `--n-jobs 1` für reproduzierbare Single-Node-Läufe; echte Parallelität nur über getrennte Worker-Prozesse (`n_jobs=1`) gegen eine RDB/PostgreSQL-Study, nicht via hohem `n_jobs` gegen SQLite (Lock-Contention). | `automation/optimizer/spaces.py`, `automation/AGENTS.md` |
| 2026-06-28 | **IMPLEMENTIERUNG GitHub-Issue #462 (Pitfall #87 Holdout Reachability Preflight):** Einführung von `data_reaches_holdout_window` in `gate.py` und `compute_holdout_window_start_ns` in `sweep.py`. Verhinderung von Compute-Waste bei stalem Katalog durch vorzeitigen Sweep-Skip. Test-Coverage über `test_issue_462_gate_holdout_reach.py` gesichert. | `automation/optimizer/gate.py`, `automation/optimizer/sweep.py`, `automation/tests/test_issue_462_gate_holdout_reach.py`, `automation/AGENTS.md` |
| 2026-06-28 | **Issue #461 (Reward-Inversion bei Constraint Failures):** Asymptotische Penalty-Kompression mittels `math.tanh` in `_constraint_failure_reward` hinzugefügt, sodass die Distanzstrafe den Reward im schmalen Kompressionsband verbleibt und niemals unter die `unevaluable_ceiling` (`-9.75`) drückt. Zuvor eingeführte globale Skalierung (`target < 0.05`) in `_shortfall_distance` entfernt, um numerische Verzerrungen zu verhindern. Die strikte Invariante der Ordnung (`eligible > near-miss > far-miss ≳ unevaluable_ceiling (-9.75) > unevaluable_shaping_band`) wurde wiederhergestellt und strukturell abgesichert. `reward_semantics_version` in `optimizer.json` auf 4 erhöht. Property-Tests überarbeitet, um die Integrität der Ordnung und Bounds abzusichern. Pitfall #86 dokumentiert. | `automation/optimizer/reward.py`, `automation/config/optimizer.json`, `automation/tests/test_issue_461_reward_no_inversion.py`, `automation/tests/test_issue_452_reward_distance.py`, `automation/AGENTS.md` |
| 2026-06-23 | **ISSUE-OPT-376 (Toter Parameter `bb_entry_tolerance` entfernt):** `bb_entry_tolerance` war in `ComboTrendVwapConfig` deklariert und in `strategy_defaults.json` gesetzt, wurde in `on_bar()` aber nie referenziert (das BB-Touch-Fenster nutzt `atr_tolerance = atr · atr_multiplier`). Restlos aus Config-Klasse und Defaults entfernt; nicht im Optuna-Suchraum. Keine Struct-Validierungsfehler (Config baut weiter, `__struct_fields__` ohne den Key); Dry-Run grün. | `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json`, `automation/AGENTS.md` |
| 2026-06-23 | **ISSUE-OPT-375 (Reward-Gradient bei `fully_eligible_pairs = 0`):** Das Shaping nicht-evaluierbarer Trials war allein an OOS-Trades gekoppelt; ohne IS-Sieger blieb der Reward flächig `penalty_unevaluable_oos` (−10.0), TPE hatte keinen Gradienten Richtung Eligibility. `compute_reward` koppelt das Unevaluable-Shaping nun monoton an die IS-Aktivität: `shaping = unevaluable_shaping_span · max(trade_progress_oos, min(1, is_total_trades / shaping_trade_target))`. Neuer Zero-Hardcoding-Knob `shaping_trade_target` (Default 50) in `optimizer.json` (+ `_schema`). Floor-Invariante strikt erhalten (`shaping ≤ span` ⇒ jeder Unevaluable-Trial < Evaluable-Floor). IS-Aktivität wird in `parsing.TournamentMetrics` aus `full_results[].metrics.total_trades` abgeleitet (bereits von `backtest_runner` exportiert) — kein Runner-Change nötig. Tests: Gradient zwischen zwei Unevaluable-Trials + harte Ordering-Invariante. | `automation/optimizer/reward.py`, `automation/config/optimizer.json`, `automation/tests/test_optimizer_reward_parser.py`, `automation/AGENTS.md` |
| 2026-06-23 | **ISSUE-OPT-374 (Self-describing Manifest — `walk_forward` + `start_capital`):** `build_trial` schreibt die effektive Walk-Forward-Geometrie (`is_window_days`, `oos_window_days`, `splits == n_folds`, `holdout_days`) **und** `start_capital` jetzt zusätzlich in `manifest.global_settings` (zuvor nur im Side-Channel der kopierten `backtest.json`). `backtest_runner.py` liest beide **autoritativ aus dem Manifest**, mit Fallback auf die trial-lokale `backtest.json`, und loggt die effektive Quelle im Startup-Header. `test_optimizer_manifest.py` prüft die Präsenz von `walk_forward` (splits == n_folds) und `start_capital`. Korridor-Geometrie (405d vs. ~365d 12M-History) dokumentiert und bewusst beibehalten (Beschaffungstiefe erhöhen statt Fenster verkleinern; Geometrie-Änderung wäre Typ S). | `automation/optimizer/trial_config.py`, `automation/backtest_runner.py`, `automation/tests/test_optimizer_manifest.py`, `automation/AGENTS.md` |
| 2026-06-22 | **ISSUE-OPT-373 (ComboTrendVwap Konjunktions-Schalter):** Die fest verdrahtete 4-fach-UND-Konjunktion im Entry-Gate ist über die booleschen Config-Schalter `require_vwap_confirmation` und `require_bb_touch` (Long- **und** Short-Zweig) auflockerbar. Beide Defaults `True` ⇒ verhaltensneutral zum Status quo (Regressionssicherheit). Optuna sucht beide kategorial via `suggest_categorical([True, False])`, sodass der Optimierer einzelne Konjunkte selbst abwählen kann — **ohne** `tournament.json`-Gates zu verändern (Gate-Gaming-Verbot §12). Dedizierter Regressionstest ergänzt und in Tier 5 des PR-Gates verdrahtet; verwaiste Duplikate von Pitfall #69 in dieser Datei bereinigt. **Typ S (Strategie-Logik-Änderung): ein frischer Baseline-Lauf ist nötig**, um die erwartete Steigerung von `fully_eligible_pairs` bei `require_*=False` zu messen und erwartete Test-Werte zu kalibrieren — wird NICHT automatisch ausgelöst. | `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json`, `automation/optimizer/spaces.py`, `automation/tests/test_combo_conjunction_switches.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |


### 🟢 Pitfall #87 — Holdout-Abdeckungs-Blindstelle: Katalog erreicht Holdout-Slice nicht ⇒ struktureller Reject [BEHOBEN: GH-#462]
**Symptom:** Symbole bestehen Preflight und fahren 100 Trials, fallen aber deterministisch am leeren Holdout (REJECT_OOS_INACTIVE / REJECTED_ON_HOLDOUT). Compute wird nutzlos verbrannt.
**Root Cause:** `data_reaches_oos_window` prüft lediglich die Erreichbarkeit von Fold 0 (`start_ns + is_window_ns`). Der Holdout-Slice (`end - holdout_days`) wird nicht evaluiert. `required_bars` prüft reine Datenmengen, keine Aktualität der Timestamps.
**Fix/Regel:** `gate.data_reaches_holdout_window` und `sweep.compute_holdout_window_start_ns` eingeführt. Sweep überspringt Kandidaten VOR der Optimierung mit `HOLDOUT_WINDOW_UNREACHABLE` und berechnet `gap_days`. Verhalten bleibt vollständig fail-open bei `None`.
**Betroffen:** `automation/optimizer/gate.py`, `automation/optimizer/sweep.py`

### 🟢 Pitfall #86 — Reward-Inversion bei OOS-Constraint-Failures (Issue #461)
**Symptom:** Aktive Trials erhalten drastisch schlechtere Rewards (z.B. -31.2) als komplett inaktive Trials (0 Trades, Reward -9.75).
**Root Cause:** Unbeschränkte quadratische Penalty in `_constraint_failure_reward` gepaart mit mikroskopischen Zielen (`oos_min_total_return` ~ 0.005) in `_shortfall_distance`, was zu einer Distanz-Explosion führt.
**Fix/Regel:** Implementierung einer asymptotischen Penalty-Kompression (`available_span * math.tanh(penalty)`). Der Constraint-Failure-Reward verbleibt so in einem *definierten Kompressionsband* und darf weder in den Evaluable-Space eindringen noch in den Unevaluable-Space abrutschen. Die neue Ordnungsinvariante ist strikt: `eligible > near-miss > far-miss ≳ unevaluable_ceiling (-9.75) > unevaluable_shaping_band`.
**Betroffen:** `automation/optimizer/reward.py`

### 🟢 Pitfall #68 — Optimizer Parallel-Safety & Reproduzierbarkeit
**Symptom:** Bei `--n-jobs 4` sporadisch `OperationalError: database is locked`; `ExperimentalWarning`-Spam; trügerischer Eindruck von Reproduzierbarkeit trotz gesetztem Seed.
**Root Cause:** SQLite ohne Busy-Timeout unter Thread-Concurrency; `TPESampler`-State ist unter Threads nicht deterministisch.
**Fix/Regel:** `RDBStorage` mit `connect_args.timeout` und `pool_pre_ping`. Nur die konkreten `ExperimentalWarning`s filtern (kein globales `simplefilter("ignore")`). Bei `seed`+`n_jobs>1` einmalig warnen; für reproduzierbare Läufe `--n-jobs 1`/`--deterministic`. Sampler-Algorithmus (TPE multivariate+group) bleibt — korrekt für abhängige MACD-Parameter.
**Empfehlung zur Ausführung (Konzept §10, ISSUE-OPT-377):** Für *single-node*-Läufe ist **SQLite mit `--n-jobs 1`** der reproduzierbare Default (fixer Seed ⇒ deterministischer TPE-State). `--n-jobs > 1` gegen eine SQLite-Study in *einem* Prozess ist nicht falsch (der Busy-Timeout fängt `database is locked` ab), aber suboptimal: Lock-Contention und nicht-deterministischer Sampler-State trotz Seed. Für **echte Parallelität** mehrere **getrennte Worker-Prozesse mit je `n_jobs=1`** gegen eine gemeinsame **RDB/PostgreSQL**-Study fahren (jeder Prozess sampelt unabhängig, die DB serialisiert die Writes) — *nicht* einen Prozess mit hohem `n_jobs` gegen SQLite. Der Storage-Default bleibt SQLite (Pitfall #53); die konfigurierbare Storage-URL ist Gegenstand einer separaten Erweiterung.
**Betroffen:** `automation/optimizer/run_optimization.py`

### 🛡️ Pitfall: Optuna Subprocess Isolation & Metadata Injection (PR #353)

**Context:** Optuna optimization runs were fatally crashing because the underlying `backtest_runner.py` subprocess failed (e.g., due to missing `strategy_module` keys generated by the optimizer).

**Architectural Constraints (DO NOT REVERT OR OVERRIDE):**

* **Rule 1: Trial Pruning over Hard Crashes:** In `automation/optimizer/runner.py`, if a subprocess exits with a non-zero return code (`returncode != 0`) or fails to generate `tournament_result.json`, the runner MUST log the subprocess `stderr` and raise `optuna.TrialPruned`. Optuna must *never* crash entirely due to a single bad trial configuration.
* **Rule 2: Explicit Manifest Injection:** The trial configuration generator (`trial_config.py`) MUST explicitly extract and inject `strategy_module` and `config_class` from the base `strategies.json` into the generated trial manifest payload. The backtest runner relies entirely on these keys for dynamic `importlib` loading.
* **Rule 3: Defensive Runner Instantiation:** `backtest_runner.py` MUST use `.get()` for all metadata keys (`strategy_class`, `strategy_module`, `config_class`). It must include a fallback for `strategy_class` (e.g., `"UnknownStrategy"`) to prevent logging crashes. If import parameters are missing, it MUST log the error and return `_empty_result()` instead of attempting dynamic imports that result in `TypeError`.

### Pitfall #60: Gate-Scope vs. Deployment-Scope (Mismatch Prevention)
- **Symptom:** Ein notorischer Einzel-Verlierer könnte fälschlicherweise live geschaltet werden, weil er innerhalb seines isolierten Symbols als lokaler Gewinner hervorging oder Teil eines Turniers war, das auf Aggregat-Ebene (Portfolio-Level) das OOS-Gate bestand, obwohl die spezifische Strategie das OOS-Gate nie individuell bestanden hat.
- **Root Cause:** Der Orchestrator (Phase 5) validierte standardmäßig das *aggregierte* OOS-Gate (Portfolio-Level). `momentum_ls_run.py` deployt jedoch die *individuellen per-Symbol-Gewinner*. Wenn das Aggregat-Gate bestanden wurde, wurde bisher die gesamte State-Datei übergeben, ohne isolierte OOS-Verlierer auf Symbol-Ebene strikt herauszufiltern.
- **Fix/Rule:**
  1. Die `daily_orchestrator.py` muss zwingend eine explizite Whitelist generieren (`whitelist_tournament.json`), bevor der Live-Run angestoßen wird. Dies wird über einen `OOS-DEPLOY-REJECT`-Filter in `_build_bots_config` hart blockiert.
  2. In dieser Whitelist werden nur diejenigen Symbole aus `per_symbol_winners` behalten, die **individuell** das OOS-Gate bestanden haben (`oos_eligible == True` UND `oos_evaluated == True`). Alle anderen werden gedroppt und geloggt.
  3. JSON-Events und Logausgaben im Orchestrator müssen zur Vermeidung von Mehrdeutigkeiten zwischen IS und OOS strikt qualifiziert werden (z. B. `median_is_sortino` vs. `aggregate_oos_sortino`).
  4. Diese Whitelist ist die *einzige* Source of Truth für das tatsächliche Deployment (`--tournament whitelist_tournament.json`). Das übergeordnete Aggregat-Gate ist lediglich ein Vorfilter für den generellen Start, qualifiziert aber keinen individuellen Ausreißer.

### 🟢 Pitfall #67 — Holdout-Fenster-Kontamination & Fold-Desync
**Symptom:** Der Holdout-Confirm „bestätigte" auf einem Fenster, dessen IS-Anteil stark mit der Optimierung überlappte; nur die letzten 30 statt 45 carved-out Tage wurden bewertet. Zudem realisierte der Subprozess die n_folds der Objective nicht zwingend.
**Root Cause:** (1) `confirm_on_holdout` koppelte die OOS-Länge nicht an `holdout_days`. (2) `build_trial` schrieb die zum Sizing genutzte `walk_forward`-Config nicht in die kopierte Trial-`backtest.json`, die `backtest_runner.py` zum Splitting liest.
**Fix/Regel:** `oos_window_days_override` koppelt OOS an die carved-out Holdout-Länge; bewerteter OOS MUSS disjunkt vom Optimierungs-Envelope sein (Invariante `holdout_OOS ∩ optimization_window = ∅`, nur Randzeitpunkt geteilt). IS-Warmup-Overlap bleibt zulässig (State-Bleed). `build_trial` schreibt die effektive `walk_forward` in die Trial-Config ⇒ Sizing und Splitting deckungsgleich. DO NOT REVERT.
**Betroffen:** `automation/optimizer/trial_config.py`, `automation/optimizer/confirm.py`
