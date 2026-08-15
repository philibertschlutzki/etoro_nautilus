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

**Anhang — chronologische Forensik-/Bug-Kaskaden-Kataloge (nicht Teil der Kernkapitel 1–19, aber verbindliche Pitfall-Quelle):**

- [ARCHITECTURAL INVARIANT: Walk-Forward Boundary Anchoring (Issue #463)](#architectural-invariant-walk-forward-boundary-anchoring-issue-463)
- [AUDIT #470 — Verifizierte mathematische Invarianten (#460–#469): HARTE AXIOME](#audit-470--verifizierte-mathematische-invarianten-460469-harte-axiome)
- [Architektonische Methodik: IS/OOS Split und "State Bleed"](#architektonische-methodik-isoos-split-und-state-bleed)
- [Known Pitfalls & Architecture Notes (Pitfalls #59–#98 Fortsetzung)](#known-pitfalls--architecture-notes)
- [Walk-Forward Validation & Look-Ahead Bias Prevention (Purge & Embargo)](#walk-forward-validation--look-ahead-bias-prevention-purge--embargo)
- [Pitfall-Kompendium — Bug-Kaskade #521–#530 (NautilusTrader ≥1.226 Equity/MtM/Annualisierung)](#pitfall-kompendium--bug-kaskade-521530-nautilustrader-1226-equitymtmannualisierung)
- [🧭 Bug-Kaskade #587–#600 — Reward-/Metrik-Pipeline-Kohärenz](#-bug-kaskade-587600--reward-metrik-pipeline-kohärenz-sortino--gate--reward--holdout--selektion)
- [Bug-Kaskade #613/#615/#617 — Selektions-/Divergenz-/Gate-Kohärenz](#bug-kaskade-613615617--selektions-divergenz-gate-kohärenz-p0p0p1)
- [Bug-Kaskade #611–#625 — Statistische Signifikanz der Selektion](#bug-kaskade-611625--statistische-signifikanz-der-selektion-p0-kaskade)
- [Bug-Kaskade #629–#639 — PSR-Migrations-Nachwirkungen & Gate-Kalibrierung](#bug-kaskade-629639--psr-migrations-nachwirkungen--gate-kalibrierung-p0-kaskade)
- [Bug-Kaskade #649–#660 — Selektions-Integrität & Deflations-Kohärenz](#bug-kaskade-649660--selektions-integrität--deflations-kohärenz-p0-kaskade)
- [Issue-Katalog #663–#672 — Fold-Kommensurabilität, CSCV-Granularität & Regime-symmetrische Gates](#issue-katalog-663672--fold-kommensurabilität-cscv-granularität--regime-symmetrische-gates-sitzung-2026-07-17-lauf-1)
- [Issue-Katalog #675–#686 — Optimizer: Mathematische Exzellenz, vier Kohorten](#issue-katalog-675686--optimizer-mathematische-exzellenz-vier-kohorten-sitzung-2026-07-17-lauf-2)
- [Issue-Katalog #695–#702 — DSR-Familien-Decluster, Gate-Konsolidierung & Purge-Klassifikation](#issue-katalog-695702--dsr-familien-decluster-gate-konsolidierung--purge-klassifikation-2026-07-18)
- [Epic #702 (Issues #703–#710) — Iterativer Champion-Warm-Start & symbol-skopierte Default-Nachführung](#epic-702-issues-703710--iterativer-champion-warm-start--symbol-skopierte-default-nachführung)
- [Issue-Katalog #710–#717 — Time-Box-Reward, Dynamisches Take-Profit & Live-Guardrails](#issue-katalog-710717--time-box-reward-dynamisches-take-profit--live-guardrails-sitzung-2026-07-18)
- [Issue-Katalog #768–#793 — Budget-Skalierung, Renditeserien-Kohärenz, DSR-Multiplizität & Denylist-Evidenz](#issue-katalog-768793--budget-skalierung-renditeserien-kohärenz-dsr-multiplizität--denylist-evidenz-github-issues-743742-sitzung-2026-07-26)
- [Issue-Katalog #794–#815 — Storage-Lebenszyklus, Inferenz-Korrektheit & Selektions-Integrität](#issue-katalog-794815--storage-lebenszyklus-inferenz-korrektheit--selektions-integrität-github-issues-745746-sitzung-2026-07-28)
- [Issue-Katalog #817–#835 — Champion-Store-Härtung, Inferenz-Integrität & Durchsatz/Berichtswesen](#issue-katalog-817835--champion-store-härtung-inferenz-integrität--durchsatzberichtswesen-github-issues-749750751-sitzung-2026-07-30)
- [Issue-Katalog #836–#855 — Zeitbox-Exit-Pfad, Symbol-Durchsatz, Inferenz-Integrität & Governance](#issue-katalog-836855--zeitbox-exit-pfad-symbol-durchsatz-inferenz-integrität--governance-github-issues-753754755756-sitzung-2026-08-04)
- [Issue-Katalog #897–#912 — Exit-Sperrklinke, Kostenmodell-Fallback, Governance-Rückstand](#issue-katalog-897912--exit-sperrklinke-kostenmodell-fallback-governance-rückstand-github-issues-771769770772-sitzung-2026-08-06)
- [Issue-Katalog #913–#936 — Inferenz-Blockade, Suchbudget, Simulations-Verifikation & Re-Run-Runbook](#issue-katalog-913936--inferenz-blockade-suchbudget-simulations-verifikation--re-run-runbook-github-issues-774775776777-sitzung-2026-08-06)
- [Issue-Katalog #993–#1002 — Deployment-Grenze, Live-Kapitalallokation & Circuit-Breaker](#issue-katalog-9931002--deployment-grenze-live-kapitalallokation--circuit-breaker-github-issues-846847-sitzung-2026-08-10)

> **Pitfall-Index-Hinweis:** Die höchste zum Zeitpunkt dieser Doku-Härtung vergebene Nummer ist **Pitfall #339** (siehe §16-Konvention). Vor dem Anlegen eines neuen Pitfalls IMMER `grep -n "Pitfall #" automation/AGENTS.md` laufen lassen — Nummern sind global eindeutig über die gesamte Datei, nicht nur innerhalb von §16. Bekannter Rückstand: Pitfalls #269–#284 (Kataloge #856–#896) sind noch NICHT eingetragen (siehe Hinweis im Abschnitt "Issue-Katalog #897–#912") — dieser Rückstand ist unverändert von der #913–#936-Sitzung, siehe dortiger Hinweis.

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
| SqueezeBreakoutStrategy | squeeze_breakout.py | BB(20,2.0)+Keltner(20,1.5) | 15.0 |
| OpeningRangeBreakoutStrategy | opening_range_breakout.py | Custom OR-Range+ATR | 15.0 |
| DonchianRegimeBreakoutStrategy | donchian_regime_breakout.py | Donchian(20)+EMA(50)-Steigung | 15.0 |
| Rsi2ReversionStrategy | rsi2_reversion.py | RSI(2)+EMA(100) | 15.0 |
| GapContinuationStrategy | gap_continuation.py | Custom Overnight-Gap+ATR | 15.0 |

### Inaktive Strategien (active=false)
| Klasse | Grund |
|--------|-------|
| TrendPullbackStrategy | (Status: Inaktiv / Maintenance) 0 FIFO-Schließungen in allen Tests; erbt von HourlyStrategyBase (EMA-Period 200 initialisiert bei kurzen Daten nie) |
| AdxAtrMomentumStrategy | (Status: Inaktiv / Maintenance) ADX-Initialisierungsproblem; erbt von HourlyStrategyBase |

### Regime-Roster-Erweiterung (Issues #689–#693, Guide #688) — fünf neue Strategien

Fünf neue Strategien füllen dokumentierte Regime-Lücken im Roster (Momentum-Ignition,
Volatilitäts-Expansion, Trend-im-Regime, Kurzfrist-Reversion, Event-Gap) — jede folgt dem
5-Datei-Muster (Strategie-Modul, `strategy_defaults.json`-Block, `strategies.json`-Registrierung,
`spaces.py`-Zweig, optionale `tournament_overrides`) und nutzt ausschließlich die
`HourlyStrategyBase`-Exit-Verwaltung (ATR-Trailing-Stop + ~1-Handelstag-Zeit-Exit).

| Strategie | Issue | Regime | Trockenlauf (echter NautilusTrader-Engine-Lauf, synthetische Daten) |
|---|---|---|---|
| SqueezeBreakoutStrategy | #689 | Volatilitäts-Expansion nach Kontraktion (TTM-Squeeze) | 7 Trades über 8 Kontraktions-/Release-Zyklen (erfordert echte Intrabar-Dochte — siehe Fallstrick unten) |
| OpeningRangeBreakoutStrategy | #690 | Momentum-Ignition am Tagesbeginn (ORB) | 68 Trades über 60 Tage |
| DonchianRegimeBreakoutStrategy | #691 | Trend-Fortsetzung nur im Trend-Regime | 34 Trades über 40 Tage (nach ADX→EMA-Steigungs-Fix, siehe Fallstrick unten) |
| Rsi2ReversionStrategy | #692 | Kurzfrist-Reversion im übergeordneten Trend (Connors-RSI-2) | 59 Trades über 60 Tage (höchste Frequenz der fünf) |
| GapContinuationStrategy | #693 | Overnight-/Event-Gap-Fortsetzung | 11 Trades über 60 Tage (seltenste — `min_trades` in `strategies.json` auf 8 gesenkt) |

**Test-Isolations-Hinweis:** `test_issue_689_squeeze_breakout.py::test_squeeze_release_logic_fires_on_realistic_wick_data` und `test_issue_691_donchian_regime_breakout.py::test_adx_directional_movement_value_is_broken_in_installed_nautilus_version` (beide konstruieren echte `nautilus_trader.model.data.Bar`-Objekte direkt) schlagen NUR im VOLLEN Suite-Lauf fehl (`TypeError: expected Bar, got MagicMock`) — Root-Cause bestätigt: `test_issue_489_embargo_shifts_not_shrinks.py` ersetzt `sys.modules['nautilus_trader.model.data']` (u. a.) modulweit durch `MockModule`-Instanzen und stellt sie nie zurück (bereits als „test_issue_489-sys.modules-Mock-Pollution" in früheren Changelog-Einträgen dokumentiert). Beide Tests sind isoliert grün; dies ist eine zusätzliche, bestätigte Instanz der bereits bekannten, vorbestehenden Suite-weiten Test-Isolationslücke — keine Regression dieser Änderung.

**Fallstrick — `DirectionalMovement.value` liefert konstant `0.0` (Pitfall #9 des Guides, empirisch verifiziert):**
Der Trockenlauf von `DonchianRegimeBreakoutStrategy` mit dem SPEC-Standard-Regime-Filter
(Option A: `adx.value >= adx_threshold`) erzeugte 0 Trades. Ein direkter Indikatortest
(`DirectionalMovement(14)` gegen einen persistenten Trendverlauf) bestätigte: `.value` bleibt
konstant `0.0` in der installierten NautilusTrader-Version (1.230.0), während `.pos`/`.neg`
plausible, trend-abhängige Werte liefern — exakt das bereits dokumentierte
`AdxAtrMomentumStrategy`-„ADX-Initialisierungsproblem" (s. o.). Die Strategie nutzt daher aktiv
den SPEC-vorbereiteten Fallback (Option B: EMA-Steigung, `ema.value > _ema_prev`); Option A bleibt
als auskommentierter Re-Aktivierungspunkt im Modul. `adx_period`/`adx_threshold` wurden
konsequent aus dem `spaces.py`-Suchraum entfernt (sonst Phantom-Tuning toter Parameter).

**Fallstrick — Squeeze-Erkennung erfordert echte Intrabar-Spannen:** `SqueezeBreakoutStrategy`s
Squeeze-Bedingung (`bb.lower > keltner.lower ∧ bb.upper < keltner.upper`) hängt vom Verhältnis
ATR (True Range, nutzt Intrabar-High/Low) zu Close-Preis-Standardabweichung ab. Mit
Close-only-Ticks (ein Tick/Stunde, die Standard-Testfixture-Konvention dieses Repos) entsteht
O=H=L=C, wodurch ATR faktisch zu `|close_t − close_{t-1}|` degeneriert und die Default-Multiplikatoren
(`bb_std_dev=2.0` vs. `keltner_multiplier=1.5`) selten eine echte Squeeze-Bedingung erzeugen. Der
Trockenlauf-Test (`test_issue_689_squeeze_breakout.py`) injiziert daher bewusst 4 Ticks/Stunde
(Open/High/Low/Close), um reale Dochte zu erzeugen — ein Live-Sweep mit echten TSLA-Intraday-Daten
wird dieses Verhältnis natürlich erfüllen (echte Kursdaten haben nie O=H=L=C).

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

### 10.1 Multi-Objective Optimization (Optuna Pareto)
Ab Issue #507 unterstützt das System Multi-Objective Optimization (MOO) als Alternative zum Legacy-Skalar-Reward-Modell.
- **Aktivierung (Feature Toggle):** Gesteuert durch `reward_mode="pareto"` in `automation/config/optimizer.json`.
- **Sampler:** Zwingende Nutzung von `optuna.samplers.NSGAIISampler()` (oder MOTPE).
- **Objectives:**
  | Dimension | Metrik | Optimierungsrichtung |
  | --- | --- | --- |
  | 1 | Return (`oos_total_return`) | Maximize |
  | 2 | Expectancy (`oos_expectancy`) | Maximize |
  | 3 | Win-Rate (`oos_win_rate`) | Maximize |
  | 4 | Sortino (`oos_sortino`) | Maximize |
  | 5 | Drawdown (`oos_max_drawdown`) | Minimize |
  | 6 | Turnover (`oos_total_trades`) | Minimize |
- **Optuna-Constraints:** Anstelle von Strafen-Terms (Penalty-Pfade) werden Hard-Constraints nativ an den Sampler übergeben. Dies erfolgt über eine Evaluierungslogik (in `make_objective`), die Differenzen (z. B. `min_trades - oos_total_trades` und `oos_max_drawdown - max_drawdown`) berechnet und in `trial.user_attrs['constraints']` als Tupel abspeichert. Optuna bewertet Werte <= 0 als valide (Constraint erfüllt).
- **Selektion aus der Pareto-Front:** Wenn Optuna eine Pareto-Front generiert (MOO), evaluiert das System (`confirm.py`) iterativ alle nicht-dominierten Kandidaten der Front (`study.best_trials`) auf dem ungesehenen Holdout. Die letztendliche Selektion und der Vergleich gegen die `promotion_margin` der globalen Baseline basieren weiterhin strikt auf dem skalaren `compute_reward` (zur Auflösung der Pareto-Gleichwertigkeit).

`backtest_runner.py` läuft als Subprocess. Multiprocessing via `ProcessPoolExecutor(max_workers=max(1, min(cpu//2, 6)))`, `max_tasks_per_child=1` (Python ≥ 3.11), expliziter `BrokenProcessPool`-Catch mit sequenziellem Fallback.

Bei der Umwandlung von Candle zu Tick wird im Backtest nun Zero-Spread-Modeling (bid=ask=close, Buy@Ask=Sell@Bid=Close) genutzt. Die Live-Ticks im `catalog_service` behalten allerdings den realen Spread.

**Engine-Setup pro Job:** `OmsType.NETTING`, `AccountType.MARGIN`, Spread-Modeling (Buy@Ask, Sell@Bid — NautilusTrader-Default mit QuoteTicks). Mock-Instrument via `create_mock_instrument()` als `Cfd(asset_class=EQUITY)`.

**Metriken** (`extract_metrics`): FIFO-Matching über `generate_fills_report()` (Fallback `generate_order_fills_report()`). Die FIFO-Teilfüllungen werden zu **Round-Trips** (ökonomischen Positionen, Position-Open → Flat) aggregiert; die primären Metriken (`metrics`/`oos_metrics`, gespiegelt unter `round_trips`) werden strikt auf Round-Trip-Ebene gebildet (Issue #508, siehe Accounting-Standard unten). Sortino nur ab n ≥ `sortino_min_trades` Round-Trips (deklarativ in `tournament.json`, Default 5, ausgeliefert mit 2 — Issue #401; vormals hartcodiert `n < 5`). Tournament-Selektion via `select_winners()`.

**Accounting-Standard: `fill_matches` vs. `round_trips` (Issue #508, HARTES AXIOM).** `extract_metrics` weist zwei streng getrennte Aggregationsebenen aus:

| Ebene | Bedeutung | Rolle |
| --- | --- | --- |
| `round_trips` (== `metrics`/`oos_metrics`) | **Ökonomische Positionen** — eine Gruppe von FIFO-Matches von Position-Open bis Flat (Net-Exposure-Zero-Crossing / Position-ID-Reset). Round-Trip-PnL = Summe aller Teil-Fill-PnLs (inkl. bereits allokierter Kosten) VOR der Win/Loss-Evaluierung. | **Primär.** EINZIGE Basis für Gate-Eligibility und Walk-Forward-Validierung. |
| `fill_matches` | **Technische Teilfüllungen** — jeder einzelne FIFO-Match (Scale-in/Scale-out-Legs). | **Sekundär.** Reine Execution-Diagnostik. NIE Gate-relevant. |

Kernmetriken auf Round-Trip-Ebene: `total_trades` == n_positions, `win_rate` == Wins / n_positions, `expectancy` == Σ PnL_positions / n_positions, `profit_factor` analog. Die proportionale Kostenallokation pro Leg (`extract_metrics`, Commission-Zeilen im FIFO-Loop) bleibt unverändert — aggregiert wird NACH der Kostenverrechnung.

**Gate-Conditions-Regel (Issue #508, nicht verhandelbar):** Sämtliche Eligibility-Evaluierungen (`_is_eligible`, `_evaluate_oos_eligibility`, `select_winners`, `parse_tournament`, Reward/Gate im `optimizer/`) MÜSSEN auf Round-Trip-Datenstrukturen (`metrics`/`oos_metrics`/`round_trips`) operieren — NIE auf `fill_matches`. Grund: FIFO-Match-Level-Aggregation inflationiert bei Scale-in/Scale-out den Trade-Count und verzerrt `win_rate`/`expectancy`/`PF`. Ein PR, der eine Gate-/Reward-Metrik auf `fill_matches` umleitet, führt die bekannte Verzerrungsklasse zurück und ist abzulehnen. Strukturelle Inversionen wie `oos_total_trades > is_total_trades` bei zeitlich kürzerem OOS-Fenster sind mit korrekter Round-Trip-Zählung mathematisch ausgeschlossen.

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

### 11.1 Die Deployment-Grenze (Issue #993, P0-blocking)

Das Repository enthält **zwei** voneinander unabhängige Verfahren, die eine (Strategie, Symbol)-Kombination bewerten. Wer an einem von beiden arbeitet, muss wissen, dass das andere existiert:

| | **System 1 — Optimizer-Sweep** | **System 2 — Phase-4-Turnier** |
|---|---|---|
| Einstieg | `python -m automation.optimizer.sweep` | `daily_orchestrator.py` Phase 3/4 |
| Kernmodule | `run_optimization.py`, `confirm.py`, `deflation.py` | `backtest_runner.py` (Matrix-Modus) |
| Ergebnis | `data/optimizer/proposal_{strategy}_{symbol}.json`, Champion-Store | `tournament_result` mit `aggregate_winner`, `per_symbol_winners` |
| Multiplizitätskorrektur | **ja** — DSR gegen `deflation_confidence`, PBO/CSCV | **nein** |
| Holdout | **ja** — separates Fenster, `confirm.py` | nein |
| Statusbegriffe | `READY_FOR_PR`, `REJECTED_ON_HOLDOUT`, `REJECTED_SELECTION_OVERFIT`, … | `oos_eligible`, `oos_evaluated` |

**Regel:** `oos_eligible` (Phase-4-Turnier) ist das Ergebnis eines **Einzelfenster-Gates ohne Multiplizitätskorrektur**. Es ist eine notwendige, niemals eine hinreichende Bedingung für Kapitaleinsatz.

**Was `whitelist_tournament.json` garantiert — und was nicht.** Issue #310 (2026-06-09, siehe Changelog-Tabelle) hat die Whitelist eingeführt und sie als „einzige Source of Truth für das tatsächliche Deployment" bezeichnet. Diese Formulierung beschreibt die **Zuständigkeit**, nicht die **Prüftiefe**. Bis Issue #993 filterte die Whitelist per-Symbol-Verlierer ausschliesslich gegen `oos_eligible ∧ oos_evaluated` (System 2) — DSR, PBO, Holdout-Bootstrap-CI, Boundary-Veto, R_symbol > R_global und Datenstand-Kohärenz (System 1) wurden **nicht** konsultiert.

**Seit Issue #993** ist `automation/optimizer/deployment_gate.py`s `evaluate_deployment_eligibility()` die einzige zulässige Quelle einer Deployment-Entscheidung. Ein (Strategie, Symbol)-Paar ist deploy-fähig genau dann, wenn **alle acht** Klauseln `True` sind (Konjunktion, kein Ersatzpfad):

```
promotion_record_exists  — ein Promotionsrecord (System 1) existiert für dieses Paar
status_ready_for_pr      — dessen status == "READY_FOR_PR"
dsr                      — deflated_dsr >= deflation_confidence (tournament.json, Default 0.95)
psr                      — oos_psr >= oos_min_psr (tournament.json, Default 0.75)
pbo                      — PBO <= 0.5 ODER (PBO nicht schätzbar UND Config-Kohorte < pbo_min_configs)
bootstrap_ci             — ci_lower(holdout_sortino) > 0
r_edge                   — R_symbol > R_global (oder R_global undefiniert ⇒ trivial erfüllt)
snapshot_drift           — data_snapshot_sha256(Promotion) == data_snapshot_sha256(Deployment)
```

**Fail-closed:** Fehlt eine der Grössen (`None`), gilt die zugehörige Klausel als **nicht erfüllt** — `None` ist keine bestandene Prüfung (Pitfall #237 auf die Deployment-Ebene übertragen). `daily_orchestrator.phase5_live_deployment` liest die Promotionsrecords ausschliesslich aus `data/optimizer/proposal_{strategy}_{symbol}.json` (`deployment_gate.load_promotion_records`) — **nicht** mehr aus `tournament_result`. Existiert für ein Symbol kein Promotionsrecord, ist es nicht deploy-fähig, auch wenn es Phase-4-`per_symbol_winner` ist. Die blockierende Invariante `invariants.check_deployment_gate_completeness` läuft direkt nach der Whitelist-Generierung und bricht mit Exit-Code 1 ab, falls ein Whitelist-Eintrag kein vollständiges `clause_results`-Dict trägt.

**Arbeitsregel für alle künftigen Änderungen:** Jede Änderung, welche die Promotionsrate erhöhen kann, muss vor dem Merge nachweisen, dass die Deployment-Grenze (`deployment_gate.evaluate_deployment_eligibility`) unverändert vollständig prüft. Ein Fix, der die Kandidatenzahl erhöht, ohne dass die Grenze vollständig bleibt, erhöht das Kapitalrisiko stärker als den erwarteten Ertrag.

### 11.2 Live-Kapitalallokation, Circuit-Breaker (Issue #999, P0-blocking)

`automation/momentum_ls_allocator.py`s `MomentumLSAllocator.get_allocation` verwendete vor Issue #999 `account_balance / pending_signals` (`pending_signals` = Zahl der Universumssymbole ohne offene Position). Bei `n` Symbolen und `k` bereits offenen Positionen erhielt das `(k+1)`-te Signal `a_{k+1} = B_k/(n−k)` — für `k → n−1` also das **gesamte** verfügbare Kapital (`a_n = B_{n−1}/1`). Die Allokationsfolge war damit monoton **steigend** in der Zahl bereits eingegangener Risiken — das exakte Gegenteil einer Risikobudgetierung, und bei einem `MARGIN`-Konto ohne Wartungsmargin ein realer Ruin-Pfad.

**Seit Issue #999** gilt eine Budget-Formel mit Erhaltungsbedingung:

```
w_neu = min( max_symbol_exposure_fraction , max_total_exposure_fraction − Σ_offen w_i ) · ψ(DD)
ψ(DD) = max(psi_min, 1 − DD_current/dd_halt_fraction)
```

`Σ_offen w_i` wird primär aus dem Entry-Notional (`|quantity| · avg_px_open`) der offenen Positionen geschätzt; ist das für irgendeine Position nicht bestimmbar, fällt die Schätzung auf eine konservative Slot-Näherung zurück (jede offene Position beansprucht ihr volles Symbol-Cap) — beide Pfade bleiben monoton **fallend** in der Zahl offener Positionen. Konfiguriert in `backtest.json["live_risk"]` (`max_total_exposure_fraction` Default 0.60, `max_symbol_exposure_fraction` Default 0.10, `dd_halt_fraction` Default 0.10, `psi_min` Default 0.2).

**Live-Circuit-Breaker** (`automation/live_risk.py`, `LiveCircuitBreakerWatchdog`): ein Hintergrund-Thread pollt periodisch die Node-Equity (`node.portfolio.equity`) und trippt bei einem von zwei ODER-verknüpften Auslösern:
- **Auslöser A (absolut, fail-closed):** `DD_live(t) = 1 − E(t)/max_τ E(τ) >= dd_halt_fraction`.
- **Auslöser B (Verteilung, fail-open solange `n_live < circuit_breaker_n_min_periods`):** die live beobachtete mittlere Rendite liegt signifikant unter der Backtest-Erwartung (Standardfehler-skalierter Ein-Stichproben-z-Test, Schwelle `distribution_z_halt`).

Ein Trip flattet alle Strategien (`node.trader.market_exit_strategy` je `strategy_id`, via `call_soon_threadsafe` auf den Event-Loop des Nodes geschoben — der Watchdog läuft in einem separaten Python-Thread) und stoppt den Node; `momentum_ls_run.py` beendet sich danach mit Exit-Code 3. Getrippt blockiert `MomentumLSAllocator.get_allocation` zusätzlich sofort jede neue Allokation (`update_risk_state(tripped=True)`), unabhängig vom Rest der Formel. Telemetrie: `LIVE_EXPOSURE_SNAPSHOT` (jede Allokationsentscheidung) und `LIVE_CIRCUIT_BREAKER_TRIPPED` (bei einem Trip); die Invariante `invariants.check_live_exposure_budget` prüft `Σ w_i <= max_total_exposure_fraction + 1e-9` gegen aufgezeichnete Snapshots.

**Nicht Teil von Issue #999 in dieser Session:** die Reconciliation-Vereinheitlichung (GH #800, „gibt über denselben Kanal einmal eine Zielgrösse und einmal ein Delta zurück") wurde als Vorschlag verworfen; die bereits vorhandene Reconciliation-Infrastruktur (`etoro_execution._reconcile_positions_on_connect`/`_reconcile_via_pnl`, `HourlyStrategyBase._reconcile_after_reconnect`) blieb unverändert. Die kostenabgeleitete Spread-Untergrenze (`S_max = max(tick_floor_bps, α·ATR₁₄)`) ist **nicht** umgesetzt — ihre Prämisse (ein bereits existierender `tick_floor_bps`/`check_cost_model_floor`-Mechanismus) traf beim Verifizieren gegen `main` nicht zu (Pitfall #333/#see unten) und gehört zu Issue #998 (nicht in dieser Session bearbeitet).

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

## 13.5 Reproducibility & Determinism

Der Optimizer und insbesondere der Sweep garantieren **strikte Reproduzierbarkeit**, wenn ein `seed` in `automation/config/optimizer.json` definiert ist.

* **Determinism Contract**: `Identischer Seed + Identische Daten/Flags = 100% identische best_value Sequenz`.
* **Study-Level vs. Sweep-Level Concurrency**:
    * **Study-Level**: Das Sampling durch Optunas TPE-Sampler innerhalb EINER SQLite-basierten Study *muss* stets sequenziell erfolgen. Darum erzwingt `optimize_symbol` intern immer `n_jobs=1`.
    * **Sweep-Level**: Das parallele Dispatching *unterschiedlicher* Studies (Paare von Strategie und Symbol) via `--n-jobs` an den Meta-Orchestrator (in `sweep.py`).
* **Determinism Guard (Issue #511)**: Um die Reproduzierbarkeit der Optuna-Sampling-Reihenfolge nicht durch die unvorhersehbare Trial-Completion-Order paralleler Worker zu zerstören (Concurrency Leak), wird die Sweep-Level Concurrency (`--n-jobs`) strikt auf `1` gezwungen, sobald ein `seed` konfiguriert ist. In diesem Fall wird der Wert der Telemetrie-Eigenschaft `n_jobs_source` auf `"ENFORCED_BY_SEED"` gesetzt.

## 14. Error-Handling-Konventionen

- WebSocket-Fehler → `os._exit(1)` (systemd-Restart). KEINE In-Process-Reconnection.
- Instrument nicht im Cache → log + `return None` aus `_compute_quantity()`.
- Immer `if qty is None: return` nach `_compute_quantity()`.
- Niemals `raise` in `on_bar()`/`on_quote_tick()`.
- Beim Auslösen von JSON-Execution-Events MUSS `emit_execution_event` aus `automation.log_manager` verwendet werden, wie in Pitfall #63 beschrieben. Lazy Imports (z.B. in Workern) müssen zudem explizit durch Execution-Tests abgedeckt sein.
- HTTP 429 → `Retry-After` respektieren; Timeout → Retry mit Backoff.
- Worker-Crash im Backtest → `BrokenProcessPool`-Catch → sequenzieller Fallback.

### 14.1 Walk-Forward-Daten-Suffizienz — projektweite Invarianten (Issue #531)

Diese drei Invarianten sind **hart** und dürfen von keinem Walk-Forward-Modul aufgeweicht werden:

1. **Strict Walk-Forward Geometry Check.** Vor jeder Walk-Forward-Auswertung MUSS die Formel
   `actual_span_days >= is_window_days + splits · oos_window_days + holdout_days` (= 405 Tage bei
   der Produktions-Geometrie 180/45/4/45) gegen die **real vorhandene** Bar-Spanne der Rohdaten
   evaluiert werden — **niemals** gegen den Config-Wert `data_history_days`. Single Source of Truth
   der Formel: `automation.optimizer.gate.required_span_days` (bewusst OHNE `embargo_period_days`
   und OHNE `gate1_buffer_days`; der Puffer ist die Backfill-Schwelle, nicht der Fail-Loud-Floor).
   Der reine Guard ist `automation.optimizer.gate.assert_walk_forward_geometry`.
2. **No-Clamping Policy.** Stillschweigendes Zurechtschneiden von Time-Series-Slices via `.loc`
   (`mtm_series.loc[start:end]`) bei Out-of-Bound-Geometrien ist **strikt verboten** — ein
   verkürzter/leerer letzter OOS-Fold oder Holdout verzerrt die Walk-Forward-Aggregation lautlos.
   Greift die Geometrie über den Datenrand, gilt das **Fail-Loud-Paradigma**: deterministischer
   Abbruch (`InsufficientGeometryError`) ODER präventiver Backfill (siehe unten), nie ein Clamp.
3. **Error Taxonomy.** Der zugehörige Fehlercode ist `REJECT_DATA_INSUFFICIENT_GEOMETRY`
   (`gate.InsufficientGeometryError.code`). Jeder Abbruch MUSS das strukturierte Telemetry-Event
   `GATE_1_REJECTION` über `automation.log_manager.emit_gate1_rejection` emittieren; die Payload
   exponiert die Diskrepanz explizit:
   ```json
   {"event": "GATE_1_REJECTION", "error_category": "REJECT_DATA_INSUFFICIENT_GEOMETRY",
    "available_days": <float>, "required_days": <float>, "delta_days": <float>}
   ```
   Verdrahtung: `sweep.enumerate_tunable_pairs` (Gate-1, macht INSUFFICIENT_HISTORY sichtbar),
   `trial_config.build_trial` (Fail-Loud gegen die durchgereichte `catalog_span_days`) und der
   Pre-Sweep-Backfill `historical_fetcher.ensure_walkforward_history` (synchroner Nachlade-Request
   an den `historical_fetcher`, sobald die reale Spanne `required_span_days + gate1_buffer_days`
   unterschreitet, z. B. < 435 Tage). Reihenfolge im Sweep: **erst Backfill, dann fail-loud** —
   ein Symbol wird nur verworfen, wenn auch nach dem Nachladen zu wenig Historie vorliegt.

---

## 15. Code-Style & Conventions

- Code: Englisch. Log-Messages: Deutsch. Kommentare: Deutsch in strategies/, Englisch akzeptabel in dev-nahen Skripten.
- Type-Hints: `str | None` (Python 3.10+).
- `StrategyConfig`-Subklassen: `frozen=True` PFLICHT.
- Logging immer `logging.getLogger(__name__)` bzw. `self._log`, nie `print()` (Ausnahme: backtest_runner DualLogger).
- Async: `asyncio.sleep`, nie `time.sleep`; `asyncio.wait_for(..., timeout=...)` für externe Calls.

---

## 16. Bekannte Pitfalls & offene Bugs

> **Nummerierungs-Konvention (verbindlich für jeden Agenten, der einen neuen Pitfall dokumentiert):**
> Pitfall-Nummern sind **global eindeutig über die gesamte Datei** — nicht nur innerhalb von §16, denn
> spätere Kataloge (`## Bug-Kaskade …`, `## Issue-Katalog …`) hängen ihre Pitfalls chronologisch ans
> Dateiende an, ausserhalb dieses Kapitels. Vor dem Vergeben einer neuen Nummer **immer**
> `grep -noE "Pitfall #[0-9]+" automation/AGENTS.md | sort -t'#' -k2 -n | tail -5` laufen lassen und die
> nächste freie Nummer nehmen — niemals eine Nummer aus dem Gedächtnis/der letzten Session raten. Die
> höchste zum Zeitpunkt dieser Session (2026-07-18, Epic #702) vergebene Nummer ist **#205**. Historische
> Kollisionen (drei verschiedene Pitfalls trugen `#89`, zwei `#93`) wurden auf `#89`/`#93` (kanonisch,
> per Cross-Referenz aus Axiom A9 bzw. der eigenen Einleitung identifiziert) sowie `#198`–`#200`
> (umnummeriert) aufgelöst — siehe Changelog 2026-07-18.

### 🟢 Pitfall #66 — Silent Worker Crash Swallowing (Fail-Fast vs Resilience, Issue #355)
**Symptom:** Der Orchestrator meldet "[Phase 3] Backtest beendet (Exit-Code: 0)" und startet das Live-Deployment, obwohl im Hintergrund Worker-Prozesse aufgrund fundamentaler Python-Fehler (z.B. `ImportError`) abgestürzt sind.
**Root Cause:** In `automation/backtest_runner.py` fing eine pauschale `try/except Exception`-Resilience-Schleife während der Future-Auswertung (`future.result()`) alle Fehler stumm ab, um marktbedingte Einzelfehler abzufangen. Dadurch wurde ein systemischer Fehler maskiert, was zu einer leeren Metrik und einem unberechtigten Exit-Code 0 führte.
**Regel:** KI-Agenten dürfen niemals globale `try...except Exception:` Blöcke um die Multiprocessing-Worker oder Core-Logik legen. Marktdaten-Fehler oder fehlende Ticks dürfen ignoriert werden (Resilience). Fundamentale Code-Fehler (Imports, Syntax, Typisierung) müssen zwingend zu einem sofortigen Crash (`sys.exit(1)`) führen (Fail-Fast), um toxische Live-Deployments in Phase 5 zu verhindern. Es ist außerdem essenziell, vor dem Exit den `ProcessPoolExecutor` ordnungsgemäß herunterzufahren (`executor.shutdown(wait=False, cancel_futures=True)`), um Zombie-Prozesse zu vermeiden. Phase 5 (Live-Trading) darf nur nach einem validen Exit-Code `0` aus Phase 3 (Backtesting/Tournament) angetriggert werden.
**Betroffen:** `automation/backtest_runner.py`

### Optimizer / `backtest_runner.py` — Config-Contract

- `backtest_runner.py` liest Strategien + Parameter **ausschließlich** aus der via `--config` übergebenen Manifest-Datei. `strategies[].params` sind vollständig aufgelöst und autoritativ; **kein** erneutes Mergen aus `strategy_defaults.json`, sobald `manifest_version` gesetzt ist.
- **Self-describing Manifest (ISSUE-OPT-374):** Die `global_settings` des Manifests tragen zusätzlich `walk_forward` (`is_window_days`, `oos_window_days`, `splits == n_folds`, `holdout_days`) **und** `start_capital`. `backtest_runner.py` liest Walk-Forward-Geometrie und Start-Kapital **autoritativ aus dem Manifest** (`global_settings`); fehlen sie (Legacy/Direkt-Lauf), fällt es auf die trial-lokale `backtest.json` zurück. Der Startup-Header loggt die effektive Quelle (`Walk-Forward Quelle: manifest|backtest.json`). Damit hängt die IS/OOS-Aufteilung nicht mehr an einem Side-Channel — Manifest und Sizing/Splitting sind deckungsgleich.
- **Korridor-Geometrie vs. 12-Monats-History (ISSUE-OPT-374 / Issue #531):** Der benötigte Korridor = `is_window_days + n_folds·oos_window_days + holdout_days` = `180 + 4·45 + 45` = **405 Tage** vor heute (Single Source of Truth: `gate.required_span_days`). `historical_fetcher --months 12` liefert ~365 Tage ⇒ am frühen Rand fehlen ~40 Tage; die frühesten Folds können datenarm sein. **Issue #531 — Fail-Loud statt stiller Klemmung:** Unterschreitet die **real** vorhandene Bar-Spanne die 405-Tage-Geometrie, ist ein stiller `.loc`-Clamp des letzten OOS-Folds/Holdouts VERBOTEN (No-Clamping-Policy, §14.1). Gate-1 (`sweep`/`build_trial`) validiert gegen die **tatsächliche** Bar-Spanne — nicht gegen `data_history_days` — und bricht mit `REJECT_DATA_INSUFFICIENT_GEOMETRY` fail-loud ab bzw. lädt via `historical_fetcher.ensure_walkforward_history` präventiv nach, sobald `required_span_days + gate1_buffer_days` (≈ 435 Tage) unterschritten wird. Der `span_tolerance_days`-Guard bleibt nur für **knappe** Near-Miss-Fälle (z. B. 402 statt 405) innerhalb der IS/OOS-Toleranz; die 45-Tage-Holdout-Lücke des Ist-Bugs (360 < 405) fällt NICHT mehr darunter. Wer alle Folds voll abdecken will, erhöht die Beschaffungstiefe (z. B. `historical_fetcher --months 14`).

### 🟢 Issue #531 — Datenfenster < Walk-Forward-Geometrie (Silent Truncation)
**Symptom:** `data_window_days = 360.0` (< 405), der Sweep läuft dennoch durch; letzter OOS-Fold und Holdout liegen jenseits der Daten und werden via `.loc` still eingekürzt ⇒ verkürzte/leere Fold-Fenster, verzerrte Walk-Forward-Aggregation.
**Root Cause:** Gate-1 validierte gegen den **konfigurierten** `data_history_days` (450), nicht gegen die **real** vorhandene Bar-Spanne (360). Die geforderte Geometrie `is_window + splits·oos + holdout` = 405 Tage wurde nie gegen die tatsächliche Datenlage geprüft; pandas-`.loc`-Slicing klemmte Out-of-Bound-Fenster lautlos.
**Fix/Regel:** Siehe §14.1 (harte Invarianten). Gate-1 prüft die **reale** Spanne (`gate.assert_walk_forward_geometry`), bricht mit `REJECT_DATA_INSUFFICIENT_GEOMETRY` fail-loud ab (`emit_gate1_rejection` → `GATE_1_REJECTION` mit `available_days`/`required_days`/`delta_days`) und lädt via `historical_fetcher.ensure_walkforward_history` präventiv nach, sobald `required_span_days + gate1_buffer_days` (≈ 435 Tage) unterschritten wird. **Kein** stilles `.loc`-Clamping mehr (No-Clamping-Policy).
**Betroffen:** `automation/optimizer/gate.py`, `automation/optimizer/trial_config.py`, `automation/optimizer/sweep.py`, `automation/historical_fetcher.py`, `automation/log_manager.py`

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
- **Round-Trip-Aggregierung ist die Gate-Basis (Issue #508):** Die primären Metriken (`metrics`/`oos_metrics`/`round_trips`) werden auf Round-Trip-Ebene (Position-Open → Flat) gebildet; `fill_matches` ist reine Execution-Diagnostik und darf NIE eine Gate-/Reward-/Selektions-Entscheidung speisen (siehe Accounting-Standard in §10). Die Round-Trip-Gruppierung in der FIFO-Schleife (`_finalize_round_trip`, Finalisierung bei leerer Gegenseite/Flat) und die per-Leg-Kostenallokation sind gekoppelt: Wer die Match-/Kosten-Mathematik ändert, MUSS die Invariante `Σ Round-Trip-PnL == Σ Fill-Match-PnL` erhalten (durch `test_extract_metrics_scale_out_round_trip_is_one_trade` und die PnL-Konservierung abgesichert). Neu eingeführte Metrik-/Record-Dicts sind via `typing` zu annotieren (`FillMatchRecord`, `RoundTripRecord`, `MetricsLevel`).
- **Tupel-Arity Koppelung:** Erzeugung (`pnls_with_ts.append(...)`, `rt_pnls_with_ts.append(...)`) und Konsum (`for ... in pnls_with_ts` / `... in rt_pnls_with_ts`) der Trade-Tupel sind als gekoppeltes Paar zu behandeln: Ändert sich die Arity der erzeugten Tupel, MUSS die Entpackung im selben Commit angepasst werden. Die OOS-Trade-Records (`_oos_trade_records`, für die Portfolio-Aggregation in `select_winners`) bleiben Round-Trip-basiert mit Arity `(pnl, ts, ht, qty, notional)`.
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
- **Granulare Rejection-Observability (Issue #453):** Der Catch-All `oos_not_evaluated` wird in dezidierte, aggregierbare Kategorien aufgelöst (`run_optimization._classify_is_rejection_detail` / `_map_oos_reason`): `REJECT_OOS_WINDOW_UNREACHABLE` (datenseitig, #455) vs. `REJECT_OOS_INACTIVE` (strategieseitig, OOS=0) vs. `REJECT_OOS_DISCARDED_BY_IS_GATE` (OOS > 0, aber präventiv vom IS-Gate verworfen, #493) vs. konkretes Gate (`REJECT_OOS_MAX_DRAWDOWN`, `REJECT_OOS_MIN_TRADES`, …). Pro Trial als `is_rejection_detail`-User-Attr persistieren, ins Event heben, modal ins Proposal (`confirm._dominant_is_rejection_detail` → `proposal["is_rejection_detail"]`) schreiben. Reine Observability — ändert KEINE Entscheidung. Hinweis: Die Proposal-Serialisierung liegt in `confirm.export_symbol_proposal` (NICHT in `_serde.py`, das ausschließlich die Nautilus-FSB16-Encodierung kapselt).

---

## AUDIT #470 — Verifizierte mathematische Invarianten (#460–#469): HARTE AXIOME

> **Status:** Forensik-Audit #470 hat die Root-Cause-Fixes #460–#469 gegen Code, Git-Historie und
> die ausgeführte Test-Suite verifiziert. Die folgenden Invarianten sind **nicht verhandelbar**.
> Ein Agent, der eine davon aufweicht, führt eine bekannte, teuer reproduzierte Fehlklasse zurück
> in die Optimierungs-Pipeline. **Jede Änderung an `reward.py`, `gate.py`, `backtest_runner.py`
> (Metriken/Splits), `trial_config.py`, `confirm.py` oder den `config/*.json`-Gates MUSS diese
> Axiome erhalten und die genannten Guard-Tests grün halten.**

### Nummerierungs-Abgleich (Issue-Nummer ↔ Test-Datei ↔ Pitfall)

Die Test-Dateinamen liegen **+1** über der realen GitHub-Issue-Nummer (Konvention des Issue-Autors).
Maßgeblich für Agenten ist die **Test-Datei** (= die Audit-#470-Nummerierung):

| Audit-/Test-Nr. | Thema | reale GH-Issue | Guard-Test |
| -- | -- | -- | -- |
| #460 | Holdout-Starvation / Anchor-Clamp | #460 | `test_issue_460_holdout_reachability.py` |
| #461 | Reward-Inversion (bounded penalty) | #461 | `test_issue_461_reward_no_inversion.py` |
| #462 | Gate-1 Holdout-Reachability-Preflight | #462 | `test_issue_462_gate_holdout_reach.py` |
| #463 | Telemetry/Anchor-Divergenz | #463 | `test_issue_463_anchor_invariant.py` |
| #464 | Sortino-Dimensionalität (√Perioden) | #464 | `test_issue_464_sortino_dimension.py` |
| #466 | Total-Return-Compounding (MtM) | **#465** | `test_issue_466_portfolio_return.py` |
| #467 | Fold-Geometrie (rolling, kein Single-Split) | **#466** | `test_issue_467_fold_geometry.py` |
| #468 | OOS-Gate-Konditionierung (Penalty-Skala) | **#467** | `test_issue_468_penalty_conditioning.py` |
| #469 | Reward-Semantics-Versionsguard (fail-loud) | **#468** | `test_issue_469_semantics_guard.py` |

### Axiom A1 (#460) — `now` ist an den Katalog gebunden, nie an die nackte Wanduhr
`compute_walk_forward_window(..., catalog_newest_ns=…)` clampt `end = min(midnight(now), catalog_end)`
VOR Wochenend-Rollback und Holdout-Abzug. Per-Symbol-Pfade (`optimize_symbol`, `confirm_per_symbol_promotion`,
`_holdout_metrics_for_params`) MÜSSEN `catalog_newest_ns` durchreichen. **Verbot:** kein Fenster-`end`
aus `now` ohne Clamp; kein Holdout-Slice, dessen OOS-Startgrenze hinter dem jüngsten Tick liegt
(`confirm` muss `REJECT_HOLDOUT_UNREACHABLE` fail-loud zurückgeben). Leerer Holdout = **Daten**-Befund,
nie Strategie-Befund. (Pitfall #85.)

### Axiom A2 (#461) — Constraint-Failure-Reward ist nach unten beschränkt
Strikte Ordnung: `eligible ≥ -sortino_clip_abs > near-miss > far-miss ≥ unevaluable_ceiling (−9.75) > unevaluable_shaping_band`. Die Decke für Failures ist nun `-sortino_clip_abs - epsilon`, was TPE einen vollen Dynamikbereich gewährt, ohne die Anti-Gaming-Invariante zu brechen. **Zwei Invarianten gelten gemeinsam:**
(1) `failure < evaluable_floor` (kein Gate-Gaming) UND (2) `failure ≥ bestes-unevaluable` ⇒ „mehr
OOS-Information macht einen Trial NIE strikt schlechter" (keine Inversion, TPE flieht OOS-Aktivität nicht).
**Verbot:** keine unbeschränkte Penalty, die unter `unevaluable_ceiling` durchschlägt.
(Pitfall #86, Pitfall #97; Guard: `test_issue_461_reward_no_inversion.py`.)

### Axiom A3 (#462) — Preflight prüft Holdout-Erreichbarkeit, nicht nur Fold-0
`gate.data_reaches_holdout_window` + `sweep.compute_holdout_window_reach_target_ns` überspringen ein Symbol
VOR dem Sweep, wenn der jüngste Tick die geforderte Holdout-Coverage-Grenze nicht erreicht. Fail-open bei
fehlender Telemetrie. **Verbot:** Preflight, das ausschließlich `start_ns + is_window` (Fold-0-OOS) prüft.
(Pitfall #87.)

### Axiom A4 (#463) — `oos_covered` ist notwendig, nicht hinreichend; Boundaries sind SSOT
Die Invariante `oos_covered ∧ (fill_ts_max ∈ OOS-Union) ⇒ oos_total_trades ≥ 1` wird in `parse_tournament`
geprüft; jede Verletzung setzt `oos_anchor_divergence=True`. **Alle** Split-Boundaries leiten sich aus dem
durchgereichten `start_ns` ab (siehe Axiom A7). **Verbot:** dynamisches Re-Anchoring der OOS-Grenzen aus
Runtime-Ticks (`_first_tick_ns`/`_last_tick_ns`); aus `oos_covered` auf OOS-Trades schließen.
(Pitfall #87-Klasse / „ARCHITECTURAL INVARIANT: Walk-Forward Boundary Anchoring"; Guards:
`test_issue_463_anchor_invariant.py` inkl. AST-Gate gegen `_first_tick_ns`.)

### Axiom A5 (#464/#465/#466) — Dimensionale & Equity-Konsistenz der Risiko-/Return-Kennzahlen
Risiko-/Return-Metriken, die ins **OOS-Gate UND** in den **Reward** fließen, MÜSSEN aus einer
**zeitindizierten MtM-Equity-Kurve** (`PortfolioMonitor.get_equity_series`) abgeleitet werden:
- **Sortino (#464):** `mean(period_rets)/downside_std · √(Perioden/Jahr)`, wobei `period_rets`
  ZEITBASIERTE Returns der Equity-Kurve (`pct_change`) sind und der Annualisierungsfaktor dynamisch aus
  dem realen Zeitspann stammt (`len(period_rets)/span_years`). **`√252` (oder jede Konstante) darf NIEMALS
  auf Trade-sequentielle Per-Trade-Returns angewandt werden.**
- **Drawdown/Calmar (#465):** aus der MtM-Equity-Kurve (`cummax`-Drawdown), nie aus der Trade-geordneten
  realisierten PnL (sonst Intra-Trade-/Floating-Drawdown blind, exit-sortierungsabhängig).
- **Total Return (#466, reale Issue #465):** `equity_end/equity_start − 1` aus der MtM-Kurve, NICHT
  sequentielles Aufzinsen `Π(1 + pnl_i/C0)` (unterstellt 100 % Kapitaleinsatz je Trade ⇒ verzerrt Gate
  und — via #461 — die dominante Penalty bei paralleler/fraktionaler Allokation).

Der `_calculate_stats`-Fallback OHNE `mtm_series` (Trade-geordnete PnL + `√252`) ist ein **reiner Legacy-/
Direkt-Unit-Pfad** und darf NIE die OOS-Gate-/Reward-Metriken speisen. **Bekannte Restgrenze (dokumentiert,
nicht aufzuweichen):** Die *Multi-Symbol-Aggregat*-Pfade in `select_winners` (`portfolio_metrics`,
Aggregat-per-Fold) besitzen noch KEINE rekonstruierte kombinierte Equity-Kurve und nutzen den Trade-PnL-
Fallback; der **per-Symbol-Sweep** (`single_symbol_oos`, der #460–#469-Fokus) ist MtM-konform. Wer den
Aggregat-Pfad ins Gate hebt, MUSS zuerst eine summierte zeitindizierte Equity-Kurve rekonstruieren.
**Re-Kalibrierungspflicht:** Eine Umstellung der `total_return`-Semantik erfordert eine Neu-Kalibrierung
von `oos_min_total_return` + Bump von `reward_semantics_version`. (Pitfall #88; Guards:
`test_issue_464_sortino_dimension.py`, `test_issue_465_mtm_drawdown.py`, `test_issue_466_portfolio_return.py`.)

**#529 — Kompoundierter `total_return`-Fallback statt `0.0`-Artefakt (verschärft Pitfall #88):**
- **Restriktion:** Fallbacks dürfen OOS-Gate-Metriken NIE speisen, sofern eine valide Equity-Kurve
  vorhanden ist. Die MtM-Priorität aus Axiom A5 bleibt unangetastet (`equity_end/equity_start − 1`).
- **Scope:** Nach Fix #521 sind Equity-Kurven regulär verfügbar; der Fallback greift daher **exklusiv**
  bei echt-leeren/spärlichen Slices an Fold-Rändern (Edge-Case: 1 Datenpunkt, `len(mtm_series) ≤ 1`
  oder `equity_start == 0`).
- **Rationale:** In diesem Fallback ersetzt der kompoundierte Wert `Π(1 + pnl_i/C0) − 1` **zwingend** die
  frühere `total_return = 0.0`-Zuweisung. Die `0.0`-Zuweisung erzeugte Zero-Return-Artefakte, die das
  OOS-Gate fälschlich als „Breakeven" interpretierte und die TPE-Signale punktuell verzerrte (siehe #521).
  Der `_calculate_stats`-Docstring ist an dieses kompoundierte Verhalten angeglichen.
- **Guard:** `test_issue_466_portfolio_return.py` (Fallback-Pfad-Tests + MtM-Pfad strikt unverändert),
  `test_issue_528_return_pnl_coherence.py` (Σ pnl < 0 ⇒ `total_return < 0`).

### Axiom A6 (#467) — `splits=N` erzeugt N echte rollende Folds, keine Degeneration
Die Fold-Geometrie ist ein **rollender** Walk-Forward: `is_start = start_ns + fold·oos_window` (IS rollt je
Fold um genau ein OOS-Fenster vor) mit Purge/Embargo `oos_start = is_start + is_window + embargo`. Damit ist
die Degeneration zu EINEM singulären, kontiguierlichen IS/OOS-Block ausgeschlossen und Lookback-Leakage
über die IS/OOS-Grenze verhindert. **Verbot:** Inline-Nachbau dieser Arithmetik — siehe Axiom A7.
(Pitfall #88-Leakage; Guard: `test_issue_467_fold_geometry.py`.)

### Axiom A7 (#467/#463) — Fold-Geometrie ist Single Source of Truth: `compute_fold_boundaries`
`backtest_runner.compute_fold_boundaries(start_ns, walk_forward_dict)` ist die **EINZIGE** Quelle der Fold-Grenzen.
Explizites Verbot: Die Inline-Berechnung `start_ns + is_window_ns` für OOS-Prüfungen ist verboten. Die Auswertung von `oos_covered` ist zwingend an die echten OOS-Intervalle (`[oos_start_k, oos_end_k)`) aus `compute_fold_boundaries` gebunden (Issue #491).

`(is_start, oos_start, oos_end)`-Tripel. Sämtliche Split-Stellen (Worker per-Trade-Klassifikation, Worker
per-Fold-Sortinos, `oos_trade_records`, Aggregat-per-Fold) MÜSSEN sie nutzen. Vier parallele Inline-Kopien
sind eine eingebaute Divergenz-Falle — exakt analog zu `compute_walk_forward_window` für die äußere
Fenster-Grenze. **Verbot:** `split_*_ns = start_ns + fold·… `-Arithmetik irgendwo inline reproduzieren.

### Axiom A8 (#468) — OOS-Gate-Konditionierung: Penalty-Krümmung ≠ Gate-Schwelle, strikte OOS-Isolation
Der Return-Shortfall wird an einer **robusten Skala** (`return_penalty_scale`) normiert, NICHT an der
winzigen Gate-Schwelle `oos_min_total_return` (sonst wird die Schwelle zum Penalty-Verstärker, schlecht
konditioniert). `_shortfall_distance(..., scale)` entkoppelt Eligibility-Schwelle und Penalty-Gradient.
**Strikte OOS-Isolation:** Alle im OOS-Pfad genutzten Schwellen sind explizit `oos_*`-gekeyt
(`oos_min_trades`, `oos_min_total_return`, `oos_min_expectancy`, `oos_min_win_rate`, `oos_min_sortino`,
`oos_min_profit_factor`) — **kein stiller Fallback auf IS-`min_*`**. `_constraint_distance_penalty` wirft
fail-loud, wenn die OOS-Keys fehlen; `compute_reward` lädt dafür `tournament.json` nach, falls mit
explizitem `weights`, aber ohne `tournament_cfg` aufgerufen. **Verbot:** IS-Schwellen im OOS-Gate;
`scale ≤ 0` (ZeroDivision). (Issue #468 — kein eigener Pitfall-Header, nicht zu verwechseln mit
Pitfall #88 [Leakage in OOS Window, Axiom A6]; Guard: `test_issue_468_penalty_conditioning.py`.)

### Axiom A9 (#469) — Reward-Semantics-Version: fail-loud gegen Posterior-Korruption
`_check_reward_semantics_version` bricht **fail-loud** (`ValueError`) ab, wenn eine geladene Study eine
ältere/fehlende Version trägt UND bereits Trials hat (`existing < current ∨ existing is None`, mit
`has_trials`). Das blockiert das Laden inkompatibler historischer Trials in die TPE-Posterior. **Pflicht:**
**Jede** Änderung an Distanz-Kalkulation, Penalty-Gewichtung, Annualisierung, Equity-Ableitung oder Gate-
Logik (Axiome A2/A5/A8) erzwingt einen Bump von `reward_semantics_version` in `optimizer.json` UND das
Löschen stale Studies (`rm -f data/optimizer/sweep/*.db data/optimizer/studies.db`). **Verbot:** WARN-statt-
`raise` bei `existing < current ∧ has_trials`. (Pitfall #89; Guard: `test_issue_469_semantics_guard.py`.)

### Test-Hygiene-Axiom — keine prozessweite `sys.modules`-Mutation in Tests
Test-Module dürfen `sys.modules` **nicht** auf Modul-Ebene irreversibel mit `MagicMock` überschreiben
(z. B. `sys.modules["pyarrow"] = MagicMock()`). pytest importiert ALLE Test-Module während der Collection,
bevor irgendein Test läuft ⇒ eine solche Mutation verseucht die gesamte Suite (Audit #470 fand so 10
fremde Tests gekippt) und bricht den Tautologie-freien Vertrag. Heavy-Deps (`pyarrow`, `nautilus_trader`)
sind installiert; reine Helfer (`compute_fold_boundaries`, `_calculate_stats`) werden DIREKT importiert und
getestet, nicht inline nachgebaut. **Verbot:** Tautologische Tests, die nur lokale Inline-Arithmetik gegen
sich selbst prüfen statt der Produktionsfunktion.

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
  #453 die granulare Kategorie (`is_rejection_detail`, `oos_rejection_reasons`), seit **#554** das
  maschinenlesbare `oos_gate_deltas`-Dict (`metric → actual − threshold`; für `max_drawdown`
  `cap − actual`, einheitlich „negativ = verfehlt"). Fold-Konsistenz (`oos_profitable_folds`,
  `oos_profitable_folds_frac`, #550) und Benchmark-Alpha (`oos_buyhold_return`, `oos_excess_return`,
  #552) reisen in `oos_metrics` der `tournament_result.json` mit.
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

### 18.5.7 OOS-Eligibility-Zustandsautomat & Fallback-Matrix (Issue-Set #546–#555, wasserdicht)

Der `backtest_runner`-Worker klassifiziert jeden Trial in **genau einen** OOS-Zustand. Der Automat ist deterministisch und config-getrieben (Zero-Hardcoding, HI-6); jede Transition ist unten explizit spezifiziert. Null Ambiguität.

**Zustände & Transitionen (`_evaluate_oos_eligibility`):**

```
                         oos_total_trades <= 0
   [START] ──────────────────────────────────────────►  NOT_EVALUABLE
      │                                                   (oos_evaluated=False, oos_eligible=False,
      │ oos_total_trades > 0                               oos_gate_deltas={}, reason="oos_not_evaluable")
      ▼
  EVALUATED ── ALLE eligible_requires_all erfüllt ──┐
      │        ∧ ≥1 eligible_requires_any erfüllt    │ ja
      │        ∧ median_notional ≥ 10 (Micro-Sizing) ▼
      │                                            ELIGIBLE  (oos_eligible=True)
      │ nein
      ▼
  FAILED  (oos_evaluated=True, oos_eligible=False, oos_rejection_reasons=[…], oos_gate_deltas={…})
```

**Reward-Pfad-Kopplung (`compute_reward`, Reihenfolge zwingend):** `oos_evaluated ∧ ¬oos_eligible` ⇒ `_constraint_failure_reward` (kontinuierliche Distanzstrafe, strikt < Evaluable-Floor). `¬oos_evaluated ∨ base_source is None` ⇒ Unevaluable-Shaping (`penalty_unevaluable_oos + shaping`). Sonst Evaluable-Base (geclippter Sortino). **Rang-Invariante (hart): jeder FAILED-Reward < jeder ELIGIBLE-Reward.** `distance_term_cap` (#547) senkt Distanzen nur ⇒ Invariante bleibt.

**`eligible_requires_all`-Bedingungen (condition_map) & ihre kanonische Metrik-Quelle:**

| Bedingung | Config-Key | Metrik (Aggregation, Issue) | Reject-Format (#554) |
|---|---|---|---|
| `min_trades` | `oos_min_trades` | `total_trades` (pooled, Summe) | `{n} < {req}` |
| `min_total_return` | `oos_min_total_return` | `total_return` (compoundiert, #465) | `.6g` + `Δ=±.3e` |
| `min_expectancy` | `oos_min_expectancy` | `expectancy` (**notional-relativ**, Fold-Median, #546/#550) | `.6g` + `Δ=±.3e` |
| `max_drawdown` | `oos_max_drawdown`/`max_drawdown` | `max_drawdown` (pooled MtM) | `.6g` + `Δ=±.3e` (`cap−actual`) |
| `min_win_rate` | `oos_min_win_rate` | `win_rate` (Fold-Median, #550) | `.6g` + `Δ=±.3e` |
| `min_sortino` (any) | `oos_min_sortino` | `sortino_ratio` (**Fold-Median, kanonisch #549**) | `.5f` |
| `min_profit_factor` (any) | `oos_min_profit_factor` | `profit_factor` (Fold-Median, #550) | `.5f` |
| `min_profitable_folds` | `oos_min_profitable_folds_frac` | `oos_profitable_folds / oos_folds_total` (#550) | `{n}/{tot} ({frac}) < {req}` |
| `min_excess_return` | `oos_min_excess_return` | `oos_excess_return` = `total_return − oos_buyhold_return` (#552) | `.6g` + `Δ=±.3e` |

**Fallback-Matrix (Zero-Hardcoding — Verhalten bei fehlendem Key; alle rückwärtskompatibel):**

| Config-Key | Default-Verhalten bei Abwesenheit | Issue |
|---|---|---|
| `notional_list` (Laufzeit-Arg) | Expectancy fällt auf `mean(pnl/starting_capital)` zurück (Legacy, bit-identisch) | #546 |
| `expectancy_penalty_scale` | `_shortfall_distance` normiert auf `target` (Legacy) | #547 |
| `distance_term_cap` | kein Cap (Distanzen ungedeckelt, Legacy) | #547 |
| `embargo_period_days` | 0 ⇒ kein Purge-Gap (Fold 0 startet bei `is_end`); Fenster-Span bit-identisch | #548 |
| `oos_min_profitable_folds_frac` | Bedingung inaktiv (trivial erfüllt) — greift nur, wenn gesetzt UND Fold-Telemetrie vorliegt | #550 |
| `oos_min_excess_return` | Bedingung inaktiv ⇒ Legacy-Absolut-Gate (`oos_min_total_return`) allein maßgeblich | #552 |
| `deflated_selection` | seit #567 `true` in Prod; `false` ⇒ keine Multiple-Testing-Korrektur (bit-identisch zu Pre-#553) | #553/#567 |
| `deflation_confidence` | `0.95` (nur wirksam bei `deflated_selection=true`) | #553 |
| `spread_bps_by_symbol` | Symbol-Override fehlt ⇒ Asset-Class-Auflösung (`resolve_spread_bps`), bit-identisch | #566 |
| `oos_min_expectancy_k_alpha` | fehlt ⇒ statisches `oos_min_expectancy` maßgeblich (kein kostenrelatives Gate) | #562 |
| `sortino_soft_scale` | fehlt/≤0 ⇒ Legacy-Hard-Clip auf `±sortino_clip_abs` | #559 |
| `w_ret` | fehlt ⇒ `0.0` (kein return-Tie-Breaker im eligiblen Ast) | #559 |
| `failure_reward_mode` | fehlt ⇒ `legacy_mean` (Mittel der aktiven Distanzen, bit-identisch) | #560 |
| `overfit_divergence_mode` | fehlt ⇒ Legacy-einseitig (`max(0, IS−base)`) | #565 |
| `overfit_oos_luck_weight` | fehlt ⇒ `penalty_overfit_weight` (symmetrisch gleichgewichtet) | #565 |
| `fold_dispersion_weight` | fehlt/0 oder < 2 Fold-Sortinos ⇒ keine Dispersions-Strafe | #565 |
| `n_startup_trials_per_dim` | fehlt/≤0 ⇒ fixer `n_startup_trials` (keine Dimensions-Kopplung) | #568 |
| `tier_escalation_min_signal` | `1e-3` (τ für das Gradienten-Gate der Tier-Eskalation) | #568 |

**Splits==1-Degenerierung (Holdout/Single-Fold):** Fold-Median == pooled (#549/#550), Buy&Hold über den einen Fold (#552) ⇒ die #549–#552-Änderungen sind für `n_folds=1`-Läufe (`confirm.py`) **bit-identisch**; nur echte Multi-Fold-Sweeps ändern ihr Verhalten. Diese Degenerierung ist die Rückwärtskompatibilitäts-Garantie des Holdout-Pfads.

---

## 19. Changelog (Agent-Maintained)

### 2026-07-06 — Issue-Set #559–#571 (Mathematische Sanierung der Reward-Landschaft des Per-Symbol-Sweeps `TSLA.ETORO`)

Chirurgische, test-gesicherte Behebung der **degenerierten Reward-Landschaft**, über die TPE optimiert: kein Tuning-Problem (mehr Trials/andere Bounds), sondern ein Konstruktionsdefekt der Zielfunktion. Drei unabhängige Defekte kollabierten die Landschaft (Winner-Deckel bei `reward==+5.0` exakt; Near-Miss-Plateau mit `corr(reward,return)≈0`; unerreichbares OOS-Gate an der 10-bps-Kostenwand). **+50 neue Tests (alle isoliert grün), 0 Regressionen** (Full-Suite identisch zu `main`: dieselben vorbestehenden Environment-Failures aus der `test_issue_489`-`sys.modules`-Mock-Pollution + `n_jobs`-Determinismus-Config; CI führt Testdateien isoliert aus). Neue Pitfalls **#115–#118** + Kern-Invariante (Reward-Gradient). Financial-Metrics-Hinweis: alle berührten Kennzahlen sind dimensionslose Verhältniszahlen (Expectancy als Return-auf-Notional, Sortino/PF-Ratios, Fold-Fraktionen) — keine währungsbehafteten Beträge, CHF-Normierung mathematisch nicht anwendbar.

**Merge-Reihenfolge (gekoppelt):** Phase 1 Kostenehrlichkeit (#561/#566/#562) → Phase 2 Gradient (#559/#560/#563) → Phase 3 Overfit-Eindämmung (#564/#565/#567) → Phase 4 Effizienz/Sichtbarkeit (#569/#568/#570). Alle neuen Config-Keys sind Zero-Hardcoding: fehlt ein Key ⇒ Legacy-Verhalten (bit-identisch).

| GH-Issue | Prio | Kernänderung | Dateien |
|---|---|---|---|
| **#561** | P0 | Kommissions-Semantik 2×→1×: halbe Rate je Leg (`per_leg_bps=commission_bps/2`), Summe == `commission_bps·notional` einmal. Schema präzisiert. Pitfall #115. | `backtest_runner.py`, `config/backtest.json` |
| **#566** | P2 | EQUITY-Spread 8→3 bps (realistischer Blue-Chip) + `spread_bps_by_symbol`-Override (`TSLA.ETORO=2`); `resolve_spread_bps` (SSOT: Symbol→Asset-Class→DEFAULT). | `backtest_runner.py`, `config/backtest.json` |
| **#562** | P0 | Kostenrelatives Expectancy-Gate `oos_min_expectancy := k_alpha·c_rt` (k_alpha=0.25); `c_rt=round_trip_cost_bps` aus dem Kostenmodell abgeleitet & telemetriert (`effective_expectancy_gate`). | `backtest_runner.py`, `config/tournament.json` |
| **#559** | P0 | Weiche Sortino-Sättigung `base=c·asinh(sortino/c)` statt Hard-Clip (Gradient lebt oberhalb der alten Grenze) + return-Tie-Breaker `+w_ret·return`. Pitfall #117. | `optimizer/reward.py`, `config/optimizer.json` |
| **#560** | P0 | Return-verankerter Near-Miss-Gradient (`failure_reward_mode='return_anchored'`, softplus) statt Mittel kosten-saturierter Terme ⇒ `corr(reward,return\|ineligible)>0`. Pitfall #117. | `optimizer/reward.py`, `config/optimizer.json` |
| **#563** | P1 | Sortino-Annualisierungs-Konsistenz: `oos_min_sortino` als ANNUALISIERTE Schwelle dokumentiert (√8766) und 0.3→1.0 kalibriert; Clip-Sättigung durch #559 entschärft. | `config/tournament.json` |
| **#564** | P1 | Shrinkage/Warm-Start-Fallback: fehlt `global_best` ⇒ `strategy_defaults` als Referenz+Seed (`resolve_symbol_shrinkage_seed`), `shrinkage_inactive`-Warnung; `param_pen` nie still 0. Pitfall #116. | `optimizer/run_optimization.py` |
| **#565** | P1 | Symmetrische Divergenz-Strafe `\|IS−base\|` (OOS-Glück bestraft) + Fold-Dispersions-Malus (`pstdev(per_fold_oos_sortino)`); `oos_fold_sortinos` in `TournamentMetrics`. Pitfall #118. | `optimizer/reward.py`, `optimizer/parsing.py`, `config/optimizer.json` |
| **#567** | P2 | Deflated-Sortino-Selektion aktiviert (`deflated_selection=true`, conf 0.95): Winner muss das deflationierte Rausch-Maximum über N Konfigurationen schlagen (FP-Rate ≤ 5 %). | `config/tournament.json` |
| **#569** | P3 | Per-Trial-Telemetrie: `oos_sortino`/`oos_expectancy`/`oos_win_rate`/`oos_profit_factor`/`is_sortino_median`/`per_fold_oos_sortino` additiv ins Event (Reward rekonstruierbar ±1e-6). | `optimizer/run_optimization.py` |
| **#568** | P2 | Budget-Steuerung: `n_startup_trials=max(base, ceil(k·dim))` (Dimensions-Kopplung) + Gradienten-Gate `study_shows_gradient_signal` (keine Eskalation ohne Signal, telemetriert). | `optimizer/run_optimization.py`, `config/optimizer.json` |
| **#570/#571** | P3/Meta | AGENTS.md: Pitfalls #115–#118, Kern-Invariante (Reward-Gradient), Fallback-Matrix, Changelog (dieser Eintrag); Verifikation des Katalogs. | `automation/AGENTS.md` |

Tests: `test_issue_560_soft_saturation.py`, `test_issue_561_nearmiss_gradient.py`, `test_issue_562_commission_semantics.py`, `test_issue_563_cost_relative_gate.py`, `test_issue_564_sortino_scaling.py`, `test_issue_565_shrinkage_fallback.py`, `test_issue_566_overfit_symmetry.py`, `test_issue_567_spread_realism.py`, `test_issue_568_deflated_selection.py`, `test_issue_569_budget_control.py`, `test_issue_570_trial_telemetry.py`.

### 2026-07-06 — Issue-Set #546–#555 (Mathematische Exzellenz & Kalkulationsmethodik des Per-Symbol-Sweeps)

Chirurgische, test-gesicherte Fixes der rechnerischen/methodischen Defekte, die (a) die risikoadjustierten Kennzahlen verfälschten, (b) den TPE-Gradienten verzerrten und (c) die Robustheitsbewertung aushebelten. **+37 neue Tests (alle grün), 0 Regressionen** (Full-Suite identisch zu `main`: dieselben 13 vorbestehenden Environment-Failures aus der `test_issue_489`-`sys.modules`-Mock-Pollution + `n_jobs`-Determinismus-Config; CI führt Testdateien isoliert aus und ist davon nicht betroffen). Neue Pitfalls **#106–#114**. Financial-Metrics-Hinweis: alle hier berührten Kennzahlen (Expectancy als Return-auf-Notional, Sortino/Profit-Factor-Ratios, Fold-Fraktionen, Excess-Return) sind **dimensionslose Verhältniszahlen** — keine währungsbehafteten Beträge; eine CHF-Normierung ist auf diesen Größen mathematisch nicht anwendbar (Profit-Targets/Stop-Loss/Limits werden von diesem Issue-Set nicht berührt).

| GH-Issue | Prio | Kernänderung | Dateien |
|---|---|---|---|
| **#546** | P1 | Expectancy notional-relativ (`mean(pnl_i / entry_notional_i)`, sizing-invariant) statt auf `starting_capital`; `_calculate_stats(..., notional_list=)` + Aufrufer; Legacy-Pfad bit-identisch. Rekalibrierung `oos_min_expectancy`/`min_expectancy` 5e-05→0.001. Pitfall #106/#107. | `backtest_runner.py`, `config/tournament.json` |
| **#547** | P1 | `expectancy_penalty_scale` (0.002) entkoppelt die Expectancy-Distanz vom Gate-Threshold; optionaler `distance_term_cap` (3.0) deckelt jeden Distanz-Term. Alle sechs Dimensionen auf vergleichbare Skala. Pitfall #108. | `optimizer/reward.py`, `config/tournament.json` |
| **#548** | P1 | `embargo_period_days` in `wf_settings` (Manifest+kopierte backtest.json) UND `compute_walk_forward_window`-Span reserviert; Fold `n−1` endet exakt bei `end`. `confirm.py` konsistent. Pitfall #109. | `optimizer/trial_config.py`, `optimizer/confirm.py` |
| **#549** | P2 | Kanonischer Fold-Median-Sortino AN DER QUELLE (`apply_fold_aggregation`) ⇒ Gate == Reward; gepoolter Wert unter `sortino_ratio_pooled`. Pitfall #110. | `backtest_runner.py`, `optimizer/parsing.py` |
| **#550** | P2 | Einheitliche Fold-Median-Aggregation (sortino/win_rate/expectancy/profit_factor), `total_return` compoundiert; deklaratives `oos_min_profitable_folds_frac`-Gate (0.5) + `oos_profitable_folds`-Telemetrie. Pitfall #110. | `backtest_runner.py`, `config/tournament.json` |
| **#551** | P2 | Halb-offene Equity-Slices `[s, e)` an allen Slice-Stellen (IS/OOS-Frames/Per-Fold), konsistent zur Trade-Klassifikation; disjunkte Fold-Segmente. Pitfall #111. | `backtest_runner.py` |
| **#552** | P2 | OPT-IN benchmark-relatives `oos_min_excess_return`-Gate (Buy&Hold über identisches OOS-Fenster; `PortfolioMonitor.get_benchmark_series`); Telemetrie `oos_buyhold_return`/`oos_excess_return`. Default deaktiviert. Pitfall #112. | `backtest_runner.py`, `config/tournament.json` |
| **#553** | P3 | OPT-IN Deflated-Sortino-Selektion (`deflated_selection`, Bailey & López de Prado); `deflation.deflated_threshold` kontrolliert die False-Positive-Winner-Rate auf `1−confidence`. Default deaktiviert ⇒ bit-identisch. Pitfall #113. | `optimizer/deflation.py` (neu), `backtest_runner.py`, `config/tournament.json` |
| **#554** | P3 | Adaptive Reject-Präzision (`.6g` + `Δ=±.3e`) + maschinenlesbares `oos_gate_deltas`-Dict bis ins `optimizer_trial_completed`-Event. Pitfall #114. | `backtest_runner.py`, `optimizer/parsing.py`, `optimizer/run_optimization.py` |
| **#555** | Meta | Katalog/Dokumentation: Pitfalls #106–#114, Changelog, Migrationsnotizen (dieser Eintrag). | `automation/AGENTS.md` |

Tests: `test_issue_546_expectancy_notional.py`, `test_issue_547_constraint_distance_balance.py`, `test_issue_548_embargo_geometry.py`, `test_issue_549_gate_reward_sortino_parity.py`, `test_issue_550_fold_consistency_gate.py`, `test_issue_551_fold_slice_disjoint.py`, `test_issue_552_benchmark_relative_gate.py`, `test_issue_553_deflated_selection_montecarlo.py`, `test_issue_554_reject_reason_precision.py`.

| 2026-06-30 | **IMPLEMENTIERUNG GitHub-Issue #493 (Forensik/OOS-Rejection-Taxonomie).** Die OOS-Eligibility-Taxonomie (`_classify_is_rejection_detail`) wurde um `REJECT_OOS_DISCARDED_BY_IS_GATE` erweitert. Zuvor wurden Trials, die das OOS-Fenster abdeckten und tradeten, aber durch das IS-Gate verworfen wurden (`is_eligible=False`), pauschal als `REJECT_OOS_INACTIVE` deklariert. Dies verfälschte die Telemetrie. Die Funktion prüft nun `oos_total_trades > 0` als Weiche. Begleitender Pipeline-Mock-Test in `test_issue_493_rejection_taxonomy.py` implementiert. Pitfall #91 dokumentiert. | `automation/optimizer/run_optimization.py`, `automation/tests/test_issue_493_rejection_taxonomy.py`, `automation/AGENTS.md` |
| 2026-07-02 | **IMPLEMENTIERUNG Issue #509 (Cost Drag & Turnover Churning):** Turnover-Penalty in Reward Shaping aufgenommen, Throttling-Parameter (cooldown_bars, min_holding_time, min_signal_strength) im Suchraum für alle aktiven Strategien ergänzt. Net vs Gross Expectancy Metriken in extract_metrics und TournamentMetrics aufgeteilt. Hard-Constraint max_trades_cap implementiert und obsoletes is_max_trades entfernt. | `automation/optimizer/reward.py`, `automation/config/optimizer.json`, `automation/strategies/hourly_strategy_base.py`, `automation/optimizer/spaces.py`, `automation/backtest_runner.py`, `automation/optimizer/parsing.py`, `automation/optimizer/run_optimization.py`, `automation/AGENTS.md` |
| 2026-07-01 | IMPLEMENTIERUNG Issue #505 (Reward Dynamic Range & Normalization): Dimensionslose Distanz-Normierung, Entfernung der `tanh`-Kompression, Ausweitung der Constraint-Failure-Spanne auf `[-9.75, -sortino_clip_abs]`. Axiom A2 aktualisiert. | `automation/optimizer/reward.py`, `automation/tests/test_issue_461_reward_no_inversion.py`, `automation/AGENTS.md` | (Reward Dynamic Range & Normalization): Dimensionslose Distanz-Normierung, Entfernung der `tanh`-Kompression, Ausweitung der Constraint-Failure-Spanne auf `[-9.75, -sortino_clip_abs]`. Axiom A2 aktualisiert. | `automation/optimizer/reward.py`, `automation/tests/test_issue_461_reward_no_inversion.py`, `automation/AGENTS.md` |
| 2026-06-10 | **1c:** Optuna-Loop (SQLite, TPE, Warm-Start), Holdout-Confirmation, PR-Proposal-Export. Autotuner V2 abgeschlossen. | `automation/optimizer/` |

- **Phase 0b:** ETORO_CONFIG_DIR/ETORO_LOGS_DIR env isolation implemented; Manifest-Contract (no re-merge if manifest_version is set); oos_fold_sortinos export added for aggregate winners.
 (Agent-Maintained)
> **Anweisung für Jules:** Bei jeder Änderung am `automation/`-Paket hier einen Eintrag (Datum, Beschreibung, Dateien) anhängen.


### 🟢 Pitfall #198 — OOS-Eval an IS-Gate gekoppelt (Issue #471 / #487)
**Symptom:** OOS-Metriken werden an das Bestehen des IS-Gates gekoppelt und bei IS-Fail verworfen, obwohl die OOS-Trades existieren. Dies führt zu OOS-Anchor Divergence (Wurzelursache für Issue #494).
**Fix:** `_oos_eval`-Zuweisung in `select_winners` iteriert über `all_results`. OOS-Evaluierbarkeit vollständig von IS-Eligibility getrennt. Fail-Loud-Invariante in `parse_tournament`.
**Betroffen:** `automation/backtest_runner.py`, `automation/optimizer/parsing.py`

### 🟢 Pitfall #91 — Reward monoton in IS-Aktivität (Issue #472 / #488)
**Symptom:** Der Optimizer nutzt das IS-Trade-Shaping (`is_total_trades`) als primäres Optimierungsziel im Unevaluable-Raum, selbst wenn die Strategie keine OOS-Evaluation auslöst und IS keinerlei Performance liefert (`_gate_proximity == 0`).
**Fix/Regel:** Shaping ist strikt als "Tie-Breaker-Gradient" definiert und darf NIEMALS ein primäres Optimierungsziel sein. Die `activity` muss zwingend durch `_gate_proximity` geclippt werden (`activity := min(activity, _gate_proximity)`).
**Betroffen:** `automation/optimizer/reward.py`, `automation/optimizer/run_optimization.py`

### 🟢 Pitfall #92 — Embargo schrumpft OOS (Issue #473 / #489)
**Symptom:** Embargo-Tage werden vom OOS-Fenster subtrahiert, was die OOS-Geometrie schrumpft, anstatt das Fenster zu verschieben.
**Fix/Regel:** Die `embargo_period_days` schiebt den `oos_start_ns` nach vorne. Invariante: `oos_end_ns = oos_start_ns + oos_window_ns`.
**Betroffen:** `automation/optimizer/trial_config.py`

### 🟢 Pitfall #200 — oos_covered Lower-Bound-only (Issue #475 / #491)
**Symptom:** `oos_covered` wird naiv durch `fill_ts_max >= oos_window_start_ns` berechnet, was bei Walk-Forward mit Lücken zwischen den Folds fehlerhaft ist.
**Fix:** `oos_covered` ist nun als Bounded-Union implementiert (`∃ fill : ∃ fold k : oos_start_k ≤ fill < oos_end_k`).
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #94 — OOS-Rejection-Taxonomie (Issue #477 / #493)
**Symptom:** Eine Trial hat das OOS-Fenster abgedeckt (`oos_covered=True`) und auch tatsächlich getradet (`oos_total_trades > 0`), aber wurde vom IS-Gate verworfen (`is_eligible=False` ⇒ `oos_evaluated=False`). Sie wird fälschlich als `REJECT_OOS_INACTIVE` klassifiziert.
**Fix:** `_classify_is_rejection_detail` nutzt nun `oos_total_trades > 0` (via Subprozess-Metadaten) als Weiche, um diese Trials als `REJECT_OOS_DISCARDED_BY_IS_GATE` zu klassifizieren.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #95 — Reward Shaping Floor-Plateau (Issue #488)
**Symptom:** Der TPE-Sampler verschwendet bei rein strukturell unevaluierbaren Symbolen 100 Trials am Flat-Floor.
**Fix:** Der Floor-Plateau Guard implementiert eine strikte Early-Stop Invariante: Wenn die ersten `K` Trials streng nach `n_startup_trials` alle `oos_evaluated=False` sind (All-Unevaluable), wird die Study hart abgebrochen und ein JSON `STUDY_EARLY_STOP` Event geloggt.
**Betroffen:** `automation/optimizer/run_optimization.py`


| Datum | Änderung | Dateien |
|-------|----------|---------|
| 2026-08-06 | **Implementierung Issue-Katalog #913–#936 (GitHub-Issues #774/#775/#776/#777 — Inferenz-Blockade, Suchbudget, Simulations-Verifikation, Re-Run-Runbook) + reward_semantics_version 20→21 + simulation_semantics_version 2→3.** Vier Kataloge auf demselben Katalog-Lauf (`be341d57_20260806T113734093100`), Basis-Commit `9ad6423e` (Vorgänger-Katalog #897–#912). **Katalog A — Inferenz-Blockade (#774, #913–#918, Pitfalls #293–#296):** #913 (`sortino_numeric_guard_reference='family_median'` war konfiguriert, aber KEINE Call-Site übergab `family_median_n_periods` — 100 % aller handelnden Trials verloren Sortino/PSR, 0 eligible Trials über den gesamten Lauf trotz 462 Trials, die jedes andere Gate bestehen; Fix: `run_optimization.py` berechnet den Familien-Median über abgeschlossene Sibling-Trials, reicht ihn über das Manifest an den Subprozess, `assert_guard_reference_injectable()` bricht beim Start fail-loud ab, falls eine künftige Call-Site die Injektion wieder verliert); #914 (`SORTINO_GUARD_REFERENCE_UNAVAILABLE` — der von #901 neu eingeführte Code — fehlte in `_inference_failure_codes`, der Prune-Pfad lief leer, 1767 Trials trugen einen regulären Failure-Reward statt geprunt zu werden); #915 (`check_guard_reference_coherence` prüfte nur die QUELLE, nicht die WIRKUNG, und PASSte bei 0 % definiertem PSR — neue `check_selection_statistic_availability`, severity `blocking`); #916 (`sortino_numeric_guard_min_periods` 1600→320, gegen die reale Verteilung um Faktor 5,25 zu gross); #917 (`REJECT_OOS_STATISTIC_UNAVAILABLE` unterscheidet "nicht messbar" von "gemessen und abgelehnt" jetzt über ALLE Rejection-Gründe, nicht nur den ersten); #918 (zentrale `InferenceDiagnosticCode`-Registry in `_contracts.py`, AST-Vertragstest gegen unregistrierte Codes). **Katalog B — Simulations-Verifikation (#776, #919–#924, Pitfalls #297–#299):** #919 (Exit-Telemetrie lag pro Trial vor [#899], wurde aber nie zu einem Study-Aggregat zusammengefasst — `report._sum_exit_reason_histograms`/`_time_box_exit_fraction`, `invariants.check_exit_reason_coverage`); #920/Pitfall #297/#298 (12 Krypto-Symbole trugen `asset_class='equity'` seit dem #898-Backfill, Round-Trip-Kosten um Faktor 4 zu niedrig — `size_precision=8` widerlegt `'equity'` bereits aus dem Datensatz selbst; `check_instrument_metadata_coherence`); #921 (SqueezeBreakout: `bb_std_dev`/`keltner_multiplier` unabhängig gesampelt trafen die für `squeeze_on` nötige enge Verhältnis-Zone selten [19/178 Trials auswertbar] — `squeeze_ratio` wird jetzt direkt gesampelt, dasselbe fast+gap-Muster wie `macd_slow`; `binding_cause`-Korrektur bereits über #926 abgedeckt); #922 (OpeningRangeBreakout verankerte den Handelstag auf `pd.Timestamp.day`, unabhängig von der RTH-Session eines Equity auf dem 24/7-Stundenraster — neuer `opening_range_session_anchor`, asset-class-aufgelöste `opening_range_session_open_hour`); #923 (Bar-Qualitäts-Preflight kannte `bar_coverage_ratio` bereits, wertete ihn aber nie als Ablehnungskriterium — neuer `min_bar_coverage_ratio`; `check_n_periods_homogeneity` gegen die beobachtete Faktor-11,3-Streuung); #924/Pitfall #299 (`atr_floor_bps` war bereits über `_effective_atr_value` angewandt [entgegen der Issue-Prämisse], aber ein flacher 2.0bps-Default ohne Asset-Class-Auflösung — `resolve_atr_floor_bps`, `backtest.json['atr_floor_bps_by_asset_class']`). **Katalog C — Suchbudget & Diagnose-Attribution (#775, #925–#930, Pitfalls #300–#303):** #925/Pitfall #300 (der Plateau-Frühstopp konnte geschlossen bewiesen frühestens bei 98,6 % des Budgets feuern, weil das gesparte Restbudget im NENNER des Risikoterms stand — neuer `plateau_stop_mode='expected_yield'`-Default: Abbruch, wenn `p_hi·r < plateau_stop_min_expected_eligible`); #926/Pitfall #301 (`binding_cause='signal_quality'` bei 10 Studies, deren wahre Ursache die #913-Inferenzblockade war — `diagnostic_writeback_enabled`, dritter Wert `inference_unavailable` ohne Denylist-Konsequenz); #927/#928/Pitfall #302 (`reward_terms_aggregates`/`gate_collinearity` liefen auf der ELIGIBLEN statt der EVALUIERTEN Kohorte und waren bei 0 eligiblen Trials leer — Selection-on-the-dependent-variable; auf `oos_evaluated` umgestellt, Jaccard ergänzt); #929 (`best_value=null` in 14/14 Studies, weil der Report-Layer den Study-Best aus der eligiblen statt der abgeschlossenen Menge zog — `_best_completed_value`, `check_search_made_progress`); #930/Pitfall #303 (`[#640]`-Eskalationsmeldung prüfte `stop_reason != BUDGET_EXHAUSTED` als Proxy für "Budget übrig", der nach #925s Verschiebung des Abbruchpunkts falsch wurde — auf `budget_executed_fraction < min_median_budget_execution` umgestellt). **Katalog D — Durchsatz & Re-Run-Runbook (#777, #931–#936, Pitfalls #304–#306):** #931/Pitfall #304 (der Disk-Preflight prüfte Plattenplatz [786 GB frei, unauffällig] statt der tatsächlich knappen Ressource Zeit [143 Symbole × 71,5 min/Symbol ≈ 170 h gegen 72 h Budget] — neues `WALLCLOCK_BUDGET_PREFLIGHT`, `wallclock_budget_policy∈{degrade,abort,warn}`); #932/Pitfall #305 (`pipeline_depth` existierte, war dokumentiert, Registry-grün, NULL ausführende Referenzen — dieselbe #913-Fehlerklasse, eine Konfigurationsdatei weiter; entfernt, Longest-Processing-Time-Dispatch statt dessen: Studies eines Symbols absteigend nach Erfahrungswert dispatcht, `barrier_wait_s`/`SYMBOL_DISPATCH_COMPLETED` telemetriert); #933/Pitfall #306 (ein 5,9-MB-Log mit 4318 Zeilen enthielt kein einziges `INVARIANT_*`-Event und keinen `SWEEP_COMPLETED`-Abschluss — `report._build_report` lief nur am Sweep-Ende, bei 170 h Laufzeit ist das der erste Befund nach einer Woche; `INVARIANT_RESULT`/`SWEEP_PROGRESS` je Symbol, atomarer Zwischenreport, `SWEEP_COMPLETED`/`SWEEP_ABORTED` als letzte Zeile jedes Laufs); #934 (`logs/filter.sh` trug den Log-Dateinamen hart kodiert — optionales Argument, `ls -t`-Fallback, abgeleiteter Ausgabename); #935 (reine Verifikation — der aus #897–#912 dokumentierte Rückstand ist vollständig abgearbeitet, keine Massnahme); #936 (dieser Eintrag — Doppel-Bump `reward_semantics_version` 20→21 [drei Auslöser: #913/#914/#917] und `simulation_semantics_version` 2→3 [zwei Auslöser: #920/#924], vollständige Auslöser-/Nicht-Auslöser-Begründung im jeweiligen `_schema`-Feld; AGENTS.md-Pitfalls #293–#306 nachgetragen). | `automation/backtest_runner.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/invariants.py`, `automation/optimizer/report.py`, `automation/optimizer/sweep.py`, `automation/optimizer/sweep_diagnostics.py`, `automation/optimizer/spaces.py`, `automation/optimizer/reward.py`, `automation/optimizer/wallclock_guard.py`, `automation/optimizer/trial_config.py`, `automation/optimizer/_contracts.py`, `automation/strategies/hourly_strategy_base.py`, `automation/strategies/opening_range_breakout.py`, `automation/config/optimizer.json`, `automation/config/tournament.json`, `automation/config/backtest.json`, `automation/config/instrument_map.json`, `automation/config/search_space_overrides.json`, `logs/filter.sh`, `automation/AGENTS.md` |
| 2026-08-06 | **Implementierung Issue-Katalog #897–#912 (GitHub-Issues #771/#769/#770/#772 — Exit-Sperrklinke, Kostenmodell-Fallback, Inferenz-Integrität, Governance) + reward_semantics_version 19→20 + simulation_semantics_version 1→2.** Vier Kataloge auf demselben Katalog-Lauf, Basis-Commit `353ff773`. **Katalog A (Simulations-Bump):** #897/Pitfall #285/#286 (`trailing_stop_anchor='price_extreme'` löst die ATR-Ratsche ab, `check_effective_stop_distance` erzwingt eine Sensitivitätsprüfung); #898/Pitfall #287 (`resolve_spread_bps` wirft `InstrumentMetadataIncompleteError` statt still auf `DEFAULT` zu fallen, `unknown_asset_class_policy`); #899 (Exit-Reason-/ATR-bps-Telemetrie über Order-Tags); #900 (Bar-Qualitäts-Preflight mit True-Range-/ATR-Skalen-Check). **Katalog B (Reward-Bump):** #901 (`sortino_numeric_guard_reference='family_median'` liefert ehrlich `None` statt still `'absolute'`); #902 (`bar_seconds` Pflichtparameter, `_contracts.BAR_SECONDS_DEFAULT`); #903 (Zeitbox-Verletzung jetzt zusätzlich auf Round-Trip-Ebene gezählt). **Katalog C (kein Bump):** #904/Pitfall #289/#290 (`deflation_n_family_effective ≤ deflation_n_family_raw`-Invariante gegen die `max()`-Annullierung); #905 (`_family_period_returns_from_studies` auf `oos_selection_statistic_available` umgestellt); #906 (Kollinearitäts-Konsolidierung bewusst auf den #897-Kalibrierlauf vertagt); #907 (`check_gate_collinearity_decision_required`/`check_fail_fast_invariants_wired`). **Katalog D (Governance):** #908/Pitfall #288 (`pipeline_depth` dokumentiert-nicht-verdrahtet, `AdxAtrMomentum`-Suchraum-Korrektur, Wallclock-Truncation); #909/Pitfall #291 (`earliest_ts_by_symbol`/`per_symbol_span_stats` lösen die Endzeitpunkt-Aggregat-Verwechslung); #910 (`champion_corroboration_mode='either'` löst den Writeback-Deadlock); #911/Pitfall #292 (`max_consecutive_structural_runs`, `quarantined_pending_simulation_review` statt `denylist`); #912 (dieser Eintrag — Doppel-Bump + AGENTS.md-Pitfalls #285–#292 nachgetragen; #269–#284 aus den Katalogen #856–#896 bleiben als dokumentierter Rückstand offen, siehe dortiger Hinweis). | `automation/strategies/hourly_strategy_base.py`, `automation/backtest_runner.py`, `automation/optimizer/invariants.py`, `automation/optimizer/parsing.py`, `automation/optimizer/report.py`, `automation/optimizer/confirm.py`, `automation/optimizer/sweep.py`, `automation/optimizer/sweep_diagnostics.py`, `automation/optimizer/champions.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/spaces.py`, `automation/optimizer/_contracts.py`, `automation/config/optimizer.json`, `automation/config/tournament.json`, `automation/config/backtest.json`, `automation/config/instrument_map.json`, `automation/AGENTS.md` |
| 2026-08-04 | **Implementierung Issue-Katalog #836–#855 (GitHub-Issues #753/#754/#755/#756 — Zeitbox-Exit-Pfad, Symbol-Durchsatz, Inferenz-Integrität & Governance) + reward_semantics_version 18→19 + simulation_semantics_version (neu, Startwert 1).** Vier Kataloge auf demselben Katalog-Lauf. **Kohorte A — Zeitbox & Exit-Pfad (Purge, #836–#839):** #836/Pitfall #259 (`_pending_cancels.clear()` löschte den asynchronen Fortsetzungs-Token im selben Block, der ihn erzeugte — der Zeit-Exit erreichte nie `_execute_market_close`; `_exit_pending`/`_exit_close_watchdog` ersetzen den Mechanismus, `EXIT_CLOSE_STALLED` als Fail-Loud-Diagnose); #837/Pitfall #260 (Trailing-Stop-Reinit und Bar-Zähler-Reset liefen im AUSLÖSER statt in `on_position_closed` — `_trailing_initialised` entkoppelt beide von `_in_position`); #838/Pitfall #262/#263 (`max_trades_cap`-Early-Return sperrte Trailing-Stop UND Zeit-Exit mit; `_entry_allowed()` extrahiert den Cap auf den Entry-Pfad; `min_holding_time > max_bars_in_trade` jetzt hart geklemmt mit WARNING); #839 (`compute_trial_timebox_violations` + `timebox_violation_tolerance` — `REJECT_INVALID_TIMEBOX` verwirft eine Study VOR jedem statistischen Gate, wenn der Zeitbox-Vertrag gebrochen wurde). **Kohorte B — Symbol-Durchsatz (kein Purge, #840–#843):** #840/Pitfall #264 (`sweep.main()` hatte KEIN `--resume`/`--run-id`, sechste Wiederkehr von Pitfall #237 nach #794/#796/#797/#818/#831 — CLI-Flags + `_strategies_fingerprint`-Validierung ergänzt); #841 (`symbol_coverage.py`, `least_recently_covered`-Rotation statt stabiler Reihenfolge, `check_symbol_coverage`); #842 (`sweep_max_wallclock_h` 24→72 + `_wallclock_forecast`-Telemetrie); #843 (Pipelining/`SuccessiveHalvingPruner` als NICHT umsetzbar analysiert und dokumentiert zurückgestellt — kein reales Multi-Symbol-Katalog-Fixture zur Verifikation der AK-1-Bit-Identitäts-Anforderung verfügbar). **Kohorte C — Inferenz & Selektion (Purge, #844–#848):** #844/Pitfall #267 (`sortino_numeric_guard_min_periods` blieb trotz #823-Dokumentation ungesetzt, fünfte Wiederkehr nach #488/#753/#769/#805/#823 — Wert gesetzt + `_required_keys.json`-Registry-Preflight verhindert küftiges stilles Fehlen); #845 (`downside_obs`-Telemetrie + `check_family_n_periods_homogeneity`/`deflation_max_n_periods_ratio` gegen Faktor-45-n_periods-Heterogenität einer DSR-Kohorte; der oos_evaluated-ändernde Issue-Text-Vorschlag bewusst zurückgestellt, siehe dortiger Code-Kommentar); #846 (`deflation_skipped_reason` erzwingt dieselbe SR0/DSR-Kohärenz-Garantie wie #651 an der Export-Grenze, vierte Wiederkehr); #847 (`_inference_method_block` erkennt jetzt auch eine gelaufene PBO-Inferenz als dokumentierte Promotions-Methode); #848 (`min_win_rate` aus `eligible_requires_any` entfernt — fünfte Wiederkehr #660→#668→#678→#812→#848; `check_selection_rule_homogeneity` FAILt jetzt statt nur zu warnen). **Kohorte D/E — Bericht, Reproduzierbarkeit & Governance (kein Purge ausser #854 selbst, #849–#855):** #849/Pitfall #849-intern (`InvariantResult.to_dict()` schrieb nur `name`, `summary_de.py` las `check` — 519× `**None**` im Bericht; beide Schlüssel jetzt exportiert, Sektion 5 auf Übersicht/Details umgebaut); #850/Pitfall #268 (`holdout_excess_return`-Varianzanteil Symbol 99,1 % vs. Strategie 0,9 % — `exposure_fraction` + `excess_variance_decomposition` machen das im Bericht sichtbar statt es als Strategie-Ranking auszugeben); #851 (Study-Zeitstempel-Telemetrie — `wallclock_by_strategy`/`symbol_barrier_wait_s`/`worker_utilisation`; echte Einzel-Trade-Longest-Trades mit `exit_reason` bewusst zurückgestellt, dieselbe FIFO-Match-Scope-Grenze wie #832); #852 (`optuna`/`numpy`/`nautilus_trader` mit oberer Grenze gepinnt, `check_library_version_drift`-Preflight, gemeinsam mit #844); #853 (Champion-Store-Deadlock — kein neuer Code-Defekt, sondern Kopplung von #834/#840-#841/#821, Pitfall #258-Klasse; `seed_source` unterscheidet jetzt `champion`/`champion_quality_stale` als positive Telemetrie, `check_champion_seed_coverage`; die volle `corroborating_snapshots`-Datenmodell-Umstellung bewusst zurückgestellt); #854 (`simulation_semantics_version` orthogonal zu `reward_semantics_version` eingeführt — `champion_is_admissible` schliesst einen Mismatch VOLLSTÄNDIG aus statt nur `quality_stale`; `reward_semantics_version` 18→19, EIN Auslöser #848, ausführlich begründet warum #845 in dieser Session KEIN Auslöser ist; `check_semantics_version_coherence`); #855 (Pitfalls #259–#268, dieser Eintrag). **Bewusst zurückgestellt:** echtes Cross-Symbol-Pipelining (#843), Einzel-Trade-Longest-Trades mit Entry-/Exit-Zeitstempel (#851 Punkt 3, dieselbe FIFO-Match-Scope-Grenze wie #832), die volle `oos_evaluated=False`-Reward-Neutralität für `SORTINO_INSUFFICIENT_DOWNSIDE` (#845 Punkt 2 — hätte eine Aenderung an der Optuna-Kernschleife erfordert, ohne dedizierten H0-Kalibrierlauf nicht risikofrei), die `corroborating_snapshots`-Datenmodell-Umstellung des Champion-Stores (#853 Punkt 1) — alle vier erfordern entweder einen echten Multi-Symbol-Sweep-Lauf mit Marktdaten oder einen dedizierten H0-Kalibrierlauf, die in dieser Sandbox nicht existieren. Zehn neue Testdateien (`test_issue_836`…`test_issue_854_semantics_versioning.py`) + mehrere bestehende Fixtures korrigiert (`test_issue_743`, `test_issue_637`/`834_reward_semantics_bump.py` auf den Bump-Präzedenzfall aktualisiert). Volle Suite: 20 vorbestehende, umgebungsbedingte Fehlschläge (identisch vor/nach jedem Fix reproduziert, NICHT durch diesen Katalog verursacht) + eine bekannte Order-abhängige Pollution-Klasse (Cohorte-A-Tests zeigen dasselbe Symptom nur im Voll-Suite-Kontext, isoliert grün), alle neuen/geänderten Tests grün. | `automation/strategies/hourly_strategy_base.py`, `automation/optimizer/sweep.py`, `automation/optimizer/symbol_coverage.py` (neu), `automation/optimizer/confirm.py`, `automation/optimizer/champions.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/report.py`, `automation/optimizer/summary_de.py`, `automation/optimizer/invariants.py`, `automation/optimizer/parsing.py`, `automation/optimizer/purge_stale_studies.py`, `automation/backtest_runner.py`, `automation/requirements.txt`, `automation/config/optimizer.json`, `automation/config/tournament.json`, `automation/config/_required_keys.json` (neu), zehn neue `test_issue_83*`/`84*`/`85*`-Dateien, `automation/AGENTS.md` |
| 2026-07-30 | **Implementierung Issue-Katalog #817–#835 (GitHub-Issues #749/#750/#751 — Champion-Store-Härtung, Inferenz-Integrität, Durchsatz & Berichtswesen) + reward_semantics_version 17→18.** Drei aufeinander aufbauende Kataloge auf demselben 35-Stunden-Lauf (69/122 Symbole). **Kohorte A — Champion-Store (kein Purge, #817–#821):** #817/Pitfall #249 (`champion_max_holdout_gate_shortfall` — eine `REJECT_HOLDOUT_GATE`-Allowlist-Mitgliedschaft allein genügt nicht mehr, zusätzlich eine gedeckelte relative Unterschreitung nötig); #820/Pitfall #250 (`champion_min_tuning_edge` — 21/76 gespeicherte Champions waren schlechter als der ungetunte globale Default; `load_global_best` filtert auf tunbare Parameter); #819/Pitfall #251 (`params_schema_version` von `reward_semantics_version` getrennt — ein Reward-Bump markiert nur noch `quality_stale` statt Params + `corroboration_count` zu verwerfen); #818/Pitfall #237-Wiederkehr (`maybe_write_back` ohne Produktions-Call-Site — `sweep._attempt_champion_writeback` läuft jetzt nach jedem `store_champion`, achter Invarianten-Check); #821/Pitfall #252 (`store_champion` verlangt den Sweep-`run_id`; `corroboration_count` inkrementiert nur über distinkte `run_id`s; Schema-inkompatible, selbst nicht zulassungsfähige Einträge werden quarantiert). **Kohorte B — Inferenz-Integrität (Purge, #822–#827):** #823/Pitfall #254/#255 (Sortino-/PSR-Punktschätzer auf der INFORMATIVEN Bar-Teilmenge statt der vollen, ggf. 24/7-aufgefüllten Kalenderachse — 617 Guard-Trips im Quelllauf waren ein fehlspezifizierter Schätzer, kein Datenfehler; `sortino_min_downside_observations`, `STUDY_GUARD_DOMINATED`; `sortino_numeric_guard_min_periods` bewusst dokumentiert, ungesetzt gelassen); #822/Pitfall #253 (`n_family` zählt `oos_selection_statistic_available`-Trials statt blosser `oos_evaluated`-Aktivität); #824 (`bootstrap_psr_z`/`sample_skew_kurtosis` resampeln dieselbe informative Teilmenge); #826/Pitfall #256 (`promotion_family_scope='per_strategy'` — `confirm()` erhält N1, die eigene Study-Zahl, statt der symbolweiten Summe über alle Strategien); #827/Pitfall #257 (`selection_rule_homogeneity_policy`, `'fail'` bricht ein Symbol mit heterogener Selektionsregel fail-loud ab; Punkte 1/2 bereits strukturell durch #826 erledigt); #825 (`liquidated_trials`-Telemetrie-Alias; die Equity-Ruin-Ausschlussklausel existierte bereits seit v17/#801; die Wartungsmargin-/Liquidations-Simulation selbst bleibt zurückgestellt). **Kohorte C — Durchsatz/Closed-Loop/Berichtswesen (kein Purge, #828–#835):** #829/Pitfall #258 (`signal_absent` verlangte 90 % Budgetausführung, `#805` kappt dieselben Studies strukturell bei 28–46 % — Deadlock zwischen Abbruch- und Aktionsregel behoben); #830/Pitfall #258 (Kehrseite: `signal_quality` deaktivierte bislang unbedingt nach einer Beobachtung — unterliegt seither demselben Evidenzregime, PLUS neue `deprioritized`-Zwischenklasse mit halbiertem Budget); #831 (der `#763`/`#777`-Bounds-Vorschlag lief nur innerhalb `confirm()` — läuft jetzt zusätzlich im Post-Study-Pfad; `WIRED_OVERRIDE_STRATEGIES`, eine seit `#681` eingefrorene 3-von-14-Allowlist, durch eine abgeleitete Prüfung ersetzt); #828 (Worker-Deckelung `min(n_jobs, len(symbol_pairs))` entfernt — verwarf bis zu 8 von 22 konfigurierten Workern; `sweep_max_wallclock_h`-Guard); #833/Pitfall #237-Wiederkehr (der `#742`-Report entstand nur am Ende von `main()` — jeder Abbruch davor lieferte null Artefakt; `sweep.main()` erzeugt seither IMMER einen `run_status`-markierten Report, bevor der Fehler weitergereicht wird; `--report-only`); #832 (`summary_de.py`, deutschsprachiger Abschlussbericht, liest ausschliesslich das `#742`-Report-Dict, erbt die `#833`-Abbruchfestigkeit); #834 (`reward_semantics_version` 17→18, vier Auslöser: #822, #823, #824, #826); #835 (Pitfalls #249–#258, dieser Eintrag). **Bewusst zurückgestellt:** die Wartungsmargin-/Zwangsliquidations-Simulation in der `BacktestEngine` (#825), die zweistufige `'per_symbol_best'`-Korrektur (#826 Punkt 1, braucht einen eigenen H0-Kalibrierlauf), die Streichung des `min_win_rate`-OR-Arms (#827 Punkt 4), echtes Cross-Symbol-Pipelining + `SuccessiveHalvingPruner` (#828 Punkte 1/2/4 — Umstrukturierung der Kern-Dispatch-Schleife bzw. architektonisch wirkungslos wie beschrieben), individuelle Einzel-Trade-Listen mit Zeitstempeln (#832 Punkt 1 — würde neue State-Verfolgung in der höchstriskanten FIFO-Match-Schleife voraussetzen), literale Shard-Dateien (#833 Punkte 1/2 — die bestehende Proposal-plus-SQLite-Rekonstruktion leistet dieselbe Abbruchfestigkeit bereits bit-identisch), der gemeinsame H0-Kalibrierlauf (seit `#667`, jetzt fünf Kataloge unausgeführt) — alle erfordern einen echten Sweep-Lauf mit Marktdaten, der in dieser Sandbox nicht existiert. 32 neue Testdateien (`test_issue_817`…`test_issue_834_reward_semantics_bump.py`) + mehrere bestehende Fixtures korrigiert, die durch die `#822`-Zähl-, `#826`-Scope- und `#830`/`#831`-Default-Umstellungen unbeabsichtigt betroffen waren. Volle Suite: 20 vorbestehende, umgebungsbedingte Fehlschläge (identisch vor/nach jedem Fix reproduziert, NICHT durch diesen Katalog verursacht), alle neuen/geänderten Tests grün. | `automation/optimizer/champions.py`, `automation/optimizer/sweep.py`, `automation/optimizer/sweep_diagnostics.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/confirm.py`, `automation/optimizer/report.py`, `automation/optimizer/invariants.py`, `automation/optimizer/parsing.py`, `automation/optimizer/deflation.py`, `automation/optimizer/wallclock_guard.py` (neu), `automation/optimizer/summary_de.py` (neu), `automation/backtest_runner.py`, `automation/config/optimizer.json`, `automation/config/tournament.json`, 32 neue `test_issue_81*`/`82*`/`83*`-Dateien, `automation/AGENTS.md` |
| 2026-07-28 | **Implementierung Issue-Katalog #794–#815 (GitHub-Issues #745/#746 — Storage-Lebenszyklus, Inferenz-Korrektheit, Suchbudget & Selektions-Integrität) + reward_semantics_version 16→17.** Zwei gekoppelte Katalog-Audits auf demselben 21-Stunden-Absturz-Lauf. **Storage (kein Purge, #794–#800):** #796 (`copy_config=False`); #797 (Subprozess-Log-Policy); #800 (`bind_study_context`-Leak symmetrisch resettet); #794/Pitfall #243 (kontinuierliche statt Sweep-Ende-Retention); #798 (`period_returns` nach Parsing gestrippt); #795 (`disk_guard` bricht vor dem nächsten Symbol ab); #799 (Per-Symbol-Sweep-Schleife transaktional, isolierter Symbol-Fehler statt Total-Abbruch). **Kohorte A — Inferenz (P0, Purge):** #801/#802/Pitfall #240/#241/#242 (`skipna=False`, `assert_pandas_version_supported`-Preflight); #803 (`REJECT_OOS_INVALID_METRICS` unbedingt); #804/Pitfall #239 (strukturierter `inference_diagnostics`-Rückkanal statt Subprozess-`logging`). **Kohorte B — Suchbudget (P0/P1):** #805/Pitfall #244 (`structural_min_modelled_trials_per_dim`, fail-loud gegen den degenerierten `floor_plateau_k=0`); #806/Pitfall #245 (`plateau_stop_missed_probability`, Dreierregel); #807 (Symbol-Degeneriertheits-Sekundärsignal); #808 (`gradient_signal_arm`, drei gleichrangige Arme); #809 (`GapContinuationStrategy` deaktiviert, bewusste Abweichung von Variante B). **Kohorte C — Selektion (P0/P1, Purge #812/#813/#814):** #810/Pitfall #246 (`gate_consolidation_priority`/`_protected` deklarativ statt eingefrorener Konstante); #811/Pitfall #247 (Jaccard-Pass-Set-Redundanz statt Spearman-Rangkorrelation der Gate-Deltas); #812/Pitfall #248 (`any_arm_unreachable_policy` Default `'drop_arm'`, `selection_rule_fingerprint`); #813 (`oos_period_returns` für ALLE `oos_evaluated` Trials, `deflation_cluster_coverage`); #814 (`deflation_family_floor_mode` Default `'attempted'`, `deflation_search_space_penalty`-Term). **Governance:** #815 (`reward_semantics_version` 16→17, vier Auslöser: #801/#802, #803, #812, #813/#814); #816 (Pitfalls #239–#248, dieser Eintrag). **Bewusst zurückgestellt:** #813-Umsetzungspunkt 2 (Autokorrelations-Signatur, "Entscheidung nach Messung, nicht vorab"); der H0-Kalibrierlauf (#814) sowie der reale Re-Run selbst (Spearman(n_family, Budgetausführung), `deflation_cluster_coverage≥0.95`, `#815`-Purge-Nachweis) — erfordern einen echten Sweep mit Marktdaten, der in dieser Sandbox nicht existiert. 91 neue Tests über 14 neue Testdateien + mehrere bestehende Fixtures korrigiert (#810→#811-Algorithmus-Umstellung, #812/#814-Default-Wechsel). Volle Suite: 20 vorbestehende, umgebungsbedingte Fehlschläge (identisch vor/nach jedem Fix reproduziert, NICHT durch diesen Katalog verursacht), alle neuen/geänderten Tests grün. | `automation/optimizer/run_optimization.py`, `automation/optimizer/confirm.py`, `automation/optimizer/report.py`, `automation/optimizer/invariants.py`, `automation/optimizer/reward.py`, `automation/optimizer/sweep.py`, `automation/optimizer/deflation.py`, `automation/optimizer/parsing.py`, `automation/optimizer/manifest.py`, `automation/optimizer/retention.py`, `automation/optimizer/disk_guard.py`, `automation/backtest_runner.py`, `automation/strategies/gap_continuation.py`, `automation/config/optimizer.json`, `automation/config/tournament.json`, `automation/config/strategies.json`, 14 neue `test_issue_79*`/`80*`/`81*`-Dateien, `automation/AGENTS.md` |
| 2026-07-26 | **Implementierung Issue-Katalog #768–#793 (GitHub-Issues #743/#742 — Budget-Skalierung, Renditeserien-Kohärenz, DSR-Multiplizität & Denylist-Evidenz) + reward_semantics_version 15→16.** Zwei aufeinanderfolgende Forensik-Audits auf einem 44,2 %/13,1 %-Budgetausführungs-Lauf. **Kohorte A (kein Purge):** #768/Pitfall #227 (`plateau_min_modelled_trials_per_dim`, dimensionsskalierte ZERO_ELIGIBLE-Modellierungsschwelle); #769/Pitfall #228/#229 (`floor_plateau_k` explizit dokumentiert; `'signal_frequency'` in `'signal_absent'`/`'signal_sparse'` aufgespalten — parameterunabhängig vs. -abhängig); #770 (`compute_budget_execution` + `check_budget_execution`, `budget_executed_fraction` als First-Class-Studien-/Sweep-Kennzahl). **Kohorte B/C (Purge):** #771/#772/#773/Pitfall #230/#231 (`total_return`/`period_rets`/Buy&Hold-Benchmark auf DIESELBE Fold-Segment-Vereinigung umgestellt, `assert_return_series_identity` + `check_log_return_coherence` als Study-Abschluss-Wächter statt Report-Nachtrag); #774/#775/Pitfall #232 (Turnover-Strafe konsumiert `round_trip_cost_bps` asset-class-aufgelöst statt des TSLA-kalibrierten `penalty_turnover_weight`; `_read_default_round_trip_cost_bps` nutzt die Symbol→Asset-Class→DEFAULT-Kette). **Kohorte D (Purge):** #776/#792 (`oos_min_excess_return` aus `eligible_requires_all` entfernt — |ρ|≥0,98 mit `oos_max_drawdown`/`oos_min_psr`; `gate_collinearity_threshold` als EINE deklarative Schwelle für alle drei Kollinearitäts-Einstiegspunkte; `check_gate_collinearity_consolidation` konsumiert den `#679`-Alarm sweep-weit). **Kohorte I (Purge nur #784):** #790/Pitfall #238 (`near_miss_deltas`→`{binding, soft}`, `binding_gate` ausschliesslich aus aktiven Gates); #786 (`holdout_gate_deltas`/`holdout_binding_gate` für JEDE Holdout-Ablehnung); #783/Pitfall #234 (`PROMOTE_GLOBAL_DEFAULT`-Status + `promotion_route`-Feld, GETRENNT von `READY_FOR_PR`; Budget-Vorbedingung `global_default_promotion_min_budget_execution`); #785/Pitfall #236 (`decision_chain` mit `passed=True/False` je Stufe, `check_rejection_chain_completeness` prüft jetzt den PROMOTETEN Pfad); #791 (`inference_method` als `{method, applied, skipped_reason}`, `'not_applicable'` als dokumentierte Nichtanwendbarkeit); #784/Pitfall #235 (`_family_n_from_studies` zählt `oos_evaluated` statt `oos_eligible`, `deflation_family_floor_mode='budgeted'` hebt abgebrochene Studies auf das geplante Budget); #789 (`check_sr0_coherence` erweitert auf den stillen Auslassungsfall). **Kohorte J (Purge #788):** #788/Pitfall #225-Klasse (`make_symbol_objective` stempelt OOS-Metrik-User-Attrs NUR bei `oos_evaluated=True`, `check_metric_sentinel_absence` auf sieben Metriken erweitert); #787/Pitfall #237 (TEILWEISE — `#762` als in der Wirkung widerlegt dokumentiert, `binding_gate_histogram_by_strategy` im Report; die volle Bounds-Kalibrierung/der PR-Deaktivierungsbeschluss für die vier 0-eligible-Strategien bleibt Restarbeit). **Kohorte E/G (kein Purge, parallel):** #777/Pitfall #232-Klasse (Bounds-Vorschlag feuert jetzt bei JEDER Randlösung `>0,3`, nicht erst ab `0,5`; `bounds_widening_factor` deklarativ, `max_bars_in_trade` hart auf die `#714`-Zeitbox gedeckelt); #778 (`recommend_diagnosis_action` eskaliert `'signal_sparse'`/`'hold_duration'` NIE mehr auf `'denylist'`; `'signal_absent'` nur bei `budget_executed_fraction>=0.9` UND `n_runs_confirmed>=2`; Cache-Einträge tragen `first_seen_run_id`); #780/Pitfall #233 (`log_manager.bind_study_context`, contextvars-basierte Study-Identität für JEDE Log-Zeile/JSONL-Zeile im parallelen Sweep). **Governance:** #781 (`reward_semantics_version` 15→16, fünf Auslöser: #771/#772, #774/#775, #776, #784, #788); #782/#793 (Pitfalls #227–#238, dieser Eintrag). **Bewusst zurückgestellt:** #779 (Reward-Term-Rekalibrierung, eigener Bump v17) — erfordert einen vollständigen Re-Run mit ≥ 50 Studies NACH allen Fixes dieses Katalogs, der in dieser Sitzung nicht produziert wurde; keine Ersatz-Kalibrierung mit erfundenen Werten. Zwölf neue Testdateien (`test_issue_776`/`792`/`784`/`788`/`777`/`778`/`780`/`781`, je 7-14 Tests) + mehrere bestehende Fixtures korrigiert, die durch die `'signal_sparse'`-Denylist-Aufhebung (#778) und die `oos_min_excess_return`-Entfernung (#776) unbeabsichtigt betroffen waren (`test_issue_649`, `test_issue_697`, `test_issue_699`, `test_issue_760`, `test_issue_652`, `test_issue_695`, `test_issue_681`, `test_issue_637`). Volle Suite: 21 vorbestehende, umgebungsbedingte Fehlschläge (identisch via `git stash` reproduziert, NICHT durch diesen Katalog verursacht), alle neuen/geänderten Tests grün. | `automation/optimizer/run_optimization.py`, `automation/optimizer/confirm.py`, `automation/optimizer/report.py`, `automation/optimizer/invariants.py`, `automation/optimizer/reward.py`, `automation/optimizer/sweep.py`, `automation/optimizer/sweep_diagnostics.py`, `automation/optimizer/parsing.py`, `automation/optimizer/spaces.py`, `automation/optimizer/champions.py` (nur verifiziert, kein Change nötig), `automation/backtest_runner.py`, `automation/log_manager.py`, `automation/config/optimizer.json`, `automation/config/tournament.json`, zwölf neue `test_issue_77*`/`78*`-Dateien, `automation/AGENTS.md` |
| 2026-07-18 | **Implementierung Issue-Katalog #710–#717 (Time-Box-Reward, Dynamisches Take-Profit & Live-Guardrails) + reward_semantics_version 13→14.** Acht Fixes über drei unabhängige Tracks (siehe Issue #707 §2 Merge-Order). **Track 1 (Objective/Suchraum):** #710/Pitfall — `oos_median_bars_held`/`oos_p95_bars_held` (Bars, 1h) in `_calculate_stats`/`TournamentMetrics`/`parse_tournament` verdrahtet, reine Telemetrie, KEIN Bump für sich genommen; #711/Pitfall #206–#208 — additiver `time_box_penalty`-Term (`penalty_time_box_weight·(oos_median_bars_held/time_box_bars)²·penalty_scale_vs_base`) NEBEN `dd_penalty`/`turnover_penalty`, `base` bleibt `psr_z` UNVERÄNDERT (Req-04 wörtlich hätte die Base ersetzt — Rückschritt hinter #559–#702), Default `penalty_time_box_weight=0.0` ⇒ bit-identisch; `assert_penalty_scale_calibrated` deckt den neuen Term ab UND wurde auf Median-über-AKTIVE-Terme gehärtet (ein struktureller inaktiver Term darf die Guard-Schärfe für andere Terme nicht verwässern, Pitfall #208); #712 — vereinheitlichtes dynamisches Take-Profit (`compute_dyn_tp_target`, `TP(t)=entry±γ·ATR·exp(−λ·bars/max_bars)`) in `HourlyStrategyBase` für alle 15 Strategien, Cancel/Replace nur bei >1-Tick-Delta, Default `dyn_tp_enabled=False`=bit-identisch; #713 — `dyn_tp_enabled/lambda/gamma` einheitlich in `spaces.sample_params` angehängt (nicht pro Strategie depliziert), konditionales Sampling. **Track 2 (Guardrails, unabhängig parallel):** #714/Pitfall #210 — 24-Bar-Zeitbox: `DEFAULT_MAX_BARS_IN_TRADE`/alle `spaces.py`-Obergrenzen auf ≤24 geklemmt, HARTE Konstruktor-Klemmung (`MAX_BARS_IN_TRADE_HARD_CAP`) für Alt-Configs aus dem Cache; #715/Pitfall #211 — Pre-Trade-Spread-Gate (`_compute_quantity`, `SPREAD_GATE_REJECT`), Schwelle `k_spread·spread_bps_model` aus `backtest.json` abgeleitet (Single Source of Truth mit dem Backtest-Kostenmodell); #716/Pitfall #212 — node-weiter `max_aggregate_open_positions`-Cap ZUSÄTZLICH zum per-Strategie-Cap + harter `max_order_notional`-Deckel, konservative AKTIVE Defaults (5 Positionen / 2000 USD, Guardrails sind bewusst NICHT opt-in); #717/Pitfall #209/#213 — `_StateManager` erweitert auf `{positionId, entry_ns, entry_bar_seq}` (migrationssicher, sticky Entry-Anker), `HourlyStrategyBase._reconcile_after_reconnect` rehydriert `_bars_in_position` bei `on_start()` aus dem Bar-Cache relativ zu Nautilus' nativem `pos.ts_opened` (Wall-Clock-Fallback ≥24h bei leerer Historie) und liquidiert sofort bei bereits abgelaufenen Positionen; `EToroExecutionClient._reconcile_positions_on_connect` erkennt Phantom-Positionen (eToro bereits geschlossen) via PnL-REST-Diff und publiziert ein msgbus-Signal statt selbst Order-State zu fabrizieren — die Strategie schliesst über den bestehenden `_close_position_base`-Pfad (schliesst `[EX-2-followup]`). **Purge-Klassifikation:** GENAU #711 bumpt `reward_semantics_version` (13→14, neue Skalen-Konstanten `penalty_time_box_weight`/`time_box_bars`); #710/#712–#717 sind purge-frei (Telemetrie-only bzw. Default-AUS-opt-in bzw. Guardrail-Code ohne Reward-Wirkung). Neue Pitfalls #206–#213 (§ „Issue-Katalog #710–#717"). Zehn neue Testdateien (`test_issue_710`…`test_issue_717_*`, 4 Dateien für #717 [State-Manager-Migration, Strategie-Reconnect, Execution-Reconcile-Diff]), insgesamt ~140 neue Tests. Volle Suite: 1347 passed (5 vorbestehende, umgebungsbedingte Fehlschläge — identisch via `git stash` reproduziert, NICHT durch diesen Katalog verursacht); zwei bestehende Fixtures korrigiert, die unbeabsichtigt vom Versions-Bump betroffen waren (`test_issue_697_gate_consolidation.py`, `test_issue_702_champion_warmstart.py` — beide nutzten hartkodierte statt dynamisch aus `optimizer.json` gelesene `reward_semantics_version`-Referenzen). | `automation/backtest_runner.py`, `automation/optimizer/parsing.py`, `automation/optimizer/reward.py`, `automation/optimizer/spaces.py`, `automation/strategies/hourly_strategy_base.py`, `automation/adapters/etoro_state_manager.py`, `automation/adapters/etoro_execution.py`, `automation/config/optimizer.json`, `automation/tests/test_issue_710_bars_held_metric.py` (neu), `automation/tests/test_issue_711_time_box_penalty.py` (neu), `automation/tests/test_issue_712_dynamic_take_profit.py` (neu), `automation/tests/test_issue_713_dyn_tp_search_space.py` (neu), `automation/tests/test_issue_714_bar_time_box.py` (neu), `automation/tests/test_issue_715_spread_gate.py` (neu), `automation/tests/test_issue_716_aggregate_exposure_cap.py` (neu), `automation/tests/test_issue_717_state_manager_migration.py` (neu), `automation/tests/test_issue_717_reconnect_reconciliation.py` (neu), `automation/tests/test_issue_717_execution_reconciliation.py` (neu), `automation/tests/test_issue_637_reward_semantics_bump.py`, `automation/tests/test_issue_697_gate_consolidation.py`, `automation/tests/test_issue_702_champion_warmstart.py`, `automation/tests/test_combo_conjunction_switches.py`, `automation/tests/test_issue_689_squeeze_breakout.py`, `automation/tests/test_optimizer_loop.py`, `automation/AGENTS.md` |
| 2026-07-18 | **Implementierung Epic #702 (Issues #703–#710) — Iterativer Champion-Warm-Start & symbol-skopierte Default-Nachführung.** Neues Modul `automation/optimizer/champions.py`: `store_champion` (#703, Champion-Store unter `data/optimizer/champions/`, aufgerufen von `sweep.py` unmittelbar nach `export_symbol_proposal`, nur im echten Storage-Pfad, fail-open); `load_champion_seed`/`load_champion_entry` (#704, neue Tier-Stufe `global_best → champion → strategy_defaults → none` in `run_optimization.resolve_symbol_shrinkage_seed`, additiv-optional via `symbol`/`opt_data`-Kwargs — HI-2-rückwärtskompatibel, Legacy-Aufrufer ohne diese Argumente bit-identisch); `champion_is_admissible` (#705, EINE zentrale Guard-Funktion für Schreiben UND Lesen: reward-version, override-keys>0, Rejection-Allowlist `{None, REJECT_HOLDOUT_DSR_DROP, REJECT_HOLDOUT_BOOTSTRAP_CI, REJECT_HOLDOUT_GATE}` — explizit OHNE `REJECT_SELECTION_PBO`/`REJECT_BOUNDARY_SOLUTION`, R_symbol-Floor, Demotion-Schwelle); `maybe_write_back` (#706, Writeback nach neuem `automation/config/strategy_symbol_seeds.json`, symbol-skopiert, NIE `strategy_defaults.json`, Gate: Korroboration UND Fensterfortschritt ODER echte READY_FOR_PR-Promotion; `optimizer/resolve.py::resolve_params`-Präzedenz additiv erweitert); Regions-Korroboration + Erhalt-vs-Ersetzung-Merge-Logik (#707, wiederverwendet `bounds.normalized_param_distance`, dieselbe Metrik wie die A4.3-Shrinkage); Regime-Degradation-Demotion (#708, `degrade_streak`, entfernt Store- + Seed-Eintrag); Study-User-Attr-Telemetrie in `optimize_symbol` (#709, `champion_seed_source`/`champion_R_symbol_at_store`/`champion_corroboration_count`/`champion_age_days`/`champion_window_advanced`/`champion_writeback_applied`, Log-Parität mit `shrinkage_*`); 49 neue Tests in `test_issue_702_champion_warmstart.py` (#710, inkl. #694-Kopplungs-Sanity). Sechs neue `champion_*`-Config-Keys in `optimizer.json` (Zero-Hardcoding, Default-Werte dokumentiert bit-identisch zum Pre-Epic-Verhalten bei `champion_enabled=false`). **Purge-Klassifikation:** das gesamte Epic ist purge-frei, KEIN `reward_semantics_version`-Bump (P0–P2 ändern die Gate-Mathematik nicht — ein Champion-Seed ist nur ein weiterer Trial, der erneut durch alle bestehenden Gates läuft; die #711-Bounds-Recentering-Option bleibt bewusst zurückgestellt/nicht umgesetzt). **Kritische Vorbedingung verifiziert:** #694/#695 (`cpcv.cluster_effective_configs` auf der DSR-Familien-Ebene, Pitfall #190) war bereits vor Epic-Start implementiert — die in Issue #705 §9 verlangte Reihenfolge (erst declustern, dann warm-starten) ist damit erfüllt, test-gesichert statt nur angenommen. Neue Pitfalls #201–#205 (§ „Epic #702"). Volle Suite (1170 passed, dieselben 20 vorbestehenden, umgebungsbedingten Fehlschläge wie vor dieser Änderung — verifiziert via `git stash`, identische Fehlerliste ohne/mit diesem Epic). | `automation/optimizer/champions.py` (neu), `automation/optimizer/sweep.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/resolve.py`, `automation/config/optimizer.json`, `automation/config/strategy_symbol_seeds.json` (neu), `automation/tests/test_issue_702_champion_warmstart.py` (neu), `automation/AGENTS.md` |
| 2026-07-18 | **AGENTS.md-Doku-Härtung (GH-#705, "absolut wasserdicht dokumentiert").** Reine Dokumentations-Integrität, kein Code-/Verhaltens-Fix, keine `reward_semantics_version`-Wirkung. (1) **TOC-Vollständigkeit:** Inhaltsverzeichnis um einen "Anhang"-Block mit allen bislang unverlinkten `##`-Sektionen ergänzt (Architectural Invariant, Audit #470, IS/OOS-Methodik, Known-Pitfalls-Fortsetzung, Walk-Forward-Validation, sowie sämtliche Bug-Kaskaden-/Issue-Kataloge #521–702). (2) **Drei fehlende `##`-Parent-Header ergänzt:** Issue-Katalog #663–#672, #675–#686 und #695–#702 hingen bisher headerlos unter `## Bug-Kaskade #649–#660`, obwohl inhaltlich eigenständige, bereits an anderer Stelle vollständig dokumentierte Kataloge (Config-Keys-Tabelle + Watertight-Invariants-Block existierten je Katalog bereits) — jetzt mit eigenem `##`-Header + kurzer Einleitung, konsistent zum Muster der älteren Kataloge. (3) **Pitfall-Nummern-Kollisionen aufgelöst:** Drei verschiedene `### Pitfall #89`-Header (OOS-Eval/IS-Gate, Reward-Shaping-Monotonie, Semantics-Guard) und zwei verschiedene `### Pitfall #93`-Header (oos_covered Lower-Bound, Annualisierungsfaktor) kollidierten auf denselben Nummern — je EIN kanonischer Header (identifiziert per Cross-Referenz aus Axiom A9 bzw. der eigenen Einleitung) behält seine Nummer, die übrigen wurden auf neue, bislang unbenutzte Nummern **#198–#200** umnummeriert (kein Content gelöscht). (4) **Axiom A8** referenzierte fälschlich "Pitfall #88-Konditionierung" (Pitfall #88 ist tatsächlich Leakage-in-OOS-Window, ein anderes Thema) — auf direkte Issue-#468-Referenz korrigiert, kein Pitfall-Header existiert dafür. (5) **§16-Einleitung** um eine Nummerierungs-Konvention ergänzt (globale Eindeutigkeit über die ganze Datei, `grep`-Pflicht vor Vergabe einer neuen Nummer), um künftige Kollisionen zu verhindern. | `automation/AGENTS.md` |
| 2026-07-18 | **Implementierung Issue-Katalog #695–#702 (Optimizer — DSR-Familien-Decluster, Gate-Konsolidierung, Strategie-Closed-Loop) + reward_semantics_version 12→13 + Purge-Klassifikation (Pitfalls #190–#197).** Acht verzahnte Fixes. **#695/Pitfall #190** (`deflation_family_period_returns` declustert die DSR-Familien-Multiplizität via `cpcv.cluster_effective_configs`, dieselbe Pearson-Schwelle wie PBO — `deflation_n_effective = max(deflation_n, deflation_n_family_effective)`, #652-Invariante gewahrt); **#696/Pitfall #191** (`deflation_n_effective` war ein Fehlname vor #695 — trug die ROHE statt der effektiven Zahl, jetzt korrigiert + `deflation_n_family_raw`/`deflation_n_family_effective` explizit telemetriert); **#697/Pitfall #192** (`min_expectancy` aus `eligible_requires_all` entfernt — |ρ|=0.961 kollinear zu `oos_min_psr`, superseded den #657-Zwischenstand; `reward.assert_eligible_requires_all_not_redundant` als neuer Fail-Loud-Konsument des #679-Alarms; `calibration.calibrate_gate_consolidation_false_positive_rate` verifiziert per Monte-Carlo, dass die FP-Rate durch die Konsolidierung nicht steigt — **EINZIGER** Fix dieses Katalogs mit gestempelter Eligibility-Wirkung); **#698/Pitfall #193** (`invalid_on_continuous_bars`-Flag für `GapContinuationStrategy` — ein Gap auf synthetischen 24/7-Bars misst nur die Differenz zweier aufeinanderfolgender Bars, kein Bounds-Problem); **#699/Pitfall #194** (zwei unabhängige Strategie-Code-Defekte statt eines Bounds-Problems: `AdxAtrMomentumStrategy`s toter `DirectionalMovement.value`-ADX-Gate durch EMA-Steigung ersetzt [dieselbe NautilusTrader-1.230.0-Klasse Defekt wie Pitfall #189]; `TrendPullbackStrategy` fehlte der verbindliche `_check_exits_and_update(bar)`-Aufruf, dadurch 0 Exits ausser Gegensignal; zusätzlich `previously_recommended_override` eskaliert eine wiederholte `search_space_override`-Empfehlung auf `denylist`); **#700/Pitfall #195** (`ZERO_ELIGIBLE_PLATEAU` verlangte `all(evaluated)`, ein GEMISCHTER Cohort — z. B. `SqueezeBreakoutStrategy` mit Trade-Cap-Treffern — fiel dadurch durch beide Early-Stop-Netze; Fix: Bedingung nur noch auf die EVALUIERTEN Trials bezogen, neue `eligibility_curve`-Fensterdiagnose); **#701/Pitfall #196** (`deflation_var_floor`, seit Pitfall #187 als DEPRECATED markiert, nach Verifikation der Unerreichbarkeit von `n_periods` VOLLSTÄNDIG entfernt — `sr0_multiple_testing_robust` verlangt `n_periods` jetzt als Pflicht-Keyword, `theoretical_var_source` immer `'lo2002'`; DSR-Drop-Rejection bleibt konservativ bei `deflation_sr0 is None`); **#702/Pitfall #197** (Purge-Disziplin: GENAU #697 bumpt `reward_semantics_version` [12→13], die übrigen sieben Fixes sind purge-frei [Confirm-/Telemetrie-/Strategie-Code-/Diagnose-only oder Entfernung bereits toten Codes] — Kapitel 7 in `manuals/strategie_optimierung.md` um die katalogspezifische Klassifikationstabelle ergänzt). Test-gesichert: 6 neue Test-Dateien (`test_issue_695`/`697`/`698`/`699`/`700`/`701`, 65 neue Tests) + mehrere bestehende Fixtures korrigiert, die unbeabsichtigt betroffen waren (`test_issue_637`, `test_issue_657`, `test_issue_670`, `test_issue_576`, `test_issue_651`, `test_issue_653`, `test_issue_685`, `test_issue_686`). | `automation/optimizer/confirm.py`, `automation/optimizer/sweep.py`, `automation/optimizer/sweep_diagnostics.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/deflation.py`, `automation/optimizer/reward.py`, `automation/optimizer/calibration.py`, `automation/strategies/adx_atr_momentum.py`, `automation/strategies/trend_pullback.py`, `automation/optimizer/spaces.py`, `automation/config/tournament.json`, `automation/config/optimizer.json`, `automation/config/strategies.json`, `manuals/strategie_optimierung.md`, `automation/AGENTS.md` |
| 2026-07-17 | **Regime-Roster-Erweiterung: fünf neue Strategien (Issues #689–#693, Guide #688).** `SqueezeBreakoutStrategy` (#689, Volatilitäts-Expansion nach Kontraktion), `OpeningRangeBreakoutStrategy` (#690, Momentum-Ignition am Tagesbeginn), `DonchianRegimeBreakoutStrategy` (#691, Trend-Fortsetzung nur im Trend-Regime), `Rsi2ReversionStrategy` (#692, Kurzfrist-Reversion im übergeordneten Trend), `GapContinuationStrategy` (#693, Overnight-/Event-Gap-Fortsetzung) — jede nach dem 5-Datei-Muster (Strategie-Modul, `strategy_defaults.json`, `strategies.json`-Registrierung, `spaces.py`-Zweig) und ausschliesslich auf `HourlyStrategyBase`-Exit-Verwaltung aufgesetzt. **Trockenlauf-Befund (Pitfall #189):** `DirectionalMovement.value` liefert in der installierten NautilusTrader-Version (1.230.0) konstant `0.0` (verifiziert per direktem Indikatortest, `.pos`/`.neg` funktionieren) — dieselbe Klasse Defekt wie das bereits dokumentierte `AdxAtrMomentumStrategy`-Problem. `DonchianRegimeBreakoutStrategy` nutzt daher den SPEC-vorbereiteten Fallback (EMA-Steigung statt ADX-Schwelle); `adx_period`/`adx_threshold` aus dem Suchraum entfernt (Phantom-Tuning-Vermeidung). Alle fünf Strategien über echte NautilusTrader-BacktestEngine-Läufe (isolierter Subprozess, `run_single_backtest_worker`) auf synthetischen Regime-Daten verifiziert (Trades: Squeeze 7, ORB 68, Donchian 34, RSI2 59, Gap 11) — kein STRUCTURAL_ALL_UNEVALUABLE. 5 neue Test-Dateien (`test_issue_689`–`test_issue_693`), je mit 5-Datei-Checkliste + Backtest-Smoke-Test. Zwei dieser Tests (direkte `Bar`-Konstruktion) sind zusätzliche, bestätigte Instanzen der bereits bekannten `test_issue_489`-`sys.modules`-Mock-Pollution (nur im VOLLEN Suite-Lauf sichtbar, isoliert grün) — keine Regression. | `automation/strategies/squeeze_breakout.py` (neu), `automation/strategies/opening_range_breakout.py` (neu), `automation/strategies/donchian_regime_breakout.py` (neu), `automation/strategies/rsi2_reversion.py` (neu), `automation/strategies/gap_continuation.py` (neu), `automation/config/strategy_defaults.json`, `automation/config/strategies.json`, `automation/optimizer/spaces.py`, `automation/AGENTS.md` |
| 2026-07-17 | **Implementierung Issue-Katalog #675–#686 (Optimizer — Mathematische Exzellenz, Sitzung 2026-07-17, Lauf 2) + reward_semantics_version 11→12.** Zwölf verzahnte Fixes in vier Kohorten. **Kohorte A (Validierungs-Geometrie):** #675/Pitfall #177 (opt-in `walk_forward.retrain`-Modus in `compute_fold_boundaries`, embargo-sicheres rollierendes IS-Fenster je Fold, additive `rolling_fold_is_oos_divergence`-Diagnose; Default `false`=bit-identisch — echtes Per-Fold-Parameter-Refit ist architektonisch out-of-scope, siehe Docstring); #676/Pitfall #178 (`oos_profitable_folds_frac`-Nenner-Bugfix auf `oos_folds_evaluable` statt `oos_folds_total`; Gate aus dem Default `eligible_requires_all` entfernt — redundant zu `fold_dispersion_weight`+PBO); #677/Pitfall #179 (`oos_min_evaluable_folds` aus demselben Grund aus dem Default entfernt + optionaler relativer Schwellen-Modus). **Kohorte B (Promotion-Kalibrierung):** #678/Pitfall #180 (der behauptete Kalibrier-Deadlock existierte nicht — `calibrate_t_adaptive_confidence` neu, Verifikation bei T≈36 bestätigt `promotion_correction_mode='conjunction'` ERNEUT); #679/Pitfall #181 (`gate_collinearity_redundancy_alarm` — strukturierter Alarm statt reinem Log, PSR-priorisierte Konsolidierungs-Empfehlung); #680/Pitfall #182 (`any_arm_unreachable_policy` Default `'warn'→'recalibrate'`, Mechanik existierte bereits seit #668). **Kohorte C (Suchraum & Deployment):** #681/Pitfall #183 (Diagnose-Closed-Loop über einen SEPARATEN Auto-Cache, `diagnosed_pairs_cache.json` — NIE die menschlich-kuratierte Denylist direkt); #682/Pitfall #184 (`PROMOTE_GLOBAL_DEFAULT_ON_SYMBOL`-Route in `confirm_per_symbol_promotion`, wenn der globale Vektor selbst das Symbol-Holdout besteht und 0 symbol-eligible Trials vorliegen). **Kohorte D (Statistische Verfeinerung):** #683/Pitfall #185 (PBO-Split-Metrik: Gruppen-Mittelwert → Gruppen-Sortino; `cpcv.cluster_effective_configs` reduziert Near-Duplicate-Configs vor der CSCV-Partitionierung); #684/Pitfall #186 (Expectancy-Gate-Fallback bei fehlender Kosten-Telemetrie: `_read_default_round_trip_cost_bps()`, Config-abgeleitet statt der 13×-strengeren statischen Konstante); #685/Pitfall #187 (`deflation_var_floor` mit unmissverständlichem `⚠️ DEPRECATED`-Marker versehen). **Zuletzt:** #686/Pitfall #188 (`reward_semantics_version` 11→12, PRO-Issue begründet — GENAU drei Fixes #676/#677/#684 ändern die gestempelte `oos_eligible`-Semantik; neues Bulk-Purge-Werkzeug `automation.optimizer.purge_stale_studies` ergänzt den bestehenden In-Process-Guard). Alle Fixes test-gesichert (12 neue `test_issue_67[5-9]_*`/`test_issue_68[0-6]_*`-Dateien + mehrere bestehende Fixtures korrigiert, die unbeabsichtigt von den Fixes betroffen waren: `test_issue_550`, `test_issue_563`, `test_issue_615`, `test_issue_619`, `test_issue_637`, `test_issue_649`, `test_issue_663`, `test_issue_672`). Volle Suite (Einzeldateien/-subsets) grün; 18 vorbestehende, ausschliesslich beim VOLLEN Suite-Lauf auftretende Test-Isolations-Fehlschläge (u. a. `test_allocator.py`, `test_sizing_precedence.py`, `test_live_allocator_smoke.py`, `test_live_execution_defaults.py`) sind NACHWEISLICH unabhängig von diesem Katalog (identisch reproduzierbar via `git stash -u` auf dem Ausgangs-Commit `634ef483`: 18/18 identische Fehlschläge, 948 passed ohne die 82 neuen/erweiterten Tests dieses Katalogs). | `automation/backtest_runner.py`, `automation/optimizer/confirm.py`, `automation/optimizer/cpcv.py`, `automation/optimizer/deflation.py`, `automation/optimizer/reward.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/sweep.py`, `automation/optimizer/sweep_diagnostics.py`, `automation/optimizer/calibration.py`, `automation/optimizer/purge_stale_studies.py` (neu), `automation/config/backtest.json`, `automation/config/tournament.json`, `automation/config/optimizer.json`, `manuals/strategie_optimierung.md`, `automation/AGENTS.md` |
| 2026-07-16 | **Optuna ValueError / Feasible Region Limit.** Implementation of exception-based fallback handling for non-feasible trial exports. `study.best_value` raises `ValueError` if 0 feasible trials are present (e.g., early stop by `STRUCTURAL_ALL_UNEVALUABLE`). Added `try...except ValueError` block in `export_symbol_proposal` to assign `None` as fallback, ensuring JSON null-compatibility downstream. | `automation/optimizer/confirm.py` |
| 2026-06-28 | **AUDIT #470 — Strikte Verifikation #460–#477 + Härtung.** Code-/Git-/Test-Audit der Root-Cause-Fixes inkl. der OOS-Anchor-Bug Kaskade (#471–#477). **Befunde & Remediation:** (1) #467-Testdatei mutierte `sys.modules` (pyarrow/nautilus MagicMock) prozessweit ⇒ 10 fremde Tests gekippt + Selbstbruch + tautologischer Test; ersetzt durch echten Test der neuen SSOT `compute_fold_boundaries`. (2) Reale Issue #465 (Audit #466, „total_return via 100%-Kapital-Aufzinsung") war als *completed* markiert, aber NICHT implementiert (kein `test_issue_466_portfolio_return.py`); `total_return` jetzt aus MtM-Equity `equity_end/equity_start−1` abgeleitet (Fallback sequentiell), Test ergänzt. (3) #467/#468-Strict-Isolation (`oos_min_*` Pflicht, fail-loud) brach #461/#401-Tests + war latent crash-anfällig bei `weights` ohne `tournament_cfg`; `compute_reward` lädt `tournament.json` im Constraint-Pfad nach; Fixtures auf `oos_min_*` migriert. (4) #468/#469-Versionsguard (warn→`raise`, Version 4→6) brach 5 #410-Tests; auf fail-loud-Contract aktualisiert. (5) Fold-Geometrie als SSOT `compute_fold_boundaries` extrahiert (4 Inline-Duplikate ersetzt). Harte Axiome A1–A9 dokumentiert. Erweiterung um OOS-Fixes #471–#477 (OOS-Gate Entkopplung, Bounded-Union `oos_covered`). Suite: 413 passed / 0 failed. | `automation/backtest_runner.py`, `automation/optimizer/reward.py`, `automation/tests/test_issue_466_portfolio_return.py`, `automation/tests/test_issue_467_fold_geometry.py`, `automation/tests/test_issue_461_reward_no_inversion.py`, `automation/tests/test_issue_410_reward_versioning.py`, `automation/tests/test_issue_471_oos_eval_decoupled_from_is_gate.py`, `automation/tests/test_issue_472_shaping_not_overtrading_monotone.py`, `automation/tests/test_issue_474_single_pass_contiguous_oos.py`, `automation/tests/test_issue_475_oos_covered_bounded_union.py`, `automation/tests/test_issue_489_embargo_shifts_not_shrinks.py`, `automation/tests/test_issue_492_preflight_backtest_anchor_parity.py`, `automation/tests/test_issue_493_rejection_taxonomy.py`, `automation/AGENTS.md` |
| 2026-06-27 | **Implementierung Issue #460: Pitfall #85 Holdout Reachability & Anchor-Clamp** | `automation/optimizer/trial_config.py`, `automation/optimizer/confirm.py`, `automation/optimizer/sweep.py`, `automation/optimizer/run_optimization.py`, `automation/AGENTS.md` |
| 2026-06-26 | **FORENSIK GitHub-Issues #460–#469 (Sweep TSLA.ETORO, optimizer_20260626.log).** Zehn verifizierte Defekte/Methodik-Befunde. **#460 (P0, Pitfall #85) — Holdout-Starvation:** `compute_walk_forward_window` ankert an `now`, nicht am Katalog; Holdout-OOS-Slice 2026-05-12→2026-06-26 liegt 2.2 Tage hinter dem letzten Tick (~2026-05-09) ⇒ 100 % `REJECTED_ON_HOLDOUT` (alle 6 Proposals), parameter-unabhängig. Fix: Anker-Clamp auf `min(now, catalog_newest)` + `REJECT_HOLDOUT_UNREACHABLE`-Assertion in `confirm.py`. **#461 (P0, Pitfall #86) — Reward-Inversion:** `_constraint_failure_reward` unbeschränkt nach unten; `oos_min_total_return=0.005` dominiert (`d_return=69.32`); Trial 234 (242 OOS-Trades) = −31.259 << Trials mit 0 OOS-Trades (−9.75) ⇒ TPE flieht OOS-Aktivität (exakt reproduziert). Fix: Penalty-Band-Clamp/bounded Transform, Invariante „mehr OOS-Info ⇒ nie schlechter". **#462 (P1) — Gate-1 ohne Holdout-Reach:** Preflight prüft nur fold-0-OOS-Start, nicht den Holdout-Slice. **#463 (P1, Pitfall #87) — Anker-Divergenz:** `oos_covered=true` ∧ `fill∈Union` ∧ `oos_total_trades=0`; Telemetrie (`start_ns`) vs. Zähler (`_first_tick_ns`) vereinheitlichen + Invarianten-Assertion. **(Symptom-Detektor, Fehldiagnose der Wurzel, siehe Issue #471 für Root-Cause)** **#464 (P1, Pitfall #88) — Sortino-`√252`** auf Per-Trade-Returns (Frequenz-Bias). **#465/#466 (P2, Pitfall #88) — DD/Calmar/total_return** aus Trade-geordneter PnL statt zeitbasierter MtM-Equity (Intra-Trade-DD blind, 100 %-Kapital-Annahme im OOS-Gate). **#467 (P2) — `splits=4`** degeneriert zu einem kontiguierlichen IS/OOS-Block (keine echte OOS-Diversität; Purge/Embargo erwägen). **#468 (P3) — Penalty-Konditionierung:** Return-Shortfall an Gate-Schwelle normiert ⇒ Schwelle wird Penalty-Verstärker; an robuste Skala entkoppeln. **#469 (P3) — Semantics-Drift:** „Using an existing study" (best=Trial 68) trotz Reward-Änderungen; Versions-Guard fail-loud. **Merge-Reihenfolge #460→#461→#462→#463→#464→#465–#469.** Verifiziert via `/tmp/wf_check.py` (Fenster-Arithmetik) + `/tmp/reward_check.py` (Reward exakt −31.25938). | `automation/optimizer/trial_config.py`, `automation/optimizer/confirm.py`, `automation/optimizer/reward.py`, `automation/optimizer/gate.py`, `automation/optimizer/sweep.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/parsing.py`, `automation/backtest_runner.py`, `automation/config/optimizer.json`, `automation/config/tournament.json`, `automation/config/backtest.json`, `automation/AGENTS.md` |
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

### Issue #509 (Cost Drag & Turnover Churning) - Feature Summary

| Feature | Modifikation | Auswirkung / Funktion |
|---|---|---|
| **Turnover-Penalty** | `automation/optimizer/reward.py`, `automation/config/optimizer.json` | Zieht einen `turnover_penalty` Term vom OOS-Reward ab (`penalty_turnover_weight * oos_total_trades`), um hochfrequentes Cost Drag Trading systematisch zu bestrafen. |
| **Entry-Throttling** | `automation/optimizer/spaces.py`, `automation/strategies/hourly_strategy_base.py` | Fügt `min_holding_time` (Blockiert Exits vor Haltezeit) und `min_signal_strength` zum Strategie-Konfigurations- und Optimierungs-Suchraum hinzu. `cooldown_bars` ist global verfügbar. |
| **Expectancy Metrics** | `automation/backtest_runner.py`, `automation/optimizer/parsing.py` | Trennt Expectancy in `gross_expectancy` (Pre-Cost) und `net_expectancy` (Post-Cost) auf Basis des arithmetischen Per-Trade PnL Mittels. Beide Werte fließen ins JSON-Event. |
| **Trade-Cap Enforcing** | `automation/strategies/hourly_strategy_base.py`, `automation/optimizer/parsing.py` | Ersetzt historisch redundantes `is_max_trades` durch hartes Constraint `max_trades_cap`. Erreicht `self._executed_trades` das Cap, terminiert die Strategie rigoros in `on_bar()`. Telemetrie (`hit_trade_cap`) loggt Aktivierung. |

## Architektonische Methodik: IS/OOS Split und "State Bleed"

Der `daily_orchestrator.py` und der `backtest_runner.py` nutzen nun ein echtes, rollierendes Walk-Forward (`walk_forward_active: true`). Das Train/Test (IS/OOS) Splitting basiert aus Performancegründen weiterhin auf einem einzigen, durchgehenden Engine-Run des `backtest_runner.py`. Die Aufteilung in `n=splits` rollierende Fenster erfolgt jedoch *retrospektiv* anhand der Timestamp-Filterung über die gesamte Spanne während der Metrik-Extraktion in `extract_metrics`.

**Wichtige Limitationen für Agenten (State Bleed):**
- **Kein Hard-Reset:** An der IS/OOS-Grenze findet kein Zurücksetzen der Engine statt. Das bedeutet, dass laufende offene Positionen, das angesammelte Account-Guthaben sowie die Historie aller Indikatoren (z.B. aufgewärmte EMAs, RSI-Werte) ungefiltert aus der In-Sample Phase in den Out-of-Sample Zeitraum überfließen ("State Bleed").
- **Gültigkeit der OOS-Metriken:** OOS-Ergebnisse sind somit methodisch nicht 100% "rein" oder vollständig unabhängig vom In-Sample Lauf. Dieser Kompromiss wird derzeit bewusst akzeptiert, um Backtesting-Overhead und Laufzeiten zu minimieren.
- Zukünftige Code-Änderungen an Strategien oder Evaluierungs-Metriken müssen diese architektonische Gegebenheit berücksichtigen.

---

*Zuletzt aktualisiert: 2026-07-06 (Issue-Set #559–#571: Mathematische Sanierung der Reward-Landschaft — weiche Sortino-Sättigung, return-verankerter Near-Miss-Gradient, kostenrelatives Expectancy-Gate, Kommissions-Semantik 2×→1×, Shrinkage-Fallback, symmetrische Overfit-/Fold-Dispersions-Strafe, Deflated-Selektion aktiv, Per-Trial-Telemetrie, Budget-Gating; Pitfalls #115–#118 + Kern-Invariante Reward-Gradient. Vorher: Issue-Set #546–#555). Datum und Changelog bei jeder Änderung an dieser Datei aktualisieren.*

## Known Pitfalls & Architecture Notes
### 🟢 Pitfall #199 — Reward Shaping Monotonie-Guard & Floor-Plateau (Issue #488) [Konsolidierung von #91 + #95]
**Symptom:** Der Optimizer nutzt das IS-Trade-Shaping (`is_total_trades`) als primäres Optimierungsziel im Unevaluable-Raum, selbst wenn die Strategie keine OOS-Evaluation auslöst und IS keinerlei Performance liefert (`_gate_proximity == 0`). Zudem verschwendet der TPE-Sampler bei rein strukturell unevaluierbaren Symbolen 100 Trials am Flat-Floor. *(Diese Zusammenfassung dupliziert #91 und #95 — für Details siehe dort; hier nur als kompakte Querreferenz erhalten.)*
**Fix/Regel:**
1. Shaping ist strikt als "Tie-Breaker-Gradient" definiert und darf NIEMALS ein primäres Optimierungsziel sein. Die `activity` (Trade-Fortschritt) muss zwingend durch `_gate_proximity` geclippt werden (`activity := min(activity, _gate_proximity)`).
2. Der Floor-Plateau Guard implementiert eine strikte Early-Stop Invariante: Wenn die ersten `K` Trials streng nach `n_startup_trials` alle `oos_evaluated=False` sind (All-Unevaluable), wird die Study hart abgebrochen und ein JSON `STUDY_EARLY_STOP` Event mit Reason `STRUCTURAL_ALL_UNEVALUABLE` geloggt. Dieser Guard verändert die Reward-Landschaft mathematisch nicht (reine Compute-Reduktion).
**Betroffen:** `automation/optimizer/reward.py`, `automation/optimizer/run_optimization.py`

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
| 2026-06-29 | **IMPLEMENTIERUNG Issues #466, #467, #468**: Embargo-Splits, OOS-Penalty Decoupling, Semantics Guard. Pitfalls #88 & #89 dokumentiert. | `automation/backtest_runner.py`, `automation/optimizer/reward.py`, `automation/optimizer/run_optimization.py`, `automation/config/optimizer.json`, `automation/config/tournament.json`, `automation/config/backtest.json`, `automation/AGENTS.md` |
| 2026-06-28 | **IMPLEMENTIERUNG GitHub-Issue #462 (Pitfall #87 Holdout Reachability Preflight):** Einführung von `data_reaches_holdout_window` in `gate.py` und `compute_holdout_window_start_ns` in `sweep.py`. Verhinderung von Compute-Waste bei stalem Katalog durch vorzeitigen Sweep-Skip. Test-Coverage über `test_issue_462_gate_holdout_reach.py` gesichert. | `automation/optimizer/gate.py`, `automation/optimizer/sweep.py`, `automation/tests/test_issue_462_gate_holdout_reach.py`, `automation/AGENTS.md` |
| 2026-06-28 | **Issue #461 (Reward-Inversion bei Constraint Failures):** Asymptotische Penalty-Kompression mittels `math.tanh` in `_constraint_failure_reward` hinzugefügt, sodass die Distanzstrafe den Reward im schmalen Kompressionsband verbleibt und niemals unter die `unevaluable_ceiling` (`-9.75`) drückt. Zuvor eingeführte globale Skalierung (`target < 0.05`) in `_shortfall_distance` entfernt, um numerische Verzerrungen zu verhindern. Die strikte Invariante der Ordnung (`eligible > near-miss > far-miss ≳ unevaluable_ceiling (-9.75) > unevaluable_shaping_band`) wurde wiederhergestellt und strukturell abgesichert. `reward_semantics_version` in `optimizer.json` auf 4 erhöht. Property-Tests überarbeitet, um die Integrität der Ordnung und Bounds abzusichern. Pitfall #86 dokumentiert. | `automation/optimizer/reward.py`, `automation/config/optimizer.json`, `automation/tests/test_issue_461_reward_no_inversion.py`, `automation/tests/test_issue_452_reward_distance.py`, `automation/AGENTS.md` |
| 2026-06-23 | **ISSUE-OPT-376 (Toter Parameter `bb_entry_tolerance` entfernt):** `bb_entry_tolerance` war in `ComboTrendVwapConfig` deklariert und in `strategy_defaults.json` gesetzt, wurde in `on_bar()` aber nie referenziert (das BB-Touch-Fenster nutzt `atr_tolerance = atr · atr_multiplier`). Restlos aus Config-Klasse und Defaults entfernt; nicht im Optuna-Suchraum. Keine Struct-Validierungsfehler (Config baut weiter, `__struct_fields__` ohne den Key); Dry-Run grün. | `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json`, `automation/AGENTS.md` |
| 2026-06-23 | **ISSUE-OPT-375 (Reward-Gradient bei `fully_eligible_pairs = 0`):** Das Shaping nicht-evaluierbarer Trials war allein an OOS-Trades gekoppelt; ohne IS-Sieger blieb der Reward flächig `penalty_unevaluable_oos` (−10.0), TPE hatte keinen Gradienten Richtung Eligibility. `compute_reward` koppelt das Unevaluable-Shaping nun monoton an die IS-Aktivität: `shaping = unevaluable_shaping_span · max(trade_progress_oos, min(1, is_total_trades / shaping_trade_target))`. Neuer Zero-Hardcoding-Knob `shaping_trade_target` (Default 50) in `optimizer.json` (+ `_schema`). Floor-Invariante strikt erhalten (`shaping ≤ span` ⇒ jeder Unevaluable-Trial < Evaluable-Floor). IS-Aktivität wird in `parsing.TournamentMetrics` aus `full_results[].metrics.total_trades` abgeleitet (bereits von `backtest_runner` exportiert) — kein Runner-Change nötig. Tests: Gradient zwischen zwei Unevaluable-Trials + harte Ordering-Invariante. | `automation/optimizer/reward.py`, `automation/config/optimizer.json`, `automation/tests/test_optimizer_reward_parser.py`, `automation/AGENTS.md` |
| 2026-06-23 | **ISSUE-OPT-374 (Self-describing Manifest — `walk_forward` + `start_capital`):** `build_trial` schreibt die effektive Walk-Forward-Geometrie (`is_window_days`, `oos_window_days`, `splits == n_folds`, `holdout_days`) **und** `start_capital` jetzt zusätzlich in `manifest.global_settings` (zuvor nur im Side-Channel der kopierten `backtest.json`). `backtest_runner.py` liest beide **autoritativ aus dem Manifest**, mit Fallback auf die trial-lokale `backtest.json`, und loggt die effektive Quelle im Startup-Header. `test_optimizer_manifest.py` prüft die Präsenz von `walk_forward` (splits == n_folds) und `start_capital`. Korridor-Geometrie (405d vs. ~365d 12M-History) dokumentiert und bewusst beibehalten (Beschaffungstiefe erhöhen statt Fenster verkleinern; Geometrie-Änderung wäre Typ S). | `automation/optimizer/trial_config.py`, `automation/backtest_runner.py`, `automation/tests/test_optimizer_manifest.py`, `automation/AGENTS.md` |
| 2026-06-22 | **ISSUE-OPT-373 (ComboTrendVwap Konjunktions-Schalter):** Die fest verdrahtete 4-fach-UND-Konjunktion im Entry-Gate ist über die booleschen Config-Schalter `require_vwap_confirmation` und `require_bb_touch` (Long- **und** Short-Zweig) auflockerbar. Beide Defaults `True` ⇒ verhaltensneutral zum Status quo (Regressionssicherheit). Optuna sucht beide kategorial via `suggest_categorical([True, False])`, sodass der Optimierer einzelne Konjunkte selbst abwählen kann — **ohne** `tournament.json`-Gates zu verändern (Gate-Gaming-Verbot §12). Dedizierter Regressionstest ergänzt und in Tier 5 des PR-Gates verdrahtet; verwaiste Duplikate von Pitfall #69 in dieser Datei bereinigt. **Typ S (Strategie-Logik-Änderung): ein frischer Baseline-Lauf ist nötig**, um die erwartete Steigerung von `fully_eligible_pairs` bei `require_*=False` zu messen und erwartete Test-Werte zu kalibrieren — wird NICHT automatisch ausgelöst. | `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json`, `automation/optimizer/spaces.py`, `automation/tests/test_combo_conjunction_switches.py`, `.github/workflows/pytest-gate.yml`, `automation/AGENTS.md` |



### 🟢 Pitfall #88 — Leakage in OOS Window durch Indikator-Lookback [BEHOBEN: GH-#466]
**Symptom:** Time-Series-Splits in `backtest_runner.py` generieren eine kontiguierliche OOS-Union, bei der der Start des OOS-Folds exakt auf das Ende des IS-Folds fällt, was zu Leakage durch Indikator-Lookbacks führt.
**Root Cause:** Fehlende Embargo-Periode (Purge/Embargo). Indikatoren, die im OOS-Fenster berechnet werden, beziehen IS-Daten mit ein.
**Fix/Regel:** `embargo_period_days` aus `walk_forward_dict` (Default 0) in die Berechnung des `split_oos_start_ns` integriert. OOS-Start = IS-Start + IS-Window + Embargo-Period. Diese mengentheoretische Invariante (Lookback_Window ∩ IS_Period = ∅) schreibt den Embargo-Mechanismus vor.

### 🟢 Pitfall #89 — Semantics Guard Posterior-Korruption [BEHOBEN: GH-#468]
**Symptom:** Optuna mischt Trials aus alten und neuen Reward-Semantiken, was den TPE-Sampler korrumpiert und die Suche divergiert.
**Root Cause:** `_check_reward_semantics_version` hat bisher nur geloggt, den Lauf aber nicht abgebrochen.
**Fix/Regel:** Zwingender Fail-Loud (`ValueError`) bei `existing < current` mit bestehenden Trials. Jede Modifikation an der mathematischen Distanz-Kalkulation, Penalty-Gewichtung oder Gate-Logik erfordert einen zwingenden Bump der `reward_semantics_version` in der `optimizer.json`.

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

| 2026-06-28 | **Issue #464 & #465 (MtM Drawdown & Sortino Dimensionality):** Architektur-Shift von event-basierter (Exit-TS) PnL-Akkumulation zu zeitsynchroner Mark-to-Market (MtM) Equity-Kurve mittels In-Flight `PortfolioMonitor` Actor. `max_drawdown` und `calmar_ratio` nutzen nun die kontinuierliche MtM-Serie. Annualisierungsparameter (`annualization_periods_per_year`) in `optimizer.json` eingeführt zur Dimensionskonsistenz der Sortino Ratio, was Hardcoding auf 252 abschafft. `reward_semantics_version` erhöht. | `automation/backtest_runner.py`, `automation/config/optimizer.json`, `automation/AGENTS.md`, `automation/tests/test_issue_465_mtm_drawdown.py`, `automation/tests/test_issue_464_sortino_dimension.py` |



| Komponente | Zwingender Dokumentationsinhalt |
| -- | -- |
| **Drawdown-Semantik** | Explizite Streichung der alten Definition ("Realized FIFO PnL"). Neue Definition: "Bar-resolved Mark-to-Market (MtM) Equity inklusive Floating Drawdowns offener sowie chronologisch überlappender Trades". |
| **Score Invalidation** | Explizite Dokumentation des Bumps der `reward_semantics_version`. Zwingende Handlungsanweisung an den Operator: Löschung alter SQLite-Datenbanken (`rm -f data/optimizer/studies.db`), da alte Realized-Drawdown-Metriken strukturell inkompatibel zum neuen MtM-Regime sind. |
| **Config Parameter** | Der Annualisierungsskalierungsfaktor (Sortino) wird dynamisch pro Instrument berechnet. |
| **Performance-Profil** | Deklaration des `PortfolioMonitor` Actors als integraler Core-Bestandteil der Backtest-Engine-Laufzeit. |

| 2026-06-28 | **Hotfix CI Precision Mismatch:** PortfolioMonitor Actor Typecast für `BarType` hinzugefügt und Equity-Read-Methode auf `margin_balance` (NautilusTrader 1.229.0 kompatibel) aktualisiert, um TypeError Abstürze im isolierten Worker-Thread zu beheben. | `automation/backtest_runner.py` |
| 2026-06-29 | **Fix Issue #471 / #487 (OOS-Entkopplung):** `_oos_eval`-Zuweisung in `select_winners` iteriert über `all_results`. OOS-Evaluierbarkeit vollständig von IS-Eligibility getrennt. Fail-Loud-Invariante für `universe_size==1` in `parse_tournament` implementiert, um Regressionen (OOS=0) abzufangen. | `automation/backtest_runner.py`, `automation/optimizer/parsing.py`, `automation/tests/test_issue_471_oos_eval_decoupled_from_is_gate.py` |
| 2026-06-29 | **Fix Issue #472 / #488 (Reward Shaping Monotonicity & Floor Plateau Guard):** Reward-Shaping (`is_total_trades`) durch `_gate_proximity` geclippt (Tie-Breaker-Gradient). Floor-Plateau Guard bricht Suchen nach `K` All-Unevaluable Trials frühzeitig mit JSON Event ab. | `automation/optimizer/reward.py`, `automation/optimizer/run_optimization.py` |
| 2026-06-30 | **Fix Issue #473 / #503 (Walk-Forward Boundary Hardcoding):** Walk-Forward `oos_lo_ns` arithmetisch auf `window_start` anstatt auf fehlerhaft ausgepacktes `holdout_start` gestützt. Zero-Hardcoding durch dynamische Auslesung von `oos_window_days` realisiert. | `automation/optimizer/confirm.py`, `automation/tests/test_holdout_window.py` |
| 2026-07-01 | **Fix Issue #506 (Expectancy Penalty Korruption):** `expectancy` als arithmetisches Mittel der Per-Trade-Returns (additive PnLs) umdefiniert. Völlig von `total_return` entkoppelt. Das OOS Gate `oos_min_expectancy` wurde entsprechend angepasst, um die statistisch invalide Division einer Mehrperioden-Größe durch `n_trades` abzulösen. | `automation/backtest_runner.py`, `automation/optimizer/reward.py`, `automation/config/tournament.json`, `automation/optimizer/parsing.py`, `automation/tests/test_issue_506_expectancy.py` |

### 🟢 Pitfall #90: Hot-Path Exception Swallowing (Empty Equity Curve)
**Symptom:** OOS-Trades sind vorhanden, aber die Mark-to-Market (MtM) Equity-Kurve ist leer. Dies verzerrt Core-Metriken (`total_return`, `max_drawdown`, `sortino_ratio`) massiv.
**Anti-Pattern:** Die Verwendung von `except Exception: pass` im Hot-Path (insbesondere in State-Tracking oder `on_bar`-Methoden) schluckt potenziell Exceptions, wodurch die Equity-Kurve nicht aufgebaut, der Backtest aber nicht abgebrochen wird. Die Evaluation ist dann nicht vertrauenswürdig. Das Maskieren von Fehlern durch generische Catch-All-Blöcke ist strikt untersagt (Fail-Loud-Pattern zwingend erforderlich).
**Mathematische Invariante (Kohärenz-Invariante):**
Bei einer **flachen Endposition** (keine offenen Positionen am Ende des Evaluierungsfensters) gilt das mathematische Gesetz:
`sign(total_return) == sign(sum(realisierte PnL))`
Ist dies nicht der Fall (z. B. `total_return == 0.0` trotz non-zero PnL), deutet das zwingend auf asynchrone Fehler im MtM-Tracking oder PnL-Aggregation hin.

### 🟢 Pitfall #96 — OOS-Gate verwirft profitable asymmetrische Strategien (Win-Rate Inkompatibilität) [BEHOBEN: GH-#504]
**Symptom:** Profitable Trials (z.B. +2.56 % Return) mit `p = 0.15` und `payoff = 8` werden ausschließlich durch `oos_min_win_rate < 0.25000` als nicht-evaluierbar abgelehnt, obwohl ihr Expectancy und Profit Factor sehr stark positiv sind.
**Root Cause:** (1) `min_win_rate` war ein hartes Kriterium in `eligible_requires_all` (5-fach-UND-Konjunktion). Win-Rate ist aber bei asymmetrischen Trend-/Breakout-Strategien keine hinreichende Statistik für einen Edge. (2) `_evaluate_oos_eligibility` hat alle Schwellen statisch und hart überprüft und die in `tournament_cfg` definierten `eligible_requires_all` und `eligible_requires_any` (wie in der In-Sample Phase `_is_eligible`) komplett ignoriert.
**Fix/Regel:** `min_win_rate` in `tournament.json` von `eligible_requires_all` nach `eligible_requires_any` verschoben, um es neben `min_profit_factor` zu einem Score- bzw. weichen Kriterium zu machen. `_evaluate_oos_eligibility` wurde vollständig refaktoriert, sodass es sich nun dynamisch an `eligible_requires_all` und `eligible_requires_any` orientiert und sich kongruent zur In-Sample-Prüfung (`_is_eligible`) verhält.
**Betroffen:** `automation/config/tournament.json`, `automation/backtest_runner.py`


### 🟢 Pitfall #97 — Verschwindender Dynamikbereich & Term-Dominanz im OOS-Gate (Issue #505)
**Symptom:** TPE-Sampler optimiert ausschließlich Rauschen, da alle Constraint-Failure-Trials durch `tanh`-Kompression in ein mikroskopisches Band von 0.001 gequetscht werden. Zudem dominiert der Expectancy-Term aufgrund unskalierter Quadrierung den Gradienten.
**Fix/Regel:** (1) Lineare, dimensionslose Normierung aller Distanzfunktionen (Entfernung von `**2`), Aggregation per Durchschnitt. (2) Ausweitung des Reward-Bands: `Feasible_Floor` wird auf `-sortino_clip_abs` (Default -5.0) angehoben. Constraint-Failures belegen nun die volle Spanne `[-9.75, -5.0 - epsilon]`. Die `tanh`-Kompression wurde restlos entfernt. Die Anti-Gaming-Invariante bleibt mathematisch gewahrt.

### 🟢 Pitfall #98 — Expectancy Penalty Korruption (Issue #506)
**Symptom:** Mathematisch korrupte Per-Trade Expectancy verzerrt das Penalty-Routing im Optimizer. Dies passierte, da `total_return` (eine pfadabhängige, kompoundierte Mehrperioden-Metrik wie `equity_end/equity_start - 1`) durch die reine Trade-Anzahl `n_trades` geteilt wurde.
**Fix/Regel:** `expectancy` ist nun konsequent umdefiniert als das arithmetische Mittel der Per-Trade-Returns (additive PnLs) und konzeptionell völlig von `total_return` entkoppelt. Das `oos_min_expectancy` Gate (`5e-5`) arbeitet nun auf Basis dieser reinen Trade-Renditen.

## Walk-Forward Validation & Look-Ahead Bias Prevention (Purge & Embargo)
* Einzelpass-Backtest mit fragmentiertem Holdout, KEIN re-trainierender Walk-Forward. Optuna-Trials werden als Single-Pass mit fixen Parametern exekutiert. Die Kachelung in OOS-Sub-Folds dient ausschließlich der Messung der *Per-Fold-Sortino-Dispersion* und beinhaltet *kein* Re-Fitting.
* Purge & Embargo (López de Prado): In walk-forward optimization, an embargo period must be used to separate the static IS end and the start of the OOS folds. The `embargo_period_days` pushes the `oos_start_ns` forward, meaning it **shifts** the effective OOS window rather than shrinking it. Thus, `oos_end_ns = oos_start_ns + oos_window_ns`.
* IS Fallback Prevention: Trades executed during the embargo period (the gap between IS and OOS) must be explicitly purged. They cannot fall back into the In-Sample dataset, as this would cause look-ahead bias and contaminate IS-metrics (e.g., `is_sortino_median`, `is_total_trades`).
* OOS Fold Kachelung: The OOS folds are contiguous in duration (e.g., each OOS split is precisely `oos_window_ns` long) and sequential starting after the embargo period.

> "Risikometriken (MDD, Calmar, Sortino) MÜSSEN auf Bar-level MtM-Equity-Vektoren operieren, niemals auf PnL-Aggregaten."

### 1. Metric Paradigms
- **Period Definition:** Zwingende Anwendung des Trading-Time-Paradigmas für sämtliche Risk- und Return-Metrics. Die Division durch absolute Kalenderjahre (`span_years`) führt bei Time-Series mit Market-Gaps (Overnight/Weekend) zu systematischen Verzerrungen und ist untersagt.

### 2. Agent Constraints
- **Time Constants:** Absolutes Verbot von hardcodierten Zeitkonstanten (z. B. `252`, `365`, `math.sqrt(252)`) in der Evaluierungs-Logik. Annualisierungsfaktoren werden **empirisch** aus der realen Time-Series-Frequenz abgeleitet (siehe *Time Series Invariants* unten); `annualization_periods_per_year` ist nur noch ein **expliziter Override** (non-null), kein stiller Default (Issue #532).

### 3. Watertight Invariants (Issues #532–#534)
Diese drei Invarianten sind bindend und dürfen von Gate-Logik, Objective-Funktionen und Metrik-Engine niemals verletzt werden:

- **Metrics Engine Invariants:** Sortino ratios evaluate to `None` on lossless periods. Gate logic and objective functions MUST universally implement a fallback to `total_return > 0` or `expectancy > 0` to prevent rejection of optimal edge cases. *(Umsetzung: `reward.py::compute_reward` via `oos_sortino_fallback`, `confirm.py::_holdout_gate_passed` — eine gemeinsame Quelle für die »evaluable-but-sortino-undefined«-Regel; Issue #533.)*
- **Time Series Invariants:** Annualization factors MUST be derived empirically from the actual time span of the `mtm_series` using `n_periods · 31_557_600.0 / total_span_seconds` (zero-hardcoding policy). Static factors (e.g., 252) are explicitly deprecated unless required for forced config overrides. This correctly scales for RTH instruments (e.g., TSLA ≈ 1638) as well as 24/7 crypto (≈ 8766), ensuring cross-asset comparability and correct scaling of absolute Sortino gates (`oos_min_sortino`). *(Umsetzung: `backtest_runner.py::_get_annualization_factor`; Issue #595.)*
- **Optimizer Invariants:** Penalty calculations for Near-Miss gradients MUST normalize exclusively against ACTIVE dimensions. Inactive constraints must carry zero weight in the divisor to prevent gradient noise during Hyperparameter Optimization (TPE). *(Umsetzung: `reward.py::_constraint_distance_penalty`, `sum(active_dists) / len(active_dists)` statt Division durch die feste Gesamtzahl der Dimensionen; Distanzen bleiben linear pro Issue #505, Floor-Invariante `unevaluable < evaluable-Floor` bleibt gewahrt; Issue #534.)*

### 🟢 Pitfall #102 — Walk-Forward Geometrie Divergenz (Zero Hardcoding Policy for Optimizer Geometries) [BEHOBEN: GH-#512]
**Symptom:** Validation Set Invalidation durch inkonsistente OOS/Holdout-Fold-Berechnung zwischen Runner und Optimizer-Gate (`confirm.py`). Die Optimierung lief mit konfigurierten Werten aus `backtest.json`, das Holdout-Gate hardcodierte jedoch Overrides (`oos_window_days=30`, `n_folds=1`), wodurch Overlaps und Leakage zwischen Train und Validation entstanden.
**Root Cause:** Hardcodierte Walk-Forward und Holdout Literale (`30`, `1`) in der Bestätigungs-Logik (`confirm.py`).
**Fix/Regel (Zero Hardcoding Policy for Optimizer Geometries):** `backtest.json` ist die **Single Source of Truth (SSOT)** für alle Walk-Forward-, Holdout- und Embargo-Parameter im gesamten Agenten-Lifecycle. Keine Hardcodierung von Optimizer-Geometrien in Python-Code erlaubt. Data Leakage Protection (Embargo-Abstand zwischen Train und Test) muss zwingend bei PRs per Date-Overlap Test evaluiert werden.

### 🟢 Pitfall #103 — OOS Geometrie Inkonsistenz in nicht-kontiguierlichen Slices (Issue #530)
**Symptom:** Verzerrte Return- und Drawdown-Metriken im Out-Of-Sample, da OOS-Slices als synthetischer kontiguierlicher Block gehandhabt wurden, obwohl Walk-Forward mit Embargos strukturell Lücken erzeugt.
**Fix/Regel (Single Source of Truth für Folds):**
1. **OOS-Slicing:** `compute_fold_boundaries` ist die **exklusive Schnittstelle** für alle Walk-Forward Evaluationen (Trade-Klassifikation, Logging, Slicing). Der MtM-OOS-Slice muss zwingend als Union der aus `compute_fold_boundaries` generierten OOS-Intervalle konstruiert werden.
2. **Segmentiertes Return-Compounding:** Bei der Konkatenation nicht-kontiguierlicher Segmente verbietet sich die naive End/Start-Division (`iloc[-1]/iloc[0] - 1`), da Embargo-Lücken andernfalls fälschlich als Return abgebildet werden. `total_return` ist bei Vorliegen von Lücken **zwingend** über das Produkt der Segment-Returns zu kompoundieren: $R_{total} = \prod (1 + R_{segment}) - 1$.
3. **State Alignment:** Eine identische Embargo-Konvention (Ausschluss des Embargos) ist strikt über Trade-Klassifikation, Logging (`oos_window_start_ns`) und MtM-Slice-Generierung durchzusetzen.

### NautilusTrader API-Handling (Version >=1.226)

* **Axiom 1 (State & Portfolio Access):**
Der Zugriff auf Portfolio-Accounts erfolgt ausschliesslich über die Methoden-Signatur `Portfolio.account(venue=None, account_id=None)`. Attribut-basierte Zugriffe werfen `AttributeError`.
* **Axiom 2 (Equity Handling & Type Safety):**
`Portfolio.equity(venue)` liefert zwingend ein Mapping vom Typ `dict[Currency, Money]`. Ein direktes Chaining mit `.as_double()` ist strengstens verboten. Die Extraktion der Währung (Base Currency, z.B. USD oder CHF) muss über `.get()` mit Fallback-Logik implementiert werden. Realisierter plus Floating-PnL wird exakt durch diese Funktion abgebildet.
* **Axiom 3 (Silent Failure Prohibition):**
Das Maskieren von Fehlern in der Berechnung von Core-Metriken (MTM-Equity, Drawdown, Sortino) durch generische Catch-All-Blöcke (`except Exception: pass` wie in #522) ist ein P0-Violation. State-Evaluationen müssen bei Typfehlern deterministisch failen.

## Pitfall-Kompendium — Bug-Kaskade #521–#530 (NautilusTrader ≥1.226 Equity/MtM/Annualisierung)

```text
Pitfall #90 — Equity-Kurve über NautilusTrader-Account-API:
  Portfolio.account ist eine METHODE (venue-Argument), kein Attribut; margin_balance()
  existiert seit >=1.226 nicht (nur balance/balance_total); Portfolio.equity() liefert ein
  dict[Currency, Money]. Für MtM-Equity IMMER Portfolio.equity(venue).get(base_ccy) nutzen.
  Ein leerer equity_curve => total_return/max_drawdown/sortino kollabieren still auf 0/0/None,
  während Expectancy (aus pnl_list) weiterlebt — das ist die Signatur dieses Bugs.

Pitfall #91 — Kein nacktes `except Exception: pass` im Bar-Hot-Path:
  Deterministische API-Fehler pro Bar werden sonst 10^4x lautlos verschluckt. Warn-once
  statt pass; zusätzlich in extract_metrics harte Warnung, wenn OOS-Trades>0 aber mtm leer.

Pitfall #92 — MtM-OOS-Slice und Trade-OOS-Klassifikation MÜSSEN aus derselben
  compute_fold_boundaries-Quelle stammen (inkl. identischer Embargo-Konvention). Nicht-
  kontiguierliche Fold-Segmente produktweise kompoundieren, nie last/first-1 über Lücken.

Pitfall #93 — Annualisierungsfaktor asset-class-/bar-frequenz-bewusst: Config-Konstante
  darf empirische Bar-Frequenz nicht still überstimmen; √252 auf 1h-Returns unterschätzt
  den Sortino ~2.5x.  [BEHOBEN: #532]
```

### 🟢 Pitfall #93 — Statische Annualisierung überstimmt empirische Bar-Frequenz [BEHOBEN: GH-#532]
**Symptom:** `annualization_periods_per_year=252` (Config) gewann IMMER; der dynamische Bar-Frequenz-Fallback (#510) war toter Code. Auf 1h-Kerzen unterschätzte `√252` den annualisierten Sortino systematisch (Faktor real ≫ 252).
**Fix/Regel:** Präzedenz invertiert (`_get_annualization_factor`) — EMPIRISCHE Frequenz (`n_periods * 31_557_600.0 / total_span_seconds`) ist Default; Config wirkt nur als expliziter, non-null Override. `optimizer.json['annualization_periods_per_year'] = null`. Nur ein echter `DatetimeIndex` triggert die Empirik (RangeIndex-Direct-Calls fallen sauber auf Override/1.0 zurück). Siehe *Watertight Invariants → Time Series Invariants*.
**Betroffen:** `automation/backtest_runner.py`, `automation/config/optimizer.json`

### 🟢 Pitfall #104 — Holdout-Gate blockiert verlustfreie (Sortino=None) profitable OOS-Folds [BEHOBEN: GH-#533]
**Symptom:** Ein verlustfreier OOS-Fold (`losses_count==0`) liefert per Definition `oos_sortino=None`. Das Holdout-Gate koerzierte `None→0.0` und verwarf so mit `0.0 > 0.0 = False` das BESTE denkbare Ergebnis. Der Sweep-Reward kannte den `oos_sortino_fallback` bereits, das Holdout-Gate nicht (Inkonsistenz).
**Fix/Regel:** `confirm.py::_holdout_gate_passed` als Single Source of Truth für die »evaluable-but-sortino-undefined«-Regel; bei `oos_sortino is None` greift `oos_total_return > 0` (Parität zu `reward.py`, config-gegatet über `oos_sortino_fallback`). `oos_total_return <= 0` passiert NIE (kein Gate-Gaming); Risk-DD-Cap bleibt hart. Siehe *Watertight Invariants → Metrics Engine Invariants*.
**Betroffen:** `automation/optimizer/confirm.py`

### 🟢 Pitfall #105 — Near-Miss-Penalty inkonsistent normalisiert (Division durch Gesamt- statt Aktiv-Dimensionen) [BEHOBEN: GH-#534]
**Symptom:** `_constraint_distance_penalty` summierte nur aktive Distanzen, teilte aber durch die feste Gesamtzahl der Dimensionen (`len(distances)==6`). Damit hing die effektive Strafe pro aktiver Dimension von der Anzahl inaktiver Gates ab — Gradientenrauschen für den TPE-Sampler.
**Fix/Regel:** Normalisierung strikt über die AKTIVEN Dimensionen (`sum(active_dists) / len(active_dists)`); inaktive (erfüllte) Gates tragen null Gewicht im Divisor. Distanzen bleiben LINEAR (Issue #505; quadratische Distanzen würden #461-Anti-Saturation und #505-Term-Dominanz regredieren) und `return_penalty_scale` entkoppelt die Krümmung vom engen Gate (0.005). Floor-Invariante `unevaluable < evaluable-Floor` bleibt gewahrt. Siehe *Watertight Invariants → Optimizer Invariants*.
**Betroffen:** `automation/optimizer/reward.py`

### 🟢 Pitfall #106 — Expectancy auf `starting_capital` statt eingesetztes Notional normiert [BEHOBEN: GH-#546]
**Symptom:** `_calculate_stats` normierte Expectancy auf das fixe `starting_capital` (`mean(pnl_i / start_capital)`). Die Kennzahl skalierte damit mit der Positionsgröße: eine Strategie mit 10 % Einsatz hatte bei identischem Per-Trade-Edge eine 10× kleinere Expectancy als eine mit 100 %. Das erzwang die mikroskopische Gate-Schwelle 5e-05 (0.5 bps), die als Cross-Strategie-Kennzahl fast wertlos ist und (über Pitfall #108) die Constraint-Penalty dominierte.
**Fix/Regel:** Expectancy IMMER auf das je Trade eingesetzte Notional normieren (`mean(pnl_i / entry_notional_i)`, sizing-invariant), sobald eine längen-kongruente `notional_list` vorliegt. `_calculate_stats` erhielt den keyword-only Parameter `notional_list`; alle Aufrufer in `extract_metrics`/`select_winners` reichen die parallelen Per-Trade-Notionals durch. **Direkt-Unit-Calls ohne `notional_list` bleiben bit-identisch (Legacy-Pfad `mean(pnl_i / start_capital)`).** Migration: `oos_min_expectancy`/`min_expectancy` von `5e-05` auf `0.001` (= 10 bps Netto-Return je Trade auf eingesetztem Kapital) rekalibriert — direkt interpretierbar.
**Betroffen:** `automation/backtest_runner.py` (`_calculate_stats`, Aufrufer), `automation/config/tournament.json`

### 🟢 Pitfall #107 — Expectancy immer auf eingesetztes Notional normieren, nie auf `starting_capital` [BEHOBEN: GH-#546]
**Regel (Kompendium):** Sonst skaliert die Kennzahl mit der Positionsgröße und erzwingt mikroskopische Schwellen (5e-05), die nachgelagerte Distanz-Penalties dominieren (Pitfall #108). Der `entry_notional` je Round-Trip ist in `extract_metrics` (FIFO-Match) bereits vorhanden — nie durch das fixe Startkapital ersetzen.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #108 — Neue OOS-Distanz-Terme in `_constraint_distance_penalty` brauchen `scale`-Entkopplung [BEHOBEN: GH-#547]
**Symptom:** Der Near-Miss-Gradient war ~90 % expectancy-getrieben. `_shortfall_distance` normiert defaultmäßig auf `target`; für `oos_min_expectancy = 5e-05` ergab ein winziger absoluter Miss (−0.00037) eine Distanz von ~8.4, während alle anderen aktiven Distanzen < 1 lagen. Über das Aktiv-Mittel (#534) riss dieser eine Term den Mittelwert nach oben und maskierte Return/Sortino/Profit-Factor/Win-Rate.
**Fix/Regel:** Jede auf einen (potenziell mikroskopischen) `target` normierte Distanz braucht eine `scale`-Entkopplung analog `return_penalty_scale` (#467). `expectancy_penalty_scale` (0.002) bringt einen typischen Miss auf die Skala der übrigen Terme (~0…1.5). Zusätzlich deckelt der optionale `distance_term_cap` (3.0) jeden Term robust gegen Kalibrierfehler (`min(d, cap)`; senkt Distanzen nur ⇒ Rang-Invariante `failed < Evaluable-Floor` bleibt strikt). **Regel: alle sechs Distanz-Dimensionen auf vergleichbare Skala (≈ 0…2) bringen.** Fehlt der Key ⇒ Legacy-Pfad (`scale=target`).
**Betroffen:** `automation/optimizer/reward.py`, `automation/config/tournament.json`

### 🟢 Pitfall #109 — `wf_settings` (Manifest) MUSS alle Walk-Forward-Keys tragen, inkl. `embargo_period_days` [BEHOBEN: GH-#548]
**Symptom:** `backtest.json` konfigurierte `embargo_period_days=21` (Leakage-Prevention #466), aber `wf_settings` (ins Manifest UND in die kopierte `backtest.json` geschrieben) ließ den Key weg. Der Backtest-Subprozess las `walk_forward.get("embargo_period_days", 0)` → **0**: der Purge-Gap zwischen IS-Ende und OOS-Start war wirkungslos (Indikator-Lookback bleedet über die IS→OOS-Grenze). Zusätzlich reservierte `compute_walk_forward_window` keinen Embargo-Platz im äusseren Fenster — fixte man nur (1), liefe der letzte OOS-Fold `embargo` Tage über den Datenrand `end`.
**Fix/Regel:** `compute_walk_forward_window` UND `wf_settings` gemeinsam ändern (nie nur eines): der Embargo wird im Span reserviert (`start = end − (is + embargo + n_folds·oos)`), sodass Fold `n_folds−1` exakt bei `end` endet (kein Overflow). Geometrie-Gegenrechnung: `is+embargo+folds·oos+holdout = 180+21+180+45 = 426 ≤ data_history_days(450)`. `embargo_period_days=0` reproduziert das Alt-Verhalten bit-identisch. Analog in `confirm.py` (dieselbe Fenster-Funktion).
**Betroffen:** `automation/optimizer/trial_config.py`, `automation/optimizer/confirm.py`

### 🟢 Pitfall #110 — Aggregation Mismatch in Walk-Forward Folds (Kanonisch an der Quelle) [BEHOBEN: GH-#549/#550, REVIDIERT: GH-#574]
**Symptom:** Das Gate nutzte ursprünglich den GEPOOLTEN OOS-Sortino, der Reward aber den Median. Nach #549/#550 wurden fälschlicherweise auch Count-/Häufigkeits-Kennzahlen (`win_rate`, `profit_factor`, `expectancy`) als Fold-Mediane aggregiert. Dies führte bei 45-Day-Folds (Issue #549/#550) und spärlichen Treffern zu extrem hohen Rejection-Rates (49–99 %), weil 2 Folds mit 0 Gewinnern einen Median von 0.0 erzwangen, was die aggregierte Performance völlig maskierte.
**Fix/Regel:** EINE kanonische Aggregation AN DER QUELLE (`apply_fold_aggregation` in `extract_metrics`) mit strikter Trennung nach Kennzahltyp: Risikoadjustierte Ratios (`sortino_ratio`) erfordern **Fold-Mediane** zur Ausreißer-Elimination. Häufigkeits-/Count-Metriken (`win_rate`, `profit_factor`, `expectancy`) erfordern ZWINGEND **Trade-Pooling** über Folds hinweg (wobei sie zusätzlich unter `<metric>_pooled` gesichert werden). `total_return` bleibt compoundiert (#465), `max_drawdown`/`total_trades` bleiben pooled. Für `splits==1` (Holdout) ist der Fold-Median == pooled (bit-identisch). Zusätzlich (#550) das deklarative `oos_min_profitable_folds_frac`-Gate (Fold-Konsistenz; belohnt echte Robustheit statt eines Glücks-Sub-Fensters). Fehlt der Key ⇒ inaktiv.
**Betroffen:** `automation/backtest_runner.py`, `automation/optimizer/parsing.py`, `automation/config/tournament.json`

### 🟢 Pitfall #111 — Equity-Slicing halb-offen `[s, e)` halten, konsistent zur Trade-Klassifikation [BEHOBEN: GH-#551]
**Symptom:** `pandas.loc[a:b]` ist auf BEIDEN Seiten geschlossen. Bei kontinuierlichen Folds (`oos_end_k == oos_start_{k+1}`, Embargo=0) lag der Grenz-Bar in ZWEI benachbarten Fold-Segmenten und wurde im compoundierten Return (`mtm_frames`, nicht dedupliziert) doppelt gezählt. Die Trade-Klassifikation war halb-offen `[s, e)`, die Equity-Slices aber geschlossen `[s, e]` — zwei Intervallkonventionen im selben Code.
**Fix/Regel:** Equity-Slices halb-offen schneiden (`end_excl = pd.to_datetime(e_ns) − 1ns`), konsistent zur Trade-Klassifikation (`s <= ts < e`), an ALLEN Slice-Stellen (IS, OOS-Frames, Per-Fold). Folge: der Bar exakt am exklusiven Fenster-Ende gehört zum NÄCHSTEN Fenster (auch der Buy&Hold-Benchmark aus #552 nutzt diese Konvention). Kein Bar-Timestamp erscheint mehr in zwei Segmenten.
**Betroffen:** `automation/backtest_runner.py`

### 🟢 Pitfall #112 — Absolutes OOS-Return-Gate misst Markt-Beta, nicht Strategie-Alpha [BEHOBEN: GH-#552, opt-in]
**Symptom:** `oos_min_total_return = 0.005` ist ein absolutes Gate. In einem steigenden Markt ist +0.5 % durch bloßes Long-Bias trivial; es misst dann Marktrichtung, nicht Signalqualität.
**Fix/Regel:** OPT-IN benchmark-relatives Alpha-Gate: `oos_excess_return = oos_total_return − oos_buyhold_return` (Buy&Hold des Symbols über EXAKT dasselbe halb-offene, deduplizierte OOS-Fenster, #551-Konvention). `oos_min_excess_return` (default deaktiviert ⇒ Legacy-Absolut-Gate). Die Telemetrie-Felder `oos_buyhold_return`/`oos_excess_return` werden geschrieben, sobald die Benchmark-Serie (`PortfolioMonitor.get_benchmark_series`) verfügbar ist. Aktivieren: `oos_min_excess_return` setzen UND `min_excess_return` in `eligible_requires_all` aufnehmen.
**Betroffen:** `automation/backtest_runner.py`, `automation/config/tournament.json`

### 🟢 Pitfall #113 — Winner-Selektion ohne Multiple-Testing-Korrektur (Selection-Bias) [BEHOBEN: GH-#553, opt-in]
**Symptom:** Der beste von N getesteten Konfigurationen ist auch unter H0 (kein Edge) positiv und wächst mit N. Ohne Korrektur ist die False-Positive-Winner-Rate hoch.
**Fix/Regel:** OPT-IN Deflated-Sortino-Selektion (`deflated_selection: true`, Bailey & López de Prado). `automation/optimizer/deflation.deflated_threshold(n_trials, dispersion, confidence)` liefert das `confidence`-Quantil des Maximums von N i.i.d. Rausch-Sortinos; der Winner muss dieses Rausch-Maximum schlagen. Kontrolliert die False-Positive-Rate auf `1 − confidence`. Default false ⇒ bit-identisch. Effektive Schwelle wird je Study geloggt und als `deflated_min_sortino` telemetriert.
**Betroffen:** `automation/optimizer/deflation.py`, `automation/backtest_runner.py`, `automation/config/tournament.json`

### 🟢 Pitfall #114 — Reject-Gründe mit fixem `:.5f` sind bei mikroskopischen Schwellen unaktionierbar [BEHOBEN: GH-#554]
**Symptom:** `oos_min_expectancy: 0.00005 < 0.00005` — der Ist-Wert (0.0000499…) und die Schwelle 5e-05 wurden durch die 5-stellige Rundung identisch dargestellt; der echte Rest-Gap war unsichtbar.
**Fix/Regel:** Adaptive Präzision (`.6g`) plus ein explizites numerisches Δ im Reject-String (`Δ={actual−thresh:+.3e}`) UND ein maschinenlesbares `oos_gate_deltas`-Dict (`metric → actual − threshold`; für `max_drawdown` `cap − actual`, damit einheitlich „negativ = verfehlt"). Das Dict wird durch `single_symbol_oos`/`parse_tournament` bis ins `optimizer_trial_completed`-Event durchgereicht — Forensik ohne String-Parsing.
**Betroffen:** `automation/backtest_runner.py`, `automation/optimizer/parsing.py`, `automation/optimizer/run_optimization.py`

---

> ### 🧭 Kern-Invariante: Reward-Gradient (Issue-Set #559–#570)
> **Die Zielfunktion MUSS über den *gesamten* relevanten Metrik-Bereich streng monoton in der ökonomischen Zielgröße sein** — kein Hard-Clip, kein Mittelwert saturierter Terme, keine stückweise Konstante am Ort der Optima. **Prüfbar:** `corr(reward, oos_total_return) > 0` sowohl im eligiblen (Winner-Ast) als auch im ineligiblen (Near-Miss-Ast). Verletzt eine künftige Änderung diese Invariante (z. B. ein neuer `min/max`-Clip auf einer Reward-Komponente, oder ein Mittelwert über Terme, von denen einige kosten-/schwellen-saturiert bei ~konstant kleben), kollabiert die TPE-Landschaft zu Plateau/Deckel und der Sweep wird bei beliebigem Budget nutzlos. Jede Reward-Berührung braucht einen Gradienten-Property-Test (siehe `test_issue_560_soft_saturation.py`, `test_issue_561_nearmiss_gradient.py`).

### 🟢 Pitfall #115 — `commission_bps` auf beide Legs = 2× der „pro-Round-Trip"-Semantik [BEHOBEN: GH-#561]
**Symptom:** `backtest.json._schema` deklariert `commission_bps` als „pro Round-Trip", der FIFO-Match belastete aber beide Legs (Entry + Exit) mit der VOLLEN Rate ⇒ `commission_bps=1` kostete real 2 bps/Round-Trip. In Summe mit dem Spread ergab sich eine Kostenwand von exakt 10 bps — genau die Höhe, an der 81,5 % der Trials ihre Expectancy pinnten (unerreichbares Expectancy-Gate).
**Fix/Regel:** Round-Trip-Kommission == `commission_bps · notional_avg` **einmal** ⇒ halbe Rate je Leg (`per_leg_bps = commission_bps / 2.0`). Unit-Test verrechnet **exakt 10 USD** (nicht 20) für `notional=10k @ commission_bps=10`. Die Kosten sind **Single Source of Truth** für das kostenrelative Gate (#562, `round_trip_cost_bps` in die Metriken gestempelt) — nie doppelt pflegen.
**Betroffen:** `automation/backtest_runner.py`, `automation/config/backtest.json`

### 🟢 Pitfall #116 — Per-Symbol-Sweep ohne `global_best`: Shrinkage & Warm-Start silent inaktiv [BEHOBEN: GH-#564]
**Symptom:** Im Standalone-`sweep` erzeugt kein Artefakt `global_best` ⇒ `study.enqueue_trial(global_best)` übersprungen (kein Gate-2-Warm-Start) UND `normalized_param_distance(sampled, {}, bounds) = 0` ⇒ `param_pen ≡ 0` ⇒ die A4.3-Shrinkage ist wirkungslos. Alle eligiblen Trials `reward == clip` exakt; der ungezügelt symbol-getunte Vektor überfittet, während die globalen Defaults am Holdout teils bestehen (Per-Symbol-Tuning netto schädlich).
**Fix/Regel:** Fehlt `global_best` im Per-Symbol-Pfad ⇒ **LAUT** warnen (Study-Attr `shrinkage_inactive=true`) UND auf `strategy_defaults.json` als Shrinkage-Referenz + Warm-Start-Seed zurückfallen (`resolve_symbol_shrinkage_seed`). `param_pen` zieht dann Richtung Default (der ökonomisch begründete Prior) statt ins Leere — es darf **nie still 0** werden.
**Betroffen:** `automation/optimizer/run_optimization.py`

### 🟢 Pitfall #117 — Hard-Clip / Mittelwert saturierter Terme tötet den TPE-Gradienten [BEHOBEN: GH-#559/#560]
**Symptom:** Eligible `reward == +5.0` exakt (Deckel: `base = clip(oos_sortino, ±5)` ist stückweise konstant oberhalb der Klemmgrenze — genau dort, wo die Winner leben). Ineligible `corr(reward, return) ≈ 0` (Plateau: der Failure-Reward war das Mittel von 6 Distanz-Termen, von denen 3 kosten-saturiert bei ~1.0 kleben, sodass der einzige performance-tragende Return-Term auf ~2,5 % verdünnt wurde). 5 von 6 Strategien fanden 0 Winner — nicht mangels Winner, sondern weil der Sampler ihn nicht finden *konnte*.
**Fix/Regel:** Weiche Sättigung (`base = c·asinh(oos_sortino/c)`, überall streng monoton) statt `min/max`-Clip; Failure-Reward **return-verankert** (`softplus(−(return−gate)/s)·w`), nicht Mittel gesättigter Terme; kleiner return-Tie-Breaker (`+ w_ret·oos_total_return`) im eligiblen Ast. **INVARIANTE:** `corr(reward, oos_total_return) > 0` in BEIDEN Ästen; kein einzelner Distanz-Term > 60 % des Aktiv-Mittels. Alle neuen Keys fehlen ⇒ Legacy (Hard-Clip / `legacy_mean`), bit-identisch.
**Betroffen:** `automation/optimizer/reward.py`, `automation/config/optimizer.json`

### 🟢 Pitfall #118 — `overfit_gap` einseitig ⇒ OOS-Glück wird belohnt [BEHOBEN: GH-#565]
**Symptom:** `overfit_gap = max(0, is_sortino_median − base)` bestraft NUR `IS > OOS`. Der Fall `OOS ≫ IS` (Overfit auf ein günstiges OOS-Sub-Fenster, das am Holdout revertiert, `R_symbol ≈ −5.1`) erhält `gap = 0` und **vollen Kredit**. Zusätzlich floss nur der Median-OOS-Sortino in den Reward, nicht die Streuung über die Folds — ein einzelner starker Fold konnte den Wert tragen.
**Fix/Regel:** Symmetrische Divergenz-Strafe `w_of·|is_sortino_median − base|` (OOS≫IS via `overfit_oos_luck_weight` optional milder, aber ≠ 0) PLUS Fold-Dispersions-Malus (`w_disp·pstdev(per_fold_oos_sortino)`, die Reward-seitige Ergänzung zum Gate-seitigen #550). Ergänzend die Deflated-Sortino-Selektion (#567) gegen das Rausch-Maximum über N Konfigurationen. Fehlen die Keys ⇒ Legacy-einseitig, bit-identisch.
**Betroffen:** `automation/optimizer/reward.py`, `automation/optimizer/parsing.py`, `automation/config/optimizer.json`

### Issue #535 — Config-Orphans & Gate-1 Fallback
* **Config Deprecation:** `constraint_penalty_scale` wurde aus `optimizer.json` entfernt. Das System verwendet stattdessen `constraint_distance_penalty_weight` und `return_penalty_scale` (Konfigurations-Drift Prävention).
* **Gate-1 Datenprüfung (Issue #525):** `data_history_days` (in `backtest.json`) wird in der realen Gate-1 Logik als Fallback-Wert für die Datenspanne genutzt, anstatt die echte Time-Series-Spanne zu evaluieren (Fail-Loud Implikation korrigiert).

### 🟢 Issue #545 — Sortino Target-Downside-Deviation & Symmetric Clipping
**Symptom:** OOS-Sortinos lieferten unzuverlässige absolute Werte und instabile Metriken unter homogenen Verlusten. Mean-centering der negativen Subsets in `pandas.Series.std()` und falscher Divisor ($k-1$ statt $N$) verzerrten die Metrik und schadeten TPE-Gradienten und Gate-Decisions. Asymmetrisches Clipping hinterließ extreme negative Ausreißer, was die Fold-Median-Aggregation zerstörte.
**Fix/Regel:** Der Sortino-Quotient wird systemweit als Target-Downside-Deviation abgeleitet, basierend auf dem konfigurierten MAR (Minimum Acceptable Return) via `sortino_mar`. Mean-centering wurde entfernt. Symmetrisches Clipping auf `[-RATIO_CAP, +RATIO_CAP]` sichert die Gradientenstabilität ab.

Zwingend in AGENTS.md zu integrierende formale Spezifikationen zur Gewährleistung absoluter Eindeutigkeit für nachgelagerte Evaluierungs-Agents und TPE-Optimizer:

#### 1. Mathematische Definition (Reward Base)
Der Sortino-Quotient wird systemweit als Target-Downside-Deviation abgeleitet.
$$ Sortino = \frac{E[R] - MAR}{DD_{Target}} $$
$$ DD_{Target} = \sqrt{\frac{1}{N} \sum_{t=1}^{N} \min(0, R_t - MAR)^2} $$
Wobei N die Gesamtzahl der Evaluierungsperioden und MAR der deklarierte Minimum Acceptable Return (sortino_mar) ist.

#### 2. Telemetrie & Gating Boundaries
| Parameter | Metrik | Restriktion | Impact (TPE & Gating) |
|---|---|---|---|
| sortino_mar | MAR | Default 0.0 (Konfigurabel) | Statische Null-Baseline für RMS-Berechnung. |
| RATIO_CAP | Clip_{sym} | [-50.0, +50.0] | Symmetrischer Überlaufschutz. Verhindert Gradienten-Tod bei $DD_{Target} \to 0$. |
| oos_min_sortino | Gate | Median-Aggregat der Folds | Erhöhte Sensitivität auf Verlusthäufigkeit (Frequenz) anstatt reiner Verlustamplitude. |

### Pitfall #119: Per-Fold-Sortino-Explosion durch unzureichende Downside-Deviation (Metrik-Fundament)
**Symptom:** Fold-Sortinos explodieren auf harte Caps (z.B. 50.0), wenn Folds wenige, geringfügige Verlust-Bars aufweisen ($dd\_dev \approx 0$). Dies korrumpiert Gate-Eligibility (falscher Median) und treibt Dispersion-Strafen in die Höhe.
**Mitigation:**
1. Niemals ungeschützte Division durch Return-Deviations.
2. Zwingende Implementation eines deklarativen $dd\_dev$-Floors via `sortino_downside_floor` (z.B. $\max(dd\_dev, 0.002)$).
3. Etablierung eines lokal validen Minimum-Trade-Counts via `sortino_min_trades` (z.B. >= 10), unabhängig von der globalen `oos_min_trades` Semantik.
4. Pre-Aggregation Winsorizing von Fold-Metriken anwenden (via `fold_winsorize_lower` und `fold_winsorize_upper`), unter Verwendung von `interpolation="nearest"`, um Lineare-Interpolations-Artefakte bei Folds mit geringer Kardinalität zu vermeiden.

### Pitfall #120: Reward Scaling Discrepancies
**Context:** TPE Optimizer Reward Calculation (`compute_reward`).
**Failure Mode:** Mixing raw bounded variables (e.g., clipped at ±50) with softly saturated variables (e.g., `asinh` compressed) in additive penalty terms or distance calculations. This causes penalties to scale exponentially against the compressed base signal, forcing the optimizer to target zero-dispersion over positive edge.
**Invariant:**
1. **Scale Parity:** All operands in distance functions (`diff`) and dispersion metrics (`pstdev`) MUST exist in the same mathematical space. If `base` is compressed via `asinh`, all penalty inputs MUST be compressed using the exact same scaling factor `c` prior to operation.
2. **Relative Penalty Capping:** Additive penalty terms must never structurally dominate the base signal. Enforce declarative bounding (`penalty_relative_cap * abs(base)`) on all dispersion and divergence penalties. Zero hardcoding applies; caps must be configurable.


### 🟢 Issue #578 — Drawdown-Penalty (Soft Penalty) Unzureichend
**Symptom:** Drawdown-Penalty (`dd_excess * 8.0`) in `compute_reward` war unzureichend implementiert (Dead Code). Hard-Cap-Gate (`max_drawdown`) deklariert Trials mit `dd > cap` präemptiv als `oos_eligible = False`. Ineligible Trials triggern `_constraint_failure_reward` **vor** der Penalty-Kalkulation im Execution-Flow. Eligible Trials passieren das Gate zwingend mit `dd <= cap`, resultierend in `dd_excess = 0`. Effektive Gewichtung der Penalty war mathematisch konstant 0.
**Fix/Regel:** Restlose Entfernung der obsoleten `dd_excess`-Logik. Implementierung eines progressiven Penalty-Terms für *eligible* Trials zur Glättung des Optimizer-Gradienten *unterhalb* des Hard-Caps. Risiko muss zwingend bepreist werden.

**Wasserdichte Audit-Anforderungen:**
* **Formel:** $Reward_{final} = Reward_{base} - penalty\_dd\_weight \cdot \left(\frac{DD_{current}}{DD_{cap}}\right)^2$
* **Execution Order:** Die Penalty wird *ausschließlich* auf Trials angewendet, die `oos_eligible == True` via Hard-Cap-Gate bestanden haben.
* **Parameter Space:** Zulässige Bounds für `penalty_dd_weight` sind z. B. `[0.0, 5.0]` für künftige Meta-Optimizations.

**Betroffen:** `automation/optimizer/reward.py`, `automation/config/optimizer.json`

### 🟢 Issue #576 — Deflated Holdout Selection (Top-k Median & Dispersion Filter)
**Symptom:** Hohe Rejektionsrate auf dem Holdout-Datensatz (`REJECTED_ON_HOLDOUT`), da `confirm_per_symbol_promotion` historisch nur `study.best_trial` evaluierte (Single-Point-Failure via Rausch-Maximum/Overfitting). Zudem wurde das deflationierte Rausch-Korrektiv (Issue #553) nicht für den finalen Holdout-Check angewandt, wodurch unkorrigierte Selektions-Bias unentdeckt blieben.
**Root Cause:**
1. **Argmax-Overfitting:** TPE sucht nach dem maximalen Signal — oft maximiert es lediglich Rauschen. Die Auswahl von `best_trial[0]` erwischt das Rausch-Maximum, welches OOS (Holdout) am stärksten revertiert.
2. **Deflations-Parität:** Die `deflated_threshold` aus `automation/optimizer/deflation.py` wurde im Matrix-Lauf korrekt angewandt, aber in `confirm_per_symbol_promotion` ignoriert.
3. **Dispersions-Verunreinigung:** 50.0-Clipping-Sentinels, die verlustfreie OOS-Folds repräsentieren, vergrößerten fälschlich die Cross-Trial-Dispersion (Standardabweichung `pstdev`), was den Deflations-Threshold artifiziell in die Höhe trieb und solide Modelle unfair bestrafte.
**Fix:**
- **Top-k Median:** `confirm_per_symbol_promotion` identifiziert die Top-$k$ eligiblen Trials (Standard $k=5$, konfiguriert in `tournament.json['holdout_top_k']`), führt den OOS-Backtest für alle aus und bildet einen **Median-Holdout** (Robustheitsmaximierung).
- **Deflation Gate in Confirm:** Das deflationierte Threshold-Korrektiv (`deflated_min_sortino`) wird berechnet und fungiert (falls aktiviert) als zusätzliches Holdout-Ausschluss-Gate gegen die Median-Holdout-Performance.
- **50-Clip Sentinel Ausschluss:** `50.0` Werte (Sentinels für `Zero-Loss`) sind jetzt bei der Dispersion-Berechnung (`cand_sortinos`) sowohl in `backtest_runner.py` als auch in `confirm.py` strikt ausgeschlossen.

### 🟢 Issue #595 — Sortino-Annualisierungsfaktor nutzt 24/7-Kalenderfrequenz auf einem RTH-Equity-Instrument [BEHOBEN: GH-#595]
**Symptom:** Der Median-Δt extrapolierte die dominante Intra-Session-Frequenz (1h) auf ein Kalenderjahr und unterstellte somit fälschlicherweise 8766 Handelsstunden pro Jahr für alle Instrumente. Dies führte bei RTH-Equities (wie TSLA) zu einer systematischen Überschätzung des annualisierten Sortino um den Faktor $\sqrt{8766 / 1638} \approx 2.31$, wodurch absolute Sortino-Gates (`oos_min_sortino: 1.0`) inflationiert und Hard-Clips verfälscht wurden.
**Fix/Regel:** Die empirischen Observations-per-Year werden nun direkt aus der realen Zeitspanne abgeleitet:
$$ periods\_per\_year = \frac{n\_periods \cdot 31\,557\,600.0}{total\_span\_seconds} $$
Dies liefert für TSLA-1h automatisch $\approx 1638$ und für 24/7-Krypto $\approx 8766$ Bars pro Jahr. `annualization_periods_per_year` bleibt als optionaler, expliziter non-null Override aktiv.
**Betroffen:** `automation/backtest_runner.py`, `automation/config/optimizer.json`, `automation/tests/test_issue_510_annualization.py`

---

## 🧭 Bug-Kaskade #587–#600 — Reward-/Metrik-Pipeline-Kohärenz (Sortino → Gate → Reward → Holdout → Selektion)

> **Kontext (Forensik aus dem 2026-07-13-Sweep-Log):** Eine geschlossene Kausalkette entkoppelte den
> risikoadjustierten Sortino vom tatsächlichen Ergebnis: eine 24/7-Annualisierung auf RTH-Equity (#587),
> ein Hard-Clip an der Quelle (#588), ein Fold-Median statt einer gepoolten Grösse (#589), eine über
> Fold-Degeneration umgehbare Dispersionsstrafe (#590), ein an den Clip gekoppelter Reward-Floor (#591),
> eine unerreichbare Deflationsschwelle (#592), eine den Sortino aushebelnde ODER-Gate-Klausel (#593) und
> ein Holdout-Reward aus Platzhalter + Frankenstein-Vektor (#594). Diese Sektion ist die **wasserdichte**
> Referenz aller Fixes. **Reward-Semantik-Version 7 → 8** — alte SQLite-Studies (`{WORK}/sweep/*.db`) sind
> inkompatibel und werden vom Semantics-Guard fail-loud mit `REJECT_STALE_STUDY_SEMANTICS` verworfen.

### 🟢 Pitfall #121 — Sortino-Hard-Clip an der Quelle vernichtet die Rangordnung [BEHOBEN: GH-#588]
**Symptom:** `RATIO_CAP = 15.0` klemmte `sortino = max(-15, min(sortino_raw, 15))` in `_calculate_stats`. 34–40 % der Fold-Sortinos lagen im Log exakt auf ±15; zwei Trials mit `sortino_raw` 18 und 47 waren danach ununterscheidbar (Gradient 0), und die weiche #559-`asinh`-Sättigung im Reward konnte die gelöschte Information nicht rekonstruieren (**zwei Sättigungsstufen hintereinander, die erste hart**).
**Fix/Regel:** KEIN Clip mehr in `_calculate_stats`. Der Sortino ist ungeklemmt; nur ein **reiner Numerik-/Datenfehler-Guard** `sortino_numeric_guard` (tournament.json, Default `1e6`) greift: `|sortino_raw| > guard` ⇒ `None` + **fail-loud** `SORTINO_GUARD_TRIPPED` (WARNING mit `sortino_raw`/`n_periods`/`dd_dev`). Der Guard ist NIE `min/max`. Die semantische Sättigung passiert **ausschliesslich** in `reward.py` via `sortino_soft_scale` (streng monoton, TPE-Gradient überlebt). `profit_factor` und `calmar_ratio` nutzen einen **eigenen** Cap `profit_factor_cap` (tournament.json, Default `15.0`) — NICHT denselben wie der Sortino (rechtsschiefe Kennzahlen, dort ist ein Cap sinnvoll). **Invariante:** `grep sortino_clip_abs automation/backtest_runner.py` = 0 Treffer (der Reward-Floor ist seit #591 entkoppelt).
**Betroffen:** `automation/backtest_runner.py` (`_calculate_stats`, `_read_sortino_numeric_guard`, `_read_profit_factor_cap`), `automation/config/tournament.json`, `automation/tests/test_issue_588_no_source_clip.py`

### 🟢 Pitfall #122 — Drei Aggregationsebenen entkoppeln den Sortino vom Ergebnis (Fold-Median maskiert Katastrophen) [BEHOBEN: GH-#589]
**Symptom:** Der OOS-Sortino war der **Fold-Median** (n=4, Standardfehler ≈ `1.25·σ/√4 = 0.63σ` ⇒ ±3–5 Sortino-Einheiten bei `σ(fold_sortino) ≈ 5–8`), während `total_return` compoundiert war. Ergebnis: `corr(oos_sortino, oos_total_return)` je Study zwischen **−0.44 und +0.24**; **245/600 Trials** hatten `return > 0 ∧ sortino < 0` (ökonomisch unmöglich). Ein einzelner katastrophaler Fold (`per_fold = [−15, 15, 15, 6.56]` ⇒ Median 10.78) wurde vom Median vollständig maskiert.
**Fix/Regel:** **EINE** Aggregationsebene: der Sortino ist der **gepoolte** Wert aus der konkatenierten, purged OOS-Equity-Kurve (`oos_mtm`) — **derselbe Pfad**, aus dem `total_return` kommt (`mtm_frames`). Damit sind Zähler und Nenner *per Konstruktion* kohärent. `apply_fold_aggregation` überschreibt `sortino_ratio` NICHT mehr mit dem Fold-Median; der Median bleibt nur forensisch als `sortino_ratio_fold_median`, der gepoolte Wert zusätzlich als `sortino_ratio_pooled`. `parse_tournament` liest den gepoolten `oos_metrics["sortino_ratio"]` (nicht mehr `median(oos_fold_sortinos)`). **Kohärenz-Invariant** `_assert_sortino_return_coherence`: `sign(oos_sortino) == sign(oos_total_return)` bei `|total_return| > 1e-4`, Verletzung ⇒ `ERROR` + `oos_coherence_violation`-Flag. Die Fold-**Konsistenz** bleibt ein **eigenständiges** Signal (nicht in den Sortino gemittelt): Gate `oos_min_profitable_folds_frac` + `oos_min_evaluable_folds`, Reward `fold_dispersion_weight` auf `pstdev(per_fold_RETURN)`. `apply_fold_aggregation` liest `tournament.json` **nicht mehr doppelt** (der Fold-Median-Winsorize-Read entfiel).
**Betroffen:** `automation/backtest_runner.py` (`apply_fold_aggregation`, `collect_oos_fold_returns`, `_assert_sortino_return_coherence`), `automation/optimizer/parsing.py`, `automation/tests/test_issue_589_sortino_return_coherence.py`

### 🟢 Pitfall #123 — Fold-Degeneration umgeht die Dispersionsstrafe (Reward-Hacking: Bewertung LÖSCHEN statt Performance verbessern) [BEHOBEN: GH-#590]
**Symptom:** `atr_trailing 0.713 + max_bars 7` (engster Stop, kürzeste Haltedauer) ⇒ sehr wenige Verlust-Trades ⇒ `losses_count == 0` ⇒ `sortino = None` ⇒ der Fold **verschwand** aus `oos_fold_sortinos` ⇒ `len(fold) < 2` ⇒ `fold_dispersion_penalty = 0.0`, und der verbleibende Fold clippte auf den Maximal-Reward. **Der Optimierer lernte, die Bewertung zu löschen statt die Performance zu verbessern** (FlashCrash-Gewinner #56: `per_fold = [15.0]`, `reward = 5.247`, der höchste aller Studies — der Holdout quittierte es sofort mit `R_symbol: −6.23`).
**Fix/Regel:** (1) **`losses_count == 0` ist KEIN Ausstiegsgrund mehr** — ein verlustfreier Fold hat eine wohldefinierte Downside-Deviation von 0, der `sortino_downside_floor` (#573) liefert einen endlichen, positiven Sortino. (2) **Fehlende Folds sind eine Strafe, keine Auslassung**: `fold_dispersion_penalty` normiert über `oos_folds_total` (nicht `len(valid_folds)`); ein fehlender Fold trägt eine Return-äquivalente Maximal-Unsicherheit `missing_fold_penalty_scale · (fehlende/gesamt)` (optimizer.json, Default `0.05`, deklarativ). (3) **Gate `min_evaluable_folds`**: ein Trial mit `oos_folds_total > 1`, aber `< oos_min_evaluable_folds` validen Fold-Sortinos ist nicht eligible (tournament.json `oos_min_evaluable_folds: 2`, in `eligible_requires_all`).
**Betroffen:** `automation/backtest_runner.py` (`_calculate_stats`, `_evaluate_oos_eligibility`), `automation/optimizer/reward.py`, `automation/config/{optimizer,tournament}.json`, `automation/tests/test_issue_590_fold_degeneration.py`

### 🟢 Pitfall #124 — Reward-Floor an den Sortino-Clip gekoppelt ⇒ Plateau mit Gradient 0 im eligiblen Ast [BEHOBEN: GH-#591]
**Symptom:** Der eligible Reward-Floor war `−sortino_clip_abs` (−5.0) und lag **über** dem, was der eligible Ast bei realistischen Sortinos natürlich produziert ⇒ **6 von 8** SmaCrossover-Trials (die gesamte Kandidatenmenge) klemmten exakt auf −5.0 (TPE-Plateau, Gradient 0). Zusätzlich deckelte `penalty_relative_cap` die Strafen auf `0.5·|base|` — bei **negativem** `base` ein Konditionierungsfehler (je schlechter die Base, desto grösser die erlaubte Strafe).
**Fix/Regel:** (1) Eigener, von `sortino_clip_abs` **entkoppelter** `evaluable_reward_floor` (optimizer.json, `−12.0`); seine Aufgabe ist die **Ordnungsinvariante** (`evaluable > failure > unevaluable`), nicht die Kompression. `penalty_unevaluable_oos` von −10 auf −20 verschoben, damit die Bänder **disjunkt und breit** bleiben: `unevaluable [−20, −19.75] < failure (−19.75, −12.001] < evaluable [−12, +∞)`. Die Invariante `max(failure) < min(evaluable)` bleibt hart getestet. (2) `penalty_relative_cap` bindet an eine **positive Skalenkonstante** `sortino_soft_scale` (`cap_abs = penalty_relative_cap · soft_scale`), NICHT an `|base|` — vorzeichen-invariant (`base = −5` und `base = +5` ⇒ gleiche Cap-Höhe). (3) `reward_semantics_version` **7 → 8**; der Semantics-Guard bricht bei Mismatch **fail-loud** mit `REJECT_STALE_STUDY_SEMANTICS` ab.
**Betroffen:** `automation/optimizer/reward.py` (`_evaluable_floor`, `compute_reward`, `_constraint_failure_reward`), `automation/optimizer/run_optimization.py` (`_check_reward_semantics_version`), `automation/config/optimizer.json`, `automation/tests/test_issue_591_floor_decoupled.py`

### 🟢 Pitfall #125 — Deflations-Schwelle über der Clip-Grenze ⇒ strukturell unerreichbar [BEHOBEN: GH-#592]
**Symptom:** Die Deflations-Schwelle (`σ·Φ⁻¹(0.95^(1/N))`) wurde auf der **geklemmten Sortino-Skala** geschätzt und angewandt (Kategorienfehler: `deflated_threshold` modelliert das Maximum von N i.i.d. **unbeschränkten** Normalen). In **4 von 6** Studies lag sie über dem maximal erreichbaren Sortino (RATIO_CAP 15) ⇒ garantierter Reject. Zusätzlich griff ein `!= 50.0`-Sentinel-Filter ins Leere (der Clip war 15, nicht 50 — hartcodierte Wert-Drift) und blähte die Dispersion mit Clip-Artefakten auf.
**Fix/Regel:** Die Deflation läuft auf der **unbeschränkten Reward-Skala** (dem tatsächlichen Selektionskriterium `argmax(reward)`) mit **`baseline = median(rewards)`** (die Reward-Skala ist NICHT nullzentriert — `baseline = 0.0` wäre auf einer bei −6.8 zentrierten Skala sinnlos). `deflated_reward_threshold(rewards, confidence)` in `deflation.py` (Single Source of Truth für Study-Telemetrie **und** Holdout-Drop in `confirm.py`). Der Sentinel-Filter **entfällt ersatzlos** (nach #588 gibt es keine Clip-Sentinels; `grep -rn "!= 50.0" automation/*.py` = 0). Telemetrie je Study in `optimizer_study_completed`: `deflated_min_reward`, `deflation_n`, `deflation_sigma`, `deflation_baseline`. Der `deflated_threshold`-Monte-Carlo (test_issue_553) bleibt grün (die Funktion war korrekt, nur ihre Anwendung war falsch).
**Betroffen:** `automation/optimizer/deflation.py` (`deflated_reward_threshold`), `automation/optimizer/confirm.py`, `automation/optimizer/run_optimization.py` (`_emit_study_summary`), `automation/backtest_runner.py` (`_ALL_WIN_SENTINEL`), `automation/tests/test_issue_592_deflation_reachable.py`

### 🟢 Pitfall #126 — `eligible_requires_any` hebelt das Sortino-Gate aus (ODER-Klausel verknüpft ungleichgerichtete Kennzahlen) [BEHOBEN: GH-#593]
**Symptom:** `eligible_requires_any: [min_sortino, min_profit_factor, min_win_rate]` liess **190/600 Trials** über den Profit-Factor passieren, obwohl ihr Sortino negativ war. Eine ODER-Verknüpfung eines **risikoadjustierten** Kriteriums (Sortino) mit **Häufigkeits**-Kennzahlen (PF/Win-Rate) ist genau die Struktur, die ein Optimierer ausnutzt. Zusätzlich sah `_any_condition_distance` nur Sortino + PF (nicht `min_win_rate`) ⇒ Gate und Reward sahen unterschiedliche Klauselmengen (Verstoss gegen die #549-Parität).
**Fix/Regel:** Nach #587–#589 ist der Sortino wieder kohärent ⇒ er gehört in **`eligible_requires_all`** (HART) mit einer auf die √1638-Skala rekalibrierten Schwelle (`oos_min_sortino`: `1.0 · √(1638/8766) ≈ 0.43`, empirisch aus einem Kalibrierlauf zu verfeinern). `eligible_requires_any` reduziert sich auf **gleichgerichtete** Häufigkeits-Kennzahlen (`min_profit_factor` ODER `min_win_rate`). `_any_condition_distance` spiegelt EXAKT die Config-Klauseln (inkl. `min_win_rate`); `assert_any_condition_parity` wirft beim Config-Load **`ValueError`**, wenn eine `eligible_requires_any`-Klausel keinen korrespondierenden Distanz-Term hat (die erlaubte Menge ist `_ANY_CONDITION_CLAUSES`). Der Parity-Guard läuft im Sweep-Preflight (`_assert_gate_reward_parity`).
**Betroffen:** `automation/config/tournament.json`, `automation/optimizer/reward.py` (`_any_condition_distance`, `assert_any_condition_parity`, `_ANY_CONDITION_CLAUSES`), `automation/optimizer/sweep.py`, `automation/tests/test_issue_593_gate_reward_clause_parity.py`

### 🟢 Pitfall #127 — Holdout-Reward aus Platzhalter (`is_sortino_median=0.0`) + Frankenstein-Metrikvektor [BEHOBEN: GH-#594]
**Symptom:** (A) `median_m_symbol` wurde mit `is_sortino_median = 0.0` (Platzhalter) konstruiert; bei negativem `base` erzeugte `compute_reward` daraus eine **fiktive Overfit-Strafe** `0.5·|base|`, die direkt in die Promotion-Entscheidung (`R_symbol`) floss. (B) Jede Metrik (sortino/drawdown/return) wurde **unabhängig** über die Top-k gemedianed ⇒ der resultierende Vektor gehörte zu **keinem einzigen real gelaufenen Backtest**. Study-Reward und Holdout-Reward liefen damit **nicht** auf derselben Funktion — ihr Vergleich (der »Overfit-Spread«) war formal bedeutungslos.
**Fix/Regel:** (A) `compute_reward(holdout=True)` schaltet die IS-abhängigen Terme (Overfit-Divergenz, Fold-Dispersion) **ab** (kein 0.0-Platzhalter); ohne `holdout=True` ist `is_sortino_median is None` ein **`ValueError`** (kein stiller 0.0-Default). Study- **und** Holdout-Reward laufen nachweislich über `compute_reward`. (B) **Vektor-Median** statt komponentenweisem Median: `_median_rank_index` (dokumentierte Tie-Break-Regel — unterer Median, `None` als −inf) wählt den Lauf mit dem medianen `oos_total_return`-Rang; sein **vollständiger, kohärenter** Metrikvektor geht in Gate und Reward. `_lower_median_or_none` ist durch diese benannte, getestete Funktion ersetzt.
**Betroffen:** `automation/optimizer/reward.py` (`compute_reward(holdout=...)`), `automation/optimizer/confirm.py` (`_median_rank_index`, `confirm_per_symbol_promotion`), `automation/tests/test_issue_594_holdout_reward.py`

### 🟢 Pitfall #128 — `--strategies all` evaluiert nur 6 von 10 Strategien — lautlos [BEHOBEN: GH-#595 (GitHub #603)]
**Symptom:** `spaces.sample_params` kennt 6 Strategien und wirft `ValueError: Unknown strategy` für alle anderen; der Fehler wurde in der Fault-Isolation des Sweeps **still verschluckt** ⇒ 40 % des aktiven Strategieraums nie evaluiert, **0 ERROR-Zeilen**. Zusätzlich loggte `is_symbol_tunable` nur **einen** von **drei** Ablehnungsgründen (`INSUFFICIENT_HISTORY`; `PARAM_DATA_RATIO_TOO_LOW`/`OOS_FOLD_TOO_SHORT` waren still).
**Fix/Regel:** (1) `assert_strategy_space_parity(strategies)` läuft im Sweep-Preflight **fail-loud VOR dem ersten Trial**: jede aktive Strategie MUSS einen Suchraum haben (sonst `ValueError`/`STRATEGY_NO_SEARCH_SPACE`). *Auflösung des Konfigurationswiderspruchs:* die 4 spraumlosen Strategien (MeanReversion, DynamicBreakout, TrendPullback, AdxAtrMomentum) sind in `strategies.json` bereits `active:false`. (2) `enumerate_tunable_pairs` fängt den `ValueError` explizit und emittiert `STRATEGY_NO_SEARCH_SPACE` (ERROR, strukturiert). (3) **Alle drei** `is_symbol_tunable`-Gründe werden geloggt. (4) `sweep_completed` trägt `strategies_requested`/`strategies_enumerated`/`strategies_skipped[]` (Grund je Strategie); eine Differenz > 0 erzeugt eine WARNING-Zusammenfassung.
**Betroffen:** `automation/optimizer/sweep.py` (`strategy_has_search_space`, `assert_strategy_space_parity`, `enumerate_tunable_pairs`, `run_per_symbol_sweep`), `automation/tests/test_issue_595_strategy_space_parity.py`

### 🟢 Pitfall #129 — `required_span_days` widerspricht `compute_walk_forward_window` um exakt das Embargo [BEHOBEN: GH-#596 (GitHub #604)]
**Symptom:** `required_span_days` liess `embargo_period_days` weg (405 d), `compute_walk_forward_window` reserviert es (426 d, Issue #548). Der Fail-Loud-Guard `assert_walk_forward_geometry` (Issue #531, Zweck: Verhinderung stiller `.loc`-Klemmung) prüfte damit gegen eine um **exakt `embargo_period_days`** zu kleine Zahl ⇒ ein Symbol mit 405–425 d Historie passierte, obwohl `start` vor den Datenanfang fiel und das IS-Fenster still verkürzt wurde (**genau die No-Clamping-Verletzung, die #531 ausschliessen sollte** — im Lauf zufällig durch `gate1_buffer_days` maskiert).
**Fix/Regel:** `required_span_days` **und** `required_bars` um `embargo_period_days` erweitert (`is + embargo + splits·oos + holdout` = 426 d Produktions-Geometrie). Regressionstest prüft die beiden Geometrie-Quellen **numerisch gegeneinander**: `(end − start).days + holdout_days == required_span_days(wf)` für alle `embargo ∈ {0,7,21}`, `splits ∈ {1,2,4}`. Docstring korrigiert (die alte Begründung war sachlich falsch und hätte einen künftigen Agenten erneut in die Falle geführt).
**Betroffen:** `automation/optimizer/gate.py` (`required_span_days`, `required_bars`, `is_symbol_tunable`), `automation/tests/test_issue_596_geometry_source_parity.py`

### 🟢 Pitfall #130 — Turnover-Strafe inaktiv, Drawdown-Term strukturell tot, Randlösungen unbeobachtet [BEHOBEN: GH-#597 (GitHub #605)]
**Symptom:** (A) `penalty_turnover_weight` **fehlte** in `optimizer.json` ⇒ Default 0.0 ⇒ die Turnover-Strafe (#509) war seit Einführung wirkungslos; **jede** Randlösung zeigte Richtung »Trade-Frequenz maximieren« (der HourlyMeanReversion-Gewinner machte 293 OOS-Trades ≈ 176 bps Kostendrag). (B) `oos_max_drawdown` ist portfolio-relativ (auf `start_capital`), die Strategie setzt nur einen Bruchteil ein ⇒ realer DD 0.6–2.4 %, gegen den 30 %-Gate-Cap normiert ergab `dd_penalty ≈ 0.004` (**vier Grössenordnungen** unter den übrigen Termen — Gate nie bindend, Reward-Term nie wirksam).
**Fix/Regel:** (A) `penalty_turnover_weight` gesetzt, **aus den Round-Trip-Kosten abgeleitet**: `c_rt = commission_bps (1.0) + spread_bps (TSLA 2.0) = 3 bps = 3e-4` (backtest.json) — ein zusätzlicher Trade kostet im Reward genau seine Erwartungskosten (kein Magic-Value). (B) `dd_reward_scale` (optimizer.json, `0.03`) normiert die Drawdown-Strafe im Reward auf die **realisierte Risiko-Skala**, entkoppelt vom Gate-Cap (`dd_penalty = penalty_dd_weight·(oos_max_drawdown/dd_reward_scale)²`); die vollständig notional-relative Gate-Normierung (analog #546) bleibt der robuste Langfrist-Weg. (C) `boundary_hit_fraction` in `optimizer_study_completed`: Anteil der Gewinner-Parameter innerhalb 2 % einer Suchraumgrenze; > 0.3 ⇒ WARNING (Randlösungs-Signatur).
**Betroffen:** `automation/config/optimizer.json`, `automation/optimizer/reward.py` (`_dd_penalty`), `automation/optimizer/run_optimization.py` (`_boundary_hit_fraction`, `_emit_study_summary`), `automation/tests/test_issue_597_turnover_active.py`

### 🟢 Issue #598/#599/#600 — Benchmark-Gate aktiviert, Bootstrap-CI & CPCV/PBO als getestete Utilities [BEHOBEN: GH-#606]
- **#598 (Benchmark-relatives Gate aktiviert):** `oos_min_excess_return: 0.0` gesetzt UND `min_excess_return` in `eligible_requires_all` — die einzige Klausel, die echtes **Alpha** von **Beta** trennt (`excess = oos_total_return − oos_buyhold_return`; TSLA als Buy&Hold-Benchmark über dasselbe OOS-Fenster, bereits via `mtm_monitor.get_benchmark_series()` verdrahtet). **Fail-open**: greift nur, wenn die Benchmark-Telemetrie vorliegt.
- **#599 (Stationary-Bootstrap-CI):** `automation/optimizer/bootstrap.py` — Politis/Romano-Bootstrap mit Blocklänge aus der Autokorrelation (`optimal_block_length`), CI für beliebige Statistiken (`bootstrap_ci`), Gate-Kriterium `ci_lower_bound_passes` (prüft die **Untergrenze** des CI statt des Punktschätzers — die saubere Version von `deflated_selection`). Reine, getestete Funktionen, bereit für Gate-/Telemetrie-Verdrahtung.
- **#600 (CPCV + PBO):** `automation/optimizer/cpcv.py` — Combinatorial Purged Cross-Validation (`cpcv_paths` erzeugt `C(N,k)` Train/Test-Pfade, `purged_train_groups` mit Purge+Embargo) und **Probability of Backtest Overfitting** (`probability_of_backtest_overfitting`, Rang-Logit-Methode nach Bailey/López de Prado). `PBO > 0.5` ⇒ der IS-Gewinner ist OOS schlechter als der Median. Reine, getestete Funktionen, bereit als Study-Metrik.
**Betroffen:** `automation/config/tournament.json`, `automation/optimizer/bootstrap.py`, `automation/optimizer/cpcv.py`, `automation/tests/{test_issue_599_bootstrap_ci,test_issue_600_cpcv_pbo}.py`

### 📋 Neue/geänderte Config-Keys (Bug-Kaskade #587–#600)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `sortino_numeric_guard` | tournament.json | `1e6` | Reiner Numerik-Guard (#588), KEINE Sättigung; fail-loud `SORTINO_GUARD_TRIPPED` |
| `profit_factor_cap` | tournament.json | `15.0` | Eigener Cap für PF/Calmar (#588), entkoppelt vom Sortino |
| `oos_min_evaluable_folds` | tournament.json | `2` | Fold-Degenerations-Gate (#590), in `eligible_requires_all` |
| `oos_min_sortino` | tournament.json | `1.0 → 0.43` | √1638-Rekalibrierung (#587) + in `eligible_requires_all` verschoben (#593) |
| `oos_min_excess_return` | tournament.json | `0.0` | Benchmark-Alpha-Gate aktiviert (#598), in `eligible_requires_all` |
| `eligible_requires_all` | tournament.json | +`min_sortino`,`min_evaluable_folds`,`min_excess_return` | Sortino/Fold/Alpha als HARTE Kriterien |
| `eligible_requires_any` | tournament.json | `[min_profit_factor, min_win_rate]` | nur noch gleichgerichtete Häufigkeits-Kennzahlen (#593) |
| `evaluable_reward_floor` | optimizer.json | `-12.0` | Von `sortino_clip_abs` entkoppelter Reward-Floor (#591) |
| `penalty_unevaluable_oos` | optimizer.json | `-10.0 → -20.0` | Bänder disjunkt halten nach Floor-Entkopplung (#591) |
| `missing_fold_penalty_scale` | optimizer.json | `0.05` | Strafe für fehlende Folds in der Dispersion (#590) |
| `penalty_turnover_weight` | optimizer.json | `0.0003` | Aus `c_rt` (commission+spread) abgeleitet (#597) |
| `dd_reward_scale` | optimizer.json | `0.03` | Realisierte Risiko-Skala für dd_penalty (#597) |
| `reward_semantics_version` | optimizer.json | `7 → 8` | Alte Studies inkompatibel; `REJECT_STALE_STUDY_SEMANTICS` (#591) |

### 🔒 Watertight Invariants (Bug-Kaskade #587–#600) — für künftige Agenten
- **Sortino, EIN Ort der Sättigung:** `_calculate_stats` klemmt den Sortino NIE (nur `sortino_numeric_guard` ⇒ `None`+Log). Die einzige semantische Sättigung ist `reward.py::_apply_soft_scale` (asinh). Ein Hard-`min/max` auf den Sortino ist **verboten** (tötet den TPE-Gradienten, Pitfall #121/#117).
- **Sortino = gepoolt, kohärent mit Return:** Gate UND Reward lesen `oos_metrics["sortino_ratio"]` aus `oos_mtm`. Ein Fold-Median-Sortino als Gate-/Reward-Grösse ist **verboten** (Pitfall #122). `sign(oos_sortino) == sign(oos_total_return)` bei `|return| > 1e-4` ist hart geprüft.
- **Fold-Konsistenz ist ein eigenes Signal:** Dispersion läuft über `per_fold_RETURN` (nie über `per_fold_SORTINO`), normiert über `oos_folds_total`; fehlende Folds werden bestraft, nie ausgelassen (Pitfall #123).
- **Reward-Floor entkoppelt:** Der eligible Floor ist `evaluable_reward_floor`, NIE `−sortino_clip_abs`. Bandinvariante `max(failure) < min(evaluable)` hart getestet. `penalty_relative_cap` bindet an eine **positive** Skalenkonstante, nie an `|base|` (Pitfall #124).
- **Deflation auf der Reward-Skala:** Multiple-Testing-Korrektur immer auf dem tatsächlichen Selektionskriterium (`reward`, `baseline=median`), nie auf einer geklemmten Teil-Kennzahl. Kein hartcodierter Sentinel-Filter (Pitfall #125).
- **Kein Platzhalter in einem Reward-Ausdruck:** Holdout-Rewards laufen mit `holdout=True` (IS-Terme aus); `is_sortino_median is None` ohne `holdout` ⇒ `ValueError`. Holdout-Metriken sind **kohärente Vektoren EINES** Laufs, nie komponentenweise gemedianed (Pitfall #127).
- **Geometrie-Quellen numerisch identisch:** `required_span_days == (compute_walk_forward_window-Span) + holdout`, inkl. `embargo_period_days`. Zwei Geometrie-Formeln müssen gegeneinander getestet werden, nie unabhängig (Pitfall #129).
- **Strategie/Suchraum-Parität fail-loud:** Jede aktive Strategie MUSS einen Suchraum haben (`assert_strategy_space_parity` vor dem ersten Trial). Gate- und Reward-Klauseln müssen dieselbe `eligible_requires_any`-Menge sehen (`assert_any_condition_parity`).

---

## Bug-Kaskade #613/#615/#617 — Selektions-/Divergenz-/Gate-Kohärenz (P0/P0/P1)

Drei orthogonale, aber im selben Selektions-Reward-Pfad wirkende Defekte. Alle drei sind
»die-Bewertung-umgehen-statt-Performance-liefern«-Klassen: ein toter Selektions-Filter, eine
inkohärente Divergenz-Grösse und ein vakuanter Gate-Guard.

### 🟢 Pitfall #131 — Top-k-Holdout-Selektion ist toter Code: `is_rejection_detail` ist der STRING `"NONE"`, nie `None` [BEHOBEN: GH-#615]
**Symptom:** `holdout_top_k = 5` (Default), faktisch lief **genau ein** Holdout-Backtest je Study (Wallclock-Beleg: ≈ 1·`backtest_ms_median`, nicht 5). Issue #576 (Top-k-Robustheit) und #594 (kohärenter Median-Vektor, Index immer 0) waren komplett inert.
**Root-Cause:** `run_optimization._classify_is_rejection_detail` stempelt für eligible Trials den **String** `"NONE"`; `confirm.confirm_per_symbol_promotion` filterte auf `t.user_attrs.get("is_rejection_detail") is None` (Python-`None`). `"NONE" is None` ist **nie** wahr ⇒ `eligible_trials == []` in **jeder** Study ⇒ stiller Fallback `[study.best_trial]` (k=1). Zusätzlich exportierte das Proposal einen **gemischten Vektor**: `symbol_params` von Trial Y (argmax reward), aber `R_symbol`/`holdout_passed`/Deflation von Trial X (Median-Rang) ⇒ es wurden **nie validierte Parameter** promotet.
**Fix/Regel:** (1) Filter auf das **explizit gestempelte** `oos_eligible` (neu in `make_symbol_objective`; kohärent zu `is_rejection_detail == IS_REJECTION_NONE`). Benannte Konstante `IS_REJECTION_NONE = "NONE"` ersetzt die verstreuten String-Literale. (2) **KOHÄRENZ-Verbindlichkeit:** Der Median-Rang-Trial wird **VOLLSTÄNDIG** promotet — `symbol_params`, `R_symbol`, `holdout_passed` UND der Deflations-Check stammen aus **EINEM** `trial_dir` (`promoted_trial_dir`, im Proposal ausgewiesen). Ein gemischter Vektor ist **verboten**. Der Median-Rang (nicht argmax) ist die bewusste Robustheits-Wahl: Top-k filtert IS-Glück, der Median filtert Holdout-Glück. (3) `holdout_top_k` in `tournament.json` deklariert (Zero-Hardcoding). (4) Leere eligible-Menge ⇒ **fail-loud** `HOLDOUT_NO_ELIGIBLE_TRIALS`-Event + Rejection; **KEIN** Floor-Trial (argmax reward über alle Trials, evtl. `evaluable_reward_floor`) wandert unbemerkt in den Holdout.
**Betroffen:** `automation/optimizer/confirm.py`, `automation/optimizer/run_optimization.py` (`IS_REJECTION_NONE`, `oos_eligible`-Stempel), `automation/config/tournament.json` (`holdout_top_k`), `automation/tests/test_issue_615_topk_holdout_selection.py`

### 🟢 Pitfall #132 — Aggregations-Asymmetrie IS (Fold-/Symbol-Median) ↔ OOS (pooled) macht die Divergenz-Strafe bedeutungslos [BEHOBEN: GH-#613]
**Symptom:** `corr(is_sortino_median, oos_sortino) = 0.185`; **96 %** aller eligiblen Trials im `oos_luck`-Ast der symmetrischen Divergenzstrafe, **72 %** exakt am Cap (2.5) — ein **konstanter Term ohne Gradient**.
**Root-Cause:** Issue #589 stellte den OOS-Sortino korrekt auf den **gepoolten** Wert aus der konkatenierten OOS-Equity-Kurve um. Die IS-Seite blieb `is_sortino_median` (Fold-/Symbol-Median). `reward.py` verglich in `overfit_gap = is_sortino_val − base` zwei Grössen **verschiedener Aggregationsebenen**, Fenster (180 d IS vs. 4×45 d OOS) und Skalen ⇒ die Grössenordnungs-Diskrepanz trieb den Term systematisch in die Sättigung.
**Fix/Regel:** (1) `is_sortino_pooled` aus der IS-Equity-Kurve (`_split_and_stats`, **derselbe** `_calculate_stats`-mtm-Pfad wie `oos_sortino`), als `TournamentMetrics.is_sortino_pooled` durchgereicht. (2) `reward.py` nutzt **ausschliesslich** `is_sortino_pooled` für die Divergenz (Fallback `is_sortino_median` nur bei Legacy-JSONs ⇒ bit-identisch); `is_sortino_median` bleibt **rein forensische** Telemetrie. (3) **Fail-loud Kohärenz-Assertion** `_assert_is_oos_sortino_coherence` (analog `_assert_sortino_return_coherence`): IS- und OOS-Sortino MÜSSEN dieselbe `sortino_aggregation_basis` tragen (`pooled_equity_curve`); ein `trade_sequential`-Sortino darf nie gegen einen pooled verglichen werden. (4) `penalty_relative_cap` von 0.5 auf **1.0** rekalibriert (cap_abs 5.0): der alte Cap 2.5 war für die **inkohärente** Differenz gewählt; bei kommensurablen Skalen bindet er nur noch für eine genuin pathologische Divergenz.
**Invariante:** IS- und OOS-Sortino der Divergenz-Strafe stammen aus **derselben** Aggregationsebene (`pooled_equity_curve`); identische IS-/OOS-Equity ⇒ `divergence_penalty == 0.0`.
**Betroffen:** `automation/backtest_runner.py` (`_split_and_stats`-Basis-Tag, `_build_single_symbol_oos`, `_assert_is_oos_sortino_coherence`, Aggregat-Winner), `automation/optimizer/parsing.py` (`is_sortino_pooled`), `automation/optimizer/reward.py`, `automation/config/optimizer.json` (`penalty_relative_cap`), `automation/tests/test_issue_613_is_oos_sortino_coherence.py`

### 🟢 Pitfall #133 — Sortino/PF-`None`-Bypass im OOS-Gate: `oos_min_trades = 1` macht den Guard vakuant [BEHOBEN: GH-#617]
**Symptom:** Aktiver Reward-Hacking-Kanal (im Lauf nicht ausgelöst — `min(oos_total_trades)=37` —, aber strukturell offen): ein Trial mit **9 OOS-Trades und 0 Verlusten** (`sortino=None`, `pf=None`, `win_rate>0`) passiert **beide** Gates (`min_sortino`, `min_profit_factor`) **gratis**.
**Root-Cause:** `sortino` wird `None` bei `n < sortino_min_trades` (=10); `profit_factor` bei `gross_loss<=0` **oder** (`losses_count<2 ∧ n<50`). Der `None`-Guard prüfte `n_trades < req_trades` mit `req_trades = oos_min_trades = 1` ⇒ `9 < 1` = False ⇒ `sortino_valid` blieb True. Der Guard war **schwächer** als die Bedingung, unter der die Kennzahl überhaupt definiert ist — genau der `n < sortino_min_trades`-Pfad, den #590 offen liess.
**Fix/Regel:** (1) Der `None`-Guard nutzt `req_trades_guard = max(oos_min_trades, sortino_min_trades)` — nie schwächer als die Definierbarkeits-Bedingung. (2) `oos_min_trades` von **1 → 20** (≥ `sortino_min_trades`, an `min_trades` ausgerichtet). (3) Explizite, benannte Policy `undefined_metric_policy` (`"fail"` | `"fallback_total_return"`) in `tournament.json` — **kein impliziter Pass**: bei ausreichender Stichprobe, aber undefinierter Kennzahl (verlustfreier Fold) entscheidet die Policy, `"fallback_total_return"` ⇒ Pass iff `oos_total_return>0` (Parität zu `oos_sortino_fallback` / `_holdout_gate_passed`). Fehlt der Key ⇒ `"fail"` (strengster Default).
**Invariante:** Kein Trial mit `oos_total_trades < sortino_min_trades` erreicht je `oos_eligible == True`.
**Betroffen:** `automation/backtest_runner.py` (`_evaluate_oos_eligibility`), `automation/config/tournament.json` (`oos_min_trades`, `undefined_metric_policy`), `automation/tests/test_issue_617_undefined_metric_gate.py`

### 📋 Neue/geänderte Config-Keys (Bug-Kaskade #613/#615/#617)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `holdout_top_k` | tournament.json | `5` | Anzahl eligibler Trials im Top-k-Holdout (#576/#615); EXPLIZIT deklariert, lief vorher nie (k=1) |
| `undefined_metric_policy` | tournament.json | `"fallback_total_return"` | Policy für undefinierte Pflicht-Kennzahl bei ausreichender Stichprobe (#617); kein impliziter Pass |
| `oos_min_trades` | tournament.json | `1 → 20` | OOS-Mindest-Trades; `1` machte den `None`-Guard vakuant (#617) |
| `penalty_relative_cap` | optimizer.json | `0.5 → 1.0` | Divergenz-Cap für kommensurable (pooled) IS↔OOS-Skalen rekalibriert (#613) |

### 🔒 Watertight Invariants (Bug-Kaskade #613/#615/#617) — für künftige Agenten
- **Selektion filtert auf `oos_eligible`, nie auf einen String-vs-`None`-Vergleich:** `confirm` selektiert Holdout-Kandidaten über das gestempelte `oos_eligible`. `is_rejection_detail` ist ein **String** (`IS_REJECTION_NONE == "NONE"`), nie Python-`None` — ein `is None`-Filter darauf ist **verboten** (Pitfall #131). Leere eligible-Menge ⇒ `HOLDOUT_NO_ELIGIBLE_TRIALS`, **nie** ein stiller Floor-Trial-Fallback.
- **Ein `trial_dir` für den promoteten Vektor:** `symbol_params`, `R_symbol`, `holdout_passed` und der Deflations-Check stammen **nachweislich** aus **einem** Holdout-Lauf (dem Median-Rang-Trial). Ein gemischter Vektor (Params[Y] + Gate/Reward[X]) ist **verboten** (Pitfall #131).
- **IS↔OOS-Sortino: dieselbe Aggregationsebene:** Die Divergenz nutzt `is_sortino_pooled` (pooled equity curve) gegen `oos_sortino` (pooled equity curve) — beide `pooled_equity_curve`. Ein Fold-/Symbol-Median gegen einen pooled Wert ist **verboten** (`_assert_is_oos_sortino_coherence` fail-loud, Pitfall #132). `is_sortino_median` ist **nur** Telemetrie.
- **Der `None`-Guard ist nie schwächer als die Definierbarkeit:** Für eine bei `n < sortino_min_trades` undefinierte Kennzahl gilt `req_trades_guard = max(oos_min_trades, sortino_min_trades)`. Kein impliziter Pass — eine bei ausreichender Stichprobe undefinierte Pflicht-Kennzahl entscheidet die **benannte** `undefined_metric_policy` (Pitfall #133). `oos_total_trades < sortino_min_trades` ⇒ **nie** eligible.

---

## Bug-Kaskade #611–#625 — Statistische Signifikanz der Selektion (P0-Kaskade)

Elf Defekte auf **einer** Achse: das Selektionskriterium war nicht auf der Skala, auf der es
statistisch interpretierbar ist. Der Reward mischte eine unbeschränkte, annualisierte Kennzahl mit
einer Multiple-Testing-Korrektur auf der **falschen** (Bernoulli-Reward-)Skala; die Feasibility war
ein **Reward-Term** statt eines Constraints; ganze Verteidigungslinien (Bootstrap-CI, CPCV/PBO,
Fold-Dispersion) waren **verdrahtet-aber-tot** oder **numerisch inert**; das Trial-Budget war für die
Dimensionalität Zufallssuche; und die Holdout-Länge kann die eigene 0.95-Schwelle geometrisch gar
nicht tragen. Behoben in der vom Betreiber gewählten Reihenfolge (#614, #612, #611, #616, #618, #619,
#620, #622, #624, #625, #623); die PSR-Skalenkalibrierung ist ein **empirisch** getroffener,
dokumentierter Judgment-Call.

### 🟢 Pitfall #134 — Reward-Base ist der **annualisierte** Sortino: skalenabhängig, unbeschränkt, nicht signifikanz-interpretierbar [BEHOBEN: GH-#614]
**Symptom:** Die Reward-Base war der annualisierte Sortino (`·√A`). Zwei Trials mit **identischer** per-Periode-Risiko-Rendite, aber unterschiedlicher Bar-Frequenz (RTH vs. 24/7) bekamen **verschiedene** Bases; der TPE optimierte teils die Annualisierungs-Konstante statt die Strategie. Die Grösse war unbeschränkt ⇒ ein einzelner Ausreisser-Fold dominierte die Rangordnung.
**Root-Cause:** Der annualisierte Sortino ist **nicht** annualisierungs-invariant und trägt keine Stichprobenlänge `T` — er sagt nichts über die *Wahrscheinlichkeit*, dass der Edge echt ist. Die Deflation (Pitfall #136) braucht aber genau eine ∈ [0, 1]-Wahrscheinlichkeit.
**Fix/Regel:** Reward-Base ist die **Probabilistic Sharpe/Sortino Ratio** `PSR(0) = Φ[ŜR·√(T−1) / √(1 − γ₃·ŜR + ((γ₄−1)/4)·ŜR²)]` (López de Prado), berechnet in `backtest_runner._calculate_stats` aus dem **per-Periode**-Sortino `sortino_period`, `n_periods`, Sample-Schiefe/Kurtosis (`sample_skew_kurtosis`), `sr_star=0`. PSR ist **annualisierungs-invariant**, ∈ [0, 1] und **direkt** eine Signifikanz-Aussage. `reward.py`: `psr_base_active = getattr(m, "oos_psr", None) is not None` ⇒ `base = float(m.oos_psr)`; Legacy-JSONs ohne `oos_psr` fallen bit-identisch auf die asinh/Clip-Sortino-Base zurück. Der annualisierte Sortino bleibt **reine Telemetrie** (`oos_sortino_annualized`). Zusätzlich `sortino_numeric_guard` **1e6 → 25.0** (realistische Obergrenze; ein per-Periode-Sortino > 25 ist ein Daten-/Numerikfehler, kein Signal ⇒ `None` + `SORTINO_GUARD_TRIPPED`).
**Invariante:** Die Reward-Base ist annualisierungs-invariant und ∈ [0, 1] (PSR), niemals eine unbeschränkte, skalenabhängige Kennzahl. Der annualisierte Sortino ist **nie** ein Reward-Argument.
**Betroffen:** `automation/backtest_runner.py` (`_calculate_stats`, `oos_psr`/`sortino_period`/`n_periods`/`ret_skew`/`ret_kurtosis`), `automation/optimizer/deflation.py` (`probabilistic_sharpe_ratio`, `sample_skew_kurtosis`), `automation/optimizer/reward.py` (`psr_base_active`), `automation/optimizer/parsing.py`, `automation/config/tournament.json` (`oos_min_psr`, `sortino_numeric_guard`, `min_psr` in `eligible_requires_all`), `automation/tests/test_issue_614_psr_reward_base.py`

### 🟢 Pitfall #135 — Feasibility als **Reward-Term** (Straf-Cliff) verzerrt die TPE-Surrogatfläche statt sie zu begrenzen [BEHOBEN: GH-#612]
**Symptom:** Ineligible Trials bekamen einen tiefen Reward-Floor (`penalty_unevaluable_oos`/Failure-Band). Der TPE lernte sein Surrogat auf einer Fläche mit einem **Cliff** an der Eligibility-Grenze ⇒ der Sampler wich der Grenze systematisch aus, statt sie zu **erkunden** (der beste eligible Punkt liegt oft direkt daneben).
**Root-Cause:** Feasibility ist ein **Constraint**, keine Rendite. In den Reward gepresst, verfälscht sie den Gradienten der eigentlichen Zielgrösse und verschenkt die native Constraint-Behandlung des Samplers.
**Fix/Regel:** `_compute_oos_constraints(metrics)` stempelt je Trial `oos_constraint_violations` (`(0.0,)` eligible, sonst `(Σ max(0,−delta),)` aus den Gate-Deltas); `_oos_constraints_func(trial)` liest den Stempel; **beide** Sampler-Erzeugungen (`TPESampler`, plus `NSGAIISampler`-Pfad) bekommen `constraints_func=_oos_constraints_func`. Optuna 4.9 behandelt Feasibility **nativ**: `study.best_trial` rankt feasible ≻ infeasible **ohne** dass die Reward-Bänder gelöscht werden mussten (verifiziert). Die Failure-/Unevaluable-Bänder bleiben als *Ordnungs*-Sicherung erhalten, sind aber nicht mehr der Feasibility-Kanal.
**Invariante:** Eligibility fliesst als Sampler-**Constraint** (`oos_constraint_violations ≤ 0` = feasible), nie als der dominante Reward-Term. Der Reward misst *Güte*, nicht *Zulässigkeit*.
**Betroffen:** `automation/optimizer/run_optimization.py` (`_compute_oos_constraints`, `_oos_constraints_func`, beide Sampler), `automation/tests/test_issue_612_sampler_constraints.py`

### 🟢 Pitfall #136 — Deflation auf der **Bernoulli-Reward-Skala** über **allen** Trials: die Multiple-Testing-Korrektur misst das Falsche [BEHOBEN: GH-#611/#618]
**Symptom:** Die alte `deflated_reward_threshold` deflationierte die **Reward-Streuung** über **alle** Trials. Der Reward ist keine Sharpe/Sortino-Grösse; die Korrektur war dimensionslos falsch und bezog gepru­nte/ineligible Trials ein ⇒ die Latte war teils absurd hoch, teils bedeutungslos.
**Root-Cause:** Die Deflated Sharpe Ratio ist **definiert** auf per-Periode-Sharpe/Sortino über die **selektierte (eligible)** Kohorte, mit `SR₀ = √V[ŜR_trials]·E[max_N]` als Multiple-Testing-Latte. `E[max_N]` (Erwartungswert des Maximums von N Standardnormalen) fehlte ganz; die Kohorte war falsch.
**Fix/Regel:** Voller DSR in `deflation.py`: `expected_max_standard_normal(n)` (Euler-Mascheroni-Approx), `sr0_multiple_testing(var, n) = √var·E[max_N]` (`0.0` bei `var≤0`/`n≤1`), `deflated_sharpe_ratio(sr, T, *, var_sr_trials, n_trials, skew, kurtosis) = PSR(sr_star=SR₀)`. `confirm.py`: die Kohorte sind **ausschliesslich** die *eligiblen* `oos_sortino_period` (nicht die Reward-Skala, nicht alle Trials); `deflation_var = V[ŜR_eligible]`, `deflation_n = |eligible|`, `deflation_sr0 = sr0_multiple_testing(...)`, der DSR wird auf dem **promoteten** `oos_sortino_period` gezogen (`deflated_dsr`). `deflation_n ≥ 2` Pflicht (sonst keine Deflation). Bewusst gekoppelt an #612: erst wenn die Selektion Feasibility respektiert, ist die eligible Kohorte die richtige Grundgesamtheit.
**Invariante:** Multiple-Testing-Deflation läuft **immer** auf per-Periode-Sortino über die **eligible** Kohorte mit `SR₀ = √V·E[max_N]`, **nie** auf der Reward-/Bernoulli-Skala und **nie** über gepru­nte/ineligible Trials.
**Betroffen:** `automation/optimizer/deflation.py` (`expected_max_standard_normal`, `sr0_multiple_testing`, `deflated_sharpe_ratio`), `automation/optimizer/confirm.py` (eligible Kohorte, `deflated_dsr`/`deflated_sr0`/`deflation_n_eligible`), `automation/optimizer/run_optimization.py` (`_emit_study_summary`: `p_eligible`, `deflation_sr0`/`deflation_var`), `automation/tests/test_issue_618_dsr.py`, `automation/tests/test_issue_576_*.py`

### 🟢 Pitfall #137 — `fold_dispersion`-Strafe **numerisch inert**: Roh-Streuung ≈ 0.03 · Gewicht ≈ 0 [BEHOBEN: GH-#616]
**Symptom:** Der `fold_dispersion_weight`-Term war strukturell fast null. Ein Trial mit stark streuenden Fold-Returns (instabil) und einer mit gleichmässigen bekamen **praktisch dieselbe** Strafe ⇒ die Fold-Konsistenz (Pitfall #123) war im Reward de facto abgeschaltet.
**Root-Cause:** `base_disp = pstdev(per_fold_return)` liegt in **realisierter** Skala bei ≈ 0.03. Ohne Normierung multiplizierte das Gewicht eine O(0.01)-Zahl ⇒ der Term verschwand neben Base (O(1)) und den anderen Strafen. Ein **struktureller** blinder Fleck, kein Tuning-Fehler.
**Fix/Regel:** `reward.py` normiert die Dispersion auf ihre **realisierte** Skala: `_fds = weights.get("fold_dispersion_scale")`; `norm_disp = base_disp / fold_dispersion_scale` (nur wenn `scale > 0`, sonst Roh-Wert = Legacy); die Strafe nutzt `norm_disp` (+ `miss_scale·frac_missing`). `fold_dispersion_scale = 0.03` (optimizer.json, = realisierte Fold-Return-Streuung) hebt den Term auf O(1), sodass das Gewicht **tatsächlich bindet**.
**Invariante:** Ein additiver Strafterm wird auf seine **realisierte** Skala normiert, bevor ein Gewicht ihn multipliziert — ein Term, dessen Roh-Grössenordnung ihn strukturell unter die anderen Terme drückt, ist ein toter Term.
**Betroffen:** `automation/optimizer/reward.py` (`norm_disp`), `automation/config/optimizer.json` (`fold_dispersion_scale`), `automation/tests/test_issue_616_fold_dispersion_scale.py`

### 🟢 Pitfall #138 — Bootstrap-CI & CPCV/PBO sind **getestete, aber nie verdrahtete** Utilities (Overfit-Diagnose ohne Konsequenz) [BEHOBEN: GH-#619]
**Symptom:** #598/#599/#600 lieferten Stationary-Bootstrap-CI und CPCV/PBO als **getestete** Funktionen — die aber **nichts** blockierten. Die Selektions-Overfit-Diagnose existierte im Code, floss aber in **keine** Promotion-Entscheidung.
**Root-Cause:** Utility ≠ Verdrahtung. Ein getesteter Overfit-Detektor, den kein Gate aufruft, ist Dekoration.
**Fix/Regel:** `confirm.py`: (1) `_study_pbo(study, min_trials=4)` baut aus den eligiblen `oos_fold_sortinos` eine `(n_strategies, n_folds)`-Matrix, CSCV via `cpcv_paths(n_folds, n_folds//2)`, liefert `probability_of_backtest_overfitting`; **PBO > 0.5 ⇒ Hard-Stop `REJECTED_SELECTION_OVERFIT`** (T-unabhängiges Overfit-Signal). (2) `_holdout_bootstrap_ci_passes(metrics, confidence=0.95)` (Stationary-Bootstrap-CI, Politis/Romano) als **opt-in** Gate über `holdout_bootstrap_ci` (tournament.json); < 5 Returns ⇒ pass (nicht genug Daten). PBO/CI-Telemetrie im Proposal (`pbo`, `deflated_dsr`, `deflation_n_eligible`).
**Invariante:** Eine getestete Overfit-Diagnose (PBO, Bootstrap-CI) MUSS an einem Gate hängen, das eine Promotion tatsächlich blocken kann — `PBO > 0.5 ⇒ REJECTED_SELECTION_OVERFIT`. Ein Detektor ohne Konsequenz ist verboten.
**Betroffen:** `automation/optimizer/confirm.py` (`_study_pbo`, `_holdout_bootstrap_ci_passes`, `pbo_overfit`-Hard-Stop), `automation/config/tournament.json` (`holdout_bootstrap_ci`), `automation/tests/test_issue_619_bootstrap_pbo_wired.py`

### 🟢 Pitfall #139 — `oos_coherence_violation` wird im Subprozess berechnet, aber nie zur Study eskaliert (blinder Kohärenz-Alarm) [BEHOBEN: GH-#620]
**Symptom:** Die `sign(oos_sortino)≠sign(oos_total_return)`-Verletzung (Pitfall #122/#132) wurde je Backtest berechnet, aber im Subprozess-Ergebnis **begraben**. Eine systematische Inkohärenz (z. B. ein Aggregations-Regressionsbug) hätte über eine ganze Study laufen können, **ohne** ein einziges sichtbares Signal.
**Root-Cause:** Ein je-Trial berechnetes Flag ohne Study-Aggregation ist unbeobachtbar — Observability endet an der Subprozess-Grenze.
**Fix/Regel:** `run_optimization`: `trial.set_user_attr("oos_coherence_violation", bool(metrics.oos_coherence_violation))`; `_emit_study_summary` zählt `coherence_violations = Σ trials[oos_coherence_violation is True]`; **> 1 % ⇒ WARNING** (`[#620] …`); der Study-Summary-Event trägt `coherence_violations`; beide Trial-Events tragen `oos_coherence_violation`. `parsing.py` parst das Flag None-safe (`oos_coherence_violation=False` Default).
**Invariante:** Ein je-Trial-Integritäts-Flag wird auf Study-Ebene aggregiert und schwellwert-überwacht (`> 1 % ⇒ WARNING`). Ein im Subprozess berechnetes Flag, das nie eskaliert wird, ist verboten.
**Betroffen:** `automation/optimizer/run_optimization.py` (`_emit_study_summary`, `coherence_violations`), `automation/optimizer/parsing.py` (`oos_coherence_violation`), `automation/tests/test_issue_620_coherence_observability.py`

### 🟢 Pitfall #140 — `n_trials = 100` bei 14 Dimensionen ist Zufallssuche; Randlösungen sind eine **folgenlose** WARNING [BEHOBEN: GH-#622]
**Symptom:** Fixe `n_trials = 100` unabhängig von der Suchraum-Dimension. Bei `ComboTrendVwapStrategy` (14 Dim) ist das faktisch Zufall — der TPE kann 14 Dimensionen mit 100 Punkten nicht auflösen. Randlösungen (Optimum an der Suchraumgrenze = fast immer Overfit/Miss-Spezifikation) erzeugten nur eine **WARNING** ohne Konsequenz.
**Root-Cause:** Das Budget war nicht an die Dimensionalität gekoppelt; das Randlösungs-Signal war nicht an ein Gate gekoppelt.
**Fix/Regel:** (1) `derive_n_trials(strategy, base, opt_data)`: `k = n_trials_per_dim` (≥ 20) ⇒ `n_trials = max(base, ceil(k·dim))` (ComboTrendVwap 14 Dim ⇒ 280; Legacy ohne Key ⇒ `base`). Aufgerufen in `optimize` **und** `optimize_symbol`. (2) `_boundary_hit_fraction(study, strategy) > 0.3 ⇒ Hard-Stop **REJECTED_BOUNDARY_SOLUTION`** in `confirm` (statt WARNING) — Bounds prüfen ODER Reward-Konditionierung.
**Invariante:** Das Trial-Budget skaliert mit der Suchraum-Dimension (`≥ k·dim`, `k ≥ 20`). Eine Randlösungs-Fraktion `> 0.3` blockt `READY_FOR_PR` — ein Optimum an der Grenze ist nie stillschweigend promotionsfähig.
**Betroffen:** `automation/optimizer/run_optimization.py` (`derive_n_trials`, `_boundary_hit_fraction`), `automation/optimizer/confirm.py` (`boundary_overfit`), `automation/config/optimizer.json` (`n_trials_per_dim`), `automation/tests/test_issue_622_trial_budget.py`

### 🟢 Pitfall #141 — Tote Config-Keys & stiller `--tier refine`-No-Op: ein Robustheitsversprechen, das der Code nie einlöst [BEHOBEN: GH-#623]
**Symptom:** `fold_winsorize_lower/upper` (0 Referenzen im Code) und `--tier refine` (stiller No-Op statt Fehler) suggerierten Fähigkeiten, die nicht existierten. Ein Betreiber, der `refine` fährt, bekam **lautlos** `deployable`-Verhalten.
**Root-Cause:** Config-Keys ohne Call-Site und CLI-Optionen ohne Implementierung sind ein **falsches** Vertragsversprechen.
**Fix/Regel:** (1) `fold_winsorize_lower/upper` **verdrahtet**: `_winsorize(values, lower, upper)` (Perzentil-Clamp) + `_read_fold_winsorize()` (gecached) in `apply_fold_aggregation` — `oos_fold_sortinos`/`oos_fold_returns` werden winsorisiert. (2) `--tier refine ⇒ NotImplementedError` (**fail-loud**, kein stiller Fallback). (3) `holdout_top_k` deklariert (#615). (4) **CI-Invariante** `test_issue_623`: **jeder** Top-Level-Key aus `tournament.json`/`optimizer.json` hat `≥ 1` grep-Treffer im Produktionscode (tote Keys ⇒ roter Test).
**Invariante:** Kein Config-Key ohne Call-Site (CI-geprüft), keine CLI-Option ohne Implementierung (`NotImplementedError` statt No-Op). Ein deklarierter Key ist ein eingelöster Vertrag.
**Betroffen:** `automation/backtest_runner.py` (`_winsorize`, `_read_fold_winsorize`, `apply_fold_aggregation`), `automation/optimizer/sweep.py` (`--tier refine`), `automation/tests/test_issue_623_config_call_sites.py`

### 🟢 Pitfall #142 — Ein 45-Tage-Holdout kann die eigene 0.95-PSR-Schwelle **geometrisch** nicht tragen [BEHOBEN: GH-#624]
**Symptom:** Selbst der beste Grenzkandidat (per-Periode-Sortino ŜR ≈ 0.114) erreicht auf 45 d Holdout (T ≈ 202 MTM-Perioden) nur `PSR(0) = 0.9464 < 0.95`. Die 0.95-Promotionslinie ist mit der heutigen Katalog-Geometrie für Grenzfälle **strukturell unerreichbar**.
**Root-Cause:** `PSR(0) = Φ[ŜR·√(T−1)]` wächst mit `√T`. Bei ŜR ≈ 0.114 braucht `PSR(0) ≥ 0.95` ein `T ≥ 211` — das ist eine **Eigenschaft der Fensterlänge**, kein Software-Fehler. (Die Zahl ist erst seit #614/#611/#618/#619 überhaupt *korrekt* berechnet.)
**Fix/Regel:** Die Schwelle wird **bewusst NICHT gesenkt**. Der Sweep-Start loggt die Holdout-Geometrie (`[#624] Holdout-Geometrie: required_span_days=… verfügbare Katalog-Spanne=… d (deckt: JA/NEIN)`). Der akzeptierte Rest-Typ-I-Fehler ist durch DSR-Deflation (#136) und den PBO-Hard-Stop (#138) beschränkt. Bevorzugter Auflösungspfad: **Historie backfillen bis T ≥ 211** (gewinnt Signifikanz, statt die Latte zu senken). Die vollständige Entscheidung, die reproduzierbare Referenz-Mathematik und die drei Optionen stehen in **`manuals/strategie_optimierung.md §Holdout-Signifikanz`**.
**Invariante:** Eine Signifikanz-Schwelle wird nie gesenkt, um zu kurze Daten passieren zu lassen; fehlende Signifikanz wird geloggt und durch mehr Historie (T ≥ 211) *gewonnen*, nicht wegdefiniert. Eine bewusste Absenkung von `oos_min_psr` gehört in einen dokumentierten PR mit benanntem Typ-I-Fehler.
**Betroffen:** `automation/optimizer/sweep.py` (Geometrie-Log), `manuals/strategie_optimierung.md` (§Holdout-Signifikanz), `automation/tests/test_issue_624_625_holdout_significance.py`

### 🟢 Pitfall #143 — Per-Study-Deflation ignoriert, dass je Symbol **mehrere Strategien-Studies** konkurrieren (familienweise Fehlerrate unterschätzt) [BEHOBEN: GH-#625]
**Symptom:** Die DSR deflationiert je Study (N ≈ 100). Je Symbol laufen aber mehrere Strategien-Studies (z. B. 6 × 100 = 600 Kandidaten), aus denen **ein** Gewinner selektiert wird. Die tatsächliche familienweise Fehlerrate liegt über dem nominellen 5 % der Per-Study-Korrektur.
**Root-Cause:** Multiple Testing endet nicht an der Study-Grenze — die Symbol-Selektion ist selbst ein Multiple-Testing-Schritt über alle Studies des Symbols.
**Fix/Regel:** `_family_n_from_proposals(proposals)` summiert je Symbol die eligible-Trial-Zahl (`deflation_n_eligible` unter `holdout.symbol`) über die Studies ⇒ `deflation_n_family` (konservative familienweise Obergrenze; N_eff = eligible, da TPE-Vorschläge nicht i.i.d.). Telemetriert in `sweep_completed`. **Regressionsfalle:** `proposals` sind die von `export_symbol_proposal` geschriebenen **Path**-Objekte, keine Dicts — die JSON MUSS je Proposal gelesen werden (ein `isinstance(dict)`-Guard allein liefert **immer** eine leere Map). Die per-Study-DSR bleibt unverändert; die familienweise Zahl + PBO (#138) sind die orthogonale Absicherung.
**Invariante:** Die familienweise Multiple-Testing-Last wird über **alle** Studies eines Symbols aggregiert (`deflation_n_family = Σ N_eff`), nie nur je Study betrachtet.
**Betroffen:** `automation/optimizer/sweep.py` (`_family_n_from_proposals`, `deflation_n_family`), `automation/tests/test_issue_624_625_holdout_significance.py`

### 📋 Neue/geänderte Config-Keys (Bug-Kaskade #611–#625)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `oos_min_psr` | tournament.json | `0.75` | PSR-Eligibility-Gate (`min_psr`); ersetzt den unbeschränkten annualisierten Sortino als Signifikanz-Kriterium (#614) |
| `sortino_numeric_guard` | tournament.json | `1e6 → 25.0` | Numerik-Guard an die realistische per-Periode-Sortino-Skala gebunden (#614) |
| `min_psr` (in `eligible_requires_all`) | tournament.json | *neu* | PSR-Pflichtbedingung im UND-verknüpften Eligibility-Gate (#614) |
| `holdout_bootstrap_ci` | tournament.json | `true` | Opt-in Stationary-Bootstrap-CI (95 %) im Holdout-Gate (#619) |
| `fold_dispersion_scale` | optimizer.json | `0.03` | Normierung der Fold-Return-Dispersion auf ihre realisierte Skala; ohne sie war der Term inert (#616) |
| `n_trials_per_dim` | optimizer.json | `20` | Trial-Budget an die Suchraum-Dimension koppeln (`n_trials ≥ k·dim`) (#622) |
| `fold_winsorize_lower` / `fold_winsorize_upper` | optimizer.json | `null` / `null` | Fold-Winsorize-Perzentile jetzt in `apply_fold_aggregation` verdrahtet (Default null = aus) (#623) |

### 🔒 Watertight Invariants (Bug-Kaskade #611–#625) — für künftige Agenten
- **Reward-Base ist PSR (∈ [0, 1], annualisierungs-invariant):** Die Selektionsgrösse trägt eine Stichprobenlänge `T` und ist eine Signifikanz-Wahrscheinlichkeit. Ein unbeschränkter, annualisierter Sortino als Reward-Base ist **verboten** (Pitfall #134); er bleibt reine Telemetrie. `psr_base_active` ist an `oos_psr is not None` gebunden.
- **Feasibility ist ein Sampler-Constraint, kein Reward-Term:** `oos_constraint_violations ≤ 0` = feasible; Optuna 4.9 rankt feasible ≻ infeasible nativ. Ein Straf-Cliff im Reward als Feasibility-Kanal ist **verboten** (Pitfall #135).
- **Deflation: per-Periode-Sortino über die eligible Kohorte, `SR₀ = √V·E[max_N]`:** Nie auf der Reward-/Bernoulli-Skala, nie über gepru­nte/ineligible Trials, `deflation_n ≥ 2` (Pitfall #136). Der DSR wird auf dem **promoteten** Vektor gezogen.
- **Additive Strafterme werden auf ihre realisierte Skala normiert:** `norm_disp = base_disp / fold_dispersion_scale`. Ein Term, dessen Roh-Grössenordnung ihn strukturell unter Base + andere Strafen drückt, ist tot (Pitfall #137).
- **Jede Overfit-Diagnose hängt an einem blockierenden Gate:** `PBO > 0.5 ⇒ REJECTED_SELECTION_OVERFIT`; Bootstrap-CI opt-in über `holdout_bootstrap_ci`. Eine getestete, aber unverdrahtete Diagnose ist **verboten** (Pitfall #138).
- **Je-Trial-Integritäts-Flags werden auf Study-Ebene aggregiert & schwellwert-überwacht:** `coherence_violations > 1 % ⇒ WARNING`. Ein im Subprozess begrabenes Flag ist **verboten** (Pitfall #139).
- **Trial-Budget skaliert mit der Dimension; Randlösungen blocken die Promotion:** `n_trials ≥ k·dim` (`k ≥ 20`); `boundary_hit_fraction > 0.3 ⇒ REJECTED_BOUNDARY_SOLUTION` (Pitfall #140).
- **Kein Config-Key ohne Call-Site, keine CLI-Option ohne Implementierung:** CI-geprüft (`test_issue_623`); `--tier refine ⇒ NotImplementedError` (Pitfall #141).
- **Signifikanz-Schwellen werden nie gesenkt, um kurze Daten passieren zu lassen:** Fehlende Holdout-Signifikanz (`T < 211` bei ŜR ≈ 0.114 ⇒ `PSR(0) < 0.95`) wird geloggt und durch Backfill (`T ≥ 211`) *gewonnen*, nicht wegdefiniert (Pitfall #142, `manuals/strategie_optimierung.md §Holdout-Signifikanz`).
- **Multiple Testing wird familienweise über alle Studies eines Symbols aggregiert:** `deflation_n_family = Σ N_eff`; `proposals` sind Path-Objekte (JSON lesen, nie als Dict behandeln) (Pitfall #143).

---

## Bug-Kaskade #629–#639 — PSR-Migrations-Nachwirkungen & Gate-Kalibrierung (P0-Kaskade)

Die #611–#625-Kaskade (Pitfalls #134–#143) ersetzte die Reward-Base durch die PSR (#614) und die
Feasibility durch einen nativen Sampler-Constraint (#612). Ein 07-15-Audit auf dem so migrierten
System zeigte: die Migration war korrekt, aber **unvollständig nachgezogen**. Ein REDUNDANTER
zweiter Feasibility-Mechanismus (die alte Reward-Klippe) lief neben dem neuen Constraint her und
dominierte 99 % der Reward-Varianz (#629); die davon abhängige Eskalationsdiagnose maß folglich
dasselbe Artefakt statt echtes Signal (#640); ALLE additiven Strafterme und der Return-Tie-Breaker
waren gegen die alte, weit gestreute asinh-Sortino-Base kalibriert und überstimmten auf der neuen,
eng gestreuten `psr_z`-Base das Qualitätssignal (#631/#638); die Versionsnummer der Reward-Semantik
hinkte der Migration hinterher (#637); und drei strukturell unabhängige Gate-Defekte — ein
Benchmark, der über eine andere Zeitspanne aggregiert wird als die Strategie (#632), ein OR-Arm mit
strukturell unerreichbarer Schwelle (#633), und ein rausch-getriebener Fold-Zähler (#634) —
verzerrten die Eligibility-Grenze selbst. Die #612-Constraint-Aggregation erbte dabei denselben
Skalen-Inkohärenz-Fehler wie der alte Reward (#635), und die Multiple-Testing-Korrektur (DSR, #618)
war an eine Pass-Kette gekoppelt, die sie in der Praxis nie erreichte, UND ungeschützt gegen
Small-Cohort-Degeneration (#636). #639 ist kein eigenständiger Defekt, sondern der dokumentierte
Hinweis, dass die bereits gemergte #626–#628-Kaskade Voraussetzung für eine valide Top-k/Median-Rang-
Selektion ist. Behoben in der vom Betreiber vorgegebenen Reihenfolge (#629, #640, #631, #638, #637,
#632, #633, #634, #635, #636, #639); mehrere Kalibrierwerte (`penalty_scale_vs_base`, `w_ret`,
`fold_profit_epsilon`, `deflation_var_floor`) sind empirisch hergeleitete Startwerte, im Schema als
solche annotiert.

### 🟢 Pitfall #144 — Reward-Klippe redundant zum #612-Constraint: ein zweiter, widersprüchlicher Feasibility-Mechanismus dominiert die Reward-Varianz [BEHOBEN: GH-#629]
**Symptom:** 0 Trials landeten je in der Totzone (−12,0; 0,34); der Reward war strikt bimodal (Failure ≈ −13,8 ± 0,8; Eligible ≈ +0,75 ± 0,11). `reward_pstdev = 6,41` (Combo) bestand zu ≈ 99 % aus dem Klippen-Term. Innerhalb des Eligible-Bandes trug die Base (PSR) nur σ = 0,017 bei.
**Root-Cause:** Zwei sich widersprechende Feasibility-Mechanismen liefen parallel: (1) der #612-Constraint (`constraints_func`, Optuna rankt feasible ≻ infeasible nativ) UND (2) `reward.py` kodierte Feasibility ein ZWEITES Mal als Band-Ordnung (`unevaluable_ceiling < failure_ceiling < evaluable_reward_floor`, `_constraint_failure_reward`). Die 12,3-Einheiten-Klippe war redundant und verseuchte den TPE-Surrogat: der `l(x)/g(x)`-Split modellierte eine Kennzahl, deren Streuung zu 99 % die Gate-Passrate war, nicht die Qualität.
**Fix/Regel:** `_constraint_failure_reward` und die drei Band-Konstanten (`evaluable_reward_floor`, `evaluable_floor_epsilon`, `failure_reward_mode`, `failure_return_softplus_scale`, `failure_return_penalty_weight`) sind ENTFALLEN. `compute_reward` führt JEDEN evaluierten Trial (`m.oos_evaluated and base_source is not None`) — ob eligible oder evaluated-aber-ineligible — durch DENSELBEN Qualitäts-Kern (Base − Divergenz − Drawdown − Turnover − Fold-Dispersion + Tie-Breaker, KEIN Floor-Clamp). Ein evaluated-aber-ineligible Trial erhält zusätzlich die bereits existierende, kontinuierliche `gate_distance_penalty` (`_constraint_distance_penalty`, #452/#505/#534) additiv obendrauf — near-miss bleibt näher an einem gleich guten eligiblen Trial als ein katastrophaler Miss, aber ohne künstliche Unter-/Obergrenze. Die Feasibility-Rangordnung selbst kommt ausschliesslich vom #612-Sampler-Constraint.
**Invariante:** Feasibility ist NIEMALS ein Reward-Term (kein Band, keine Klippe) — sie fliesst ausschliesslich als `constraints_func`-Constraint. Der Reward ist EIN stetiges, nicht-gesättigtes Qualitätsziel über ALLE evaluierten Trials.
**Betroffen:** `automation/optimizer/reward.py` (`compute_reward`, `_constraint_distance_penalty`), `automation/config/optimizer.json` (fünf Keys entfernt), `automation/tests/test_issue_629_reward_cliff_removed.py` (neu), zwölf bestehende Reward-Tests aktualisiert (u. a. `test_issue_452/461/534/547/561/591/614`).

### 🟢 Pitfall #145 — `gradient_signal` misst die (jetzt entfernte) Klippe statt das Feasible-Region-Signal [BEHOBEN: GH-#640]
**Symptom:** `gradient_signal: true` für alle Studies, obwohl das *nutzbare* Signal im Feasible-Bereich flach war (Eligible-Reward-σ = 0,108). `reward_pstdev = 6,41` bestand zu ≈ 99 % aus dem Klippen-Term (Mischungsvarianz 43,0, davon μ-Gap-Term 42,5) — ein falsches Positiv.
**Root-Cause:** `gradient_signal` wurde aus dem GLOBALEN `reward_pstdev` (über ALLE Trials: Unevaluable + Failure + Eligible gemischt) abgeleitet. Bei der bimodalen Verteilung ist dieser pstdev näherungsweise die BERNOULLI-Streuung der Gate-Passrate `p_eligible·(1−p_eligible)`, nicht die Optimierbarkeit innerhalb der feasiblen Region — anti-monoton zur tatsächlichen Studien-Qualität, exakt derselbe Fehlerklasse wie Pitfall #136 (Deflation auf der Bernoulli-Skala) vor dessen Fix.
**Fix/Regel:** `_emit_study_summary` berechnet `feasible_rewards` (NUR `oos_eligible=True`-Trials) und `feasible_reward_pstdev`; `gradient_signal = study_shows_gradient_signal(feasible_rewards, p_eligible, tau)` — ersetzt den globalen `rewards`/`evaluable_fraction`-Input. `reward_pstdev`/`evaluable_fraction` bleiben als ROHE, globale Populations-Diagnose erhalten (Telemetrie), sind aber NICHT mehr die Eskalationsgrundlage. Neue Event-Keys `feasible_reward_pstdev`, `feasible_p_eligible`; WARNING `[#640]`, wenn kein Signal.
**Invariante:** Die Tier-Eskalations-Entscheidung (`gradient_signal`) misst IMMER die Reward-Varianz INNERHALB der feasiblen (eligiblen) Kohorte, NIE die globale, populations-gemischte Varianz einer bimodalen/mehrmodalen Verteilung.
**Betroffen:** `automation/optimizer/run_optimization.py` (`_emit_study_summary`, `study_shows_gradient_signal`).

### 🟢 Pitfall #146 — Die #614-PSR-Migration hat die Straf-Ko-Kalibrierung zerstört: additive Strafterme überstimmen die neue, eng gestreute Base [BEHOBEN: GH-#631]
**Symptom:** Innerhalb des Eligible-Bandes: `corr(reward, dd_penalty) = −0,929`, `corr(reward, turnover) = −0,756`, während `corr(reward, base) = 0,767` nur griff, weil die Base fast konstant war. `dd_penalty`-Median: eligible 0,087 vs. failure 1,633 (18,9×). Der Optimierer wählte effektiv den eligiblen Trial mit dem kleinsten Drawdown/Turnover, nicht den mit dem besten risiko-adjustierten Ertrag.
**Root-Cause:** Alle additiven Strafterme (`dd_penalty` über `dd_reward_scale=0,03` #597, `fold_dispersion` über `fold_dispersion_scale=0,03` #616, `turnover = trades·0,0003` #509, `param_pen = lambda_reg·dist` mit `lambda_reg=0,25`) wurden gegen die ALTE Base-Skala (asinh-Sortino, Magnitude ~1–15) kalibriert. Seit #614/#630 ist `base = psr_z` mit einer viel ENGEREN realisierten Eligible-Kohorten-Streuung (σ ≈ 0,05–0,11, aus der #614-Referenz σ(psr)=0,017 via Delta-Methode). Die Strafen sind absolut klein, aber ihre VARIANZ übersteigt die Base-Varianz um das ~4,5-Fache ⇒ sie dominieren das *Ranking*, obwohl sie nur Korrekturterme sein sollen — derselbe Skalen-Inkohärenz-Fehler wie beim Pre-#597-`dd_penalty` und Pre-#616-`fold_dispersion`, diesmal durch die *Base*-Änderung ausgelöst.
**Fix/Regel:** Neuer globaler Faktor `penalty_scale_vs_base` (`_penalty_scale_vs_base(weights)`, Default 1.0 = Legacy bei fehlendem Key), multiplikativ angewandt auf `dd_penalty`, `turnover_penalty`, `fold_dispersion_penalty`, `param_pen` (`lambda_reg`-Term). Kalibriert auf `0.2` gegen ein deklaratives, deterministisches Kalibrier-Fixture (`_CALIBRATION_FIXTURE_PSR_Z/_DD/_TRADES/_FOLD_RETURNS`). NEUE fail-loud Assertion `assert_penalty_scale_calibrated(weights)`: rechnet `compute_reward` über das Fixture, prüft `median(σ_penalty_terms) ≤ σ_base`; `ValueError PENALTY_SCALE_MISCALIBRATED` bei Verletzung. Defensiver No-Op, wenn `weights` die Kern-Keys nicht trägt (Test-/DI-Sonderpfade). Aufgerufen beim Config-Load in `optimize()` UND `optimize_symbol()`.
**Invariante:** Ein additiver Strafterm MODIFIZIERT die Base-Ordnung, er ÜBERSTIMMT sie nie: `σ(penalty_term) ≲ 0,25·σ(base)` auf der eligiblen Kohorte — fail-loud beim Config-Load geprüft (`assert_penalty_scale_calibrated`), nicht erst empirisch im Feld entdeckt.
**Betroffen:** `automation/optimizer/reward.py` (`_penalty_scale_vs_base`, `_dd_penalty`, `assert_penalty_scale_calibrated`, Kalibrier-Fixture), `automation/config/optimizer.json` (`penalty_scale_vs_base`), `automation/optimizer/run_optimization.py` (Aufruf in `optimize`/`optimize_symbol`), `automation/tests/test_issue_631_penalty_scale_calibration.py`.

### 🟢 Pitfall #147 — `w_ret = 2,0` überstimmt die (jetzt eng gestreute) Base: der Tie-Breaker ist ein Primärterm [BEHOBEN: GH-#638]
**Symptom:** `return_tie_breaker = w_ret·oos_total_return = 2,0·(~0,02) ≈ 0,04` — dieselbe Größenordnung wie die GESAMTE Eligible-Base-Varianz (σ = 0,017) und wie `dd_penalty` (Median 0,087). Der „Tie-Breaker“ war faktisch ein Primärterm, kein Tie-Breaker.
**Root-Cause:** `w_ret = 2,0` (#559) war gegen die alte, weich gesättigte asinh-Sortino-Base kalibriert — dort ein echter Tie-Breaker. Auf der seit #614/#630 viel enger gestreuten `psr_z`-Base übernahm derselbe Wert die Führung: `reward ≈ const − dd_penalty − turnover + 2·return` — Return-Chasing durch die Hintertür, genau das, was die PSR-Base (#614) ersetzen sollte.
**Fix/Regel:** `w_ret` 2,0 → 0,05 (Faktor 40 kleiner, im vom Issue vorgeschlagenen Korridor 20–50×), gegen die neue `psr_z`-Streuung kalibriert. Der Return bleibt nur der letzte Entscheider bei tatsächlich gleichem `psr_z`.
**Invariante:** `σ(w_ret·oos_total_return) ≪ σ(base)`. Ein Tie-Breaker-Term ändert das Ranking NUR bei nahezu identischer Base (`|Δbase| ≈ 0`), niemals als Primärtreiber — bei jeder künftigen Base-Skalen-Änderung ist `w_ret` (wie jeder andere Strafterm, Pitfall #146) neu zu kalibrieren.
**Betroffen:** `automation/config/optimizer.json` (`w_ret`), `automation/optimizer/reward.py` (Kommentar bei `return_tie_breaker`), `automation/tests/test_issue_638_tie_breaker_rescale.py`.

### 🟢 Pitfall #148 — `reward_semantics_version` blieb auf 8, obwohl vier Reward-Semantik-Brüche seit v8 akkumuliert waren [BEHOBEN: GH-#637]
**Symptom:** `optimizer.json: reward_semantics_version = 8`, obwohl #614 (Base asinh-Sortino → PSR) die Reward-BEDEUTUNG bereits fundamental geändert hatte — und mit #630/#629/#631/#638 (dieselbe Kohorte) drei weitere.
**Root-Cause:** `_check_reward_semantics_version` erkennt eine geladene Study nur dann als stale, wenn die gestempelte Version von der aktuellen Config-Version ABWEICHT. Blieb die Version unverändert 8, während die Reward-SKALA mehrfach fundamental wechselte (alter Sortino-Reward ~[−12; +15] vs. neuer PSR-Reward ~[−13; +1] vs. `psr_z`-Base), wurde eine unter der alten Semantik angelegte SQLite-Study NICHT als stale erkannt — der TPE würde mit Rewards auf inkompatibler Skala geprimt.
**Fix/Regel:** `reward_semantics_version` 8 → 9, GEBÜNDELT für VIER seit v8 akkumulierte, nie versionierte Semantikbrüche in EINEM Bump: #614 (Base asinh-Sortino→PSR), #630 (Ranking-Base PSR→`psr_z`), #629 (Reward-Klippe entfällt, Pitfall #144), #631/#638 (Strafterme + `w_ret` reskaliert, Pitfall #146/#147). Jede einzelne dieser vier Änderungen macht v8-Rewards mit v9-Rewards INKOMMENSURABEL — vollständiger Changelog im `_schema.fields`-Text von `optimizer.json` dokumentiert. `_check_reward_semantics_version` bleibt strukturell unverändert fail-loud (`REJECT_STALE_STUDY_SEMANTICS`, Study-Purge); als Nebenfund die Fehlermeldung um den literalen String `(.db)` ergänzt (Test-Erwartung).
**Invariante:** JEDE Änderung an `compute_reward`/`_constraint_distance_penalty`/`_dd_penalty` (`reward.py`) ODER an einem Reward-relevanten `*_weight`/`*_scale`-Key (`optimizer.json`) MUSS `reward_semantics_version` bumpen — auch wenn die einzelne Änderung klein erscheint oder Teil einer bereits gemergten Migration war. Ein Rückstand über mehrere PRs hinweg wird in EINEM Bump nachgeholt, nie stillschweigend übersprungen.
**Betroffen:** `automation/config/optimizer.json` (`reward_semantics_version`, Changelog), `automation/optimizer/run_optimization.py` (`_check_reward_semantics_version`, Message-Fix), `automation/tests/test_issue_637_reward_semantics_bump.py`, `automation/tests/test_issue_410_reward_versioning.py`.

### 🟢 Pitfall #149 — Benchmark-Excess-Return über eine ANDERE Zeitspanne aggregiert als der Strategie-Return [BEHOBEN: GH-#632]
**Symptom:** `oos_min_excess_return ≥ 0` war das bindende Gate: 285/285 Rejections zitierten es, 52 (18,2 %) scheiterten AUSSCHLIESSLICH daran, Median-Marge −0,567 %, 42/52 innerhalb 1 %. Die Strategien lagen konsistent knapp unter dem Benchmark.
**Root-Cause:** Zähler und Nenner des Excess-Returns wurden über UNTERSCHIEDLICHE Fensterungen aggregiert. Der Strategie-Return (`_calculate_stats`) wurde PER-FOLD kompoundiert über die 4 disjunkten OOS-Segmente (Lücken aus IS-Fenstern + Embargos ausgeschlossen). Der Benchmark (`oos_buyhold_return`, #552) wurde auf der KONKATENIERTEN Serie gebildet — `letzter_OOS_Bar / erster_OOS_Bar − 1` über die VOLLE Spanne Fold-0-Start → Fold-3-Ende, INKLUSIVE der ~180 IS-Tage + Embargos dazwischen. Der Benchmark wurde damit für ≈ die doppelte Zeit-im-Markt gutgeschrieben; in einem steigenden Markt trieb das `excess_return = strat − bench` systematisch < 0, unabhängig vom echten Fold-Alpha der Strategie.
**Fix/Regel:** Der Benchmark wird jetzt IDENTISCH zum Strategie-Return per Fold kompoundiert: `comp_b *= (1,0 + bseg_ret)` je Fold über `fold_boundaries`, mit derselben halb-offenen Slicing-Konvention (`_slice_half_open`, Pitfall #111) wie die Strategie-Segmente. Zähler und Nenner decken jetzt BIT-IDENTISCH dieselbe Bar-Menge ab.
**Invariante:** Eine Benchmark-relative Kennzahl (Excess-Return, Alpha) MUSS über EXAKT dieselbe Fold-Fensterung aggregiert werden wie die Kennzahl, gegen die sie verglichen wird — niemals über eine konkatenierte Vollspanne, die Lücken zwischen den OOS-Folds mit einschliesst.
**Betroffen:** `automation/backtest_runner.py` (`oos_buyhold_return`-Block, `_calculate_stats`), `automation/tests/test_issue_632_benchmark_per_fold_compounding.py`.

### 🟢 Pitfall #150 — `oos_min_win_rate ≥ 0,25` ist ein strukturell unerfüllbarer OR-Arm [BEHOBEN: GH-#633]
**Symptom:** Über 336 Trials war die MAXIMALE beobachtete OOS-Win-Rate 0,197 — die Schwelle 0,25 wurde NIE erreicht. `eligible_requires_any = ['min_profit_factor', 'min_win_rate']` kollabierte damit zu reinem `PF ≥ 1,1`; 174 Trials scheiterten am ANY-Gate.
**Root-Cause:** Trend-/Breakout-Strategien auf 1h-Bars mit asymmetrischem Payoff (z. B. PF bis 3,80 bei 13 % Win-Rate) haben klassenbedingt niedrige Trefferquoten. Eine 25-%-Schwelle ist für diese Strategie-Klasse kategorial unpassend — der OR-Arm war toter Code, der das Gate stumm verschärfte, statt (wie vorgesehen) eine echte Alternative zu bieten.
**Fix/Regel:** `oos_min_win_rate` 0,25 → 0,15, empirisch gegen die realisierte Verteilung kalibriert (p99 des dokumentierten Kalibrier-Fixtures = 0,197). ZUSÄTZLICH neue WARNING-only-Diagnose `check_any_arm_reachability(tournament_cfg)`: vergleicht jede `eligible_requires_any`-Schwelle gegen das p99 einer dokumentierten Referenzverteilung (`_CALIBRATION_FIXTURE_WIN_RATES`, `_ANY_ARM_CALIBRATION`); loggt `[#633]`, wenn eine Schwelle strukturell über dem p99 liegt (bewusst KEIN Hard-Fail — die wahre Erreichbarkeit ist strategie-/symbolabhängig). Aufgerufen beim Config-Load in `optimize()`/`optimize_symbol()`.
**Invariante:** Kein OR-Arm einer `eligible_requires_any`-Klausel darf eine Schwelle oberhalb der empirischen p99-Metrikverteilung tragen, OHNE dass `check_any_arm_reachability` dies laut meldet — ein unerreichbarer Arm darf eine ANY-Klausel nie lautlos auf die übrigen Arme kollabieren lassen.
**Betroffen:** `automation/config/tournament.json` (`oos_min_win_rate`), `automation/optimizer/reward.py` (`_CALIBRATION_FIXTURE_WIN_RATES`, `_ANY_ARM_CALIBRATION`, `check_any_arm_reachability`), `automation/optimizer/run_optimization.py` (Aufruf), `automation/tests/test_issue_633_any_arm_reachability.py`.

### 🟢 Pitfall #151 — `oos_min_profitable_folds` zählt auf degenerierten, hoch-varianten Folds ohne Rausch-Schwelle [BEHOBEN: GH-#634]
**Symptom:** 208/285 Rejections zitierten `oos_min_profitable_folds`. 87/336 Trials hatten < 4 Folds; der Per-Fold-OOS-Sortino wechselte in 37,3 % der Fold-Übergänge das Vorzeichen, within-Trial-σ bis 16,59.
**Root-Cause:** `n_folds_profitable = sum(1 for f in valid_folds if total_return > 0.0)` — ein striktes `> 0` auf Fold-Returns, die bei T ≈ 50/Fold hoch-variant sind. Ein Fold mit Return +1e-6 zählte als „profitabel“, einer mit −1e-6 nicht — der Zähler kippte an Rausch-Nullen. Die konfigurierte Fold-Winsorisierung (`fold_winsorize_lower/upper`, Pitfall #141) war im Zähler UNGENUTZT. Ergebnis: ein Rausch-Gate rejizierte einen Grossteil der Trials und bestrafte dieselbe Fold-Instabilität DOPPELT (die Fold-Dispersions-Strafe #589/#616 bildet sie bereits im Reward ab).
**Fix/Regel:** (1) ε-Schwelle: `r > fold_profit_epsilon` (neuer Key, `tournament.json`, `0,0005`) statt striktem `> 0,0`, mit `_read_fold_profit_epsilon()` (gecachter Reader, analog `_read_fold_winsorize`). (2) Zählung auf der WINSORISIERTEN Fold-Return-Sequenz (`winsorized_fold_returns`, identisch zu `oos_metrics["oos_fold_returns"]`) statt auf den rohen `per_fold_oos_list`-Werten — die konfigurierte Winsorisierung ist jetzt im Zähler selbst wirksam.
**Invariante:** Ein binärer Fold-Zähler (profitabel/nicht) prüft IMMER gegen dieselbe winsorisierte Sequenz UND eine ε-Rausch-Schwelle wie der Reward-Pfad — striktes `> 0,0` auf rohen, hoch-varianten Fold-Kennzahlen ist verboten.
**Betroffen:** `automation/backtest_runner.py` (`apply_fold_aggregation`, `_read_fold_profit_epsilon`, `_fold_profit_epsilon_cache`), `automation/config/tournament.json` (`fold_profit_epsilon`), `automation/tests/test_issue_634_profitable_folds_epsilon.py`.

### 🟢 Pitfall #152 — Der #612-Constraint summiert UN-normierte Gate-Deltas: kleinskalige Gates sind im Sampler-Sort unsichtbar [BEHOBEN: GH-#635]
**Symptom:** Der #612-Feasibility-Constraint sortierte infeasible Trials nach einer Summe heterogener Gate-Verletzungen; kleinskalige Gates (`excess_return ~[0; 0,04]`, `expectancy ~[0; 0,001]`) waren gegenüber grossskaligen (`PSR ~[0; 0,75]`, `drawdown ~[0; 0,3]`) im Sort um den Faktor ~19 unsichtbar.
**Root-Cause:** `_compute_oos_constraints` (vor #635) summierte ROHE, un-normierte `oos_gate_deltas` (`actual − threshold`) — inkonsistent zum Reward-Near-Miss-Pfad, dessen `_shortfall_distance` korrekt auf Target/Scale normiert (Pitfall #101/#108). Der Sampler steuerte infeasible Trials faktisch fast nur nach PSR-Nähe.
**Fix/Regel:** `_normalized_gate_distances(m, weights, risk_dd_cap, tournament_cfg)` als GETEILTE Scale-Auflösung extrahiert — Single Source of Truth für `_constraint_distance_penalty` (Reward-Pfad, nutzt nur die ursprünglichen sechs `_CORE_DISTANCE_KEYS`, bit-identisch) UND `_compute_oos_constraints` (Sampler-Constraint, nutzt ALLE Keys). Um `oos_min_psr` (Target-basiert via `_shortfall_distance`) und `oos_min_excess_return` (bespoke — `_shortfall_distance`'s Target>0-Guard passt nicht auf den legitimen Target `0,0`, daher direkte Normierung auf `return_penalty_scale`) erweitert. `_compute_oos_constraints` mittelt jetzt die normierten aktiven Distanzen statt rohe Deltas zu summieren. KRITISCHER Nebenfund: `oos_buyhold_return`/`oos_excess_return` wurden von `backtest_runner.py` berechnet, aber NIE nach `TournamentMetrics` geparst — die #635-Erweiterung wäre sonst strukturell inert geblieben, in `parsing.py` nachgezogen.
**Invariante:** Reward-Near-Miss-Shaping (`_constraint_distance_penalty`) und Sampler-Constraint (`_compute_oos_constraints`) operieren IMMER auf DENSELBEN normierten Skalen (`_normalized_gate_distances`, Single Source of Truth) — niemals rohe, un-normierte Deltas summieren, egal wie klein die Änderung erscheint.
**Betroffen:** `automation/optimizer/reward.py` (`_normalized_gate_distances`, `_CORE_DISTANCE_KEYS`, `_constraint_distance_penalty`), `automation/optimizer/run_optimization.py` (`_compute_oos_constraints`), `automation/optimizer/parsing.py` (`TournamentMetrics.oos_buyhold_return`/`oos_excess_return`), `automation/tests/test_issue_635_constraint_normalization.py`, `automation/tests/test_issue_612_sampler_constraints.py` (Rewrite).

### 🟢 Pitfall #153 — DSR-Berechnung an eine Pass-Kette gekoppelt, die sie nie erreichte + ungeschützt gegen Small-Cohort-Degeneration [BEHOBEN: GH-#636]
**Symptom:** `V[ŜR_trials]` aus einer 2-3-Punkte-Kohorte (VwapExhaustion N=3, Hourly N=2) ist statistisch bedeutungslos (beobachtet: `deflation_var_sr = 2,4e-9` für Hourly — eine Rundungsartefakt-Grössenordnung). Zusätzlich blieb `deflated_dsr` in ALLEN Proposals `None`: der DSR-Block lief nur `if holdout_passed and ...`, aber JEDE Strategie scheiterte bereits an einem FRÜHEREN Holdout-Gate (Excess-Return #629, negativer Holdout-Sortino), bevor die DSR exerziert wurde.
**Root-Cause:** Zwei orthogonale Defekte an derselben Stelle: (1) keine Mindestkohorte für eine belastbare `V[ŜR_trials]`-Schätzung, (2) die DSR-WERT-Berechnung war an den Pass-Kette-Ausgang gekoppelt statt eigenständig zu laufen — ein Diagnose-Wert, den ein vorgelagertes Gate strukturell verschluckte, bevor er je berechnet wurde.
**Fix/Regel:** (1) `sr0_multiple_testing_robust(var_sr_trials, n_trials, *, min_cohort=10, var_floor=0,0018)` (`deflation.py`): unterhalb `min_cohort` ersetzt ein dokumentierter, KONSERVATIVER Floor (`var_floor = 0,0018`, die im Code belegte reale Referenz-Varianz VwapExhaustion N=100 aus `deflated_sharpe_ratio`'s Docstring) die empirische Stichproben-Varianz — NIE unterschritten. Rückgabe `(sr0, used_fallback)`. (2) `confirm.py`: die DSR-WERT-Berechnung (`deflation_dsr`, `deflation_dsr_z` via `psr_z(..., sr_star=deflation_sr0)`) läuft jetzt UNABHÄNGIG von `holdout_passed`, sobald `deflation_n ≥ 2` und ein definierter promoteter per-Perioden-Sortino vorliegen. NUR der DROP-EFFEKT (`holdout_passed = False` bei `DSR < deflation_confidence`) bleibt an das bisherige `holdout_passed` gekoppelt (`[DSR-Drop #618]`, WARNING). `deflation_used_var_floor` neu in Telemetrie/Proposal (`[DSR #618/#636]`, WARNING bei Fallback).
**Invariante:** Eine Multiple-Testing-Diagnose (DSR) wird IMMER berechnet und telemetriert, sobald genug Kohorte + ein definierter promoteter Wert vorliegen — unabhängig vom Ausgang FRÜHERER Gates in derselben Pass-Kette; ein Diagnose-Wert darf nie durch einen vorgelagerten Gate-Fail unsichtbar bleiben. Unterhalb einer dokumentierten Mindestkohorte ersetzt ein konservativer Varianz-Floor die Stichproben-Varianz, NIE eine zufällig winzige Roh-Varianz.
**Betroffen:** `automation/optimizer/deflation.py` (`sr0_multiple_testing_robust`), `automation/optimizer/confirm.py` (DSR-Block entkoppelt), `automation/optimizer/run_optimization.py` (`_emit_study_summary`-DSR-Telemetrie), `automation/config/tournament.json` (`deflation_min_cohort`, `deflation_var_floor`), `automation/tests/test_issue_636_dsr_decoupling.py`.

### 🔵 Pitfall #154 — Median-Rang-Holdout-Promotion auf rausch-geranktem Top-k (Enabler-Hinweis, kein Code-Fix) [DOKUMENTIERT: GH-#639]
**Symptom:** `confirm.py` promotet den Median-nach-OOS-Return-Trial aus den Top-k=5 (per IS-Reward selektiert). War das IS-Ranking eine Straf-Lotterie (die vor dieser Kohorte behobene #611–#625-Kaskade, Pitfalls #134–#143), waren die 5 quasi-zufällig; der Median von 5 quasi-zufälligen Holdout-Läufen liegt ≈ Break-even und scheitert konstruktionsbedingt am Holdout-Gate.
**Root-Cause:** KEIN eigenständiger Defekt — ein DOWNSTREAM-Symptom der bereits gemergten #626–#628-Kaskade (Reward-Term-Dekomposition #621, Selektions-/Divergenz-/Gate-Kohärenz #613/#615/#617, statistische Signifikanz #611–#625). Der Median-Rang (#576/#594) ist die bewusste Robustheits-Wahl (filtert Holdout-Glück doppelt) und KORREKT, *sobald das IS-Ranking valide ist* — auf einem blinden IS-Ranking selektiert er aus Rauschen.
**Fix/Regel:** KEIN separater Code-Change. Verbindlichkeit: #626–#628 (bereits gemergt, VOR dieser Kohorte) sind Voraussetzung dafür, dass Top-k/Median-Rang echte Gewinner statt Rauschen selektieren. Nach JEDER substanziellen Reward-/Gate-Kalibrierung (wie dieser #629–#639-Kohorte) ist zu re-evaluieren, ob die Top-k-Kandidaten korreliert-gut statt quasi-zufällig sind — Diagnose-Metrik: `Spearman(IS-Reward-Rang, OOS-Holdout-Rang)` über die Top-k (Ziel > 0,5).
**Invariante:** Die Top-k/Median-Rang-Holdout-Selektion ist NUR so vertrauenswürdig wie das zugrundeliegende IS-Reward-Ranking. Nach JEDEM `reward_semantics_version`-Bump (Pitfall #148) ist eine Spearman-Re-Evaluation fällig, BEVOR der Median-Rang-Mechanismus wieder blind vertraut wird — kein Code-Merge unter einer neuen DSR-/Gate-Kalibrierung, ohne diese Korrelation neu zu prüfen.
**Betroffen:** `automation/optimizer/confirm.py` (Median-Rang-Selektion, nur Re-Evaluation — keine Code-Änderung in dieser Kohorte).

### 📋 Neue/geänderte Config-Keys (Bug-Kaskade #629–#639)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `penalty_scale_vs_base` | optimizer.json | *neu* `0,2` | globaler Skalierungsfaktor auf alle additiven Strafterme, kalibriert gegen die neue `psr_z`-Base-Streuung (#631) |
| `w_ret` | optimizer.json | `2,0 → 0,05` | Return-Tie-Breaker auf echte Tie-Breaker-Magnitude reskaliert (Faktor 40 kleiner) (#638) |
| `reward_semantics_version` | optimizer.json | `8 → 9` | bündelt vier seit v8 akkumulierte Semantikbrüche (#614/#630/#629/#631+#638) in einem Bump (#637) |
| `evaluable_reward_floor`, `evaluable_floor_epsilon`, `failure_reward_mode`, `failure_return_softplus_scale`, `failure_return_penalty_weight` | optimizer.json | *entfernt* | redundantes Reward-Band; Feasibility läuft ausschliesslich über den #612-Sampler-Constraint (#629) |
| `oos_min_win_rate` | tournament.json | `0,25 → 0,15` | strukturell unerreichbarer OR-Arm (beobachtetes Maximum 0,197 über 336 Trials) auf klassen-angemessenen Wert kalibriert (#633) |
| `fold_profit_epsilon` | tournament.json | *neu* `0,0005` | Rausch-Boden für den Profitable-Folds-Zähler (ε statt striktem `> 0,0`) (#634) |
| `deflation_min_cohort` | tournament.json | *neu* `10` | Mindest-Kohorte für eine belastbare `V[ŜR_trials]`-Schätzung; darunter greift der Varianz-Floor (#636) |
| `deflation_var_floor` | tournament.json | *neu* `0,0018` | konservativer Varianz-Floor unterhalb `deflation_min_cohort` (reale VwapExhaustion-N=100-Referenz) (#636) |

### 🔒 Watertight Invariants (Bug-Kaskade #629–#639) — für künftige Agenten
- **Feasibility ist ausschliesslich ein Sampler-Constraint, nie ein Reward-Band:** kein Reward-Wert liegt in einem eigenen Floor-/Ceiling-Band; jeder evaluierte Trial (eligible oder nicht) durchläuft denselben stetigen Qualitäts-Kern (Pitfall #144).
- **Die Tier-Eskalations-Entscheidung misst die Feasible-Region-Varianz, nie die globale Populations-Mischung:** `gradient_signal` basiert auf `feasible_reward_pstdev`/`feasible_p_eligible`, nicht auf dem bimodalen globalen `reward_pstdev` (Pitfall #145).
- **Additive Strafterme skalieren mit der REALISIERTEN Base-Streuung, nicht mit einer historischen Referenzskala:** `penalty_scale_vs_base` ist fail-loud kalibriert (`assert_penalty_scale_calibrated`); jede künftige Base-Skalen-Änderung MUSS gegen dasselbe Kalibrier-Fixture geprüft werden (Pitfall #146).
- **Ein Tie-Breaker bleibt ein Tie-Breaker:** `σ(w_ret·return) ≪ σ(base)` — ein Term, der bei einer Base-Skalen-Änderung zum Primärtreiber wird, ist ein Kalibrierungsfehler, kein Feature (Pitfall #147).
- **Jede Reward-Semantik-Änderung bumpt `reward_semantics_version` — auch kumulative Nachwirkungen einer bereits gemergten Migration:** ein Rückstand über mehrere PRs wird in einem gebündelten Bump nachgeholt, nie stillschweigend übersprungen (Pitfall #148).
- **Eine Benchmark-relative Kennzahl deckt exakt dieselbe Fensterung ab wie die Kennzahl, gegen die sie verglichen wird:** Zähler und Nenner eines Excess-Return-Gates dürfen nie über unterschiedlich lange Spannen aggregiert werden (Pitfall #149).
- **Ein OR-Arm-Gate darf keine strukturell unerreichbare Schwelle tragen:** `check_any_arm_reachability` warnt fail-loud gegen ein dokumentiertes Kalibrier-Fixture, bevor eine Klausel lautlos auf die übrigen Arme kollabiert (Pitfall #150).
- **Ein binärer Fold-Zähler nutzt dieselbe winsorisierte Sequenz und eine ε-Rausch-Schwelle wie der Reward-Pfad:** striktes `> 0,0` auf hoch-varianten, rohen Fold-Returns ist verboten (Pitfall #151).
- **Reward-Near-Miss-Shaping und Sampler-Constraint sehen dieselben normierten Skalen:** `_normalized_gate_distances` ist die einzige Quelle für Gate-Distanzen — nie rohe Deltas summieren (Pitfall #152).
- **Eine Multiple-Testing-Diagnose (DSR) wird immer berechnet, sobald die Kohorte reicht — unabhängig vom Ausgang früherer Gates:** der DSR-DROP-Effekt bleibt gated, der DSR-WERT nie (Pitfall #153).
- **Top-k/Median-Rang-Holdout-Selektion ist nur so vertrauenswürdig wie das zugrundeliegende IS-Ranking:** nach jedem `reward_semantics_version`-Bump ist eine Spearman-Re-Evaluation (IS-Rang ↔ Holdout-Rang) fällig, bevor der Mechanismus wieder blind vertraut wird (Pitfall #154).

## Bug-Kaskade #649–#660 — Selektions-Integrität & Deflations-Kohärenz (P0-Kaskade)

Ein 07-16-Audit auf dem #629–#639-migrierten System (Pitfalls #144–#154) zeigte: die Statistik-
Infrastruktur (PSR, DSR, PBO, Bootstrap-CI, per-Fold-Benchmark) ist vorhanden und grösstenteils
korrekt gerechnet — aber die GATES, die entscheiden, welcher Trial überhaupt gewinnen darf, benutzen
sie nicht. Vier von acht harten `eligible_requires_all`-Klauseln — darunter das komplette #614-PSR-
Gate und das #552/#598-Alpha-Gate — waren durch einen Config/Code-Key-Mismatch still tot (#649), wodurch
die Selektion faktisch auf einem groben, absoluten `+0,5%`-Return-Gate lief (#650), das mit dem
kollinearen `min_expectancy` doppelt kodiert war (#657). Sekundär war die Deflation bei Small-Cohorts
inkohärent (die Entscheidung nutzte ein anderes SR₀ als die Telemetrie, #651) mit einem
diskontinuierlichen Varianz-Floor (#653) und per-Study statt familienweitem N (#652); die
Forensik-Attribution der Promotion-Ablehnung zeigte den falschen (modalen IS-, statt den tatsächlichen
Holdout-)Grund (#654). Hygiene-Funde: ein Reward-Sentinel (−20) kontaminierte Cross-Strategy-
Vergleiche (#655), zwei Strategien liefen strukturell leer ohne früh erkannt zu werden (#656), und die
Reward-Semantik-Version hinkte der Kohorte-A-Gate-Änderung hinterher (#658). Re-Kalibrierung (nach A+B,
strukturell statt geraten): gestapelte Multiple-Testing-Korrekturen kompoundieren den Type-II-Fehler
(#659), und der `min_win_rate`-OR-Arm bleibt für einzelne Symbole strukturell unerreichbar, obwohl das
globale Kalibrier-Fixture ihn als erreichbar einstuft (#660). Behoben in der vorgegebenen Reihenfolge:
Kohorte A (#649 → #650 → #657, MUSS zuerst), Kohorte B (#651 → #653 → #652 → #654, parallel zu A),
Hygiene (#655, #656), Version+Purge (#658, letzte strukturelle Aktion vor einem Re-Run), Re-Kalibrierung
(#659, #660, strukturell — die konkreten Schwellen erfordern einen echten Kalibrierlauf).

### 🟢 Pitfall #155 — Vier `eligible_requires_all`-Gates still inaktiv (Config-Key ≠ `condition_map`-Key) [BEHOBEN: GH-#649]
**Symptom:** Über 76 geloggte Trials feuerte keine der vier neueren Klauseln (`oos_min_profitable_folds_frac`, `oos_min_evaluable_folds`, `oos_min_psr`, `oos_min_excess_return`) je als Rejection-Grund. Die #614-PSR-Migration war am Selektionspunkt wirkungslos — ein Trial mit PSR=0,10 (klar negativer Edge) passierte `oos_eligible=True`.
**Root-Cause:** `tournament.json` listet die neueren Gates MIT `oos_`-Präfix (`oos_min_psr`), `_evaluate_oos_eligibility`s `condition_map` ist durchgehend UN-präfigiert (`min_psr`). Die Auswertung (`for cond_name in eligible_requires_all: if cond_name in condition_map`) fand die präfigierten Namen nie ⇒ STILLE Übersprungen, kein Fehler, keine Warnung. Ein ZWEITER, von der reinen Präfix-Normalisierung unabhängiger Mismatch betraf `oos_min_profitable_folds_frac`, dessen kanonische Form (`min_profitable_folds_frac`) nicht dem Handler-Namen (`min_profitable_folds`, ohne `_frac`) entsprach. Die bestehende Startup-Validierung (`load_tournament_config`) prüfte nur config-INTERNE Konsistenz (Metrik definiert ↔ referenziert), nie gegen die tatsächliche `condition_map`-Handler-Menge — "clean", keine Warnung. `test_issue_614_psr_reward_base.py` blieb grün, weil es ein EIGENES Fixture mit dem un-präfigierten Namen konstruierte (Fixture-vs-Produktion-Drift, Pitfall #156b/#130).
**Fix/Regel:** `_canonical_gate_key(key)` (entfernt ein optionales `oos_`-Präfix) als EINE kanonische Normalisierung, angewandt VOR jedem `condition_map`-Lookup in `_evaluate_oos_eligibility` (beide Klausel-Loops: `eligible_requires_all` UND `eligible_requires_any`). Der `condition_map`-Handler für die Fold-Konsistenz-Klausel wurde von `min_profitable_folds` auf `min_profitable_folds_frac` umbenannt (matcht jetzt die kanonische Form der Config-Klausel). `load_tournament_config` validiert zusätzlich JEDEN `eligible_requires_all`/`_any`-Eintrag (kanonisch normalisiert) gegen die neue Registry-Konstante `OOS_CONDITION_MAP_KEYS` und bricht bei einem unbekannten Eintrag **fail-loud** (`ValueError`) ab — ausserhalb des Lade-`try/except` platziert, damit die Validierungs-Exception nicht vom breiten Ladefehler-Handler verschluckt wird.
**Invariante:** Jeder `eligible_requires_all`/`_any`-Eintrag MUSS (nach kanonischer Normalisierung) auf einen echten `condition_map`-Handler resolven — geprüft gegen die tatsächliche Registry, nicht nur config-intern. Ein Test, der eine EIGENE Fixture-Config konstruiert, testet NIE die ausgelieferte `tournament.json` (Pitfall #156b).
**Betroffen:** `automation/backtest_runner.py` (`_canonical_gate_key`, `OOS_CONDITION_MAP_KEYS`, `_evaluate_oos_eligibility`, `load_tournament_config`), `automation/config/tournament.json`, `automation/tests/test_issue_593_gate_reward_clause_parity.py` (Fixture-Drift korrigiert), `automation/tests/test_issue_550_fold_consistency_gate.py` (Handler-Umbenennung nachgezogen).

### 🟢 Pitfall #156 — `min_total_return` als quasi-alleiniges, nicht-risikoadjustiertes Selektions-Gate [BEHOBEN: GH-#650]
**Symptom:** Mit den vier toten Gates aus Pitfall #155 reduzierte sich das harte Selektions-Gate faktisch auf `min_trades ∧ min_total_return(0,005) ∧ max_drawdown ∧ min_expectancy` — `max_drawdown` band nie, `min_win_rate` war unerreichbar (Pitfall #166) ⇒ `min_total_return` entschied ALLEIN. Ein Trial mit Sortino 1,75, PF 1,22, positiver Expectancy wurde bei Δ=−0,088 % verworfen; gleichzeitig unterbot derselbe Trial (korrigierter Benchmark) Buy&Hold um 0,375 % — das Alpha-Gate hätte ihn korrekt verworfen, tat es aber nicht (tot, Pitfall #155).
**Root-Cause:** Ein absoluter Return-Floor über ein variabel langes, gepooltes OOS-Fenster ist bei hochfrequenten Strategien (100–150 Trades, ~0-Expectancy nach Kosten) eine harte, aber statistisch bedeutungslose Wand — sie censoriert die Oberkante der Reward-Verteilung ohne Risiko-/Alpha-Bezug. Die drei ökonomisch sinnvollen risikoadjustierten Kriterien (PSR, Excess-Return, Sortino) waren entweder tot (Pitfall #155) oder bewusst nur Telemetrie.
**Fix/Regel:** `min_total_return` aus `eligible_requires_all` ENTFERNT (nach Pitfall #155 tragen `oos_min_psr`/`oos_min_excess_return` die Profitabilitätsentscheidung — fenster- und annualisierungsinvariant). `min_total_return`/`oos_min_total_return` von `0,005` auf `0,0` (Breakeven) gesenkt und bleiben als dokumentierte, rein WEICHE Sanity-Untergrenze (Telemetrie/Near-Miss-Distanz in `reward._normalized_gate_distances`) erhalten — NIE mehr ein bindender Diskriminator.
**Invariante:** Ein absolutes, nicht-risikoadjustiertes Return-Gate über ein kurzes/variables OOS-Fenster darf niemals die risikoadjustierten Gates (PSR, Alpha) dominieren oder ersetzen — es censoriert sonst die Verteilungs-Oberkante und verwirft hoch-Sortino/hoch-PSR-Trials an marginalen Return-Deltas.
**Betroffen:** `automation/config/tournament.json` (`eligible_requires_all`, `min_total_return`, `oos_min_total_return`, `_schema`-Doku).

### 🟢 Pitfall #157 — Kollinearität `min_total_return` ↔ `min_expectancy` [BEHOBEN: GH-#657]
**Symptom:** Zwei harte Gates massen dieselbe Grösse: `total_return ≈ Σ(expectancy_i)` bzw. `expectancy ≈ total_return / n_trades`. Bei `min_trades=20` und `min_total_return=0,005` war ein unabhängiges `min_expectancy=0,001` eine DOPPELTE Kodierung derselben Bedingung.
**Root-Cause:** Zwei absolute Mittelwert-Gates auf derselben Achse (Netto-Return pro Kapital, aggregiert vs. per-Trade) — sie verschärfen sich gegenseitig, ohne Zusatzinformation gegenüber einem risikoadjustierten PSR-Gate zu tragen.
**Fix/Regel:** Nach Pitfall #156 (Entfernung von `min_total_return` aus `eligible_requires_all`) existiert bereits GENAU EIN absolutes Profitabilitäts-Gate: `min_expectancy`, das via `oos_min_expectancy_k_alpha` (#562) zur Laufzeit auf das kostenrelative Kriterium `k_alpha · c_rt` ("schlage die Kosten um k_alpha") umgestellt wird — Breakeven-nach-Kosten, kein Ersatz für die risikoadjustierten Gates.
**Invariante:** Es existiert zu jedem Zeitpunkt HÖCHSTENS ein absolutes (nicht-risikoadjustiertes) Profitabilitäts-Gate in `eligible_requires_all` — niemals zwei kollineare Return-Mittelwert-Gates gleichzeitig als harte Klauseln.
**Betroffen:** `automation/config/tournament.json` (`_schema`-Doku für `min_total_return`, konsolidierter Verweis auf `min_expectancy`/`oos_min_expectancy_k_alpha`).

### 🟢 Pitfall #158 — DSR-Entscheidung nutzt ein anderes SR₀ als die Telemetrie [BEHOBEN: GH-#651]
**Symptom:** Bei Small-Cohorts (Hourly N=9) nutzte die PROMOTION-entscheidende `deflated_dsr` ein ANDERES SR₀ als die telemetrierte `deflation_dsr_z`/`deflated_sr0`. Numerischer Beleg: `deflated_sr0` (Telemetrie, gefloort) = 0,06452; das intern in `deflated_dsr` verwendete SR₀ (ungefloort) = 0,01852 — Inflationsfaktor 3,48×, die Entscheidung nutzte das 3,48× KLEINERE SR₀ ⇒ eine zu hohe, zu lasche DSR.
**Root-Cause:** `deflated_sharpe_ratio(sr, n_periods, var_sr_trials=, n_trials=)` berechnete SR₀ INTERN via `sr0_multiple_testing` (ungefloort, ohne den #636-Small-Cohort-Floor), während `confirm.py` für Telemetrie SEPARAT `sr0_multiple_testing_robust` (gefloort) aufrief und dessen Ergebnis nur `psr_z`/`deflated_sr0` fütterte — zwei Berechnungspfade für dieselbe Grösse.
**Fix/Regel:** `deflated_sharpe_ratio`s Signatur geändert: nimmt `sr0` jetzt als PARAMETER (vom Aufrufer bereits berechnet), rekonstruiert es nie mehr intern aus `var_sr_trials`/`n_trials`. `confirm.py` übergibt an beide Konsumenten (`deflation_dsr` via `deflated_sharpe_ratio(..., sr0=deflation_sr0)` UND `deflation_dsr_z` via `psr_z(..., sr_star=deflation_sr0)`) dasselbe, EINMAL berechnete `deflation_sr0` — Entscheidung und Telemetrie sind damit per Konstruktion bit-identisch.
**Invariante:** Eine robuste Statistik-Grösse (SR₀ oder jede andere gefloorte/geshrinkte Grösse) stammt aus EINER Quelle für Entscheidung UND Telemetrie — nie einmal roh (Entscheidung) und einmal gefloort (Telemetrie) berechnet.
**Betroffen:** `automation/optimizer/deflation.py` (`deflated_sharpe_ratio`-Signatur), `automation/optimizer/confirm.py` (DSR-Block), `automation/tests/test_issue_618_dsr.py` (Signatur-Migration bestehender Tests), `automation/tests/test_issue_651_dsr_sr0_consistency.py`.

### 🟢 Pitfall #159 — Varianz-Floor als harte Konstante + `N_min`-Diskontinuität [BEHOBEN: GH-#653]
**Symptom:** Der Floor war eine einzige hartcodierte Konstante (`0,0018`, empirischer VwapExhaustion-N=100-Anker) mit einem harten Cutover bei `deflation_min_cohort=10`: N=9 nutzte `max(observed, 0,0018)`, N=10 sprang auf die rohe (oft winzige) Mini-Stichproben-Varianz — ein Faktor-~3,5-Sprung zwischen zwei fast identischen Kohorten.
**Root-Cause:** Die Kohorten-Varianz aus 3–9 Gate-Überlebenden ist selbst ein Selektions-Artefakt (das Gate censoriert die Verteilung und behält nur nahezu identische Passierer) — eine Konstante + Hard-Cutoff ist eine grobe Reparatur eines Problems, das eine kontinuierliche, theoriegestützte Lösung verlangt.
**Fix/Regel:** `lo2002_sharpe_variance(sr, n_periods)` — die theoretische Stichprobenvarianz nach Lo (2002), `Var[ŜR] ≈ (1 + ŜR²/2)/T` (`ŜR*=0` als konservative Nullhypothesen-Referenz) — T-BEWUSST: kürzeres OOS-Fenster ⇒ höhere Schätz-Unsicherheit ⇒ konservativerer Floor. `_cohort_shrinkage_weight(N, min_cohort) = min_cohort/(min_cohort+N)` — ein STETIGES Shrinkage-Gewicht (kein Cutover) Richtung dieser theoretischen Referenz; `sr0_multiple_testing_robust` blendet `effective_var = λ(N)·theoretical_var + (1−λ(N))·observed`. Der alte Anker `0,0018` bleibt Fallback-Referenz, wenn `n_periods` fehlt (Legacy-Aufrufer, bit-identisch). Issue #652-Synergie: `n_trials` (treibt `E[max_N]`, kann familienweit sein) und `variance_n_trials` (treibt NUR das Shrinkage-Gewicht, IMMER die tatsächliche Kohortengrösse) sind ABSICHTLICH entkoppelte Parameter — würden beide dieselbe (grössere, familienweite) Zahl teilen, verschöbe eine grosse N_family das Shrinkage-Gewicht fälschlich Richtung "viele Datenpunkte" und liesse eine winzige, unzuverlässige empirische Varianz dominieren (SR₀ könnte mit wachsendem N_family sogar SINKEN — das Gegenteil der beabsichtigten strengeren Hürde).
**Invariante:** SR₀(N) ist über JEDEN N-Übergang stetig — kein harter Cutover an einer Konstante. Ein theoretisch begründeter Floor skaliert mit T; die Verlässlichkeit einer Varianz-SCHÄTZUNG (Shrinkage-Gewicht) hängt IMMER von der tatsächlichen Stichprobengrösse ab, nie von einer grösseren, unabhängig motivierten Multiplizität.
**Betroffen:** `automation/optimizer/deflation.py` (`lo2002_sharpe_variance`, `_cohort_shrinkage_weight`, `sr0_multiple_testing_robust`), `automation/optimizer/confirm.py`, `automation/optimizer/run_optimization.py` (`_emit_study_summary`, `oos_n_periods`-Stempel), `automation/config/tournament.json` (`deflation_min_cohort`/`deflation_var_floor`-Doku), `automation/tests/test_issue_653_variance_floor_continuity.py`, `automation/tests/test_issue_636_dsr_decoupling.py` (Erwartungswert nachgezogen).

### 🟢 Pitfall #160 — Multiple-Testing-N ist per-Study, die Selektion aber cross-Study [BEHOBEN: GH-#652]
**Symptom:** Die Promotions-DSR nutzte ausschliesslich `deflation_n_eligible` DIESER Study (9–162). Der Lauf wählt aber den besten von mehreren Strategien-Studies je Symbol (`deflation_n_family` bis 496 eligible) — die familienweite Multiplizität floss in KEINE Promotion-Entscheidung ein, obwohl SR₀=√V·E[max_N] monoton in N wächst (E[max₃₇]=2,16 vs. E[max₄₉₆]=3,05, Faktor 1,41).
**Root-Cause:** `deflation_n_family` existierte bereits seit #625 als reine SWEEP-Telemetrie (`sweep._family_n_from_proposals`), berechnet ERST NACHDEM alle Promotions eines Symbols bereits gelaufen waren (Proposals existieren erst nach `confirm_per_symbol_promotion`) — ein Henne-Ei-Problem, das die Zahl strukturell zu spät verfügbar machte für die Entscheidung selbst.
**Fix/Regel:** `sweep.run_per_symbol_sweep` läuft jetzt in ZWEI Phasen: Phase 1 (`optimize_symbol` für alle Paare, weiterhin über `n_jobs` parallelisiert) sammelt die Studies; `sweep._family_n_from_studies(pairs, studies)` (neu) berechnet die familienweite N JE SYMBOL direkt aus den Study-Objekten (Σ `oos_eligible`-Trials über alle Strategien desselben Symbols) — VOR jeder Promotion. Phase 2 (Confirm + Export) erhält `deflation_n_family` je Symbol und reicht es an `confirm_per_symbol_promotion(..., deflation_n_family=...)` durch; dort gilt `deflation_n_effective = max(deflation_n, deflation_n_family)` (nie kleiner als das lokal Bekannte) für die SR₀-Berechnung. Die bestehende post-hoc `_family_n_from_proposals` (Sweep-Summary-Telemetrie) bleibt UNVERÄNDERT als separate, nachgelagerte Diagnose erhalten.
**Invariante:** Wird der Beste aus K Strategien × M Trials gewählt, korrigiert die Deflation gegen ~K·M (das familienweite N), nicht gegen die per-Study-Kohorte — Letzteres unterkorrigiert die Cross-Study-Auswahl strukturell.
**Betroffen:** `automation/optimizer/sweep.py` (`_family_n_from_studies`, zweiphasiger Dispatch in `run_per_symbol_sweep`), `automation/optimizer/confirm.py` (`deflation_n_family`-Parameter, `deflation_n_effective`), `automation/optimizer/deflation.py` (`variance_n_trials`-Entkopplung, siehe Pitfall #159), `automation/tests/test_issue_652_family_wide_multiplicity.py`.

### 🟢 Pitfall #161 — `is_rejection_detail` im Proposal ist der modale IS-Grund, nicht die Promotion-Ursache [BEHOBEN: GH-#654]
**Symptom:** DynamicBreakout zeigte `is_rejection_detail: REJECT_OOS_MIN_TOTAL_RETURN`, obwohl das Symbol-Holdout-Gate BESTANDEN war (eligible, Sortino +3,45) und die Promotion AUSSCHLIESSLICH vom DSR-Drop (0,735 < 0,95) getötet wurde — die Forensik wies die falsche Ursache aus.
**Root-Cause:** Im Promotion-Pfad war `is_rejection_detail_override` UNCONDITIONAL `None`; `export_symbol_proposal` fiel daher auf `_dominant_is_rejection_detail(study)` zurück — den modalen IS-STUDY-Trial-Grund (70/76 = `REJECT_OOS_MIN_TOTAL_RETURN`), der mit der Holdout-/Promotion-Entscheidung nichts zu tun hat.
**Fix/Regel:** Ein neuer `holdout_reject_detail`-Tracker verfolgt die TATSÄCHLICHE blockierende Ursache: Default `REJECT_HOLDOUT_GATE` (Symbol-Holdout-Gate selbst gescheitert), überschrieben zu `REJECT_HOLDOUT_DSR_DROP` bzw. `REJECT_HOLDOUT_BOOTSTRAP_CI`, sobald der jeweilige Block `holdout_passed` flippt (da jeder Block nur greift, wenn `holdout_passed` noch `True` ist, gewinnt genau EIN Grund — keine Ambiguität). Die finale Status-/Override-Zuordnung deckt JEDEN Ausgang ab: `REJECT_SELECTION_PBO`, `REJECT_BOUNDARY_SOLUTION`, `holdout_reject_detail`, `None` (READY_FOR_PR) oder `REJECT_NO_EDGE_OVER_GLOBAL`. `export_symbol_proposal` trennt jetzt ZWEI Felder: `is_rejection_detail` (NUR noch der Override, kein OR-Fallback mehr) und das NEUE `dominant_is_rejection_detail` (der modale IS-Grund, weiterhin für die Study-Diagnose).
**Invariante:** Die Promotion-Forensik (`is_rejection_detail`) und die IS-Study-Diagnose (`dominant_is_rejection_detail`) sind ZWEI GETRENNTE Felder — ein Proposal darf NIE den modalen IS-Grund als Promotion-Ursache ausgeben, nur weil kein spezifischer Override gesetzt wurde.
**Betroffen:** `automation/optimizer/confirm.py` (`confirm_per_symbol_promotion`, `export_symbol_proposal`), `automation/tests/test_issue_451_gate_diagnosis.py` (Vertrag korrigiert), `automation/tests/test_issue_654_rejection_detail_attribution.py`.

### 🟢 Pitfall #162 — `R_global = −20,0`-Sentinel verunreinigt Cross-Strategy-Vergleichbarkeit [BEHOBEN: GH-#655]
**Symptom:** AdxAtr/TrendPullback (0 eligible Trials, Pitfall #163) exportierten `R_global = −20,0` — ununterscheidbar von einem echten, sehr schlechten Reward und damit ein giftiger Sentinel in jeder Cross-Strategy-`R_global`-Aggregation.
**Root-Cause:** `compute_reward(..., holdout=True)` fiel bei einem nie-OOS-evaluierten Holdout-Backtest (der globale Vektor produziert auf DIESEM Symbol keine Trades) auf denselben numerischen Unevaluable-Shaping-Pfad zurück wie der IS/Study-Reward (`penalty_unevaluable_oos + shaping ≈ −20,0`) — eine Formel, die existiert, um dem TPE-Sampler WÄHREND der Study einen Gradienten Richtung Eligibility zu geben; ein Holdout-Backtest ist aber ein einmaliger, abgeschlossener Lauf, kein Optimierungsschritt, für den diese Shaping-Logik kategorial fehl am Platz ist.
**Fix/Regel:** `compute_reward(..., holdout=True)` liefert `None` (statt der Shaping-Zahl), sobald `not m.oos_evaluated`. `confirm.py` behandelt `R_symbol`/`R_global` seither explizit `None`-sicher: ein undefiniertes `R_global` (keine Baseline zu schlagen) gilt für die Promotion-Entscheidung als TRIVIAL erfüllt (dokumentiert, ersetzt den impliziten Alt-Effekt OHNE die kontaminierte Zahl zu exportieren); ein undefiniertes `R_symbol` kann NIE einen Edge belegen.
**Invariante:** Ein degeneriertes/nicht-evaluiertes Ergebnis wird IMMER mit `None`/NaN markiert, nie mit einer Zahl, die von einem echten, sehr schlechten Wert ununterscheidbar ist — jeder Aggregations-/Baseline-/Ranking-Pfad muss `None` explizit ausschliessen.
**Betroffen:** `automation/optimizer/reward.py` (`compute_reward`, Holdout-Zweig), `automation/optimizer/confirm.py` (`None`-sichere `R_symbol`/`R_global`-Vergleiche), `automation/tests/test_issue_655_no_reward_sentinel.py`.

### 🟢 Pitfall #163 — AdxAtr/TrendPullback: 0 eligible Trials ohne frühe Erkennung [BEHOBEN: GH-#656]
**Symptom:** Beide Strategien erzeugten über die GESAMTE Study null eligible Trials — obwohl die Hürde durch die vier toten Gates (Pitfall #155) niedriger als beabsichtigt war; nach deren Fix wird die Hürde höher, diese Strategien liefen dann garantiert leer.
**Root-Cause:** Der bestehende #409/#413-Floor-Guard (`floor_plateau_callback`) erkennt NUR das Muster "kein Trial je evaluable" (`oos_evaluated` immer `False`, Pitfall #75-Klasse — Daten-/Coverage-Problem). Ein STRUKTURELL ANDERES Kollaps-Muster — ALLE Trials wurden evaluiert (echte OOS-Backtests liefen durch), aber KEINER war je `oos_eligible` (Suchraum-Problem, nicht Daten-Problem) — blieb gänzlich unerkannt; 100+ Trials liefen nutzlos durch, bevor ein Mensch das im Proposal (0 eligible) bemerkte.
**Fix/Regel:** `floor_plateau_callback` erkennt zusätzlich das "Zero-Eligible-Plateau" (alle Trials `oos_evaluated=True`, alle `oos_eligible=False`) und warnt mit Trade-Count-/Trade-Cap-Diagnose (`oos_total_trades`-Median, `hit_trade_cap`-Anteil — neu als Per-Trial-User-Attrs gestempelt); `stop_on_plateau` beendet die Study früh (analog #456), statt die restlichen Trials nutzlos durchlaufen zu lassen. Ein separater `zero_eligible_plateau_warned`-Marker hält die Warnung idempotent (einmal je Study).
**Invariante:** Ein Suchraum, der strukturell NIE einen eligiblen Lauf erzeugt, wird früh erkannt und gemeldet (Trade-Count-Verteilung, Boundary-/Cap-Fraktion) — nicht erst nach dem vollen Trial-Budget im fertigen Proposal bemerkt.
**Betroffen:** `automation/optimizer/run_optimization.py` (`floor_plateau_callback`, `make_symbol_objective`-Stempel `oos_total_trades`/`is_total_trades`/`hit_trade_cap`), `automation/tests/test_issue_656_zero_eligible_diagnosis.py`.

### 🟢 Pitfall #164 — `reward_semantics_version` hinkt der Kohorte-A-Gate-Änderung hinterher [BEHOBEN: GH-#658]
**Symptom:** #649/#650/#657 ändern die effektive OOS-Eligibility-Definition (welche Trials feasible sind) materiell — die Feasible-Region der Reward-Landschaft verschiebt sich, obwohl `compute_reward` selbst unverändert bleibt. `reward_semantics_version` blieb bei 9.
**Root-Cause:** Der bestehende Guard (`_check_reward_semantics_version`, Pitfall #148) erkennt eine Study nur als stale, wenn die gestempelte von der aktuellen Config-Version ABWEICHT — Alt-Trials aus SQLite-Studies, die unter der ALTEN (defekten) Gate-Semantik bewertet wurden, vergiften sonst unbemerkt den TPE-Surrogat eines Re-Runs.
**Fix/Regel:** `reward_semantics_version` 9 → 10, EXPLIZIT als Eligibility-Semantik-Bump dokumentiert (nicht als Reward-Skalen-Änderung — der `_SCALE_KEYS`-Fingerprint aus Pitfall #148 bleibt bei v10 identisch zu v9, da keine Reward-Skalen-Konstante berührt wurde). Der bestehende Fail-Loud-Mechanismus (`REJECT_STALE_STUDY_SEMANTICS` ⇒ Study-Purge) bleibt strukturell unverändert wirksam.
**Invariante:** JEDE Änderung, die die effektive Eligibility-/Reward-Semantik verschiebt — auch ohne `compute_reward` selbst zu berühren — bumpt `reward_semantics_version`; der Bump ist die Kohärenz-Bedingung dafür, dass ein Re-Run nicht durch vergiftete Alt-Trials unterkorrigiert.
**Betroffen:** `automation/config/optimizer.json` (`reward_semantics_version`, Changelog), `automation/tests/test_issue_637_reward_semantics_bump.py` (Untergrenze statt exaktem Pin), `automation/tests/test_issue_658_reward_semantics_bump.py`.

### 🟢 Pitfall #165 — Gestapelte Multiple-Testing-Korrekturen kompoundieren Type-II-Fehler [BEHOBEN: GH-#659]
**Symptom:** Die Promotion verlangt gleichzeitig `deflated_selection`-Kohorten-Hürde (IS) UND DSR ≥ `deflation_confidence` UND Bootstrap-CI-Untergrenze > 0 UND PBO ≤ 0,5 UND `R_symbol > R_global + margin`. DynamicBreakout (PBO 0,0 = "nicht overfit", `dsr_z` +0,63) scheiterte GENAU an dieser Stapelung, obwohl PBO und DSR sich nicht widersprechen, sondern unterschiedliche Aspekte prüfen.
**Root-Cause:** PBO und DSR sind unterschiedliche Multiple-Testing-Korrekturen mit teils redundanter, teils unabhängiger Aussage; ihre KONJUNKTIVE Verknüpfung (plus Bootstrap-CI) ist für ein Per-Symbol-Micro-Tuning (Universe-Size 1, kurzer Holdout) potenziell über-konservativ und lehnt strukturell auch reale Edges ab (95 %-DSR allein ist bei 37 Trades bereits ehrlich streng).
**Fix/Regel:** Neuer, OPT-IN `tournament.json['promotion_correction_mode']`: `"conjunction"` (Default, fehlt der Key ⇒ bit-identisch zum Pre-#659-Verhalten) verlangt weiterhin alle drei Bestätigungen; `"dsr_or_robust_pair"` ersetzt die Konjunktion durch EINE der beiden unabhängigen Bestätigungen (DSR selbst ODER PBO-sicher-UND-Bootstrap-CI) — ein gescheitertes Symbol-Holdout-Gate SELBST bleibt in BEIDEN Modi ein Hard-Stop, kein Ersatzpfad umgeht das Basisgate. Ein unbekannter Modus-Wert bricht fail-loud ab. Dies ist eine STRUKTURELLE Bereitstellung — die KONKRETE Wahl (Modus + `deflation_confidence`) MUSS aus einem dedizierten Null-Kalibrierlauf (empirische OOS-Sortino-Verteilung) abgeleitet werden, bevor `"dsr_or_robust_pair"` produktiv aktiviert wird; sie ist NICHT in diesem Fix geraten oder vorentschieden.
**Invariante:** Die Korrektur-Konjunktion einer Promotion-Entscheidung ist NIE eine geratene Kombination — sie ist entweder der dokumentierte, bit-identische Default oder eine aus einem echten Kalibrierlauf empirisch begründete, reproduzierbare Wahl.
**Betroffen:** `automation/optimizer/confirm.py` (`_VALID_PROMOTION_CORRECTION_MODES`, `promotion_correction_mode`-Reinstate-Block), `automation/config/tournament.json` (`_schema`-Doku), `automation/tests/test_issue_659_correction_stacking.py`.

### 🟢 Pitfall #166 — `min_win_rate`-OR-Arm für einzelne Symbole strukturell unerreichbar [BEHOBEN: GH-#660, erweitert #633]
**Symptom:** `oos_min_win_rate=0,15` liegt UNTER dem #633-Cross-Strategy-Kalibrier-Fixture-p99 (0,197) und wird von der bestehenden, config-load-time `check_any_arm_reachability` daher als "erreichbar" eingestuft — die für TSLA.ETORO Hourly-Tier tatsächlich beobachtete OOS-Win-Rate blieb aber unter ~0,11. Der `Requires ANY of ['min_profit_factor', 'min_win_rate']`-Arm kollabiert für DIESEN Lauf still auf ein reines PF-Gate.
**Root-Cause:** `check_any_arm_reachability` (#633) läuft beim Config-Load, BEVOR irgendein Trial existiert — sie kann nur gegen ein STATISCHES, globales Cross-Strategy-Fixture prüfen, nie gegen die tatsächliche, SYMBOL-/STRATEGIE-spezifische empirische Verteilung eines konkreten Laufs.
**Fix/Regel:** Neue `check_any_arm_reachability_live(tournament_cfg, observed_values)` (reward.py), aufgerufen NACH Studienabschluss (`_emit_study_summary`) gegen die tatsächlich in DIESER Study beobachtete Win-Rate-Verteilung (`oos_win_rate`, neu als Per-Trial-User-Attr gestempelt) — ergänzend zur (unverändert bleibenden) #633-Fixture-Prüfung, nicht als Ersatz. Bei < 5 Beobachtungen erfolgt kein Urteil (zu wenig Daten). Das Ergebnis (`any_arm_live_unreachable`) ist Teil des `optimizer_study_completed`-Events.
**Invariante:** Ein OR-Arm-Gate wird sowohl gegen ein globales Kalibrier-Fixture (Config-Load-Zeit) ALS AUCH gegen die tatsächlich beobachtete, lauf-spezifische Verteilung (Study-Abschluss-Zeit) auf Erreichbarkeit geprüft — ein global "erreichbar" eingestufter Arm kann für ein spezifisches Symbol/eine Strategie dennoch strukturell kollabieren, ohne dass die statische Prüfung dies je erkennen könnte.
**Betroffen:** `automation/optimizer/reward.py` (`check_any_arm_reachability_live`, `_ANY_ARM_LIVE_THRESHOLD_KEYS`), `automation/optimizer/run_optimization.py` (`_emit_study_summary`, `oos_win_rate`-Stempel), `automation/tests/test_issue_660_any_arm_reachability.py`.

### 📋 Neue/geänderte Config-Keys (Bug-Kaskade #649–#660)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `eligible_requires_all` | tournament.json | `min_total_return` entfernt | quasi-alleiniges, nicht-risikoadjustiertes Gate entkoppelt (#650); die vier präfigierten Klauseln sind jetzt real durchsetzbar (#649) |
| `min_total_return` / `oos_min_total_return` | tournament.json | `0,005 → 0,0` | Breakeven-Sanity-Untergrenze statt bindender Diskriminator (#650) |
| `deflation_min_cohort` | tournament.json | Doku erweitert | jetzt Halbwertspunkt eines STETIGEN Shrinkage-Gewichts, kein harter Cutover mehr (#653) |
| `deflation_var_floor` | tournament.json | Doku erweitert | Fallback-Referenz nur ohne T-Telemetrie; primär T-bewusster Lo-2002-Floor (#653) |
| `promotion_correction_mode` | tournament.json | *neu* (Default `"conjunction"`) | opt-in DSR-ODER-(PBO+CI)-Konjunktion statt starrer UND-Verknüpfung (#659) |
| `reward_semantics_version` | optimizer.json | `9 → 10` | Kohorte-A-Eligibility-Semantik-Bump (#649/#650/#657), Pflicht-Purge vor Re-Run (#658) |

### 🔒 Watertight Invariants (Bug-Kaskade #649–#660) — für künftige Agenten
- **Ein Config-Gate-Name wird IMMER kanonisch normalisiert und gegen die echte Handler-Registry geprüft, nie nur config-intern:** ein `eligible_requires_all`/`_any`-Eintrag, der auf keinen `condition_map`-Handler resolved, muss `load_tournament_config` fail-loud abbrechen lassen (Pitfall #155).
- **Ein Test, der eine eigene Fixture-Config konstruiert, ist KEIN Test der ausgelieferten Config:** für jede config-getriebene Gate-/Reward-Semantik gehört zusätzlich ein Test, der die reale `tournament.json`/`optimizer.json` lädt (Pitfall #155/#130).
- **Ein absolutes, nicht-risikoadjustiertes Return-Gate dominiert NIE die risikoadjustierten Gates:** höchstens eine weiche Sanity-Untergrenze, nie ein bindender Diskriminator (Pitfall #156).
- **Höchstens EIN absolutes Profitabilitäts-Gate gleichzeitig:** zwei kollineare Return-Mittelwert-Gates (Total-Return und Expectancy) verschärfen sich gegenseitig ohne Zusatzinformation (Pitfall #157).
- **Eine robuste Statistik-Grösse (SR₀ o.ä.) stammt aus EINER Quelle für Entscheidung UND Telemetrie:** nie einmal roh, einmal gefloort berechnet (Pitfall #158).
- **Ein Varianz-Floor ist STETIG in N, nie ein harter Cutover an einer Konstante; er skaliert mit T:** kürzeres OOS-Fenster ⇒ konservativerer Floor (Pitfall #159).
- **Die Verlässlichkeit einer Varianz-SCHÄTZUNG hängt von der TATSÄCHLICHEN Stichprobengrösse ab, nie von einer grösseren, unabhängig motivierten Multiplizität (z. B. familienweitem N):** beide Grössen bleiben in der SR₀-Berechnung entkoppelte Parameter (Pitfall #159/#160).
- **Multiple-Testing-N spiegelt die Breite der SELEKTION, nicht einer Teil-Study:** wird der Beste aus K Strategien × M Trials gewählt, korrigiert die Deflation gegen ~K·M, nicht gegen die per-Study-Kohorte (Pitfall #160).
- **Promotion-Forensik (`is_rejection_detail`) und Study-Diagnose (`dominant_is_rejection_detail`) sind ZWEI getrennte Felder:** ein Proposal gibt nie den modalen IS-Grund als Promotion-Ursache aus, nur weil kein spezifischer Override gesetzt ist (Pitfall #161).
- **Magic-Sentinels (z. B. −20,0) leaken NIE in Cross-Entity-Ranking/Aggregation:** degenerierte/nicht-evaluierte Ergebnisse werden mit `None`/NaN markiert, jeder Aggregations-/Baseline-/Ranking-Pfad schliesst sie explizit aus (Pitfall #162).
- **Ein Suchraum, der strukturell nie einen eligiblen Lauf erzeugt, wird früh erkannt, nicht erst im fertigen Proposal bemerkt:** Trade-Count-/Boundary-Diagnose plus früher Stop, analog zum Pitfall-#75-Floor-Guard (Pitfall #163).
- **Jede Änderung, die die effektive Eligibility-/Reward-Semantik verschiebt — auch ohne `compute_reward` zu berühren — bumpt `reward_semantics_version`:** der Bump ist Bedingung dafür, dass ein Re-Run nicht durch vergiftete Alt-Trials unterkorrigiert (Pitfall #164).
- **Eine Korrektur-Konjunktion für Promotion-Entscheidungen ist nie geraten:** ein bit-identischer, dokumentierter Default ODER eine aus einem echten Kalibrierlauf empirisch begründete, reproduzierbare Wahl (Pitfall #165).
- **Ein OR-Arm-Gate wird sowohl global (Kalibrier-Fixture, Config-Load) als auch lauf-spezifisch (beobachtete Verteilung, Study-Abschluss) auf Erreichbarkeit geprüft:** global "erreichbar" schliesst einen strukturellen Kollaps für ein spezifisches Symbol nicht aus (Pitfall #166).

## Issue-Katalog #663–#672 — Fold-Kommensurabilität, CSCV-Granularität & Regime-symmetrische Gates (Sitzung 2026-07-17, Lauf 1)

Ein Forensik-Lauf auf dem #649–#660-migrierten System deckte zehn weitere, verzahnte Defekte in der
Fold-/Selektions-Statistik auf: annualisierte Fold-Sortinos sind über Folds nicht kommensurabel (#167),
PBO/CSCV auf den vier groben Walk-Forward-Folds misst Regime-Transfer statt Overfit (#168), ein
gleichgewichtetes Fold-Konsistenz-Gate ist an Regime-Heterogenität gekoppelt (#169), und ein absolutes
Excess-Return-Gate ist im Bärenmarkt blind (#170). Ergänzend: kollineare Gates kompoundieren den
Type-II-Fehler (#171), ein OR-Arm-Gate kollabiert lautlos auf einer per-Study-unerreichbaren Schwelle
(#172), `--strategies all` verbrennt Trial-Budget an trade-armen Strategien (#173), ein
Rückwärtskompat-Boolean darf nie als Telemetrie-Proxy umgedeutet werden (#174), der modale IS-Grund
verdeckt die tatsächliche Confirm-Ablehnungsursache (#175), und `reward_semantics_version` bumpt nur
bei tatsächlich default-aktiven Eligibility-Wechseln (#176). Pitfalls #167–#176, Issues #663–#672.

### 🟢 Pitfall #167 — Eine per-Fold annualisierte Sortino-Serie ist über Folds NICHT kommensurabel [BEHOBEN: GH-#665]
**Symptom:** `per_fold_oos_sortino` streute über die Kohorte in [−24.6, +15.6] mit Median ≈ −19 in Fold 1–3; dieselben Werte konsumierten PBO/CSCV (Pitfall #168) und die Fold-Median-Telemetrie (`sortino_ratio_fold_median`).
**Root-Cause:** `_get_annualization_factor(mtm_series)` leitet den Annualisierungsfaktor EMPIRISCH aus der Kalender-Span JEDES Folds ab (`n_periods·31_557_600/total_span_seconds`) — unterschiedliche Kalenderabdeckung (Wochenenden/Feiertage/Lücken) erzeugt einen FOLD-SPEZIFISCHEN Faktor. Zwei Folds mit identischer per-Perioden-Performance, aber unterschiedlicher Kalender-Span, liefern damit unterschiedliche annualisierte Sortinos — jede fold-übergreifende Mittelung/Aggregation dieser Serie ist strukturell falsch (misst teilweise Kalender-Artefakte, nicht Performance-Unterschiede). Zusätzlich ist der annualisierte Wert bei kleinem Fold-T (z. B. T≈137 Bars) statistisch bedeutungslos (dieselbe Pathologie wie Pitfall #134, jetzt auf Fold-Granularität).
**Fix/Regel:** `collect_oos_fold_sortino_periods`/`oos_fold_sortino_periods` (per-Perioden, NICHT annualisiert) ist die KANONISCHE fold-übergreifende Grösse — annualisierungs-invariant, über Folds direkt kommensurabel. `collect_oos_fold_sortinos`/`oos_fold_sortinos` (annualisiert) bleibt NUR forensische Anzeige-Telemetrie, DEPRECATED für jede Aggregation. `sortino_period_fold_median` ersetzt `sortino_ratio_fold_median` als Konsumenten-Grösse. Ein optionaler T-bewusster Guard (`sortino_numeric_guard_min_periods`, opt-in, Default inaktiv/bit-identisch) skaliert den Numerik-Guard proportional zu `sqrt(n_periods/min_periods)` unterhalb der Referenzgrösse.
**Invariante:** JEDE fold-übergreifende Aggregations-/Vergleichslogik (PBO, Dispersion, Diagnose) MUSS die per-Perioden-Serie konsumieren — ein annualisierter Fold-Wert ist NIE über Folds vergleichbar, weil sein Skalierungsfaktor selbst vom Fold abhängt.
**Betroffen:** `automation/backtest_runner.py` (`collect_oos_fold_sortino_periods`, `apply_fold_aggregation`, `_effective_sortino_numeric_guard`), `automation/optimizer/parsing.py`, `automation/optimizer/run_optimization.py`, `automation/tests/test_issue_665_fold_sortino_period_invariance.py`.

### 🟢 Pitfall #168 — CSCV/PBO auf den 4 Walk-Forward-Folds ≠ CSCV auf einer eigenen, feineren Partition [BEHOBEN: GH-#663]
**Symptom:** VwapExhaustion (bester IS-Reward, 8 eligible Configs, `oos_sortino` bis 3.28) wurde ALLEIN durch `PBO=1.000 > 0.5` als `REJECTED_SELECTION_OVERFIT` verworfen — die einzige Strategie, die die PBO-Stufe mit positivem IS-Best überhaupt erreichte.
**Root-Cause:** `_study_pbo` baute die CSCV-IS/OOS-Matrix aus den 4 Walk-Forward-Folds (`k_test=2` ⇒ nur `C(4,2)=6` Pfade) — statistisch grob (Bailey/López de Prado empfehlen S≳10–16 Gruppen). Bei stark regime-heterogenen Folds (z. B. Fold 1–3 defunkt, Fold 4 tradeable, siehe Pitfall #169) misst eine derart grobe CSCV faktisch REGIME-TRANSFER zwischen Folds, nicht Overfit — PBO→1.0 selbst bei Configs mit identischem Rang-Profil und nur einem Level-Offset (kein echter Selektions-Overfit). Die Split-Metrik (annualisierte Fold-Sortinos) war zusätzlich die inkommensurable Grösse aus Pitfall #167.
**Fix/Regel:** PBO rechnet auf einer EIGENEN, feineren CSCV-Partition (`pbo_n_groups`, Default 12, S≥8 Minimum) der GEPOOLTEN OOS-Per-Perioden-Return-Serie jedes eligiblen Trials (`oos_period_returns`, für eligible Trials gestempelt) — unabhängig von der (groben) Walk-Forward-Fold-Geometrie. Split-Metrik ist der mittlere Perioden-Return je Gruppe (`pbo_metric='period_return'`). Zu wenige Configs (`< pbo_min_configs`, Default 10) ODER zu wenige Gruppen (< 8) ⇒ `PBO=None` (kein Veto, das Punkt-/DSR-Gate bleibt maßgeblich) statt eines rausch-getriebenen Hard-Stops. Degenerierte (NaN-Gruppen-)Config-Zeilen werden explizit ausgeschlossen, nicht über einen Clamp maskiert.
**Invariante:** PBO/CSCV rechnet NIE auf der (groben) Walk-Forward-Fold-Geometrie — immer auf einer eigenen, feineren Partition mit S≥8 Gruppen der gepoolten Perioden-Serie; zu wenig Daten für ein belastbares Urteil ⇒ `None`, nie ein forciertes Ergebnis.
**Betroffen:** `automation/optimizer/confirm.py` (`_study_pbo`, `_PBO_DEFAULT_MIN_CONFIGS`/`_PBO_DEFAULT_N_GROUPS`), `automation/optimizer/cpcv.py` (`cpcv_group_boundaries`, unverändert wiederverwendet), `automation/optimizer/run_optimization.py` (`oos_period_returns`-Stempel für eligible Trials), `automation/config/tournament.json` (`pbo_min_configs`, `pbo_n_groups`), `automation/tests/test_issue_663_pbo_group_granularity.py`.

### 🟢 Pitfall #169 — Ein gleichgewichtetes Fold-Konsistenz-Gate ist bei nicht-stationärem Deployment-Ziel strukturell an Regime-Heterogenität gekoppelt — Diagnose vor Fix [BEHOBEN: GH-#664]
**Symptom:** `oos_min_profitable_folds_frac` war das EINZIGE Gate, das je solo verwarf (10 von 138 rejected evaluable Trials) — positive Expectancy, PSR/PF/WR bestanden, gescheitert AUSSCHLIESSLICH an `< 2/4 profitablen Folds`. Bei Fold-1–3-Positivraten von 8–14 % verlangt "≥ 50 % profitabel" faktisch Profitabilität in Fold 3 UND 4.
**Root-Cause:** Das Gate gewichtet alle Folds GLEICH; bei einem nicht-stationären Deployment-Ziel (Symbol-Regime-Wechsel) repräsentieren Alt-Folds ein defunktes Regime, während die deployment-relevanteste Evidenz (jüngster Fold) nur eine von N gleichgewichteten Stimmen ist. Das ist eine ECHTE Design-Spannung (Gleichgewicht = Robustheit gegen Regime-Wechsel; Recency-Gewicht = Deployment-Relevanz, aber Overfit-Risiko auf den jüngsten Fold) — KEIN Bug, der blind "gefixt" werden darf, ohne die Alternative empirisch zu belegen.
**Fix/Regel:** (1) Ein Diagnose-Artefakt (`_fold_regime_diagnostics`: Vorzeichen-Autokorrelation + Fold-Sign-Flip-Anteil) trennt "Symbol-Regime-Signal" von "Strategie-Schwäche", rein informativ. (2) Ein opt-in `profitable_folds_weighting: 'equal'|'recency'` (tournament.json). Default `'equal'` ist BIT-IDENTISCH zum Status quo — `'recency'` (exponentielle Fold-Gewichte, `recency_halflife_folds`) ist rein deklarativ bereitgestellt, NICHT produktiv voreingestellt, bis ein dedizierter Kalibrierlauf die Fold-4-Edge-Stabilität belegt.
**Invariante:** Eine Design-Spannung zwischen zwei validen Gewichtungsphilosophien wird NIE durch einen stillen Default-Wechsel aufgelöst — der Status quo bleibt Default, bis ein Kalibrierlauf die Alternative empirisch rechtfertigt (dieselbe Disziplin wie Pitfall #165).
**Betroffen:** `automation/backtest_runner.py` (`apply_fold_aggregation`, `_fold_recency_weights`, `_fold_regime_diagnostics`, `_evaluate_oos_eligibility`), `automation/config/tournament.json` (`profitable_folds_weighting`, `recency_halflife_folds`), `automation/tests/test_issue_664_profitable_folds_weighting.py`.

### 🟢 Pitfall #170 — Ein absolutes Excess-Return-Gate (≥0) ist im Bärenmarkt blind: misst negatives Beta statt Alpha [BEHOBEN: GH-#666]
**Symptom:** `oos_min_excess_return ≥ 0` (= `oos_total_return − oos_buyhold_return`) war in ALLEN 113 evaluierten Trials positiv (Median +0.1115) — das einzige Gate, das echtes Alpha von Beta trennen soll, passierte JEDER Trial trivial, inklusive verlustbringender Strategien.
**Root-Cause:** Buy&Hold fiel ~11 % über das OOS-Fenster ⇒ jede Strategie, die nicht schlimmer als −11 % verliert, "schlägt" den Benchmark. In einem fallenden Markt misst "schlage B&H im absoluten Endpunkt-Return" nur NEGATIVES BETA (Nicht-im-Markt-Sein), kein positives Alpha — eine Zufalls-/Flat-Strategie passiert das Gate allein, weil der Markt fiel.
**Fix/Regel:** |Benchmark|-bewusstes, REGIME-SYMMETRISCHES Gate: im Bull-/Flat-Markt (`oos_buyhold_return ≥ 0`) bleibt das absolute Excess-Gate unverändert (bit-identisch) maßgeblich. Im Bär-Markt (`oos_buyhold_return < 0`) verlangt das Gate stattdessen einen ECHTEN positiven risikoadjustierten Return (`sortino_period > 0`, dieselbe annualisierungs-invariante Grösse aus Pitfall #167) — ein undefinierter `sortino_period` ist NICHT eligible (kein impliziter Pass, analog Pitfall #133). `reward._normalized_gate_distances` spiegelt DIESELBE Regime-Fallunterscheidung (Gate/Reward-Parität) — eine undefinierte Kennzahl erfindet dort KEINEN Distanz-Wert (Präzedenzfall: `oos_min_psr`).
**Invariante:** Ein Alpha-/Excess-Gate MUSS in Bull- UND Bear-Regimen symmetrisch diskriminieren; ein rein absolutes Return-Gate ohne Risikoadjustierung ist per Konstruktion in GENAU EINEM Regime blind (hier: fallender Markt). Jede regime-abhängige Fallunterscheidung im Gate MUSS identisch in der Reward-Distanz gespiegelt werden (Parität, vgl. Pitfall #93/#122).
**Betroffen:** `automation/backtest_runner.py` (`_evaluate_oos_eligibility`, Excess-Return-Bedingung), `automation/optimizer/reward.py` (`_normalized_gate_distances`), `automation/config/tournament.json` (`oos_min_excess_return`-Doku), `automation/tests/test_issue_666_excess_return_regime_symmetry.py`. **`reward_semantics_version` 10→11** (ändert die effektive Eligibility-Definition für jeden Bär-Markt-Trial, siehe Pitfall #176).

### 🟢 Pitfall #171 — Kollineare eligible-Gates + konjunktive Confirm-Korrekturen kompoundieren den Type-II-Fehler [BEHOBEN: GH-#667]
**Symptom:** Unter 138 rejected evaluable Trials fielen `expectancy` (70 %), `profitable_folds` (69 %), `requires_any [PF|WR]` (68 %) und `psr` (66 %) fast immer GEMEINSAM — effektiv eine einzige latente "net-of-cost-profitabel"-Achse, 4× redundant kodiert. Darüber liegt der Confirm-Stack aus DSR UND Bootstrap-CI UND PBO (`promotion_correction_mode='conjunction'`, Default).
**Root-Cause:** DSR, PBO und Bootstrap-CI sind unterschiedliche, teils widersprüchliche Multiple-Testing-Korrekturen; ihre KONJUNKTIVE Verknüpfung auf einem kurzen Universe-1-Holdout ist potenziell über-konservativ — die 95%-DSR-Schwelle allein ist bereits streng, jede weitere UND-Bedingung kompoundiert die Type-II-Rate OHNE notwendig einen Type-I-Gewinn zu liefern. Ein Wechsel des Default-Modus darf aber NICHT geraten werden (vgl. Pitfall #165) — er erfordert einen echten Kalibrierlauf.
**Fix/Regel:** (1) Ein Null-Kalibrierlauf (`automation/optimizer/calibration.py::calibrate_promotion_correction_mode`, Monte-Carlo unter H0 auf synthetischen i.i.d.-Rausch-Kohorten) misst die REALISIERTE False-Positive-Winner-Rate beider Modi. Ergebnis (N_configs=12, T=200, confidence=0.95): `conjunction` ≈ 8.8 % (nahe dem nominellen 5 %-Niveau), `dsr_or_robust_pair` ≈ 31 % (Faktor ~3.5× höher) — bestätigt über eine zweite Parametrisierung (N=8, T=137: 9.6 % vs. 25.4 %). ENTSCHEIDUNG: der Default BLEIBT `'conjunction'` — `dsr_or_robust_pair` zeigt einen ECHTEN Type-I-Kostenanstieg, kein reines Type-II-ohne-Type-I-Free-Lunch; ein Wechsel wäre NICHT gerechtfertigt. (2) Eine Rang-Korrelationsmatrix der vier eligible-Gates (`reward.gate_rank_correlation_matrix`) wird telemetriert; ein Regressions-Wächter (`assert_gate_collinearity_guard`) warnt fail-loud-artig bei `|ρ| > 0.95` — Diagnose bleibt aktiv, auch wenn (noch) kein Gate entfernt wird.
**Invariante:** Ein Kalibrierlauf, der die GEGENTEILIGE Schlussfolgerung liefert als ursprünglich vermutet (hier: die Konjunktion ist NICHT übermäßig konservativ, sondern zeigt echten Type-I-Nutzen), ist ein GÜLTIGES, dokumentiertes Ergebnis — "kein Wechsel" ist eine ebenso legitime, evidenzbasierte Entscheidung wie ein Wechsel (Pitfall #165 gilt symmetrisch in beide Richtungen).
**Betroffen:** `automation/optimizer/calibration.py` (neu), `automation/optimizer/reward.py` (`gate_rank_correlation_matrix`, `assert_gate_collinearity_guard`, `_spearman_rank_correlation`), `automation/optimizer/run_optimization.py` (`_emit_study_summary`-Telemetrie), `automation/config/tournament.json` (`promotion_correction_mode`-Doku mit Kalibrier-Zahlen), `automation/tests/test_issue_667_gate_collinearity.py`.

### 🟢 Pitfall #172 — Ein OR-Arm-Gate mit global kalibrierter, per-Study unerreichbarer Schwelle kollabiert LAUTLOS auf die übrigen Arme [BEHOBEN: GH-#668]
**Symptom:** `eligible_requires_any-Klausel 'min_win_rate': Schwelle 0.1500 > beobachtetes p99=0.0968 DIESER Study` — der OR-Arm `(min_profit_factor ≥ 1.1 ODER min_win_rate ≥ 0.15)` kollabierte für dieses (Symbol, Strategie)-Paar STILL auf ein reines PF-Gate. Pitfall #166 (GH-#660) lieferte bereits die LIVE-Diagnose (WARNING), aber keine Konsequenz.
**Root-Cause:** Die Live-Diagnose war reine Beobachtung ohne konfigurierte Handlungsoption — ein strukturell unerreichbarer Arm blieb Teil der Disjunktion, kollabierte aber faktisch lautlos, ohne dass dies je EXPLIZIT entschieden/telemetriert wurde.
**Fix/Regel:** `any_arm_unreachable_policy ∈ {'warn', 'drop_arm', 'recalibrate'}` (tournament.json, Default `'warn'` = bit-identisch zu GH-#660). `'drop_arm'` markiert den Arm EXPLIZIT als gedroppt (`any_arm_reduced` im Winner-Eintrag); `'recalibrate'` setzt die Schwelle symbol-spezifisch auf p99(beobachtet), gefloort (`min_win_rate_recalibration_floor`). Bei einer sonst leeren `eligible_trials`-Selektion (`HOLDOUT_NO_ELIGIBLE_TRIALS`) werden bereits abgeschlossene Trials retroaktiv unter der angepassten Policy aus den gestempelten `oos_gate_deltas` neu bewertet (KEIN Re-Backtest) — die Rettung wird im Winner-Eintrag telemetriert, statt den Kollaps lautlos zu belassen. Ein unbekannter Policy-Wert bricht fail-loud ab (analog Pitfall #165).
**Invariante:** Ein strukturell unerreichbarer OR-Arm wird NIE nur beobachtet — die Konsequenz (Drop, Rekalibrierung oder bewusstes Belassen) ist eine KONFIGURIERTE, im Ergebnis sichtbare Policy-Entscheidung, keine stille Beobachtung ohne Wirkung.
**Betroffen:** `automation/optimizer/reward.py` (`resolve_any_arm_policy`), `automation/optimizer/confirm.py` (`_rescue_eligible_trials_under_any_arm_policy`), `automation/optimizer/run_optimization.py` (`oos_gate_deltas`-Trial-Stempel, Study-Summary-Telemetrie), `automation/config/tournament.json` (`any_arm_unreachable_policy`, `min_win_rate_recalibration_floor`), `automation/tests/test_issue_668_any_arm_symbol_calibration.py`.

### 🟢 Pitfall #173 — `--strategies all` heisst NICHT `strategies contribute all`: unkalibrierte Bounds verbrennen das Trial-Budget trade-armer Strategien [BEHOBEN: GH-#669]
**Symptom:** 7 von 10 Strategien trugen auf TSLA-1h NICHTS zur familienweiten Multiplizität bei: TrendPullback/AdxAtr `STRUCTURAL_ALL_UNEVALUABLE` (0/16 Trials ≥ `oos_min_trades`), HourlyMeanReversion `ZERO_ELIGIBLE_PLATEAU` (16/16 evaluiert, 0 eligible, median 214 Trades). `N_family=123` stammte ausschliesslich aus 3 von 10 Strategien.
**Root-Cause:** Der #656-Diagnose-/Early-Stop-Mechanismus (Pitfall #163) ERKENNT den Kollaps korrekt, unterscheidet aber NICHT die bindende Ursache: ein Suchraum, der zu SELTEN ein Signal erzeugt (Bounds-Problem, `spaces.py`), von einem Suchraum, der genug Trades, aber nie ein risikoadjustiert eligibles Ergebnis erzeugt (Signal-QUALITÄT, KEIN Bounds-Problem — Bounds-Kalibrierung würde hier nichts beheben).
**Fix/Regel:** `sweep_diagnostics.diagnose_trade_frequency` trennt `'signal_frequency'` (IS-Aktivität selbst < `oos_min_trades`), `'hold_duration'` (genug IS-Aktivität, aber Mehrheit trifft die Haltedauer-/Trade-Cap-Grenze) und `'signal_quality'` (alle evaluiert, keiner eligible) explizit — telemetriert in `STRUCTURAL_ALL_UNEVALUABLE`/`ZERO_ELIGIBLE_PLATEAU`-Events. Symbol-spezifische Suchraum-Bounds-Überschreibungen (`spaces._bounds_for`/`search_space_overrides.json`, opt-in, LEER per Default — kein Override ohne dokumentierten Kalibrierlauf) für die drei explizit benannten trade-armen Strategien. Eine deklarative (Symbol, Strategie)-Deaktivierungsliste (`symbol_strategy_denylist.json`, LEER per Default) überspringt bereits diagnostizierte, strukturell nicht-viable Paare VOR dem Sweep (Log-Zeile mit Grund), statt 16 Trials zu verbrennen.
**Invariante:** Ein 0-evaluable/0-eligible-Kollaps wird NIE pauschal als "Bounds-Problem" behandelt — die bindende Ursache (Frequenz vs. Haltedauer vs. Qualität) entscheidet, ob eine Bounds-Kalibrierung überhaupt der richtige Hebel ist. Ein Suchraum-Override ist NIE ohne einen dokumentierten Diagnose-/Kalibrierbefund voreingestellt (Zero-Hardcoding, kein erfundener Zahlenwert).
**Betroffen:** `automation/optimizer/sweep_diagnostics.py` (neu), `automation/optimizer/spaces.py` (`_bounds_for`, `sample_params(..., symbol=...)`), `automation/optimizer/sweep.py` (`enumerate_tunable_pairs`-Denylist-Skip), `automation/optimizer/run_optimization.py` (`floor_plateau_callback`-Diagnose-Wiring), `automation/config/search_space_overrides.json`, `automation/config/symbol_strategy_denylist.json`, `automation/tests/test_issue_669_search_space_trade_frequency.py`.

### 🟢 Pitfall #174 — Ein Rückwärtskompat-Boolean darf NIE in eine Telemetrie-Message umgedeutet werden [BEHOBEN: GH-#670]
**Symptom:** `[DSR #618/#636] TSLA.ETORO: N_eligible=8 < N_min=10 ⇒ Fallback-SR₀ (Varianz-Floor 0.0018 statt der 2-3-Punkte-Stichproben-Varianz V[ŜR]=0.000068) ⇒ SR₀=0.0328` — obwohl der T-bewusste Lo-2002-Floor (Pitfall #159) korrekt verdrahtet war und die theoretische Referenz TATSÄCHLICH Lo-2002 war, behauptete die Message "Varianz-Floor 0.0018" UND reaktivierte wörtlich das "N < N_min ⇒ Fallback"-Diskontinuitäts-Framing, das Pitfall #159 durch stetiges λ(N)-Shrinkage ERSETZT hatte. Die Message führte die Forensik zur Fehldiagnose, Pitfall #159 sei nicht verdrahtet — obwohl der Code korrekt war.
**Root-Cause:** Die Message war an `deflation_used_var_floor` gekoppelt, das `sr0_multiple_testing_robust` als `floor_dominant = (λ ≥ 0.5)` zurückgibt — ein Rückwärtskompat-Boolean, der NUR "Shrinkage-Gewicht dominant" bedeutet, NICHT "die var_floor-Konstante wurde verwendet". Diese beiden Aussagen sind ORTHOGONAL: bei vorhandenem `n_periods` ist die theoretische Referenz seit Pitfall #159 IMMER Lo-2002, unabhängig davon, ob λ ≥ 0.5 ist.
**Fix/Regel:** `sr0_multiple_testing_robust` gibt zusätzlich das PRÄZISE `shrinkage_lambda` (das tatsächliche Gewicht) und `theoretical_var_source ∈ {'lo2002', 'var_floor'}` (welche Referenz TATSÄCHLICH verwendet wurde) zurück. Jede Log-Message/Telemetrie koppelt an `theoretical_var_source`, NIE an das rückwärtskompatible `floor_dominant`/`deflation_used_var_floor`. Das "< N_min"-Vokabular ist gestrichen — es gibt seit Pitfall #159 keinen Cutover mehr, nur ein stetiges Gewicht.
**Invariante:** Ein Boolean, der aus Rückwärtskompat-Gründen erhalten bleibt, behält NUR seine URSPRÜNGLICH dokumentierte Bedeutung — er wird NIE als Proxy für eine andere, orthogonale Aussage (hier: "welche Konstante wurde verwendet") in eine neue Log-Message/Diagnose übersetzt, ohne die tatsächlich zugrundeliegende Grösse zu prüfen.
**Betroffen:** `automation/optimizer/deflation.py` (`sr0_multiple_testing_robust`-Rückgabe erweitert), `automation/optimizer/confirm.py` (Message-Block, `deflation_lambda`/`deflation_theoretical_var_source`-Telemetrie), `automation/optimizer/run_optimization.py` (Study-Summary-Telemetrie), `automation/tests/test_issue_670_dsr_message_variance_source.py`.

### 🟢 Pitfall #175 — Der modale IS-Grund im Proposal verdeckt die tatsächliche Confirm-/Holdout-Ablehnungsursache [BEHOBEN: GH-#671, #654-Nachprüfung]
**Symptom:** Proposals zeigten weiter `dominant_is_rejection_detail: REJECT_OOS_MIN_EXPECTANCY` neben dem echten Selektions-/Holdout-Grund (`is_rejection_detail: REJECT_SELECTION_PBO`) — Pitfall #161 (GH-#654) hatte `is_rejection_detail_override` bereits korrekt für JEDEN Confirm-Ausgang gesetzt, aber das Top-Level `dominant_rejection`-Feld blieb weiterhin an den modalen PER-TRIAL-IS-Grund gekoppelt, und die tatsächliche Ursache war nicht unter einem eindeutigen, nicht mit der IS-Diagnose verwechselbaren Namen exponiert.
**Root-Cause:** Zwei ähnlich benannte Felder (`is_rejection_detail` vs. `dominant_is_rejection_detail`) im selben Proposal — nur eines davon (das erste) erklärt tatsächlich die Promotion-Entscheidung, das andere ist reine IS-Study-Diagnose. Diese Namensähnlichkeit lädt zur Verwechslung ein, GENAU wie schon vor Pitfall #161.
**Fix/Regel:** Ein erstklassiges `holdout_reject_detail`-Feld (identischer Wert wie `is_rejection_detail`, aber unter einem Namen, der nicht mit `dominant_is_rejection_detail` verwechselbar ist). `dominant_rejection` (Top-Level) richtet sich auf die Confirm-Ursache aus, SOBALD die Strategie tatsächlich einen Holdout-Lauf erreichte (`_CONFIRM_STAGE_REJECTIONS`: `REJECT_HOLDOUT_GATE/_DSR_DROP/_BOOTSTRAP_CI`, `REJECT_SELECTION_PBO`, `REJECT_BOUNDARY_SOLUTION`, `REJECT_NO_EDGE_OVER_GLOBAL`) — NUR wenn die Selektion NIE einen Holdout-Lauf erreichte (`HOLDOUT_NO_ELIGIBLE_TRIALS`/`REJECT_HOLDOUT_UNREACHABLE`), bleibt der modale IS-Grund die sinnvollste Top-Level-Erklärung.
**Invariante:** Zwei Felder mit ähnlichem Namen, aber unterschiedlicher Semantik (Promotion-Ursache vs. Study-Diagnose), sind in einer Proposal-/Telemetrie-Struktur IMMER durch mindestens eines davon EINDEUTIG (nicht nur inhaltlich, auch NAMENTLICH) als "die tatsächlich blockierende Ursache" gekennzeichnet (Fortsetzung von Pitfall #161).
**Betroffen:** `automation/optimizer/confirm.py` (`export_symbol_proposal`, `_CONFIRM_STAGE_REJECTIONS`), `automation/tests/test_issue_671_holdout_detail_first_class.py`.

### 🟢 Pitfall #176 — In einem gemischten Opt-in/Default-Issue-Katalog bumpt NUR der tatsächlich default-aktive Eligibility-Wechsel die Version [BEHOBEN: GH-#672]
**Symptom:** Ein Katalog von 10 Gate-/Kalibrierungs-Issues (#663–#672) enthält eine Mischung aus reinen Confirm-/Telemetrie-Fixes (kein gespeicherter Reward betroffen), BEWUSST opt-in Mechanismen mit bit-identischem Default, und GENAU EINEM tatsächlich default-aktiven Eligibility-Codepfad-Wechsel — ein pauschaler Bump für den GESAMTEN Katalog (oder ein pauschales Unterlassen) würde die Versionshistorie unpräzise machen.
**Root-Cause:** Ohne eine explizite, issue-für-issue geführte Klassifikation ("ändert dieser Fix die Eligibility per DEFAULT, oder nur bei explizitem Opt-in?") besteht das Risiko, entweder unnötig zu bumpen (und damit gültige Alt-Studies grundlos zu purgen) oder einen echten Eligibility-Wechsel zu übersehen (und damit Alt-Trials mit inkommensurabler Semantik weiterzuverwenden, exakt die Pitfall-#164-Pathologie).
**Fix/Regel:** `reward_semantics_version` 10→11, EXPLIZIT begründet mit GENAU dem einen qualifizierenden Fix (Pitfall #170/GH-#666 — regime-symmetrisches Excess-Return-Gate, default-aktiv). Der Changelog-Eintrag benennt zusätzlich EXPLIZIT, WARUM die übrigen neun Issues NICHT selbst bumpten (Confirm-only vs. opt-in mit unverändertem Default) — eine reine "Issue X behoben"-Notiz ohne diese Begründung liesse offen, ob die Nicht-Bump-Entscheidung bewusst oder ein Versehen war. Der `_SCALE_KEYS`-Reward-Skalen-Fingerprint (Pitfall #137/GH-#637) bleibt zwischen v10 und v11 identisch (reiner Eligibility-, kein Skalen-Bump). Ein Re-Run-Runbook (`manuals/strategie_optimierung.md` Kapitel 7) dokumentiert den SQLite-Purge (`{WORK}/sweep/*.db`) als LETZTE Aktion — NACH allen Katalog-Merges, damit keine Study zweimal purgt wird.
**Invariante:** In einem Katalog mit gemischten Opt-in-/Default-Änderungen wird der Versions-Bump-Entscheid PRO ISSUE einzeln getroffen und im Changelog EXPLIZIT für jedes Issue begründet (bumpt/bumpt nicht + warum) — niemals pauschal für den ganzen Katalog entschieden (Fortsetzung von Pitfall #164).
**Betroffen:** `automation/config/optimizer.json` (`reward_semantics_version`, Changelog), `manuals/strategie_optimierung.md` (Kapitel 7, Re-Run-Runbook), `automation/tests/test_issue_672_reward_semantics_bump.py`.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #663–#672)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `sortino_numeric_guard_min_periods` | tournament.json | *neu* (Default fehlt ⇒ inaktiv) | T-bewusste Guard-Skalierung, opt-in (Pitfall #167) |
| `pbo_min_configs` / `pbo_n_groups` | tournament.json | *neu* (Default 10 / 12) | Mindest-Kohorte/Gruppenzahl der feineren PBO-CSCV-Partition (Pitfall #168) |
| `profitable_folds_weighting` / `recency_halflife_folds` | tournament.json | *neu* (Default `'equal'` / n_folds) | Opt-in Recency-Gewichtung des Profitable-Folds-Gates, bit-identischer Default (Pitfall #169) |
| `oos_min_excess_return` | tournament.json | Doku erweitert | Regime-symmetrisches (|Benchmark|-bewusstes) Alpha-Gate statt rein absolutem Delta (Pitfall #170) |
| `promotion_correction_mode` | tournament.json | Doku erweitert (Kalibrier-Zahlen) | Kalibrierlauf bestätigt `'conjunction'` als Default (Pitfall #171) |
| `any_arm_unreachable_policy` / `min_win_rate_recalibration_floor` | tournament.json | *neu* (Default `'warn'` / 0.05) | Konfigurierte Policy statt reiner Warnung für unerreichbare OR-Arme (Pitfall #172) |
| `search_space_overrides.json` | *neu* (Datei) | leer per Default | Symbol-spezifische Suchraum-Bounds-Overrides, opt-in (Pitfall #173) |
| `symbol_strategy_denylist.json` | *neu* (Datei) | leer per Default | Deklarative (Symbol, Strategie)-Deaktivierungsliste (Pitfall #173) |
| `reward_semantics_version` | optimizer.json | `10 → 11` | Eligibility-Semantik-Bump durch #666 (Pitfall #176), Pflicht-Purge vor Re-Run |

### 🔒 Watertight Invariants (Issue-Katalog #663–#672) — für künftige Agenten
- **Eine annualisierte Fold-Kennzahl ist über Folds NIE direkt vergleichbar/mittelbar:** der Annualisierungsfaktor selbst ist fold-spezifisch (Kalender-Span); jede fold-übergreifende Aggregation nutzt die per-Perioden-Grösse (Pitfall #167).
- **PBO/CSCV rechnet NIE auf der groben Walk-Forward-Fold-Geometrie:** immer auf einer eigenen, feineren Partition (S≥8 Gruppen) der gepoolten Perioden-Serie; zu wenig Daten ⇒ `None`, nie ein forciertes Urteil (Pitfall #168).
- **Eine Design-Spannung zwischen zwei validen Gewichtungsphilosophien wird NIE durch einen stillen Default-Wechsel aufgelöst:** der Status quo bleibt Default, bis ein Kalibrierlauf die Alternative empirisch rechtfertigt (Pitfall #169).
- **Ein Alpha-/Excess-Gate MUSS in Bull- UND Bear-Regimen symmetrisch diskriminieren; jede regime-abhängige Gate-Fallunterscheidung wird identisch in der Reward-Distanz gespiegelt** (Pitfall #170).
- **Ein Kalibrierlauf mit dem Ergebnis "kein Wechsel gerechtfertigt" ist ein ebenso gültiges, dokumentiertes Resultat wie ein Wechsel** — Kalibrierung entscheidet, sie bestätigt nicht immer die Ausgangsvermutung (Pitfall #171).
- **Ein strukturell unerreichbarer OR-Arm wird NIE nur beobachtet:** die Konsequenz ist eine konfigurierte, im Ergebnis sichtbare Policy-Entscheidung (Pitfall #172).
- **Ein 0-evaluable/0-eligible-Kollaps wird NIE pauschal als Bounds-Problem behandelt:** die bindende Ursache (Frequenz/Haltedauer/Qualität) entscheidet, ob eine Bounds-Kalibrierung der richtige Hebel ist; kein Suchraum-Override ohne dokumentierten Diagnosebefund (Pitfall #173).
- **Ein Rückwärtskompat-Boolean behält NUR seine ursprünglich dokumentierte Bedeutung** — er wird nie stillschweigend als Proxy für eine andere, orthogonale Aussage in eine neue Diagnose übersetzt (Pitfall #174).
- **Zwei ähnlich benannte Felder mit unterschiedlicher Semantik (Promotion-Ursache vs. Diagnose) sind immer durch mindestens eines davon eindeutig als "die tatsächlich blockierende Ursache" gekennzeichnet** (Pitfall #175, Fortsetzung #161).
- **In einem Katalog mit gemischten Opt-in-/Default-Änderungen wird der Versions-Bump-Entscheid PRO ISSUE einzeln getroffen und im Changelog explizit begründet** — nie pauschal für den ganzen Katalog (Pitfall #176, Fortsetzung #164).

## Issue-Katalog #675–#686 — Optimizer: Mathematische Exzellenz, vier Kohorten (Sitzung 2026-07-17, Lauf 2)

Zwölf verzahnte Fixes in vier Kohorten, `reward_semantics_version` 11→12. **Kohorte A (Validierungs-Geometrie):** opt-in rollierendes IS-Fenster je Fold (#675/Pitfall #177), Fold-Zähler-Nenner-Bugfix + Redundanz-Entfernung (#676/Pitfall #178, #677/Pitfall #179). **Kohorte B (Promotion-Kalibrierung):** ein behaupteter Kalibrier-Deadlock existierte nicht (#678/Pitfall #180), ein strukturierter Kollinearitäts-Alarm ersetzt reines Logging (#679/Pitfall #181), `any_arm_unreachable_policy` auf `'recalibrate'` aktiviert (#680/Pitfall #182). **Kohorte C (Suchraum & Deployment):** Diagnose-Closed-Loop über separaten Auto-Cache statt der kuratierten Denylist (#681/Pitfall #183), `PROMOTE_GLOBAL_DEFAULT_ON_SYMBOL`-Route bei 0 symbol-eligiblen Trials (#682/Pitfall #184). **Kohorte D (Statistische Verfeinerung):** PBO-Split-Metrik auf Gruppen-Sortino umgestellt + Near-Duplicate-Reduktion (#683/Pitfall #185), Expectancy-Gate-Fallback config-abgeleitet (#684/Pitfall #186), `deflation_var_floor` DEPRECATED markiert (#685/Pitfall #187). Zuletzt: `reward_semantics_version`-Bump PRO Issue begründet + Bulk-Purge-Werkzeug (#686/Pitfall #188). Pitfalls #177–#188, Issues #675–#686.

### 🟢 Pitfall #177 — Ein statisch verankertes IS-Fenster + kontiguierliche OOS-Folds testet Regime-PERSISTENZ, nicht Config-ROBUSTHEIT [BEHOBEN: GH-#675]
**Symptom:** Die per-Fold-Vorzeichenstruktur war UNIFORM `---+` über ALLE Strategien und Configs (P(letzter Fold>0) 0,62–0,98; P(frühere Folds>0) 0,06–0,13) — kein Config-Effekt, sondern ein reiner Regime-Effekt des Symbols: ein 180-Tage-IS-Modell wurde ohne Neuanpassung auf bis zu 381 Tage entfernte OOS-Daten angewandt.
**Root-Cause:** `compute_fold_boundaries` verankerte `is_start_ns = start_ns` STATISCH für ALLE Folds; die 4 "Folds" waren ein einziger Vorwärtspfad, zerteilt — kein echter Walk-Forward. Selektion/Eligibility maximieren über Fold 1–4; das Holdout (Slice 5) ist bei echtem Regimewechsel dann ein Münzwurf.
**Fix/Regel:** Ein OPT-IN `walk_forward.retrain`-Modus (backtest.json, Default `false` = bit-identisch) lässt `compute_fold_boundaries` jedem Fold ein EIGENES, unmittelbar vorangehendes (rollierendes) IS-Referenzfenster geben (`is_start = oos_start − embargo − is_window`, EMBARGO-SICHER — die literale Issue-Formel ohne Embargo-Abzug hätte den Purge-Gap auf 0 kollabiert und Lookback-Leakage reintroduziert). WICHTIGER SCOPE-HINWEIS: Dieses System optimiert EINEN Parametervektor pro Trial über die GESAMTE Zeitspanne (ein einziger kontinuierlicher Backtest, keine Per-Fold-Neuanpassung) — `retrain=true` liefert daher KEIN echtes Parameter-Refit je Fold, sondern eine additive rollierende IS/OOS-Divergenz-Diagnose (`oos_fold_is_oos_divergence`, `rolling_fold_is_oos_divergence`), OHNE jede Gate-/Reward-Wirkung. Die im Issue genannte CPCV-Alternative existiert bereits separat (`cpcv.py`, seit Pitfall #168 für PBO genutzt) und bleibt der empfohlene strukturelle Haupt-Hebel für eine echte Eligibility-Antwort auf Nichtstationarität.
**Invariante:** Eine wörtliche Issue-Formel, die eine bestehende Sicherheits-Invariante (hier: Embargo/Purge-Gap gegen Lookback-Leakage) verletzen würde, wird NIE blind übernommen — sie wird korrigiert und die Abweichung dokumentiert (technische Präzision hat Vorrang vor Wortlaut-Treue). Ein Geometrie-Primitiv, das keinen Downstream-Konsumenten hat, liefert KEINEN beobachtbaren Effekt — eine ehrliche Pitfall-Doku überclaimt das nie.
**Betroffen:** `automation/backtest_runner.py` (`compute_fold_boundaries`, `rolling_fold_is_oos_divergence`), `automation/config/backtest.json` (`walk_forward.retrain`), `automation/tests/test_issue_675_walkforward_retrain.py`.

### 🟢 Pitfall #178 — Ein Fold-Konsistenz-Gate mit No-Trade-Folds im Nenner bestraft Signal-ABWESENHEIT als wäre sie Unprofitabilität [BEHOBEN: GH-#676]
**Symptom:** `oos_min_profitable_folds: 1/4 (0.25) < 0.50` war der häufigste SOLO-Ablehnungsgrund (17 alleinige Rejections) — bei Fold-1–3-Positivraten von 6–13 % verlangt "≥ 50 % profitabel" faktisch Profitabilität in GENAU den beiden am wenigsten wahrscheinlichen Folds.
**Root-Cause (zweiteilig):** (1) Ein per-Fold-Konsistenz-Gate über einen Zeitraum mit echtem Regimewechsel bestraft die KORREKTE Erkenntnis "nur ein Regime ist tradeable" — und ist redundant zur Fold-Dispersions-Penalty (`reward.py fold_dispersion_weight`) UND zum PBO (dieselbe Streuung dreifach bestraft). (2) `oos_profitable_folds_frac = n_folds_profitable / n_folds_total`, wobei `n_folds_total` No-Trade-Folds UNGEFILTERT mitzählt, der Zähler aber nur über die validen (winsorisierten) Fold-Returns läuft — ein Fold ohne Signal zählt so als "nicht profitabel", obwohl das keine Unprofitabilität ist, sondern Signal-Abwesenheit.
**Fix/Regel:** Nenner-Fix: `oos_folds_evaluable` (Anzahl Folds MIT tatsächlichem Return, exakt die Population des Zählers) ersetzt `oos_folds_total` als Nenner — sowohl in der equal- als auch der recency-gewichteten Fraktion; `_evaluate_oos_eligibility` liest denselben korrigierten Nenner statt ihn separat (und divergierend) zu rekonstruieren. Redundanz-Fix: `oos_min_profitable_folds_frac` aus dem DEFAULT `eligible_requires_all` entfernt (Metrik-Key + Telemetrie bleiben erhalten, reaktivierbar per Config-only PR).
**Invariante:** Ein Fraktions-Nenner zählt NIE eine Kategorie mit, die den Zähler strukturell nie erreichen kann — genau das erzeugt eine künstliche Untergrenze der Fraktion (hier: 1 profitabler + 3 No-Trade-Folds ergab faktisch 0.25 statt der korrekten 1.0). Ein redundantes Gate, das dieselbe Streuung dreifach bestraft, gehört auf die Reward-Ebene (bereits vorhanden), nicht als zusätzliche harte Konjunktions-Klausel.
**Betroffen:** `automation/backtest_runner.py` (`apply_fold_aggregation`, `_evaluate_oos_eligibility`), `automation/config/tournament.json` (`eligible_requires_all`, `oos_min_profitable_folds_frac`-Doku), `automation/tests/test_issue_676_profitable_folds_denominator.py`.

### 🟢 Pitfall #179 — Ein absoluter Fold-ZÄHLER dupliziert, was die Mindest-Trade-Schwelle bereits absichert, und bestraft frequenz-heterogene Configs [BEHOBEN: GH-#677]
**Symptom:** `oos_min_evaluable_folds: 1 valide < 2 (von 4 Folds)` = 27 Vorkommnisse — eine Config, die stark in Fold 4 tradet (median 214 OOS-Trades) und in Fold 1–3 kein Signal hat, wird allein wegen des Fold-ZÄHLERS verworfen, obwohl die gepoolte Stichprobe (`oos_min_trades=20`) längst gesichert ist.
**Root-Cause:** Dieselbe `---+`-Regimestruktur, die Pitfall #178 auslöst, killt auch dieses Gate — zwei separate Gates bestrafen dasselbe strukturelle Merkmal. Zusätzlich macht die VARIABLE Fold-Anzahl je Trial (1/2/3/4 valide Folds) jede fold-übergreifende Aggregation (PBO-Gruppen, Fold-Median, Fraktions-Nenner) über Trials inkommensurabel.
**Fix/Regel:** `oos_min_evaluable_folds` aus dem DEFAULT `eligible_requires_all` entfernt (dieselbe Reversibilitäts-Logik wie Pitfall #178: Metrik-Key bleibt, reaktivierbar). Bei EXPLIZITER Reaktivierung akzeptiert das Gate zusätzlich einen RELATIVEN Schwellen-Modus: ein Wert in `(0, 1]` wird als Mindest-Anteil an `oos_folds_evaluable` (Folds MIT Signal, Pitfall #178) interpretiert statt eines absoluten Zählers — ein Wert `≥ 1` bleibt bit-identisch der Legacy-Zähler.
**Invariante:** Ein Fold-ZÄHLER-Gate dupliziert NIE eine Mindest-Stichproben-Garantie, die bereits über `min_trades`/`min_expectancy` gesichert ist. Eine variable Fold-Anzahl je Trial macht jede naive fold-übergreifende Aggregation inkommensurabel — Aggregatoren MÜSSEN den variablen Nenner explizit behandeln (nie stillschweigend `n_folds_total` annehmen).
**Betroffen:** `automation/backtest_runner.py` (`_evaluate_oos_eligibility`), `automation/config/tournament.json` (`eligible_requires_all`, `oos_min_evaluable_folds`-Doku), `automation/tests/test_issue_677_evaluable_folds_relative.py`.

### 🟢 Pitfall #180 — Ein behaupteter "Kalibrier-Deadlock" existierte nicht; eine Verifikation bei der TATSÄCHLICHEN T-Skala bestätigt die vorherige Entscheidung erneut [BEHOBEN: GH-#678]
**Symptom:** Issue-Hypothese: DSR≥0.95 auf ~36 Holdout-Trades sei praktisch unerreichbar, UND ein Kalibrierlauf zur Rechtfertigung von `dsr_or_robust_pair` sei unmöglich, weil er eine (nie erreichte) Live-Promotion voraussetze — ein "Chicken-Egg-Deadlock".
**Root-Cause-Präzisierung:** Der behauptete Deadlock existiert NICHT — `calibrate_promotion_correction_mode` (Pitfall #171/GH-#667) arbeitet bereits AUSSCHLIESSLICH auf synthetischen H0-Daten und benötigt NIE eine reale Promotion. Eine Verifikation bei der vom Issue selbst genannten T-Skala (T≈36, N_configs=8–12, 250–500 Replikationen) bestätigt die Pitfall-#171-Entscheidung ERNEUT: `conjunction` FP-Rate ≈ 13–15 % (nominal 5 %), `dsr_or_robust_pair` ≈ 25–35 % (Faktor ~2–2.3× höher) — ein Wechsel des Defaults wäre AUCH bei dieser kleinen T-Skala nicht gerechtfertigt.
**Fix/Regel:** `calibration.calibrate_t_adaptive_confidence` liefert eine WIEDERVERWENDBARE Grid-Suche nach der `deflation_confidence`, deren empirische FP-Rate am nächsten am nominellen Ziel liegt, für eine GEGEBENE (N, T)-Grössenordnung — ein Werkzeug, kein erzwungener Default-Wechsel. `promotion_correction_mode` BLEIBT `'conjunction'`. Beide Schema-Texte (`promotion_correction_mode`, `deflation_confidence`) dokumentieren die T=36-Verifikationszahlen.
**Invariante (Fortsetzung Pitfall #171, gilt SYMMETRISCH):** Eine Issue-Hypothese, die einen Default-Wechsel nahelegt, wird NIE blind übernommen — sie wird gegen die TATSÄCHLICHEN Parameter des behaupteten Symptoms verifiziert. Bestätigt die Verifikation die bestehende (dokumentierte, kalibrierte) Entscheidung erneut, bleibt der Default unverändert — "kein Wechsel, jetzt zweifach bestätigt" ist ein ebenso gültiges Ergebnis wie ein Wechsel.
**Betroffen:** `automation/optimizer/calibration.py` (`calibrate_t_adaptive_confidence`), `automation/config/tournament.json` (`promotion_correction_mode`, `deflation_confidence`-Doku), `automation/tests/test_issue_678_t_adaptive_confidence_calibration.py`.

### 🟢 Pitfall #181 — Eine Kollinearitäts-DIAGNOSE ohne strukturierte Auswertung bleibt folgenlos, egal wie oft sie loggt [BEHOBEN: GH-#679]
**Symptom:** `gate_collinearity` (Rang-Korrelationsmatrix, Pitfall #171) wurde bei jeder Study berechnet und bei `|ρ| > 0.95` als WARNING geloggt — aber niemals strukturiert ausgewertet; eine Konsolidierungs-Entscheidung blieb rein manuell, ohne maschinenlesbare Grundlage.
**Root-Cause:** `eligible_requires_all` addiert korrelierte Klauseln, deren gemeinsame Passrate ≈ der strengsten Einzelklausel entspricht, aber jede zusätzliche korrelierte Klausel senkt die eligible-Rate weiter, OHNE echte False-Positive-Kontrolle beizutragen (das leistet die DSR/PBO-Ebene bereits, siehe Pitfall #171).
**Fix/Regel:** `reward.gate_collinearity_redundancy_alarm` hebt die Diagnose auf einen STRUKTURIERTEN Alarm: für jedes Paar mit `|ρ| > threshold` (Default 0.9) wird — über eine Konsolidierungs-Priorität (`_GATE_CONSOLIDATION_PRIORITY`, PSR hat höchste Priorität, wird NIE als redundant markiert) — das niedriger priorisierte Gate als `redundant_candidate` markiert. Der Alarm (`gate_collinearity_alarm`, `gate_collinearity_redundant_candidates`) ist Teil des `optimizer_study_completed`-Events. Die Funktion selbst KONSOLIDIERT NICHTS automatisch — welches Gate tatsächlich aus `eligible_requires_all` entfernt wird, bleibt eine bewusste, dokumentierte Config-/PR-Entscheidung (dieselbe Disziplin wie Pitfall #171/#165).
**Invariante:** Eine Diagnose, die NUR loggt, ist bei genug Log-Volumen faktisch unsichtbar — ein Redundanz-Alarm MUSS als strukturiertes, von Tooling/Tests auswertbares Feld existieren, nicht nur als Log-Zeile.
**Betroffen:** `automation/optimizer/reward.py` (`gate_collinearity_redundancy_alarm`, `_GATE_CONSOLIDATION_PRIORITY`), `automation/optimizer/run_optimization.py` (Study-Summary-Event), `automation/tests/test_issue_679_gate_collinearity_alarm.py`.

### 🟢 Pitfall #182 — Eine Policy-Mechanik, die existiert, aber nie aktiv ist, ist funktional identisch zu ihrer Abwesenheit [BEHOBEN: GH-#680]
**Symptom:** `any_arm_live_unreachable=['min_win_rate']` feuerte in JEDER trade-armen Study; `any_arm_unreachable_policy` blieb aber auf `'warn'` (unkonfiguriert), obwohl die `'recalibrate'`/`'drop_arm'`-Mechanik bereits seit Pitfall #172 (GH-#668) existierte. `any_arm_recalibrated_thresholds` blieb dadurch in JEDEM Lauf leer.
**Root-Cause:** Eine Policy-Option, die IMPLEMENTIERT, aber nie KONFIGURIERT wird, hat exakt denselben beobachtbaren Effekt wie ihr Fehlen — der OR-Arm kollabiert weiterhin lautlos auf ein reines PF-Gate.
**Fix/Regel:** `any_arm_unreachable_policy` DEFAULT auf `'recalibrate'` gehoben (Config-only, Mechanik unverändert seit Pitfall #172). `'recalibrate'` erhält den Win-Rate-Filter als echtes, symbol-kalibriertes Kriterium (statt eines rein deklarativen, faktisch nie greifenden PF-ODER-WR-Gates).
**Invariante:** Eine implementierte, getestete Policy-Mechanik OHNE einen Default, der sie tatsächlich aktiviert, liefert keinen Produktionswert — "die Mechanik existiert" und "die Mechanik wirkt" sind zwei verschiedene Aussagen, die eine Doku nie verwechseln darf.
**Betroffen:** `automation/config/tournament.json` (`any_arm_unreachable_policy`, `min_win_rate_recalibration_floor`), `automation/tests/test_issue_680_any_arm_policy_default.py`.

### 🟢 Pitfall #183 — Ein automatisierter Diagnose-Schreib-Zurück-Mechanismus mutiert NIE die menschlich-kuratierte Governance-Config direkt [BEHOBEN: GH-#681]
**Symptom:** `STRUCTURAL_ALL_UNEVALUABLE`/`ZERO_ELIGIBLE_PLATEAU` (Pitfall #163/#173) feuerten korrekt, aber `symbol_strategy_denylist.json`/`search_space_overrides.json` blieben über Läufe hinweg LEER — dieselben strukturell toten (Symbol, Strategie)-Paare wurden bei JEDEM Lauf neu enumeriert und verbrannten ihr volles Trial-Budget erneut.
**Root-Cause:** Die Diagnose ist rein deklarativ und schreibt nirgendwo zurück — es gibt keinen Pfad von "Diagnose gefeuert" zu "Paar beim nächsten Lauf übersprungen", ausser einem manuellen, menschlichen PR gegen die Denylist-Datei.
**Fix/Regel:** `sweep_diagnostics.recommend_diagnosis_action` klassifiziert die Diagnose nach `binding_cause`: `'signal_quality'` ⇒ `'denylist'` (Bounds-Kalibrierung hilft hier nicht); `'signal_frequency'`/`'hold_duration'` bei einer für Bounds-Overrides VERDRAHTETEN Strategie (`WIRED_OVERRIDE_STRATEGIES`) OHNE existierenden Override ⇒ `'search_space_override'` (Bounds-Kalibrierung erst probieren); sonst ebenfalls `'denylist'`. Die Empfehlung wird in einen SEPARATEN, AUTOMATISCH gepflegten Cache geschrieben (`data/optimizer/diagnosed_pairs_cache.json`, via `record_diagnosed_pair`) — NICHT in die menschlich-kuratierte `symbol_strategy_denylist.json` selbst. `enumerate_tunable_pairs` überspringt ein `'denylist'`-empfohlenes Cache-Paar ab dem NÄCHSTEN Lauf automatisch (distinktes `SYMBOL_STRATEGY_AUTO_DIAGNOSED_SKIP`-Event, getrennt von `SYMBOL_STRATEGY_DENYLISTED`); `'search_space_override'`-Empfehlungen werden NUR aufgezeichnet, nie automatisch angewendet (Bounds-Kalibrierung bleibt ein Kalibrierlauf-/PR-Entscheid, konsistent mit `search_space_overrides.json`s eigenem Schema).
**Invariante:** Ein Budget-sparender Automatismus und eine PERMANENTE Governance-Entscheidung sind ZWEI VERSCHIEDENE Datenspeicher — ein Prozess darf den Budget-Cache selbst pflegen, aber NIE die versionierte, PR-gebundene Policy-Datei direkt beschreiben (dieselbe Trennung wie "Promotion erfolgt ausschliesslich per menschlich freigegebenem PR", HI-3).
**Betroffen:** `automation/optimizer/sweep_diagnostics.py` (`recommend_diagnosis_action`, `record_diagnosed_pair`, `load_diagnosed_pairs_cache`, `has_existing_search_space_override`), `automation/optimizer/run_optimization.py` (`floor_plateau_callback`-Wiring), `automation/optimizer/sweep.py` (`enumerate_tunable_pairs`-Skip), `automation/tests/test_issue_681_diagnosis_closed_loop.py`.

### 🟢 Pitfall #184 — Ein global-viabler Default ohne symbol-eligiblen Trial darf nicht lautlos als "keine eligiblen Trials" enden [BEHOBEN: GH-#682]
**Symptom:** `VolatilityBreakoutPumpStrategy` bestand das Symbol-Holdout-Gate mit dem UNGETUNTEN globalen Default (Sortino 4.64, `R_global=+1.71`), aber die Per-Symbol-Study fand 0 eligible Trials ⇒ `HOLDOUT_NO_ELIGIBLE_TRIALS` — ein global-viabler Kandidat verschwand lautlos, obwohl `m_global`/`R_global` (der globale Vektor AUF DIESEM Symbol-Holdout) bereits berechnet vorlagen.
**Root-Cause:** Der Per-Symbol-Promotion-Pfad verlangte einen symbol-eligiblen Trial als ZWINGENDE Voraussetzung, selbst wenn der globale Vektor selbst längst eligible war (Pfad-Diskrepanz, vgl. Pitfall #100/Shrinkage-Inaktiv-Klasse).
**Fix/Regel:** Bevor `confirm_per_symbol_promotion` bei leerer `eligible_trials`-Menge endgültig ablehnt, prüft es, ob der bereits berechnete globale Vektor SELBST das Symbol-Holdout-Gate besteht UND einen positiven Reward hat — falls ja, wird er ALS Symbol-Kandidat promotet (`R_symbol := R_global`, `symbol_params := global_params`, KEIN Micro-Tuning-Anspruch), mit einer EXPLIZITEN, strukturierten Entscheidung (`PROMOTE_GLOBAL_DEFAULT_ON_SYMBOL`-Event, `promotion_route='global_default_on_symbol'` im Proposal) statt eines stillen `HOLDOUT_NO_ELIGIBLE_TRIALS`.
**Invariante:** Ein Confirm-Pfad, der auf 0 symbol-eligible Trials trifft, prüft IMMER zuerst, ob bereits verfügbare Daten (hier: der ohnehin berechnete globale Holdout-Lauf) eine legitime Route liefern, BEVOR er lautlos ablehnt — eine Ablehnung mangels Symbol-spezifischer Daten ist NICHT dasselbe wie eine Ablehnung mangels JEDER validierten Route.
**Betroffen:** `automation/optimizer/confirm.py` (`confirm_per_symbol_promotion`, `export_symbol_proposal`), `automation/tests/test_issue_682_global_default_promotion.py`, `automation/tests/test_issue_615_topk_holdout_selection.py` (Fixture korrigiert, damit sie weiterhin den urspünglichen Fail-Loud-Fall statt der neuen #682-Route testet).

### 🟢 Pitfall #185 — CSCV/PBO auf per-BAR-Mittelwerten einer Cash-dominierten Equity-Kurve misst Regime-Timing, nicht Config-Sensitivität [BEHOBEN: GH-#683]
**Symptom:** `FlashCrashReversalStrategy` zeigte PBO=0.94 bei nur 36 Configs — die CSCV-Split-Metrik war der rohe Gruppen-MITTELWERT der per-Bar-MtM-Return-Serie (damals `oos_period_returns = mtm_series.pct_change()`, seit #802 mit explizitem `fill_method=None`; seit #801 ohnehin durch die algebraische `np.diff(np.log(...))`-Formel ersetzt), die zwischen Trades (Config in Cash) von Nullen dominiert wird.
**Root-Cause:** Ein Gruppen-Mittelwert einer meist-flachen Serie macht den "IS-Bester-je-Pfad"-Vergleich zu einem Timing-Lotto (welche Config war zufällig während der grössten Kursbewegung positioniert), nicht zu einem Parameter-Sensitivitäts-Test. Zusätzlich erzeugen Near-Duplicate-Configs (dichtes Optuna-Suchraum-Grid) near-identische Return-Vektoren ⇒ die EFFEKTIVE Anzahl unabhängiger Strategien ist ≪ die nominelle Trial-Zahl ⇒ die CSCV-Rangstatistik ist verzerrt.
**Fix/Regel:** (1) Split-Metrik ist der PER-GRUPPEN-SORTINO (Mittelwert / Downside-Deviation der Gruppen-Subserie, annualisierungsfrei) statt des rohen Mittelwerts (`pbo_metric='group_sortino'`, vorher `'period_return'`) — eine Gruppe ohne Verlust-Bar behält den rohen Mittelwert (kein erfundener, unendlicher Sortino). (2) `cpcv.cluster_effective_configs` reduziert die Kohorte VOR der CSCV-Partitionierung auf effektiv-unabhängige Vertreter (Pearson-Korrelation der VOLLEN per-Perioden-Serie `> pbo_cluster_threshold`, Default 0.99; bei (nahezu) Nullvarianz entscheidet ein direkter `np.allclose`-Vergleich statt einer numerisch instabilen Korrelation). `pbo_n_configs` (Telemetrie) ist die EFFEKTIVE, `pbo_n_configs_raw` die rohe Config-Zahl.
**Invariante:** PBO/CSCV misst NIE auf einer durch Near-Duplicate-Configs künstlich aufgeblähten Kohorten-Grösse — die EFFEKTIVE, nicht die nominelle Anzahl unabhängiger Hypothesen zählt (Bailey/López de Prado-Voraussetzung). Ein CSCV-Split auf einer meist-flachen (Cash-dominierten) Bar-Return-Serie MUSS eine risikoadjustierte, nicht eine rohe Mittelwert-Metrik verwenden.
**Betroffen:** `automation/optimizer/confirm.py` (`_study_pbo`, `_group_split_metric`), `automation/optimizer/cpcv.py` (`cluster_effective_configs`), `automation/config/tournament.json` (`pbo_cluster_threshold`), `automation/tests/test_issue_683_pbo_effective_n.py`, `automation/tests/test_issue_663_pbo_group_granularity.py` (mehrere Fixtures korrigiert, die unbeabsichtigt hochkorrelierte/identische Zeilen verwendeten).

### 🟢 Pitfall #186 — Ein kostenrelatives Gate mit einem unabhängig gepflegten Fallback-Wert ist eine stumme Diskontinuität [BEHOBEN: GH-#684]
**Symptom:** `oos_min_expectancy_k_alpha=0.25` sollte ein Gate von `k_alpha·c_rt ≈ 7.5e-5` (c_rt=3bps) erzeugen — fehlte aber die Kosten-Telemetrie (`round_trip_cost_bps` nicht gestempelt), sprang das Gate STUMM auf das statische `oos_min_expectancy=0.001` — ein ~13× strengeres Niveau.
**Root-Cause:** Zwei unabhängig gepflegte Schwellen für dieselbe Grösse — die kostenrelative UND die statische Legacy-Konstante teilen KEINE gemeinsame Quelle, obwohl beide "die Expectancy-Mindestschwelle" bedeuten sollen.
**Fix/Regel:** `backtest_runner._read_default_round_trip_cost_bps()` leitet einen Fallback-Kostenwert AUS DEMSELBEN Kostenmodell ab (`backtest.json`: `commission_bps + spread_bps_by_asset_class['DEFAULT']`), das auch `round_trip_cost_bps` speist — bei gesetztem `k_alpha`, aber fehlender Telemetrie, wird JETZT `k_alpha · default_c_rt` statt der 13×-strengeren Konstante verwendet. `expectancy_gate_cost_source ∈ {'telemetry', 'config_default', 'static'}` macht die tatsächlich verwendete Quelle telemetrisch nachvollziehbar. Nur wirksam, wenn `k_alpha` bereits konfiguriert ist (Zero-Hardcoding: ohne `k_alpha` bleibt das reine Legacy-Gate maßgeblich, unverändert).
**Invariante:** Ein kostenrelatives Gate hat GENAU EINE Kostenquelle — Live-Telemetrie ODER ein aus DEMSELBEN Kostenmodell abgeleiteter Default — NIE eine zweite, unabhängig geratene Konstante, die um Grössenordnungen abweichen kann.
**Betroffen:** `automation/backtest_runner.py` (`_evaluate_oos_eligibility`, `_read_default_round_trip_cost_bps`), `automation/config/tournament.json` (`oos_min_expectancy_k_alpha`-Doku), `automation/tests/test_issue_684_expectancy_gate_fallback.py`, `automation/tests/test_issue_563_cost_relative_gate.py` (Fixture auf die korrigierte Fallback-Semantik aktualisiert).

### 🟢 Pitfall #187 — Ein Config-Key, der nach einer Migration nur noch Legacy-Fallback ist, MUSS als solcher gekennzeichnet sein [BEHOBEN: GH-#685]
**Symptom:** `deflation_var_floor=0.0018` verblieb im Config mit einer Beschreibung, die einen aktiven, konstanten Floor suggeriert — seit der Lo-2002-Migration (Pitfall #159) ist er aber NUR NOCH ein Fallback für den seltenen Fall, dass `n_periods` dem Aufrufer unbekannt ist. Genau diese Fehldeutung ("der Wert ist aktiv") war bereits die Root-Cause von Pitfall #174.
**Root-Cause:** Eine technisch korrekte, aber dicht formulierte Schema-Beschreibung ohne ein UNMISSVERSTÄNDLICHES Deprecation-Signal am Anfang lädt bei schnellem Lesen zur Fehldeutung ein.
**Fix/Regel:** Die Schema-Beschreibung beginnt jetzt EXPLIZIT mit `⚠️ DEPRECATED (Issue #685)`, gefolgt von der Bedingung, unter der der Wert überhaupt noch konsultiert wird. Zusätzliche Code-Kommentare an ALLEN Lesestellen (`confirm.py`, `run_optimization.py`) und im `sr0_multiple_testing_robust`-Docstring selbst verhindern, dass ein Agent den Wert an irgendeiner Stelle als aktiven Mechanismus missversteht. Entfernung bleibt bewusst AUSSTEHEND, bis verifiziert ist, dass `n_periods` an JEDER Call-Site unconditional verfügbar ist (aktuell nicht der Fall — die Kohorten-Herleitung kann leer sein).
**Invariante:** Ein Legacy-Fallback-Key trägt IMMER ein unmissverständliches, an prominenter Stelle stehendes Deprecation-Signal — eine Erklärung, die nur im Fliesstext eines langen Absatzes steht, ist bei Pitfall-Klasse "Fehldeutung durch schnelles Lesen" (Pitfall #174/#159) NICHT ausreichend.
**Betroffen:** `automation/config/tournament.json` (`deflation_var_floor`-Schema), `automation/optimizer/deflation.py` (`sr0_multiple_testing_robust`-Docstring), `automation/optimizer/confirm.py`, `automation/optimizer/run_optimization.py`, `automation/tests/test_issue_685_deflation_var_floor_deprecation.py`.

### 🟢 Pitfall #188 — Drei von zwölf Katalog-Fixes ändern die gestempelte oos_eligible-Semantik; ein Bulk-Purge-Werkzeug ergänzt den Per-Study-Guard [BEHOBEN: GH-#686]
**Symptom:** Issue-Katalog #675–#685 (12 Fixes) — analog zur Pitfall-#176-Situation muss PRO Issue einzeln entschieden werden, ob der gestempelte `oos_eligible`-Wert eines Trials betroffen ist.
**Root-Cause/Klassifikation:** GENAU DREI Fixes ändern die DEFAULT-Eligibility-Entscheidung eines Trials: Pitfall #178 (`profitable_folds_frac` aus dem Default-Gate entfernt + Nenner-Bugfix), Pitfall #179 (`evaluable_folds` aus demselben Grund entfernt), Pitfall #186 (Expectancy-Gate-Fallback bei fehlender Telemetrie). Die übrigen NEUN sind entweder opt-in mit bit-identischem Default (#177/retrain, #180/Kalibrier-Tooling, #182/any_arm-Policy wirkt nur im Confirm-Schritt auf bereits gespeicherte Deltas), reine additive Telemetrie (#181), Confirm-/Cache-only ohne gestempelten Trial-Effekt (#183/#184), frisch bei jedem Confirm-Lauf neu berechnet ohne gecachten Alt-Wert (#185/PBO), oder reine Dokumentation (#187).
**Fix/Regel:** `reward_semantics_version` 11→12, mit derselben PRO-ISSUE-Begründungsdisziplin wie Pitfall #176. ZUSÄTZLICH zum bestehenden In-Process-Guard (`_check_reward_semantics_version`, purgt eine EINZELNE geladene Study fail-loud) ein neues Bulk-Werkzeug (`automation.optimizer.purge_stale_studies`, CLI mit `--dry-run`) — scannt ALLE `{WORK}/sweep/*.db`-Dateien und löscht jede mit stale gestempelter Version in EINEM Schritt, statt N Sweep-Starts nacheinander fail-loud abbrechen zu lassen.
**Invariante (Fortsetzung Pitfall #176):** Der Versions-Bump-Entscheid wird PRO Issue einzeln getroffen und im Changelog explizit begründet (bumpt/bumpt nicht + warum) — niemals pauschal für den ganzen Katalog. Ein Bulk-Purge-Werkzeug ERSETZT NIE den In-Process-Guard (beide bleiben aktiv) — es macht nur den MANUELLEN Runbook-Schritt effizienter.
**Betroffen:** `automation/config/optimizer.json` (`reward_semantics_version`, Changelog), `automation/optimizer/purge_stale_studies.py` (neu), `manuals/strategie_optimierung.md` (Kapitel 7 erweitert), `automation/tests/test_issue_686_reward_semantics_bump.py`, `automation/tests/test_issue_686_purge_stale_studies.py`.

### 🟢 Pitfall #189 — `DirectionalMovement.value` liefert konstant 0.0 in der installierten NautilusTrader-Version; `.pos`/`.neg` funktionieren [BEHOBEN: GH-#691]
**Symptom:** Der Trockenlauf von `DonchianRegimeBreakoutStrategy` (Issue #691, SPEC_03-Standard-Regime-Filter Option A: `adx.value >= adx_threshold`) erzeugte über einen echten NautilusTrader-BacktestEngine-Lauf mit persistenten, alternierenden Trendphasen konsistent 0 Trades — trotz sauber ausbrechender Donchian-Kanäle (457/960 Bars über dem 15-Bar-Hoch).
**Root-Cause:** Ein direkter Indikatortest (`DirectionalMovement(14)` gegen dieselbe Trend-Serie) bestätigte: `.value` bleibt über den gesamten Lauf konstant `0.0` (`n_initialized=947`, `value_range=(0.0, 0.0)`), während `.pos`/`.neg` reale, trend-abhängige Werte liefern (`pos∈[0, 0.81]`, `neg∈[0, 0.81]`, korrekt gegenläufig zur Trendrichtung). Ein Regime-Gate auf `adx.value` ist damit strukturell IMMER `False`. Dies ist dieselbe Klasse Defekt wie das bereits dokumentierte `AdxAtrMomentumStrategy`-„ADX-Initialisierungsproblem" (§6, Inaktive Strategien) — beide nutzen `DirectionalMovement.value` als ADX-Proxy, beide sind in dieser NautilusTrader-Version (1.230.0) tot.
**Fix/Regel:** Der SPEC selbst sah genau diesen Fall vor (Pitfall #9 des Implementierungs-Leitfadens #688: "Falls `adx.value` nie plausibel > Schwelle wird, auf Option B umschalten"). `DonchianRegimeBreakoutStrategy` nutzt daher aktiv Option B (EMA-Steigung: `ema.value > _ema_prev`), verifiziert über denselben echten Engine-Lauf: 34 Trades. Option A bleibt als auskommentierter Re-Aktivierungspunkt im Modul-Code (falls eine künftige NautilusTrader-Version `.value` korrekt berechnet). `adx_period`/`adx_threshold` wurden aus dem `spaces.py`-Suchraum entfernt (ein Config-Feld, das keinen Effekt mehr auf das Signal hat, darf nicht gesampelt werden — Phantom-Tuning, vgl. Pitfall #4 des Guides).
**Invariante:** Ein Indikator-Attribut, dessen Semantik einem SPEC/einer Doku nach "ADX" o. Ä. entsprechen soll, MUSS gegen einen echten Trend-Datensatz numerisch verifiziert werden, bevor es als Gate-Bedingung verdrahtet wird — ein plausibel benanntes `.value`-Attribut ist kein Beweis für plausible Werte. Ein Config-Feld, das durch einen solchen Fund funktional tot wird, wird SOFORT aus dem Optimizer-Suchraum entfernt (nie stillschweigend weiter gesampelt).
**Betroffen:** `automation/strategies/donchian_regime_breakout.py`, `automation/optimizer/spaces.py` (`DonchianRegimeBreakoutStrategy`-Zweig), `automation/tests/test_issue_691_donchian_regime_breakout.py`.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #675–#686)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `walk_forward.retrain` | backtest.json | *neu* (Default `false`) | Opt-in rollierende IS-Referenz je Fold, bit-identisch im Default (Pitfall #177) |
| `eligible_requires_all` | tournament.json | `oos_min_profitable_folds_frac`/`oos_min_evaluable_folds` entfernt | Redundante/anti-monotone Fold-Zähler-Gates aus dem Default (Pitfall #178/#179) |
| `oos_min_evaluable_folds` | tournament.json | Doku erweitert (relativer Modus) | Werte in `(0,1]` = Anteil an `oos_folds_evaluable` (Pitfall #179) |
| `pbo_cluster_threshold` | tournament.json | *neu* (Default 0.99) | Near-Duplicate-Reduktion vor der CSCV-Partitionierung (Pitfall #185) |
| `any_arm_unreachable_policy` | tournament.json | `'warn' → 'recalibrate'` | Bestehende Mechanik jetzt tatsächlich aktiv (Pitfall #182) |
| `deflation_var_floor` | tournament.json | Deprecation-Marker ergänzt | Unmissverständliche Legacy-Fallback-Kennzeichnung (Pitfall #187) |
| `reward_semantics_version` | optimizer.json | `11 → 12` | Eligibility-Bump durch #676/#677/#684 (Pitfall #188) |

### 🔒 Watertight Invariants (Issue-Katalog #675–#686) — für künftige Agenten
- **Eine wörtliche Issue-Formel, die eine bestehende Sicherheits-Invariante verletzen würde, wird NIE blind übernommen** — Korrektur + Dokumentation der Abweichung hat Vorrang vor Wortlaut-Treue (Pitfall #177).
- **Ein Fraktions-Nenner zählt NIE eine Kategorie, die den Zähler strukturell nie erreichen kann** (Pitfall #178).
- **Ein Fold-Zähler-Gate dupliziert NIE eine Mindest-Stichproben-Garantie, die bereits an anderer Stelle gesichert ist** (Pitfall #179).
- **Eine Issue-Hypothese für einen Default-Wechsel wird gegen die TATSÄCHLICHEN Parameter des Symptoms verifiziert — "kein Wechsel, erneut bestätigt" ist ein ebenso gültiges Ergebnis** (Pitfall #180, Fortsetzung #171/#165).
- **Eine Diagnose ohne strukturierte Auswertung bleibt folgenlos, egal wie oft sie loggt** (Pitfall #181).
- **Eine implementierte, aber nie konfigurierte Policy-Mechanik liefert keinen Produktionswert** (Pitfall #182).
- **Ein Budget-sparender Automatismus und eine permanente Governance-Entscheidung sind ZWEI getrennte Datenspeicher — ein Prozess mutiert NIE die versionierte Policy-Datei direkt** (Pitfall #183).
- **Ein Confirm-Pfad prüft IMMER zuerst bereits verfügbare Daten auf eine legitime Route, bevor er mangels Symbol-spezifischer Trials lautlos ablehnt** (Pitfall #184).
- **PBO/CSCV misst NIE auf einer durch Near-Duplicate-Configs künstlich aufgeblähten Kohorten-Grösse** (Pitfall #185).
- **Ein kostenrelatives Gate hat GENAU EINE Kostenquelle, nie eine zweite, unabhängig geratene Konstante** (Pitfall #186).
- **Ein Legacy-Fallback-Key trägt IMMER ein unmissverständliches Deprecation-Signal an prominenter Stelle** (Pitfall #187, Fortsetzung #174).
- **Der Versions-Bump-Entscheid wird PRO Issue einzeln getroffen und explizit begründet; ein Bulk-Purge-Werkzeug ersetzt nie den In-Process-Guard** (Pitfall #188, Fortsetzung #176).

## Issue-Katalog #695–#702 — DSR-Familien-Decluster, Gate-Konsolidierung & Purge-Klassifikation (2026-07-18)

Acht verzahnte Fixes, `reward_semantics_version` 12→13. `deflation_family_period_returns` declustert die
DSR-Familien-Multiplizität via `cpcv.cluster_effective_configs` — dieselbe near-Duplicate-Reduktion, die
PBO seit Pitfall #185 nutzt (#695/Pitfall #190); `deflation_n_effective` trug zuvor fälschlich die ROHE
statt der effektiven Zahl (#696/Pitfall #191); ein strukturierter Kollinearitäts-Alarm blieb erneut ohne
Fail-Loud-Konsumenten (#697/Pitfall #192 — **einziger** Fix dieses Katalogs mit gestempelter
Eligibility-Wirkung, daher der alleinige Auslöser des Versions-Bumps); ein Gap-Signal auf kontinuierlichen
24/7-Bars mass keinen echten Handelspausen-Gap (#698/Pitfall #193); zwei unabhängige Strategie-Code-Defekte
erzeugten strukturell 0 Round-Trips (#699/Pitfall #194); ein gemischter Evaluierbarkeits-Cohort fiel durch
beide Early-Stop-Netze (#700/Pitfall #195); ein als DEPRECATED markierter Fallback-Key wurde nach
Verifikation vollständig entfernt (#701/Pitfall #196); und der Katalog schliesst mit einer expliziten
Purge-Klassifikationstabelle, die künftigen Agenten die reward_semantics_version-Frage abnimmt
(#702/Pitfall #197). Pitfalls #190–#197, Issues #695–#702.

### 🟢 Pitfall #190 — Die familienweite Multiple-Testing-Multiplizität zählte near-identische Configs als unabhängige Schüsse aufs Tor [BEHOBEN: GH-#695]
**Symptom:** `deflation_n_family` (Pitfall-Fortsetzung #652) summiert die Anzahl eligibler Trials über ALLE Strategien-Studies eines Symbols ROH — ein dichtes Optuna-Suchraum-Grid liefert dabei near-identische Parametrisierungen mit near-identischen `oos_period_returns`-Serien, die je als ein EIGENER unabhängiger "Schuss aufs Tor" in `E[max_N]` (Bailey/López de Prado) gezählt wurden.
**Root-Cause:** Dieselbe Root-Cause-Klasse wie Pitfall #185 (PBO/CSCV) — nur auf der DSR-Familien-Ebene: die NOMINELLE Trial-Zahl über mehrere Studies ist strukturell grösser als die Zahl EFFEKTIV unabhängiger Hypothesen, weil ein dichtes Grid dieselbe Parametrisierung viele Male in geringfügigen Variationen testet.
**Fix/Regel:** `deflation_family_period_returns` (je eligiblem Trial ALLER Studies des Symbols dessen `oos_period_returns`, via `sweep._family_period_returns_from_studies`) wird VOR der SR₀-Berechnung mit `cpcv.cluster_effective_configs` (dieselbe Pearson-Korrelations-Schwelle `pbo_cluster_threshold`, PBO-Pfad-parallel) reduziert ⇒ `deflation_n_family_effective`. Die #652-Invariante bleibt gewahrt: `deflation_n_effective = max(deflation_n, deflation_n_family_effective)` unterschreitet NIE das lokal bekannte per-Study-N. Fehlt die Liste (Legacy-Aufrufer/Unit-Tests, die nur den Skalar `deflation_n_family` kennen) ⇒ `deflation_n_family_effective == deflation_n_family_raw` (kein Clustering möglich, bit-identisch zum Pre-#695-Verhalten).
**Invariante:** Eine multiple-testing-Korrektur (DSR-Familien-N ebenso wie PBO/CSCV) zählt NIE near-identische Configs als unabhängige Hypothesen — die EFFEKTIVE, nicht die nominelle Anzahl ist massgeblich (Fortsetzung Pitfall #185, dieselbe Bailey/López de Prado-Voraussetzung, jetzt auch auf der DSR-Seite konsequent angewendet).
**Betroffen:** `automation/optimizer/confirm.py` (`confirm_per_symbol_promotion`), `automation/optimizer/sweep.py` (`_family_period_returns_from_studies`), `automation/tests/test_issue_695_dsr_family_decluster.py`.

### 🟢 Pitfall #191 — Ein Telemetrie-Feld namens `deflation_n_effective` trug bislang den ROHEN, nicht den effektiven Wert [BEHOBEN: GH-#696]
**Symptom:** `deflation_n_effective` (seit Pitfall #190/#652) suggeriert per Namen bereits eine declusterte/effektive Familienzahl — trug VOR #695 aber die ROHE, un-declusterte Summe (`max(deflation_n, deflation_n_family_raw)`).
**Root-Cause:** Dieselbe Fehldeutungsklasse wie `deflation_used_var_floor` (Pitfall #174/#670): ein Telemetrie-Name mit einem Qualitäts-Adjektiv ("effektiv") wurde geprägt, BEVOR die zugrundeliegende Mechanik (Decluster, #695) existierte — der Name lief dem tatsächlichen Verhalten voraus und verleitete einen Operator, der nur den Namen liest, systematisch zur Fehlinterpretation ("das ist bereits die declusterte Zahl").
**Fix/Regel:** Seit #695 speist die TATSÄCHLICH declusterte Zahl `deflation_n_effective` — der Name ist jetzt korrekt. Zusätzlich zwei neue, PBO-Pfad-parallele, EXPLIZITE Felder (`deflation_n_family_raw`, `deflation_n_family_effective` — Analogon zu `pbo_n_configs_raw`/`pbo_n_configs`) im Winner-Proposal machen roh vs. declustert für einen Operator unabhängig vom (jetzt korrigierten) Legacy-Namen auditierbar.
**Invariante:** Ein Telemetrie-Feldname mit einem Qualitäts-Adjektiv ("effective", "final", "resolved") trägt IMMER den tatsächlich qualifizierten Wert; bis die zugrundeliegende Mechanik existiert, bleibt entweder der Rohwert ODER ein neutraler Name maßgeblich — NIE ein Name, der einer künftigen Fähigkeit vorgreift (Fortsetzung Pitfall #174).
**Betroffen:** `automation/optimizer/confirm.py` (Winner-Telemetrie: `deflation_n_family_raw`, `deflation_n_family_effective`), `automation/tests/test_issue_695_dsr_family_decluster.py`.

### 🟢 Pitfall #192 — Ein strukturierter Kollinearitäts-ALARM ohne Fail-Loud-Konsument blieb erneut folgenlos [BEHOBEN: GH-#697]
**Symptom:** `eligible_requires_all` behielt `min_expectancy` UND `oos_min_psr` trotz dokumentierter |ρ|=0.961-Kollinearität (der #679-Redundanz-Alarm, Pitfall #181, wies genau das aus) — der Alarm existierte, wurde aber nirgends gegen die tatsächliche Config geprüft.
**Root-Cause:** Pitfall #181 löste "die Diagnose loggt nur" — ersetzte es aber durch ein strukturiertes Feld OHNE einen Fail-Loud-KONSUMENTEN, der die Alarm-Ausgabe aktiv gegen `eligible_requires_all` abgleicht. Ein strukturiertes Signal ohne Konsument ist funktional identisch zu seiner Abwesenheit (Fortsetzung Pitfall #182 — "existiert" ≠ "wirkt").
**Fix/Regel:** `min_expectancy` wurde als Root-Cause-Fix aus `eligible_requires_all` entfernt (bleibt als WEICHE Near-Miss-Distanz über `reward._normalized_gate_distances` erhalten — superseded damit auch den #657-Zwischenstand, siehe `test_issue_657_return_expectancy_collinearity.py`). `oos_min_psr` (risikoadjustiert, skalenfrei) ist seither das ALLEINIGE harte Netto-Edge-Gate — es existiert kein absolutes Return-/Expectancy-Gate mehr. ZUSÄTZLICH ein Wächter gegen Regression: `reward.assert_eligible_requires_all_not_redundant` (neu) prüft bei jedem Confirm-Lauf `oos_gate_deltas` der LIVE-Kohorte gegen `eligible_requires_all` und loggt ERROR (`gate_collinearity_unconsolidated` im Winner-Proposal), falls ein Operator künftig versehentlich ein als redundant ausgewiesenes Gate reaktiviert.
**Invariante:** Ein strukturierter Diagnose-/Alarm-Mechanismus ist erst dann wirksam, wenn ein FAIL-LOUD-Konsument existiert, der ihn aktiv gegen den aktuellen Zustand prüft — "die Diagnose feuert korrekt" und "die Diagnose wird ausgewertet" sind zwei verschiedene Aussagen (Fortsetzung Pitfall #181/#182).
**Betroffen:** `automation/optimizer/reward.py` (`assert_eligible_requires_all_not_redundant`, `_GATE_COLLINEARITY_TO_CONJUNCTION_KEY`), `automation/optimizer/confirm.py` (`gate_collinearity_unconsolidated`), `automation/config/tournament.json` (`eligible_requires_all`), `automation/optimizer/calibration.py` (`calibrate_gate_consolidation_false_positive_rate`, Monte-Carlo-Verifikation der FP-Rate), `automation/tests/test_issue_697_gate_consolidation.py`, `automation/tests/test_issue_657_return_expectancy_collinearity.py` (aktualisiert).

### 🟢 Pitfall #193 — Ein Gap-Signal auf kontinuierlichen 24/7-Bars misst die Differenz zweier aufeinanderfolgender Bars, keinen echten Handelspausen-Gap [BEHOBEN: GH-#698]
**Symptom:** `GapContinuationStrategy` (#693, Vortagsschluss-vs-Tagesbeginn-Gap) lief strukturell ohne Edge — die Strategie erwartet echte Handelspausen zwischen Kalendertagen (Overnight-/Wochenend-Gap), das System liefert aber ausschliesslich synthetische, KONTINUIERLICHE 24/7-1h-Bars (kein RTH-Session-Katalog).
**Root-Cause:** Ohne echte Handelspausen degeneriert der "Gap" zur Differenz zweier UNMITTELBAR AUFEINANDERFOLGENDER Bars — das trägt keinerlei Continuation-Edge, ist aber kein Bounds-Kalibrierungsproblem: kein Suchraum-Weiten behebt eine strukturell ungültige Messung, die Entscheidung ist an der BAR-SEMANTIK des Systems festzumachen (SPEC_05/#693-Caveat), nicht am Backtest-Ergebnis.
**Fix/Regel:** Neues deklaratives `strategies.json`-Feld `invalid_on_continuous_bars: true` (Zero-Hardcoding — keine Python-Konstante) markiert `GapContinuationStrategy`. `sweep_diagnostics.load_continuous_bar_invalid_strategies()` liest die Liste; `sweep.enumerate_tunable_pairs` überspringt eine gelistete Strategie VOLLSTÄNDIG (alle Symbole, EIN strukturiertes `SKIPPED_INVALID_ON_CONTINUOUS_BARS`-Event) statt N nutzlose Trials über alle Symbole zu verbrennen. Fehlt der Key (Default) ⇒ die Strategie läuft unverändert (bit-identisch zum Pre-#698-Verhalten). Eine RTH-session-bewusste Variante B (`session_open_hour`) bliebe der Re-Aktivierungspfad, sollte künftig ein Session-Kalender verfügbar werden.
**Invariante:** Ein Signal, dessen ökonomische Bedeutung eine Dateneigenschaft voraussetzt, die die Datenquelle des Systems strukturell NICHT liefert (hier: echte Handelspausen), wird NIE durch Bounds-Kalibrierung "repariert" — die Entscheidung gehört auf die Bar-/Daten-Semantik-Ebene, deklarativ und Zero-Hardcoding (Fortsetzung der Denylist-/Invalid-Flag-Disziplin aus Pitfall #183/#189).
**Betroffen:** `automation/optimizer/sweep_diagnostics.py` (`load_continuous_bar_invalid_strategies`), `automation/optimizer/sweep.py` (`enumerate_tunable_pairs`), `automation/config/strategies.json` (`invalid_on_continuous_bars`), `automation/tests/test_issue_698_gap_continuation_bar_validity.py`.

### 🟢 Pitfall #194 — Ein toter ADX-Gate UND ein fehlender Exit-Vertrag verursachten strukturell 0 Round-Trips in ZWEI unabhängigen Strategien [BEHOBEN: GH-#699]
**Symptom:** `AdxAtrMomentumStrategy` und `TrendPullbackStrategy` zeigten beide strukturelle Trockenläufe (0 bzw. 0 realisierte FIFO-Schliessungen) — die #699-Diagnose-Hypothese ("Suchraum-Bounds zu eng kalibriert") war für BEIDE Strategien falsch; die tatsächlichen Root-Causes lagen im Strategie-CODE, nicht im Suchraum.
**Root-Cause (zweigeteilt, zwei unabhängige Strategien):** (1) `AdxAtrMomentumStrategy` gatete auf `adx.value > 25` — `DirectionalMovement.value` liefert in der installierten NautilusTrader-Version (1.230.0) konstant `0.0` (dieselbe, bereits für `DonchianRegimeBreakoutStrategy` dokumentierte Klasse Defekt, Pitfall #189/#691) ⇒ das Gate war strukturell IMMER `False`. (2) `TrendPullbackStrategy.on_bar()` rief entgegen dem verbindlichen `HourlyStrategyBase`-Vertrag NIEMALS `self._check_exits_and_update(bar)` auf ⇒ WEDER ATR-Trailing-Stop NOCH `max_bars_in_trade`-Time-Exit; eine offene Position schloss nur bei einem seltenen GEGENLÄUFIGEN Entry-Signal (200-Bar-Trendfilter) ⇒ 0 realisierte Round-Trips im OOS-Fenster, unabhängig von den Bounds.
**Fix/Regel:** `AdxAtrMomentumStrategy` ersetzt den toten ADX-Gate durch dieselbe EMA-Steigungs-Bestätigung ("Option B", `self._ema_prev`-Tracking) wie `DonchianRegimeBreakoutStrategy` (Pitfall #189); `adx_period` aus dem `spaces.py`-Suchraum entfernt (Phantom-Tuning-Vermeidung, dieselbe Regel wie Pitfall #189). `TrendPullbackStrategy.on_bar()` ruft jetzt `self._check_exits_and_update(bar)` als ERSTE Anweisung (der Standard-Aufruf jeder anderen Strategie im Modul); `on_position_closed()` setzt zusätzlich `current_signal = None` zurück (verhindert einen Flat-Lock, wenn ein Trailing-Stop-Exit ausserhalb der `_on_buy_signal`/`_on_sell_signal`-Pfade feuert). ZUSÄTZLICH schliesst `sweep_diagnostics.recommend_diagnosis_action`s neuer `previously_recommended_override`-Parameter die verbleibende #681-Closed-Loop-Lücke: eine `'search_space_override'`-Empfehlung, die sich über mehrere Läufe wiederholt (Override existiert weiterhin nicht, Ursache bleibt dieselbe), eskaliert beim ZWEITEN Mal auf `'denylist'` — die Override-Chance wird genau EINMAL gewährt.
**Invariante:** Eine Diagnose-Hypothese ("Bounds-Kalibrierungsproblem") wird IMMER gegen den tatsächlichen Strategie-Code verifiziert, bevor Suchraum-Bounds angepasst oder ein Paar denylisted wird — ein struktureller Code-Defekt (totes Gate, fehlender Exit-Vertrag) ist KEIN Kalibrierungsproblem, egal wie das Symptom (0 Trades) oberflächlich aussieht (Fortsetzung Pitfall #189). Ein Suchraum-Override-Vorschlag, der sich über Läufe hinweg identisch wiederholt, MUSS irgendwann eskalieren (Fortsetzung Pitfall #183/#681).
**Betroffen:** `automation/strategies/adx_atr_momentum.py`, `automation/strategies/trend_pullback.py`, `automation/optimizer/spaces.py` (`AdxAtrMomentumStrategy`-Zweig), `automation/optimizer/sweep_diagnostics.py` (`recommend_diagnosis_action`), `automation/optimizer/run_optimization.py` (`floor_plateau_callback`-Wiring), `automation/config/strategies.json` (`_note`-Felder aktualisiert), `automation/tests/test_issue_699_adxatr_trendpullback_closed_loop.py`.

### 🟢 Pitfall #195 — Ein gemischter Evaluierbarkeits-Cohort fiel durch BEIDE Early-Stop-Netze [BEHOBEN: GH-#700]
**Symptom:** `SqueezeBreakoutStrategy` (hohe Signalfrequenz, Trade-Cap-Treffer bei einem Teil der Trials) verbrannte das VOLLE 180-Trial-Budget trotz struktureller Null-Eligibilität, während `GapContinuationStrategy` (zufällig ein HOMOGENER 0-evaluable-Cohort) korrekt nach 16 Trials stoppte — dieselbe strukturelle Null-Eligibilität wurde je nach Cohort-Homogenität unterschiedlich behandelt.
**Root-Cause:** Der `ZERO_ELIGIBLE_PLATEAU`-Zweig (Pitfall #163, GH-#656) verlangte STRIKT `all(evaluated_flags is True)` — ein GEMISCHTER Cohort (einige Trials evaluable, einige nicht, z. B. durch Trade-Cap-Treffer) fiel dadurch durch BEIDE Netze: weder der `STRUCTURAL_ALL_UNEVALUABLE`-Guard oben (der verlangt "KEIN Trial evaluable") noch dieser Zweig (der verlangt "ALLE Trials evaluable") griff.
**Fix/Regel:** Die Bedingung ist jetzt ausschliesslich `p_eligible(evaluierte Trials) == 0` — unabhängig davon, ob ALLE oder nur ein TEIL der Trials evaluiert wurden (`eligible_flags_of_evaluated` gefiltert auf `oos_evaluated is True`; feuert bei `any(f is not None) and all(f is not True)`). Eine neue, reine `sweep_diagnostics.eligibility_curve(trials, window=16)`-Funktion liefert zusätzlich die per-16-Trial-Fenster-`p_eligible`-Kurve (`p_eligible_windows`-Telemetrie im `ZERO_ELIGIBLE_PLATEAU`-Event) — unterscheidet TRANSIENTE (irgendwo zwischenzeitlich eligible Trials) von PERMANENTER (jedes Fenster 0.0) Null-Eligibilität.
**Invariante:** Zwei sich ergänzende Early-Stop-Guards dürfen NIE eine Lücke zwischen "keiner" und "alle" offenlassen — ein gemischter Cohort (teilweise evaluable) MUSS von GENAU EINEM der beiden Zweige erfasst werden, nie von keinem. Bestehende Fixtures mit genau EINEM evaluierten Trial ohne gestempelten `oos_eligible`-Wert (`None` statt `True`/`False`) lösen den neuen Zweig NICHT versehentlich aus (`any(f is not None)` bleibt `False`) — verifiziert gegen `test_issue_413_floor_guard_v3.py`/`test_issue_449_oos_coverage.py`.
**Betroffen:** `automation/optimizer/run_optimization.py` (`floor_plateau_callback`, `ZERO_ELIGIBLE_PLATEAU`-Zweig), `automation/optimizer/sweep_diagnostics.py` (`eligibility_curve`), `automation/tests/test_issue_700_squeeze_early_stop_gap.py`.

### 🟢 Pitfall #196 — Ein als DEPRECATED markierter Fallback-Key wurde nach Verifikation der Unerreichbarkeit vollständig entfernt [BEHOBEN: GH-#701]
**Symptom:** `deflation_var_floor` (seit Pitfall #187/#685 mit einem `⚠️ DEPRECATED`-Marker versehen) blieb als toter Config-Key samt Schema-Eintrag bestehen — ein künftiger Agent könnte den Marker übersehen und den Wert erneut als aktiven Mechanismus missverstehen (genau das #174/#159-Fehldeutungsmuster).
**Root-Cause:** Pitfall #187 hatte die Entfernung selbst bewusst aufgeschoben, bis verifiziert ist, dass `n_periods` an JEDER Call-Site von `sr0_multiple_testing_robust` unconditional verfügbar ist. Eine Nachverfolgung aller Aufrufer (`confirm.py`, `run_optimization.py`) bestätigte: die Kohorten-Herleitung von `n_periods` (`deflation_t_periods = median(cohort_n_periods)`) ist bei `deflation_n >= 2` (der einzigen Bedingung, unter der `sr0_multiple_testing_robust` überhaupt aufgerufen wird) laut Invariante NIE leer — der `var_floor`-Fallback-Zweig ist damit nachweislich toter Code.
**Fix/Regel:** `sr0_multiple_testing_robust` verliert den `var_floor`-Parameter vollständig; `n_periods` wird zu einem PFLICHT-Keyword-Argument (`ValueError` bei `None`/`≤1`, statt eines stillen Fallbacks). `theoretical_var_source` ist seither IMMER `'lo2002'` (kein `'var_floor'`-Zweig mehr). Der Config-Key + Schema-Eintrag wurden aus `tournament.json` vollständig entfernt (kein Deprecation-Marker mehr nötig, da der Wert gar nicht mehr existiert). Der DSR-Drop-Rejection-Pfad (`confirm.py`) bleibt konservativ: erreicht `deflation_t_periods` TROTZDEM (contra Invariante) `None`, wird kein Crash ausgelöst, sondern die DSR bleibt für diesen Lauf unberechnet UND die Rejection-Prüfung greift weiterhin (`deflation_dsr is None` ⇒ HOLD) — ein defensiver Log-Pfad, kein zweiter Fallback-Wert.
**Invariante:** Ein als tot verifizierter Fallback-Zweig wird ENTFERNT, nicht auf Dauer als Deprecation-Marker mitgeschleppt — ein Deprecation-Marker (Pitfall #187) ist ein ÜBERGANGSZUSTAND bis zur Verifikation, kein Endzustand. Die Entfernung eines Fallback-Werts darf NIE einen Rejection-Pfad stillschweigend durchlässiger machen, wenn die verifizierte Invariante wider Erwarten doch bricht (fail-safe: `None` ⇒ konservative Ablehnung, kein stiller Promotion-Pass).
**Betroffen:** `automation/optimizer/deflation.py` (`sr0_multiple_testing_robust`), `automation/optimizer/confirm.py`, `automation/optimizer/run_optimization.py`, `automation/optimizer/calibration.py` (`var_floor`-Parameter entfernt), `automation/config/tournament.json` (`deflation_var_floor` entfernt), `automation/tests/test_issue_701_var_floor_removal.py`, `automation/tests/test_issue_670_dsr_message_variance_source.py`/`test_issue_685_deflation_var_floor_deprecation.py` (aktualisiert), `automation/tests/test_issue_651_dsr_sr0_consistency.py`/`test_issue_653_variance_floor_continuity.py`/`test_issue_576_holdout_robustness.py` (Fixtures korrigiert).

### 🟢 Pitfall #197 — Ein Fix-Katalog ohne explizite Purge-Klassifikation zwingt jeden Agenten, die reward_semantics_version-Frage erneut von Grund auf zu recherchieren [BEHOBEN: GH-#702]
**Symptom:** Nach Abschluss eines Fix-Katalogs (hier #695–#701) war nicht an EINER Stelle dokumentiert, WELCHE der sieben Fixes tatsächlich die gestempelte `oos_eligible`/Reward-Semantik verändern (⇒ Versions-Bump + SQLite-Purge nötig) und welche purge-frei sind — dieselbe Klassifikationsarbeit wie Pitfall #176/#188, aber ohne den dort etablierten Präzedenzfall an prominenter Stelle fortzuführen.
**Root-Cause:** Eine PRO-Issue-Klassifikation (bumpt/bumpt nicht + Begründung) existiert bereits als etablierte Disziplin (Pitfall #176, #188) — ohne sie für JEDEN neuen Katalog explizit zu wiederholen, müsste ein Agent bei jedem Re-Run erneut durch den kompletten Diff aller Issues gehen, um zu entscheiden, ob ein Purge nötig ist (Kapitel 7 des Runbooks beschreibt NUR die generische Regel, nicht die konkrete Klassifikation je Katalog).
**Fix/Regel:** GENAU EIN Fix des Katalogs #695–#701 verändert die gestempelte Default-Eligibility-Entscheidung: **#697** (`min_expectancy` aus `eligible_requires_all` entfernt — ein Trial, der zuvor am `min_expectancy`-Gate scheiterte, kann jetzt eligible sein, wenn `oos_min_psr` erfüllt ist). Die übrigen SECHS sind purge-frei: #695/#696 (Confirm-/Telemetrie-only, kein gestempelter Trial-Wert betroffen — die Decluster-/Namensgebungs-Korrektur wirkt erst NACH dem Trial, in der Promotion-Selektion), #698 (überspringt eine Strategie VOR der Enumeration, keine Trial-Semantik geändert), #699 (Strategie-Code-Fixes wirken auf zukünftige Backtests, ändern aber keine BEREITS gestempelte `oos_eligible`-Bewertung rückwirkend), #700 (reine Early-Stop-Diagnose/Observability, kein Gate-Codepfad geändert), #701 (Entfernung eines bereits TOTEN Fallback-Zweigs — kein je erreichter Trial nutzte ihn). `reward_semantics_version` wurde entsprechend nur EINMAL gebumpt (12→13, für #697). Kapitel 7 (`manuals/strategie_optimierung.md`) trägt jetzt eine katalogspezifische Klassifikationstabelle als Präzedenzfall für künftige Kataloge.
**Invariante:** Jeder Fix-Katalog dokumentiert VOR dem nächsten Re-Run explizit, PRO Issue, ob die gestempelte Eligibility-/Reward-Semantik betroffen ist — niemals eine Pauschal-Aussage für den ganzen Katalog (Fortsetzung Pitfall #176/#188). Ein Purge-Bedarf wird IMMER pro Issue begründet, nicht durch Analogieschluss von einem ähnlich klingenden Fix.
**Betroffen:** `automation/config/optimizer.json` (`reward_semantics_version`, Changelog), `manuals/strategie_optimierung.md` (Kapitel 7), `automation/AGENTS.md`.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #695–#702)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `eligible_requires_all` | tournament.json | `min_expectancy` entfernt | Kollinear zu `oos_min_psr` (\|ρ\|=0.961) — kein absolutes Return-/Expectancy-Gate mehr (Pitfall #192) |
| `deflation_var_floor` | tournament.json | Key + Schema-Eintrag vollständig entfernt | Verifiziert toter Fallback-Zweig (Pitfall #196, Fortsetzung #187) |
| `invalid_on_continuous_bars` | strategies.json | *neu* (bool, fehlt = false) | Deklarative 24/7-Bar-Ungültigkeits-Markierung, überspringt die Strategie vollständig (Pitfall #193) |
| `reward_semantics_version` | optimizer.json | `12 → 13` | Eligibility-Bump ausschliesslich durch #697 (Pitfall #197) |
| `pbo_cluster_threshold` | tournament.json | wiederverwendet (kein neuer Key) | Dieselbe Schwelle jetzt auch für die DSR-Familien-Decluster (Pitfall #190) |

### 🔒 Watertight Invariants (Issue-Katalog #695–#702) — für künftige Agenten
- **Eine multiple-testing-Korrektur zählt NIE near-identische Configs als unabhängige Hypothesen — auf der DSR-Familien-Ebene ebenso wie auf der PBO/CSCV-Ebene** (Pitfall #190, Fortsetzung #185).
- **Ein Telemetrie-Feldname mit einem Qualitäts-Adjektiv trägt IMMER den tatsächlich qualifizierten Wert** (Pitfall #191, Fortsetzung #174).
- **Ein strukturierter Diagnose-/Alarm-Mechanismus ohne Fail-Loud-Konsumenten ist funktional identisch zu seiner Abwesenheit** (Pitfall #192, Fortsetzung #181/#182).
- **Ein Signal, dessen ökonomische Bedeutung eine Dateneigenschaft voraussetzt, die die Datenquelle strukturell nicht liefert, wird NIE durch Bounds-Kalibrierung "repariert" — die Entscheidung gehört auf die Bar-/Daten-Semantik-Ebene** (Pitfall #193).
- **Eine Diagnose-Hypothese wird IMMER gegen den tatsächlichen Strategie-Code verifiziert, bevor Bounds angepasst oder ein Paar denylisted wird — ein struktureller Code-Defekt ist kein Kalibrierungsproblem** (Pitfall #194, Fortsetzung #189).
- **Zwei sich ergänzende Early-Stop-Guards dürfen NIE eine Lücke zwischen "keiner" und "alle" offenlassen** (Pitfall #195).
- **Ein Deprecation-Marker ist ein Übergangszustand bis zur Verifikation, kein Endzustand — ein verifiziert toter Fallback-Zweig wird entfernt, nicht auf Dauer mitgeschleppt** (Pitfall #196, Fortsetzung #187).
- **Jeder Fix-Katalog dokumentiert PRO Issue explizit, ob die gestempelte Eligibility-/Reward-Semantik betroffen ist — niemals eine Pauschal-Aussage für den ganzen Katalog** (Pitfall #197, Fortsetzung #176/#188).

### Optimizer Guardrails: Strategy Space Parity (Strict Constraint)
- **Rule:** Jede Strategie, die im Target-File `config/strategies.json` das Flag `"active": true` aufweist, MUSS zwingend eine korrespondierende Hyperparameter-Mapping-Definition in `automation/optimizer/spaces.py` (Function: `sample_params`) besitzen.
- **Violation:** Abweichungen resultieren im Fatal Error `STRATEGY_NO_SEARCH_SPACE` (Referenz: Issue #595) und erzwingen einen Immediate Exit der Bootstrapping-Phase.
- **Zero-Hardcoding Policy:** Optimizer-Search-Spaces (Dictionaries) dürfen ausschliesslich über Optuna-Sampling-Methoden (`trial.suggest_*`) definiert werden. Statische Parameter-Zuweisungen im Optimizer-Scope sind verboten. Sämtliche vererbten Management-Parameter der `HourlyStrategyBase` müssen ebenfalls dynamisch abgebildet werden.

## Epic #702 (Issues #703–#710) — Iterativer Champion-Warm-Start & symbol-skopierte Default-Nachführung

Der Per-Symbol-Pfad hatte bereits seit #565 einen Warm-Start + Shrinkage-Anker
(`resolve_symbol_shrinkage_seed`), aber zwei Lücken verhinderten „an bestem Punkt anknüpfen" und
„Defaults nachziehen": (A) `load_global_best` akzeptierte einen Seed AUSSCHLIESSLICH bei
`status=="READY_FOR_PR"` — bei 0 Promotions blieb `seed_source` dauerhaft `strategy_defaults`,
die tatsächlich entdeckten (aber noch nicht promoteten) Optima wurden zwischen Sweep-Läufen
verworfen; (B) es gab keinen Speicher für symbol-getunte Kandidaten ausserhalb des
menschlich-freigegebenen `strategies.json`-Pfads. Das Epic öffnet diesen Pfad **kontrolliert und
integritätsneutral** über drei Ebenen (Details: `automation/optimizer/champions.py`-Modul-Docstring):

- **Ebene 1 (Such-Anker, jeder Lauf, niedriges Risiko):** ein neues `data/optimizer/champions/`
  (`champions.store_champion`, #703) persistiert den besten *erreichten* Holdout-Kandidaten je
  (Strategie, Symbol) — auch wenn er nicht promotet wurde, sofern er die Rejection-Allowlist und
  den Qualitäts-Floor besteht. `resolve_symbol_shrinkage_seed` (#704) liest ihn als neue Tier-Stufe
  ZWISCHEN `global_best` und `strategy_defaults` (`champions.load_champion_seed`); der Seed
  durchläuft beim nächsten Enqueue erneut VOLLSTÄNDIG alle Eligibility-/Holdout-/DSR-Gates auf
  frischen Daten — integritätsneutral, solange die Multiplizität via #694/#695 (`cluster_effective_
  configs`) declustert wird (§ kritische Kopplung unten). Die Guards (reward-version, override-keys,
  Rejection-Allowlist, R_symbol-Floor, Demotion-Schwelle) sind in EINER zentralen Funktion
  zusammengezogen, `champions.champion_is_admissible` (#705) — dieselbe Prüfkette gated sowohl das
  Schreiben (Store) als auch das Lesen (Seed), keine duplizierte Guard-Logik in sweep.py/
  run_optimization.py.
- **Ebene 2 (Default-Nachführung, korroboriert, mittleres Risiko):** `champions.maybe_write_back`
  (#706) schreibt einen Champion NUR nach dem neuen, leeren `automation/config/strategy_symbol_
  seeds.json` (symbol-skopiert, NIEMALS `strategy_defaults.json` — der globale Cross-Symbol-Prior),
  wenn er entweder eine echte `READY_FOR_PR`-Promotion ist ODER über `champion_promote_after_runs`
  region-gleiche Läufe (`champions._same_region`, #707) UND ein fortgeschrittenes Datenfenster
  (`champion_min_advance_days`, Snooping-Schutz) korroboriert wurde. `optimizer/resolve.py::
  resolve_params` liest die neue Datei additiv zwischen `strategy_defaults.json` und
  `strategies.json[params]` (instrument-gated, HI-2-rückwärtskompatibel).
- **Ebene 3 (Live-Deployment) bleibt UNVERÄNDERT menschlich (HI-3):** kein Teil dieses Epics
  schreibt je `strategies.json`.

**Anti-Stagnation (#708):** ein einmal starker Champion darf nicht dauerhaft ankern, wenn sich das
Regime dreht — wird eine region-gleiche Re-Evaluierung `champion_demote_after_runs`-mal in Folge
UNTER `champion_min_R_symbol` gemessen, demotet `store_champion` den Champion (Store- + Seed-Eintrag
entfernt, Fallback auf `strategy_defaults`). **Telemetrie (#709):** `optimize_symbol` stempelt bei
`seed_source=="champion"` `champion_seed_source`/`champion_R_symbol_at_store`/`champion_
corroboration_count`/`champion_age_days`/`champion_window_advanced`/`champion_writeback_applied`
als Study-User-Attrs (Log-Parität mit den `shrinkage_*`-Attrs aus #565); eine Demotion emittiert
zusätzlich ein `CHAMPION_DEMOTED`-JSON-Event. **Test-Suite (#710):**
`automation/tests/test_issue_702_champion_warmstart.py` (49 Tests) — Guard-Matrix, Store-Merge/
Korroboration/Demotion, Resolver-Tier-Reihenfolge inkl. HI-2-Rückwärtskompatibilität,
Writeback-Gate (inkl. Snooping-Schutz auf identischem Fenster), Symbol-Scope (nie
`strategy_defaults.json`), sowie eine #694-Kopplungs-Sanity (siehe unten).

### Kritische Kopplung an #694/#695

**Iterativer Warm-Start ist ohne Familien-Declustering selbst-sabotierend.** Jeder Lauf enqueued
denselben Champion erneut; der TPE clustert bei `multivariate=True, group=True` eng um ihn — über
mehrere Läufe entstehen zunehmend korrelierte Near-Duplicate-Configs. Würde die DSR-Deflation die
**rohe** Familien-Multiplizität zählen, stiege `N` bei jedem Warm-Start-Lauf, `E[max_N]` und damit
`SR₀` monoton — die DSR-Schwelle würde sich progressiv selbst zuziehen, obwohl die Kandidaten
unverändert sind (eine Rückkopplung, die exakt die Grösse verschlechtert, die Warm-Start verbessern
soll). Diese Kopplung ist bereits geschlossen: `cluster_effective_configs` (#694-Linie, seit Pitfall
#168 für PBO genutzt) reduziert seit **#695/Pitfall #190** auch die familienweite DSR-Multiplizität
auf effektiv-unabhängige Configs — near-identische Champion-Reenqueues kollabieren auf ~1 effektive
Config, die Deflation bleibt über wiederholte Warm-Start-Läufe stabil. `test_issue_702_champion_
warmstart.py::test_repeated_champion_reenqueue_declusters_to_effective_one` sichert diese
Vorbedingung ab.

### 🔵 Pitfall #201 — Warm-Start-Seed und Multiple-Testing-Deflation sind gekoppelt [ABGESICHERT: GH-#703/#695]
**Symptom (hypothetisch, ohne #694/#695 real eingetreten):** Iteratives Re-Enqueue desselben
Champions ohne Familien-Declustering lässt die DSR-Schwelle über aufeinanderfolgende Sweep-Läufe
progressiv strenger werden, obwohl die tatsächlich getesteten Kandidaten unverändert bleiben —
Promotion würde von Lauf zu Lauf schwerer, nicht weil die Strategie schlechter wird, sondern weil
die Multiple-Testing-Buchhaltung die Near-Duplicate-Population fälschlich als unabhängige Hypothesen
zählt (siehe „Kritische Kopplung" oben).
**Fix/Regel:** `champions.py` (Epic #702) wird ERST NACH #694/#695 (`cluster_effective_configs` auf
der DSR-Familien-Ebene, Pitfall #190) aktiviert — dieselbe Reihenfolge-Vorbedingung, die GitHub-Issue
#705 §9 explizit macht. Die Kopplung ist test-gesichert (siehe oben), nicht nur dokumentiert.
**Invariante:** Ein Warm-Start-Mechanismus, der denselben Kandidaten wiederholt in dieselbe Study
enqueued, MUSS über eine Familien-Declustering-Korrektur verfügen, BEVOR er aktiviert wird — sonst
zieht sich die Multiple-Testing-Schwelle selbst zu (self-sabotierende Rückkopplung).
**Betroffen:** `automation/optimizer/champions.py`, `automation/optimizer/cpcv.py` (`cluster_effective_configs`), `automation/optimizer/confirm.py` (Familien-Decluster, #695).

### 🟢 Pitfall #202 — Symbol-getunte Parameter dürfen nie automatisch in den globalen Cross-Symbol-Prior [BEHOBEN: GH-#706]
**Symptom:** Ein Champion-/Autotuning-Mechanismus, der seinen besten gefundenen Vektor direkt nach
`strategy_defaults.json` schriebe, würde JEDES Symbol mit dem für EIN Symbol optimierten Vektor
vergiften. Empirischer Beleg (GitHub-Issue #705 §2, TSLA.ETORO-Lauf 2026-07-18):
`FlashCrashReversalStrategy` war auf dem Symbol positiv (R_symbol=+0.385), global toxisch
(R_global=−2.941) — ein realer, kein hypothetischer Fall.
**Fix/Regel:** `champions.maybe_write_back` schreibt AUSSCHLIESSLICH nach dem neuen, symbol-
skopierten `automation/config/strategy_symbol_seeds.json` (`seeds[strategy][symbol]`) — niemals
nach `strategy_defaults.json`. `optimizer/resolve.py::resolve_params` liest die neue Datei additiv
zwischen `strategy_defaults.json` (Cross-Symbol-Prior, unverändert) und `strategies.json[params]`
(menschlicher PR, gewinnt immer).
**Invariante:** Ein automatisch geschriebener Symbol-Seed berührt NIE den globalen Cross-Symbol-
Default — symbol-getunte Evidenz bleibt strikt symbol-skopiert, unabhängig davon, wie stark sie ist.
**Betroffen:** `automation/optimizer/champions.py` (`maybe_write_back`, `_write_symbol_seed`), `automation/optimizer/resolve.py`, `automation/config/strategy_symbol_seeds.json` (neu).

### 🟢 Pitfall #203 — Default-Nachführung ohne Fensterfortschritt ist Datenschnüffelei, nicht Optimierung [BEHOBEN: GH-#706]
**Symptom:** Ein Writeback-Mechanismus, der einen Champion allein aufgrund wiederholter
Bestätigungen auf DEMSELBEN (oder einem nur trivial fortgeschrittenen) Datenfenster nach
`strategy_symbol_seeds.json` schreibt, deployt Parameter, die nur auf identischen Daten „bestätigt"
wurden — exakt die Daten-Schnüffelei-Klasse, gegen die die gesamte DSR-Deflation (#611–#639)
existiert.
**Fix/Regel:** `champions.maybe_write_back` verlangt für die korroborations-basierte Route (NICHT
für eine echte `READY_FOR_PR`-Promotion, die bereits vollständig validiert ist) zusätzlich zur
Korroboration (`corroboration_count >= champion_promote_after_runs`) einen Fensterfortschritt:
`catalog_newest_ns` muss um mindestens `champion_min_advance_days` (Default: `backtest.json.
walk_forward.oos_window_days`) über dem `catalog_newest_ns` der Erst-Sichtung liegen. Test-gesichert
(`test_writeback_corroborated_but_identical_window_fails_snooping_guard`).
**Invariante:** Ein korroborations-basierter Writeback verlangt IMMER Korroboration UND
Fensterfortschritt gemeinsam — Korroboration allein auf statischen Daten ist kein Beleg für
Generalisierung.
**Betroffen:** `automation/optimizer/champions.py` (`maybe_write_back`), `automation/config/optimizer.json` (`champion_min_advance_days`).

### 🟢 Pitfall #204 — Seeds nur aus Kandidaten, die den Holdout erreichten UND nicht overfit-/randlösungs-geflaggt sind [BEHOBEN: GH-#703/#705]
**Symptom:** Ein Champion-Store, der JEDEN Confirm-Ausgang unbesehen persistiert, würde auch
Kandidaten mit `override-keys==0` (kein Vektor zum Übernehmen, z. B.
`HOLDOUT_NO_ELIGIBLE_TRIALS`/`REJECT_HOLDOUT_UNREACHABLE` — der Holdout wurde nie erreicht) oder mit
`REJECT_SELECTION_PBO`/`REJECT_BOUNDARY_SOLUTION` (Overfit-geflaggt bzw. an einer Suchraum-Kante
klebend) als Seed weiterreichen — beide destabilisieren die Folgesuche, statt sie zu verankern.
**Fix/Regel:** `champions.champion_is_admissible` (die zentrale #705-Guard-Einheit) verlangt
nicht-leere `params`, dass der Holdout tatsächlich erreicht wurde, UND eine explizite
Rejection-Allowlist (`None`/`REJECT_HOLDOUT_DSR_DROP`/`REJECT_HOLDOUT_BOOTSTRAP_CI`/
`REJECT_HOLDOUT_GATE`) — `REJECT_SELECTION_PBO` und `REJECT_BOUNDARY_SOLUTION` sind explizit
ausgeschlossen.
**Invariante:** Ein Seed-Kandidat MUSS den Holdout erreicht haben und darf nicht als Overfit- oder
Randlösungs-Kandidat geflaggt sein — sonst verankert der Seed die Folgesuche an einem Punkt, der sie
aktiv in die falsche Richtung zieht.
**Betroffen:** `automation/optimizer/champions.py` (`champion_is_admissible`, `_ADMISSIBLE_HOLDOUT_REJECT_DETAILS`, `_UNREACHED_HOLDOUT_REJECT_DETAILS`).

### 🟢 Pitfall #205 — Ein Champion ist nicht ewig: ohne Demotion ankert ein stale Optimum die Suche dauerhaft am falschen Punkt [BEHOBEN: GH-#708]
**Symptom:** Ein Champion-Mechanismus ohne Verfallslogik würde einen einmal starken Kandidaten auf
unbestimmte Zeit als Anker behalten, selbst wenn sich das Marktregime dreht (z. B. ein in einem
Bullenmarkt gefundener Champion, re-evaluiert in einem nachfolgenden Bärenmarkt-Fenster) — die Suche
bliebe dauerhaft an einem nicht mehr repräsentativen Punkt verankert.
**Fix/Regel:** `champions.store_champion` verfolgt `degrade_streak`: eine region-gleiche
Re-Evaluierung UNTER `champion_min_R_symbol` erhöht ihn, eine gesunde Re-Evaluierung resettet ihn auf
0. Erreicht `degrade_streak >= champion_demote_after_runs`, demotet `_demote_champion` den Eintrag
(Store- + `strategy_symbol_seeds.json`-Eintrag entfernt, `CHAMPION_DEMOTED`-Event, Fallback auf
`strategy_defaults`).
**Invariante:** Ein Champion, der wiederholt unter dem Qualitäts-Floor re-evaluiert wird, wird
IMMER demotet — kein Anker bleibt auf unbestimmte Zeit bestehen, unabhängig davon, wie stark er
ursprünglich war.
**Betroffen:** `automation/optimizer/champions.py` (`store_champion`, `_demote_champion`).

### 📋 Neue/geänderte Config-Keys (Epic #702, Issues #703–#710)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `champion_enabled` | optimizer.json | *neu* (Default `true`) | Globaler Kill-Switch für Ebene 1+2 (Pitfall #201) |
| `champion_min_R_symbol` | optimizer.json | *neu* (Default `0.0`) | Qualitäts-Floor fürs Persistieren/Seeding (Pitfall #204) |
| `champion_promote_after_runs` | optimizer.json | *neu* (Default `2`) | Korroborationen bis Default-Writeback (Pitfall #203) |
| `champion_demote_after_runs` | optimizer.json | *neu* (Default `2`) | Degrade-Läufe bis Demotion (Pitfall #205) |
| `champion_min_advance_days` | optimizer.json | *neu* (Default `null` → `backtest.json.walk_forward.oos_window_days`) | Mindest-Fensterfortschritt für Writeback, Snooping-Schutz (Pitfall #203) |
| `champion_region_eps` | optimizer.json | *neu* (Default `0.10`) | Relative Parameter-Distanz für Regionsgleichheit (Pitfall #202/#205) |
| `strategy_symbol_seeds.json` | *neu* (Datei) | leer per Default (`{"seeds": {}}`) | Symbol-skopierter, automatisch nachgeführter Default-Prior (Pitfall #202) |
| `data/optimizer/champions/*.json` | *neu* (Verzeichnis) | leer per Default | Champion-Store, ein File pro (Strategie, Symbol) (Pitfall #201/#204/#205) |

### 🔒 Watertight Invariants (Epic #702, Issues #703–#710) — für künftige Agenten
- **Ein Warm-Start-Mechanismus, der denselben Kandidaten wiederholt enqueued, braucht IMMER eine
  Familien-Declustering-Korrektur, bevor er aktiviert wird** — sonst zieht sich die Multiple-Testing-
  Schwelle selbst zu (Pitfall #201, Kopplung an #694/#695/Pitfall #190).
- **Ein automatisch geschriebener Symbol-Seed berührt NIE den globalen Cross-Symbol-Default
  (`strategy_defaults.json`)** — symbol-getunte Evidenz bleibt strikt symbol-skopiert (Pitfall #202).
- **Ein korroborations-basierter Writeback verlangt IMMER Korroboration UND Fensterfortschritt
  gemeinsam** — Korroboration allein auf statischen Daten ist Datenschnüffelei (Pitfall #203).
- **Ein Seed-Kandidat MUSS den Holdout erreicht haben und darf nicht Overfit-/Randlösungs-geflaggt
  sein** (Pitfall #204).
- **Ein Champion, der wiederholt unter dem Qualitäts-Floor re-evaluiert wird, wird IMMER demotet** —
  kein Anker bleibt auf unbestimmte Zeit bestehen (Pitfall #205).
- **Die Guards fürs Champion-Schreiben UND -Lesen leben in EINER zentralen Funktion
  (`champion_is_admissible`)** — keine duplizierte Guard-Logik in sweep.py/run_optimization.py.
- **Ebene 3 (Live-Deployment) bleibt IMMER menschlich (HI-3):** kein Teil dieses Epics schreibt je
  `strategies.json`.

## Issue-Katalog #710–#717 — Time-Box-Reward, Dynamisches Take-Profit & Live-Guardrails (Sitzung 2026-07-18)

Issue #707 bündelt acht Einzel-Issues (#710–#717) entlang von drei unabhängigen Tracks, die #708s
Beobachtungen zum GR-01-Zeitbox-Regime (1h-Bars, harte 24-Bar-Exit-Deadline) technisch nachziehen,
ohne die seit #614/#630/#697/#702 gehärtete Reward-/Gate-/Champion-Architektur zu verletzen:

- **Track A — Reward-Shaping (#710, #711):** ein neuer additiver `time_box_penalty`-Term, der
  Trials mit langer Haltedauer relativ zur 24-Bar-Deadline bestraft, OHNE die getestete `psr_z`-Base
  zu ersetzen. Voraussetzung ist eine neue Backtest-Metrik (`median_bars_held`/`p95_bars_held`,
  #710), die der Reward-Term (#711) konsumiert.
- **Track B — Strategieverhalten (#712, #713, #714):** ein optionales, per Default deaktiviertes
  dynamisches Take-Profit, das mit wachsender Bars-in-Position-Zahl enger zieht (#712), dessen
  Suchraum-Anbindung im Optuna-Sampler (#713), und eine Senkung der `max_bars_in_trade`-Obergrenze
  auf 24 Bars systemweit (#714), damit kein Sampler-Trial mehr eine Zeitbox-Verletzung von
  vornherein erzeugen kann.
- **Track C — Live-Guardrails (#715, #716, #717):** ein Spread-Gate, das Orders bei zu weiten
  Bid/Ask-Spreads gegenüber ATR ablehnt (#715), eine Aggregat-Positions-/Notional-Obergrenze über
  alle offenen Positionen einer Strategie-Instanz hinweg (#716), und eine Reconnect-Reconciliation,
  die verwaiste eToro-Positionen nach einem Prozess-Neustart sicher über msgbus-Signalisierung an
  die Strategie zurückmeldet, statt Order-State im Execution-Client zu fabrizieren (#717).

**Merge-Reihenfolge (wie in #707 §2 vorgegeben):** #710 vor #711 (Metrik vor Konsument), #712 vor
#713 (Feature vor Suchraum-Anbindung), #714 unabhängig aber vor dem nächsten Live-Sweep (senkt das
Sampler-Maximum, das #713s `dyn_tp`-Fenster überlappt), #715/#716/#717 unabhängig voneinander, aber
alle vor dem nächsten Live-Deployment. `reward_semantics_version` 13→14 (nur #711 berührt
`compute_reward`) mit Pflicht-Purge aller Studien vor v14 (`python -m
automation.optimizer.purge_stale_studies`) als letzte Aktion vor dem nächsten Optimizer-Run.

### 🟢 Pitfall #206 — Ein neuer additiver Reward-Term ersetzt NIE die getestete Base [BEHOBEN: GH-#711]
**Symptom:** #708-Req-04 fordert wörtlich `Obj = E[R]/σ_R⁻ − β·(t_hold/24)²` — buchstabengetreu
umgesetzt würde das die seit #614/#630 gehärtete `psr_z`-Base durch rohen Sortino ersetzen und damit
alle seit v7–v13 gewonnenen Skalen-/Eligibility-Garantien (#559–#702) rückgängig machen.
**Fix/Regel:** Nur der additive Straf-Term (`time_box_penalty`) wird in die bestehende
`compute_reward`-Assembly aufgenommen, exakt neben `dd_penalty`/`turnover_penalty`/
`fold_dispersion_penalty`. `base = psr_z`, alle Gates, Deflation und Confirm-Logik bleiben
unverändert. Bei `penalty_time_box_weight=0.0` (Default) ist der Reward bit-identisch zum Pre-#711-
Zustand (Skalen-Fingerprint `test_issue_637` UND Eligibility-Fingerprint unverändert).
**Invariante:** Eine Issue-Spezifikation, die eine bereits gehärtete Reward-Base ersetzen würde,
wird NIE wörtlich umgesetzt — nur additiv, isoliert, mit Default-Null-Wirkung integriert.
**Betroffen:** `automation/optimizer/reward.py` (`compute_reward`, `_time_box_penalty`).

### 🟢 Pitfall #207 — Skalenkohärenz: jeder neue additive Reward-Term braucht penalty_scale_vs_base [BEHOBEN: GH-#711]
**Symptom:** Ein neuer additiver Straf-Term, der ohne Skalierung direkt vom `psr_z`-Base-Betrag
subtrahiert wird, kann je nach Base-Größenordnung entweder komplett wirkungslos (zu klein) oder
dominant (zu groß) sein — die Kalibrierung ist nicht selbsterklärend aus dem rohen Gewicht ableitbar.
**Fix/Regel:** `_time_box_penalty` durchläuft dieselbe `_penalty_scale_vs_base(weights)`-Skalierung
wie `dd_penalty`/`turnover_penalty`/`fold_dispersion_penalty` — kein neuer Straf-Term bekommt eine
eigene, abweichende Skalierungs-Konvention.
**Invariante:** JEDER additive Straf-Term in `compute_reward` läuft durch dieselbe zentrale
`penalty_scale_vs_base`-Funktion — es gibt keinen Sonderpfad pro Term.
**Betroffen:** `automation/optimizer/reward.py` (`_penalty_scale_vs_base`, `_time_box_penalty`).

### 🟢 Pitfall #208 — Median-Aggregation über eine wachsende Termmenge verwässert ohne Aktiv-Filter [BEHOBEN: GH-#711]
**Symptom:** `assert_penalty_scale_calibrated` prüft Miskalibrierung über den Median der Sigmas
aller bekannten Straf-Terme. Ein vierter, strukturell-inaktiver Term (Default-Gewicht `0.0`, Sigma
`0.0`) verschiebt diesen Median nach unten und schwächt dadurch die Sensitivität der Prüfung für
die bereits gehärteten Terme — ein `test_issue_631`-Regressionstest
(`test_calibration_fixture_fails_loud_without_rescaling`) hörte auf zu raisen, sobald
`time_box_penalty` unreflektiert in `_CALIBRATION_PENALTY_TERM_KEYS` aufgenommen wurde.
**Fix/Regel:** `assert_penalty_scale_calibrated` filtert auf Terme mit `sigma > 0` ("aktive
Dimensionen") und bildet den Median NUR über diese — exakt dieselbe Philosophie wie #534s
`_constraint_distance_penalty`, das ebenfalls nur aktive Constraint-Dimensionen mittelt. Sind gar
keine Terme aktiv, wird die Prüfung übersprungen (nichts zu kalibrieren).
**Invariante:** Das Hinzufügen eines per Default inaktiven Straf-Terms darf NIE die
Erkennungsschärfe der Kalibrierungs-Prüfung für bereits aktive Terme verwässern.
**Betroffen:** `automation/optimizer/reward.py` (`assert_penalty_scale_calibrated`,
`_CALIBRATION_PENALTY_TERM_KEYS`).

### 🟢 Pitfall #209 — Shaping ≠ Gate [BEHOBEN: GH-#711]
**Symptom:** Ein Zeitbox-Straf-Term könnte fälschlich so verdrahtet werden, dass er nach
`oos_eligible` unterscheidet (z.B. nur auf eligible Trials angewendet) — das würde ihn faktisch zu
einem zweiten, verdeckten Gate machen und die seit #629/#649 etablierte strikte Trennung
Gate-vs-Shaping verletzen.
**Fix/Regel:** `_time_box_penalty` liest ausschließlich `oos_median_bars_held` und die Gewichte —
niemals `oos_eligible` oder andere Gate-Flags. Ein eligible und ein sonst identisches ineligible
Trial erhalten exakt denselben `time_box_penalty`-Betrag.
**Invariante:** Additive Reward-Terme (Shaping) und Eligibility-Gates bleiben strikt getrennte
Codepfade — kein Shaping-Term liest je ein Gate-Flag.
**Betroffen:** `automation/optimizer/reward.py` (`_time_box_penalty`).

### 🟢 Pitfall #210 — Bar-Zähler-Deadline braucht einen restart-durablen Persistenz-Anker [BEHOBEN: GH-#714/#717]
**Symptom:** Die 24-Bar-Zeitbox-Deadline wird im Live-Betrieb über einen In-Memory-Bar-Zähler
(`_bars_in_position`) auf der Strategie-Instanz verfolgt. Ein Prozess-Neustart (Deploy, Crash,
Reconnect) verliert diesen Zähler — eine Position, die bereits 20 von 24 Bars gehalten wurde, würde
nach Neustart wieder bei 0 anfangen und die Deadline effektiv um bis zu 24 Bars verlängern.
**Fix/Regel:** `_reconcile_after_reconnect` rekonstruiert den Bar-Zähler beim Start aus dem
Nautilus-nativen `Position.ts_opened`-Zeitstempel (immer verfügbar, unabhängig vom Prozess-Leben)
kombiniert mit dem Bar-Cache (`_count_bars_since`), NICHT aus einem separat zu pflegenden
Cross-Prozess-State. Für den unabhängigen Zweck der Phantom-Positions-Erkennung (verwaiste
eToro-Positionen ohne lokale Nautilus-Position) persistiert `_StateManager` zusätzlich
`entry_ns`/`entry_bar_seq` je Mapping-Eintrag — bewusst getrennt vom Bar-Zähler-Mechanismus, da
dort kein Nautilus-`Position`-Objekt existiert, aus dem rekonstruiert werden könnte.
**Invariante:** Jeder restart-kritische Zähler mit einer Handlungs-Deadline (Zeitbox, Cooldown, ...)
MUSS aus einer bereits durablen, Nautilus- oder Broker-nativen Quelle rekonstruierbar sein — niemals
ausschließlich aus zusätzlichem, selbst gepflegtem Prozess-State.
**Betroffen:** `automation/strategies/hourly_strategy_base.py` (`_reconcile_after_reconnect`,
`_count_bars_since`), `automation/adapters/etoro_state_manager.py` (`_coerce_entry`, `set`,
`get_entry`).

### 🟢 Pitfall #211 — Ein Laufzeit-Gate braucht eine explizite Runtime-Prüfung, nicht nur ein Backtest-Kostenmodell [BEHOBEN: GH-#715]
**Symptom:** Spread existierte vor #715 ausschliesslich als Backtest-Kostenmodell
(`backtest.json spread_bps_*`, `fill_model=bid_ask`) und als Reward-Turnover-Kosten. Ein
Kostenmodell verteuert Trades im Backtest, verwirft aber live KEINEN Einstieg bei realer
Spread-Ausweitung — genau die von GR-02 adressierte Ertrags-Erosion blieb ungefiltert, weil kein
Order-Pfad-Gate existierte.
**Fix/Regel:** Im Entry-Pfad (`_compute_quantity`, vor jeder Order) wird der effektive Live-Spread
aus dem aktuellen `QuoteTick` berechnet (`s = (ask − bid) / mid`, in bps) und bei Überschreiten der
Schwelle das Signal verworfen + strukturiert geloggt (`SPREAD_GATE_REJECT`). Die Schwelle selbst
bleibt an EINER Quelle verankert (`_resolve_spread_gate_bps`): `spread_gate_bps = k_spread ×
spread_bps_model` (`resolve_spread_bps_model` liest denselben `backtest.json`-Kostenmodell-Wert,
den auch die Backtest-Kostensimulation nutzt) — sonst entstünden zwei unabhängig zu pflegende
Spread-Zahlen (Pre-#562/#684-Fehler). `k_spread ≈ 2.0` sorgt dafür, dass die bereits eingepreiste
Normal-Spread nicht doppelt bestraft wird — nur Ausweitungen darüber hinaus lösen die Ablehnung
aus. Ein expliziter `config.spread_gate_bps`-Override bleibt möglich (manuelle Fixierung); ohne
`QuoteTick` (Cold-Start) ist das Gate fail-open (kein erfundener Wert).
**Invariante:** Ein Kostenmodell-Parameter, der eine reale Laufzeit-Filterung bewirken soll, MUSS
über einen expliziten Order-Pfad-Check angebunden werden — reine Backtest-Kostensimulation wirkt
nie automatisch als Live-Gate. Existieren Kostenmodell UND Live-Gate für dieselbe Grösse (Spread),
leiten sie sich von EINER Quelle ab, nie von zwei unabhängig gepflegten Zahlen.
**Betroffen:** `automation/strategies/hourly_strategy_base.py` (`resolve_spread_bps_model`,
`_resolve_spread_gate_bps`, `_compute_quantity`).

### 🟢 Pitfall #212 — Per-Entity-Cap ≠ System-Limit [BEHOBEN: GH-#716]
**Symptom:** Eine "maximale Anzahl offener Positionen"-Guardrail ließe sich fälschlich als globales,
prozessweites Limit implementieren — das würde aber nicht vor dem eigentlichen Risiko schützen:
zu viel Aggregat-Exposure durch EINE Strategie-Instanz auf EINEM Symbol, die wiederholt nachlegt.
**Fix/Regel:** `max_aggregate_open_positions` und `max_order_notional` werden pro
Strategie-Instanz (über `self.cache`, gescoped auf `self.instrument_id`/`self.config`) ausgewertet,
nicht global. Der Cap-Check (`AGGREGATE_EXPOSURE_CAP_REJECT`) läuft in `_compute_quantity` VOR dem
Spread-Gate-Ergebnis in die Order-Größen-Entscheidung ein, der Notional-Cap clamped
`trade_amount_usd` nach unten statt die Order abzulehnen.
**Invariante:** Eine Exposure-Guardrail ist immer so eng gescoped wie das Risiko, das sie
begrenzen soll — ein Per-Strategie-Instanz/Symbol-Risiko bekommt einen Per-Strategie-Instanz/
Symbol-Cap, kein globales System-Limit.
**Betroffen:** `automation/strategies/hourly_strategy_base.py` (`_compute_quantity`).

### 🟢 Pitfall #213 — Eine Execution-Client-Komponente fabriziert NIE eigenmächtig Order-State [BEHOBEN: GH-#717]
**Symptom:** Nach einem Prozess-Neustart kann `EToroExecutionClient` verwaiste eToro-Positionen
entdecken (Positionen, die broker-seitig offen sind, aber keine entsprechende lokale
Nautilus-`Position` mehr haben). Ein naiver Fix würde den Execution-Client direkt `OrderFilled`/
`OrderSubmitted`-Events für eine `ClientOrderId` fabrizieren, die nie legitim durch
`order_factory` einer Strategie erzeugt wurde — das verletzt Nautilus' Order-Lifecycle-Invariante
und kann zu inkonsistentem Cache-State führen (zusätzlich: `EToroExecutionClient` lässt sich in
Tests wegen Cython-Konstruktor-Typchecks ohnehin nicht mit Mocks instanziieren, was ein Symptom
derselben Grenzverletzung ist).
**Fix/Regel:** `_reconcile_positions_on_connect` erkennt Phantom-Positionen rein lesend
(`extract_open_position_ids`, `find_phantom_positions` — beide als reine, ohne Nautilus-Objekte
testbare Modul-Funktionen extrahiert) und publiziert stattdessen ein
`events.gr04_close_request.{instrument_id}`-msgbus-Signal. Die Strategie selbst (Empfänger via
`_on_gr04_close_request`) schließt die Position über ihren bereits etablierten, geprüften
`_close_position_base()`-Pfad — der Execution-Client erzeugt zu keinem Zeitpunkt eigenständig
Order- oder Fill-Events.
**Invariante:** Nur die Strategie-Instanz (über ihren `order_factory`) erzeugt Order-State für ihre
eigenen Positionen. Der Execution-Client kommuniziert Beobachtungen ausschließlich über
msgbus-Signale, nie durch direkte Fabrikation von Order-/Fill-Events.
**Betroffen:** `automation/adapters/etoro_execution.py` (`extract_open_position_ids`,
`find_phantom_positions`, `_reconcile_positions_on_connect`), `automation/strategies/
hourly_strategy_base.py` (`_on_gr04_close_request`, `on_start`).

### 📋 Neue/geänderte Config-Keys (Issues #710–#717)
| Key | Datei | Wert | Zweck |
|---|---|---|---|
| `penalty_time_box_weight` | optimizer.json | *neu* (Default `0.0`) | Gewicht des additiven Zeitbox-Straf-Terms, bit-identisch bei `0.0` (Pitfall #206) |
| `time_box_bars` | optimizer.json | *neu* (Default `24.0`) | Normierungs-Deadline (Bars) für `time_box_penalty` |
| `reward_semantics_version` | optimizer.json | `13` → `14` | `compute_reward` berührt (neuer additiver Term), Pflicht-Purge vor Re-Run |
| `dyn_tp_enabled` | Sampler-Suchraum (spaces.py) | *neu* (Default `False`) | Schalter für dynamisches Take-Profit pro Trial |
| `dyn_tp_lambda` | Sampler-Suchraum (spaces.py) | *neu* (log-uniform `0.1–3.0`, nur falls `dyn_tp_enabled`) | Skalierungsfaktor des dynamischen TP-Ziels |
| `dyn_tp_gamma` | Sampler-Suchraum (spaces.py) | *neu* (uniform `0.5–4.0`, nur falls `dyn_tp_enabled`) | Krümmung der Zeit-Annäherungsfunktion |
| `spread_gate_bps` | HourlyStrategyConfig | *neu* (Default `None` = deaktiviert) | Optionaler fixer Spread-Grenzwert (bps) |
| `k_spread` | HourlyStrategyConfig | *neu* (Default `2.0`) | ATR-Multiplikator für dynamisches Spread-Gate |
| `max_aggregate_open_positions` | HourlyStrategyConfig | *neu* (Default `5`) | Per-Instanz-Cap offener Positionen (Pitfall #212) |
| `max_order_notional` | HourlyStrategyConfig | *neu* (Default `2000.0`) | Per-Instanz-Notional-Cap je Order (Pitfall #212) |
| `max_bars_in_trade` (Sampler-Obergrenze) | spaces.py (`_MAX_BARS_IN_TRADE_CAP`) | bis zu `120` → `24` | Verhindert Trials, die die Zeitbox-Deadline strukturell verletzen (#714) |
| `max_bars_in_trade` (Strategie-Hard-Cap) | hourly_strategy_base.py (`MAX_BARS_IN_TRADE_HARD_CAP`) | *neu* `24` | Konstruktor-seitiges Clamping, unabhängig vom Config-Wert (#714) |
| `_StateManager`-Mapping-Schema | etoro_state_manager.py | `str` → `{"positionId", "entry_ns", "entry_bar_seq"}` | Restart-durabler Anker für Phantom-Positions-Erkennung (Pitfall #210), rückwärtskompatibel über `_coerce_entry` |

### 🔒 Watertight Invariants (Issues #710–#717) — für künftige Agenten
- **Eine wörtliche Issue-Spezifikation, die eine bereits gehärtete Reward-Base ersetzen würde, wird
  NIE wörtlich umgesetzt** — nur additiv, isoliert, mit garantiert bit-identischer
  Default-Nullwirkung (Pitfall #206).
- **Jeder additive Straf-Term in `compute_reward` läuft durch dieselbe zentrale
  `penalty_scale_vs_base`-Funktion** — kein Sonderpfad pro Term (Pitfall #207).
- **Die Median-Kalibrierungs-Prüfung mittelt NUR über strukturell aktive (Sigma > 0) Straf-Terme**
  — ein neuer, per Default inaktiver Term verwässert nie die Sensitivität für bereits aktive Terme
  (Pitfall #208).
- **Additive Reward-Terme (Shaping) lesen NIE Eligibility-Gate-Flags** — Shaping und Gate bleiben
  strikt getrennte Codepfade (Pitfall #209).
- **Jeder restart-kritische Zähler mit Handlungs-Deadline MUSS aus einer bereits durablen,
  Nautilus- oder Broker-nativen Quelle rekonstruierbar sein** — niemals nur aus zusätzlichem,
  selbst gepflegtem Prozess-State (Pitfall #210).
- **Ein Backtest-Kostenmodell wirkt NIE automatisch als Live-Gate** — eine reale Laufzeit-Filterung
  braucht einen expliziten Order-Pfad-Check; existieren Kostenmodell UND Live-Gate für dieselbe
  Grösse, leiten sie sich von EINER Quelle ab (Pitfall #211).
- **Eine Exposure-Guardrail ist immer so eng gescoped wie das Risiko, das sie begrenzen soll** —
  kein globales System-Limit für ein Per-Strategie-Instanz/Symbol-Risiko (Pitfall #212).
- **Nur die Strategie-Instanz (über ihren `order_factory`) erzeugt Order-State für ihre eigenen
  Positionen — der Execution-Client kommuniziert Beobachtungen ausschließlich über msgbus-Signale,
  nie durch direkte Fabrikation von Order-/Fill-Events** (Pitfall #213).
- **`reward_semantics_version` wird bei JEDER `compute_reward`-berührenden Änderung erhöht, mit
  Pflicht-Purge aller Studien vor der neuen Version als letzte Aktion vor dem nächsten Run** —
  unverändert seit #614, hier erneut bestätigt für v13→14.

## Issue-Katalog #740–#746 — Observability & Claude-optimierte Diagnose-Reports (2026-07-19)

Aufbauend auf der bereits vorhandenen LLM-optimierten Logging-Infrastruktur
(`automation/log_manager.py`) schliesst dieser Katalog die konkreten Beobachtbarkeits-Lücken, die
über mehrere Forensik-Sitzungen wiederholt echte Zeit gekostet haben: Rotations-Verlust des
Lauf-Anfangs bei hochvolumigen Sweeps (#740), kein valides JSONL für strukturierte Events (#741),
kein aggregiertes Sweep-Level-Report-Artefakt (#742), keine dauerhaften Regressionswächter für
einmalig von Hand verifizierte mathematische Kohärenz (#743), Verwechslungsrisiko zwischen zwei
ähnlich benannten Rejection-Detail-Feldern (#744), kein wiederverwendbares Trial-Explain-Werkzeug
(#745), zweistufige Retention bislang undokumentiert (#746). Rein additive Observability — verändert
KEINE Trading-/Optimierungs-Entscheidung, kein `reward_semantics_version`-Bump, kein SQLite-Purge.

| GH-Issue | Prio | Kernänderung | Dateien |
|---|---|---|---|
| **#740** | P0 | `setup_bot_logging(..., run_id=...)`: bei gesetztem `run_id` ein NICHT-rotierender `FileHandler` auf `logs/{log_name}_{run_id}.log` statt des tages-geteilten `RotatingFileHandler` (1 MB×5) — ein Lauf = eine vollständige Datei. `sweep.main()` generiert `run_id` einmalig (`log_manager.default_run_id()`, wiederverwendet `manifest.git_commit()`) und reicht sie durch. Dauerbetrieb-Logger (`momentum_ls_run`, `catalog_service`, …) bleiben ohne `run_id` bit-identisch auf Rotation. Pitfall #214. | `automation/log_manager.py`, `automation/optimizer/sweep.py` |
| **#741** | P1 | `emit_execution_event` schreibt additiv jedes Event als valide, dekorationsfreie JSON-Zeile nach `logs/{log_name}_{run_id_oder_datum}.events.jsonl` (`_JSONL_SIDECAR_PATHS`-Registry, indiziert über `logger.name` — keine der ~15 bestehenden Aufrufstellen musste angepasst werden). `cleanup_old_logs` erfasst seit diesem Issue zusätzlich `*.jsonl` (vorher nur `*.log*` — die Sidecar-Datei wäre sonst NIE geräumt worden). Pitfall #215. | `automation/log_manager.py`, `.gitignore` |
| **#742** | P0 | Neues `automation/optimizer/report.py`: EIN aggregiertes `data/optimizer/reports/run_{run_id}.json` (`report_schema_version: 1`) je Sweep-Lauf, atomar geschrieben (`manifest.write_json_atomic` — Tempdatei im Zielverzeichnis + `os.replace`). `generate_sweep_report` (live, am Ende von `sweep.main()`, non-fatal) und `generate_report_for_run` (standalone/nachträglich: entdeckt Proposal-JSONs + lädt jede Study frisch aus ihrer SQLite-Datei, KEIN laufender Sweep nötig) teilen sich denselben Kern (`_build_report`) — garantierte Determinismus-Äquivalenz zwischen beiden Pfaden. Bündelt `manifest.git_commit`/`sha256_file`/`catalog_fingerprint`, `sweep._family_n_from_proposals`, `run_optimization.study_shows_gradient_signal`/`_sanitize`/`resolve_storage`. | `automation/optimizer/report.py` (neu), `automation/optimizer/manifest.py` (`write_json_atomic`) |
| **#743** | P0 | Neues `automation/optimizer/invariants.py`: 5 REINE Invarianz-Prüfungen über synthetische `user_attrs`-Dicts — `check_sr0_coherence` (#651-Regressionswächter: `deflated_sr0` und `deflated_dsr`/`deflation_dsr_z` müssen ko-präsent sein), `check_n_family_consistency` (#652/#670: `deflation_n_effective == max(deflation_n_eligible, deflation_n_family_effective)`), `check_config_key_registry` (#649: importiert `backtest_runner.OOS_CONDITION_MAP_KEYS`/`_canonical_gate_key` statt einer zweiten Registry-Kopie), `check_rejection_chain_completeness` (#654/#671: kein abgelehntes Proposal ohne `holdout_reject_detail`), `check_reward_term_variance` (Verallgemeinerung von `REWARD_TERM_INERT`, #621, zu einer vollständigen Liste). In jeden #742-Report eingebettet (`invariant_checks`, je Study + ein globaler `check_config_key_registry`-Eintrag); ein FAIL emittiert zusätzlich `emit_execution_event(..., "INVARIANT_CHECK_FAILED", ..., level=ERROR)`. Pitfall #217. | `automation/optimizer/invariants.py` (neu), `automation/optimizer/report.py` |
| **#744** | P1 | `confirm._dominant_is_rejection_detail`: explizite Docstring-Warnung ("NICHT die Promotion-Ablehnung — siehe `holdout_reject_detail`"). `export_symbol_proposal` spiegelt denselben Wert zusätzlich unter dem unmissverständlich benannten `legacy_modal_is_rejection_detail` (Alias von `dominant_is_rejection_detail`, NICHT von `is_rejection_detail`) — Rückwärtskompatibilität aller bestehenden Feldnamen bleibt gewahrt. Pitfall #216. | `automation/optimizer/confirm.py` |
| **#745** | P1 | Neues CLI `python -m automation.optimizer.explain_trial --strategy S --symbol SYM --trial N [--format markdown\|json]`: rendert Reward-Dekomposition, Gate-Zustand, Rejection-Kette und Near-Miss-Deltas (`oos_gate_deltas`, knappste Marge zuerst) für EINEN Trial; bei Study-weitem 0-evaluable/0-eligible-Kollaps zusätzlich `sweep_diagnostics.diagnose_trade_frequency`s `binding_cause` (#669, wiederverwendet statt dupliziert). Fehlender Trial/Study ⇒ klare Fehlermeldung auf stderr + Exit-Code 1, nie ein rohes Traceback. Wiederverwendet dieselbe Study-Namens-/Storage-Konvention wie `report.py` (`run_optimization._sanitize`/`resolve_storage`). | `automation/optimizer/explain_trial.py` (neu) |
| **#746** | P2 | Zweistufige Retention dokumentiert + statisch abgesichert: `data/optimizer/reports/*.json` liegt strukturell ausserhalb von `cleanup_old_logs`s Verzeichnis-/Glob-Reichweite (`logs/*.log*` + `logs/*.jsonl`) — Reports bleiben langlebig (klein, aggregiert), Rohlogs/JSONL-Sidecars unterliegen weiter der 7-Tage-Retention. Pitfall #218. | `automation/AGENTS.md`, `README.md` |

**Merge-Reihenfolge:** #740 → #741 (`run_id`-Konvention) → #742 (`write_json_atomic`, Report-Kern) →
#743 (Invarianz-Checks, in #742 eingebettet) → #745 (nutzt #742/#743s Datenzugriff). #744 und #746
sind unabhängig und liefen parallel.

Tests: `test_issue_740_per_run_log.py`, `test_issue_741_jsonl_sidecar.py`,
`test_issue_742_sweep_report.py`, `test_issue_743_invariant_checks.py`,
`test_issue_744_rejection_detail_naming.py`, `test_issue_745_explain_trial.py`,
`test_issue_746_retention_layers.py`.

### 🟢 Pitfall #214 — `RotatingFileHandler(1MB×5)` für hochvolumige, NICHT-daemonartige Workloads verliert den LAUF-ANFANG, nicht nur alte Läufe [BEHOBEN: GH-#740]
**Symptom:** Ein `--tier all`-Sweep über hunderte/tausende Trials (15 Strategien × n_trials=100
Default) erzeugt allein an `optimizer_trial_completed`-Events weit mehr als 5 MB, bevor der Lauf
fertig ist. Der geteilte, tages-basierte `RotatingFileHandler` (max 1 MB, 5 Backups) auf
`logs/optimizer_<date>.log` überschreibt dann den Ringpuffer-Anfang — dokumentiertes Muster über
mehrere Forensik-Sitzungen: "Log ist Tail-Fragment" (Sitzung 2026-07-17: Sweep-Start rekonstruiert
~04:52, sichtbares Log begann erst 05:14).
**Root-Cause:** Rotation ist für Dauerbetrieb-Logger (kein natürliches Lauf-Ende) korrekt, aber
für einen EINZELNEN, hochvolumigen Lauf mit definiertem Ende systematisch zu klein dimensioniert —
der Ringpuffer kennt den Lauf-Anfang nicht als "besonders erhaltenswert".
**Fix/Regel:** Workloads mit natürlichem Ende (Sweep-/Optimizer-Läufe) bekommen eine PRO-LAUF-Datei
(`setup_bot_logging(..., run_id=...)` ⇒ nicht-rotierender `FileHandler`), keinen geteilten
Ringpuffer. Dauerbetrieb-Logger (kein Lauf-Ende) bleiben bewusst auf Rotation.

### 🟢 Pitfall #215 — `[PREFIX] {json}` innerhalb einer formatierten Log-Zeile ist KEIN valides JSONL [BEHOBEN: GH-#741]
**Symptom:** `sweep.py` bezeichnete das Logging-Format als "rotierende JSONL" (Kommentar), obwohl
`emit_execution_event` `[JSON_EVENT] {...}` nur als Substring INNERHALB einer
`StructuredFormatter`-Zeile (`TIMESTAMP | LEVEL | LOGGER | [JSON_EVENT] {...}`) schrieb.
`json.loads(line)` scheitert ohne vorherige Regex-Extraktion des Präfixes — jede automatisierte
Auswertung brauchte eine Zusatz-Parsing-Stufe.
**Root-Cause:** Ein Mensch-lesbares Präfix-Format wurde mit valider JSONL verwechselt — beides
sind unterschiedliche Anforderungen (Terminal-Lesbarkeit vs. maschinelles Parsing ohne
Vorverarbeitung).
**Fix/Regel:** Für automatisiertes Parsing IMMER eine echte, dekorationsfreie JSONL-Sidecar-Datei
zusätzlich zur Prosa-Logzeile führen — niemals ein Präfix-markiertes Format als "JSONL" bezeichnen
oder darauf verlassen.

### 🔵 Pitfall #216 — Zwei ähnlich benannte "Ablehnungsgrund"-Felder (modal vs. tatsächliche Promotion-Entscheidung) sind ein wiederkehrendes Verwechslungsrisiko [ABGESICHERT: GH-#744]
**Symptom:** Bereits Pitfall #161 (GH-#654) entstand aus genau diesem Muster — `is_rejection_detail`
(damals der modale IS-Study-Grund) wurde mit der tatsächlichen Promotion-Ursache verwechselt.
`#654`/`#671` trennten die Felder bereits korrekt (`dominant_is_rejection_detail` = modal/sekundär,
`holdout_reject_detail`/`is_rejection_detail_override` = tatsächliche Ursache) — das STRUKTURELLE
Risiko (zwei ähnlich benannte Felder im selben Proposal) bleibt aber bestehen, solange nicht
explizit an der Quelle markiert.
**Root-Cause:** Namensähnlichkeit zwischen "der modale Sekundärgrund" und "die tatsächliche
Entscheidung" lädt zu genau der Verwechslung ein, die schon einmal (#654) einen realen Bug
verursachte — unabhängig davon, ob die Felder aktuell korrekt befüllt sind.
**Fix/Regel:** Die modale Diagnose-Funktion trägt eine EXPLIZITE Docstring-Warnung ("NICHT die
Promotion-Ablehnung"); das Proposal spiegelt ihren Wert zusätzlich unter einem unmissverständlich
"legacy/modal" benannten Feldnamen (`legacy_modal_is_rejection_detail`). Generalisierte Regel: ein
älteres, ähnlich benanntes Feld neben seinem Nachfolger wird IMMER explizit als "legacy/modal,
NICHT die [tatsächliche Bedeutung]" gekennzeichnet — nie stillschweigend nebeneinander belassen.

### 🟢 Pitfall #217 — Ein manuell/einmalig verifizierter mathematischer Kohärenz-Fix bleibt ohne Regression-/Invariant-Check jederzeit erneut brechbar [BEHOBEN: GH-#743]
**Symptom:** Mehrere mathematische Kohärenz-Fixes (SR₀-Konsistenz #651, N-Konsistenz #652/#670,
Config-Key-vs-Registry #649) wurden jeweils EINMALIG in einer Forensik-Sitzung von Hand verifiziert
und dokumentiert — aber nie als automatisierter, bei jedem Lauf ausgeführter Check verdrahtet. Ein
künftiger, scheinbar unabhängiger Refactor hätte jede dieser Invarianten erneut brechen können,
ohne dass es auffällt.
**Root-Cause:** Forensische Erkenntnis ("X und Y müssen übereinstimmen") wurde als Sitzungsnotiz
festgehalten, nicht als Code, der bei jedem künftigen Lauf automatisch dagegen prüft — ein
Präzedenzfall (`REWARD_TERM_INERT`) existierte bereits, war aber nicht verallgemeinert.
**Fix/Regel:** Jede von Hand verifizierte mathematische/Konfigurations-Invariante wird als REINE,
unabhängig testbare Funktion in `automation/optimizer/invariants.py` verdrahtet und bei jedem
Sweep-Report (#742) automatisch geprüft (PASS/FAIL + die tatsächlich verglichenen Werte, nie
stillschweigend verschluckt). Ein FAIL emittiert zusätzlich ein `INVARIANT_CHECK_FAILED`-ERROR-Event
— sofort sichtbar, nicht erst beim Report-Lesen.

### 🟢 Pitfall #218 — Aggregierte Reports und Rohlogs brauchen UNTERSCHIEDLICHE Retention [BEHOBEN: GH-#746]
**Symptom:** `cleanup_old_logs` (7 Tage, `logs/*.log*`) hätte auch die #740-Pro-Lauf-Logs und das
#741-JSONL-Sidecar erfasst (beide liegen unter `logs/`) — für Untersuchungen, die nachweislich über
mehr als 7 Tage liefen (Sitzungshistorie 2026-07-13 bis 2026-07-18 zum selben Sweep-Defekt), wäre
nach 7 Tagen nur noch erhalten, was manuell in Memory-Notizen festgehalten wurde.
**Root-Cause:** Ein Report ist klein, aggregiert und lange aufzuheben; ein Rohlog ist teuer und
wächst schnell. Beide unter dieselbe Cleanup-Regel zu stellen ist ein impliziter, oft falscher
Tradeoff, der nie bewusst getroffen wurde.
**Fix/Regel:** Aggregierte Reports (`data/optimizer/reports/*.json`) leben strukturell AUSSERHALB
des `logs/`-Verzeichnisbaums und sind von `cleanup_old_logs` nie betroffen — unbegrenzte Aufbewahrung,
manuelle Bereinigung bei Bedarf (konsistent mit `champions/`, das ebenfalls schlank/JSON-only bleibt).
Rohlogs (Prosa + JSONL-Sidecar) bleiben unter der bestehenden 7-Tage-Regel. Generalisierte Regel:
ein aggregiertes Artefakt und die Rohdaten, aus denen es abgeleitet wurde, bekommen IMMER getrennte,
bewusst gewählte Retention-Entscheidungen — nie automatisch dieselbe Regel.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #740–#746)
Keine neuen Config-Keys — dieser Katalog ist rein additive Observability/Tooling ohne Einfluss auf
Trading-/Optimierungs-Parameter.

### 🔒 Watertight Invariants (Issue-Katalog #740–#746) — für künftige Agenten
- **Ein hochvolumiger Workload mit natürlichem Lauf-Ende (Sweep/Optimizer) bekommt eine PRO-LAUF-
  Datei, kein geteiltes Rotations-Limit** — die Rotation-Kappung ist für Dauerbetrieb-Logger korrekt,
  für einen einzelnen langen Lauf strukturell verlustbehaftet (Pitfall #214).
- **Ein Präfix-markiertes Format (`[TAG] {json}`) innerhalb einer formatierten Zeile ist NIE valides
  JSONL** — automatisiertes Parsing braucht eine dekorationsfreie Sidecar-Datei (Pitfall #215).
- **Zwei ähnlich benannte Felder für denselben Diagnose-Bereich (modal vs. tatsächliche
  Entscheidung) werden IMMER explizit als "legacy/modal, NICHT die tatsächliche Ursache"
  gekennzeichnet** — nie stillschweigend nebeneinander belassen (Pitfall #216, seit #161/#178 ein
  wiederkehrendes Muster).
- **Jede von Hand verifizierte mathematische/Konfigurations-Invariante wird als dauerhafter,
  automatisierter Check verdrahtet** — eine Sitzungsnotiz ist keine Regression-Absicherung
  (Pitfall #217).
- **Ein aggregiertes Report-Artefakt und die Rohdaten, aus denen es abgeleitet wurde, bekommen
  getrennte Retention-Entscheidungen** — nie automatisch dieselbe Cleanup-Regel (Pitfall #218).
- **`report.py`/`explain_trial.py` laden Studies IMMER frisch aus SQLite (nie aus Live-Prozess-
  Zustand)** — das ist die Grundlage der Determinismus-Garantie zwischen dem Live-End-of-Run-Pfad
  und der standalone/nachträglichen Rekonstruktion (#742).

### 🟢 Pitfall #219 — Ein Abbruch-Guard darf nie an dieselbe Grösse gekoppelt sein, die den Beginn der Suche markiert [BEHOBEN: GH-#753]
**Symptom:** `floor_plateau_callback` brach eine Study beim `ZERO_ELIGIBLE`-Kollaps exakt bei
`n_startup_trials + floor_plateau_k` abgeschlossenen Trials ab — derselben Grenze, ab der
`TPESampler(n_startup_trials=...)` ERST beginnt, den Suchraum zu MODELLIEREN (die ersten
`n_startup_trials` Trials sind reine Zufallsziehungen). Der Guard tötete die Study also GENAU am
Punkt, an dem die Bayes-Optimierung ihre Arbeit aufnehmen würde, und meldete "0 von 16
Zufallsziehungen feasible" als strukturellen Suchraum-Befund — SqueezeBreakoutStrategy (dim=9,
n_startup=18) war der stärkste Einzelbeleg über 124 Symbole.
**Root-Cause:** `n_startup_trials` ist die untere Schranke, ab der ein Sampler modelliert — kein
Abbruch-Trigger. Trigger (wie viele Trials seit dem Modellierungs-Beginn ohne Erfolg) und Warmlauf
(wie viele Zufalls-Startpunkte) sind zwei unabhängige Konzepte, die an DIESELBE Config-Grösse
gekoppelt waren.
**Fix/Regel:** Getrennte, deklarative Keys — `plateau_min_modelled_trials` (Anzahl ZUSÄTZLICHER
Trials NACH `n_startup_trials`, bevor der `ZERO_ELIGIBLE`-Zweig überhaupt urteilen darf) statt der
gemeinsamen Kopplung an `n_startup_trials + floor_plateau_k`. Generalisierte Regel: ein
Abbruch-Guard wird NIE an dieselbe Grösse gekoppelt, die den Beginn der eigentlichen
(nicht-zufälligen) Suche markiert — Trigger und Warmlauf brauchen getrennte Keys.

### 🟢 Pitfall #220 — „0 von N Zufallsziehungen feasible" ist keine Aussage über den Suchraum [BEHOBEN: GH-#753]
**Symptom:** Ein `STRUCTURAL_ALL_UNEVALUABLE`/`ZERO_ELIGIBLE`-Urteil stützte sich auf die
Eligibility-Passrate über ALLE bisherigen Trials, inklusive der reinen Zufalls-Startup-Phase — bei
einem eng constrained Suchraum (z. B. `cooldown_bars`×`max_bars_in_trade` gegen ein kurzes
OOS-Fenster) ist eine feasible Region PER KONSTRUKTION klein, und 0 Treffer unter 16 Zufallsziehungen
ist genau das erwartbare Ergebnis, kein Strukturbefund.
**Root-Cause:** Ein constrained Sampler (TPE mit `constraints_func`) existiert GENAU dafür, die
feasible Region zu FINDEN — nicht dafür, sie bereits in den ersten reinen Zufallsziehungen zu
TREFFEN. Ein Abbruch wegen `p_eligible == 0` über die Zufalls-Startup-Trials verwechselt die
Baseline-Trefferquote mit dem eigentlichen Sampler-Fortschritt.
**Fix/Regel:** Fortschritt wird über die MODELLIERTEN Trials gemessen (`_modelled_trials`, Index
`>= n_startup_trials`, `run_optimization.py`), zusätzlich über Constraint-ANNÄHERUNG
(`constraint_improvement_rate`, #754) statt reiner Passrate — eine Study, die sich der feasiblen
Region nähert, aber sie noch nicht erreicht hat, zeigt damit trotzdem messbaren Fortschritt.
Generalisierte Regel: "0 von N Zufallsziehungen feasible" ist erst dann eine belastbare Aussage über
den Suchraum, wenn N modellierte (nicht reine Zufalls-) Trials umfasst — und Constraint-Annäherung
ist dafür das schärfere Signal als die reine Passrate.

### 🟢 Pitfall #221 — Ein Eskalations-Gate, das auf der Zielmenge misst, die es erst erzeugen soll, ist zirkulär [BEHOBEN: GH-#754]
**Symptom:** Höheres Trial-Budget (nächstes Tier) wurde ausschliesslich über
`evaluable_fraction > 0 ∧ pstdev(reward | eligible) > τ` gerechtfertigt — bei LEERER feasibler Region
(`p_eligible == 0`, nach #753 der Normalfall, während die Suche noch läuft) IMMER `False`. Eine
Study brauchte dann eligible Trials, um mehr Budget zu bekommen, UND mehr Budget, um eligible Trials
zu finden — eine strukturelle Selbstblockade.
**Root-Cause:** Das einzige Fortschrittsmass war ausschliesslich AUF der Zielmenge (feasible Region)
definiert, die das Eskalations-Gate selbst erst ermöglichen soll — ein Gate, das seine eigene
Voraussetzung misst, kann sie nie erfüllen, solange die Zielmenge leer ist.
**Fix/Regel:** `study_shows_gradient_signal` bekam einen ZWEITEN, GLEICHRANGIGEN Arm
(`constraint_improvement_rate`: relative Verbesserung der minimalen Gesamt-Constraint-Verletzung
zwischen erster und zweiter Hälfte der modellierten Trials) — dieser Arm ist AUCH bei leerer
feasibler Region definiert. Generalisierte Regel: Fortschrittsmasse für ein Eskalations-Gate müssen
auch dann einen Wert liefern, wenn die Zielmenge, die sie beurteilen sollen, noch leer ist — sonst
ist das Gate zirkulär.

### 🟢 Pitfall #222 — Arithmetisches Mittel und geometrische Kompoundierung derselben Equity-Kurve sind nicht vorzeichengleich [BEHOBEN: GH-#756]
**Symptom:** `sign(oos_sortino_period)` wich in bis zu 43 % der Trials einer Study von
`sign(oos_total_return)` ab, obwohl `tournament.json`s `aggregation_note` behauptete, beide seien
"per Konstruktion kohärent" (dieselbe Divergenz motivierte ursprünglich fälschlich #589, das den
Aggregationspfad statt der Renditedefinition korrigierte).
**Root-Cause:** Der Sortino-Zähler war das ARITHMETISCHE Mittel der Perioden-Returns, `total_return`
ist GEOMETRISCH kompoundiert; die Differenz (Volatilitäts-Drag ≈ σ²/2) lässt bei jeder Strategie mit
`|mean(r)| < σ²/2` (Edge nahe null, Volatilität dominant) die Vorzeichen divergieren —
mathematisch, nicht als Datenqualitätsproblem.
**Fix/Regel:** Perioden-Returns werden als LOG-Returns berechnet (`np.log1p(pct_change)`,
`backtest_runner._calculate_stats`) ⇒ `Σ log(1+rᵢ) = log(1+total_return)` ⇒
`sign(sortino)==sign(total_return)` gilt seither TATSÄCHLICH per Konstruktion, ohne Toleranz.
Generalisierte Regel: `sign(mean(r)) == sign(Π(1+r)−1)` als Invariante zu prüfen prüft eine
mathematische Falschaussage — Log-Returns sind die einzige Renditedefinition, unter der Zähler und
Nenner denselben Pfad beschreiben.

### 🟢 Pitfall #223 — Der Standardfehler gehört zur Statistik, nicht zur Formel-Familie [BEHOBEN: GH-#757]
**Symptom:** Ein H0-Monte-Carlo-Test zeigte `P(PSR ≥ 0.75)` bei 31–32 % statt der nominellen 25 % —
die effektive Eligibility-/Promotion-Fehlerrate von `oos_min_psr` war systematisch inflationiert
(`T ∈ {200, 1000, 4320}`, `N ≥ 4000`).
**Root-Cause:** `psr_z`/`lo2002_sharpe_variance` sind die asymptotischen Sampling-Varianz-Formeln
eines SHARPE-Schätzers (μ̂/σ̂, Delta-Methode über (μ̂, σ̂²)); der Eligibility-/Promotion-Pfad übergab
aber `sortino_period` (μ̂/Downside-Deviation) — eine ANDERE Sampling-Verteilung, für die diese
Varianzterme nicht hergeleitet sind. Eine Kennzahl in eine fremde Signifikanzformel eingesetzt macht
die nominelle Fehlerrate ungültig, unabhängig davon, wie plausibel die Formel aussieht.
**Fix/Regel:** Stationary-Bootstrap-Standardfehler (`deflation.bootstrap_psr_z`, Politis/Romano)
statt der Sharpe-Sampling-Formel — korrekt FÜR DIE TATSÄCHLICH VERWENDETE Statistik, verifiziert via
`automation/optimizer/calibration.py::calibrate_psr_gate` (`P(PSR≥0.75) ∈ [23 %, 27 %]`).
Generalisierte Regel: jedes Gate mit einer behaupteten nominellen Fehlerrate braucht einen
H0-Monte-Carlo-Test, der genau diese Rate verifiziert — eine Signifikanzformel ist an ihre
Schätzer-Familie gebunden, nicht an einen beliebigen Punktschätzer mit ähnlicher Form.

### 🟢 Pitfall #224 — Zwei Inferenzmethoden im selben Selektionspfad sind ein Filter, kein Test [BEHOBEN: GH-#758]
**Symptom:** Die Eligibility-Stufe (`backtest_runner._calculate_stats`) und die Promotion-Stufe
(`confirm.py`s Holdout-DSR) nutzten vor #757/#758 unterschiedliche Inferenzverfahren für denselben
statistischen Grundbegriff (PSR/DSR) — eine Ablehnung auf dem Holdout konnte tatsächlich aus der
Diskrepanz zwischen den beiden Verfahren stammen, wurde aber der Holdout-Stufe selbst zugeschrieben.
**Root-Cause:** Zwei verschiedene Inferenzmethoden im selben Selektionspfad wirken wie ein
zusätzlicher, UNKONTROLLIERTER Filter (jede Divergenz zwischen den Verfahren selektiert Kandidaten,
die zufällig auf der einen Seite der einen Schwelle liegen, auf der anderen aber nicht) — kein
zusätzlicher, informativer statistischer Test.
**Fix/Regel:** Beide Stufen verwenden seit #757/#758 DIESELBE Bootstrap-Inferenz
(`bootstrap_psr_z`) — Weg B aus #757 vereinheitlicht dies automatisch; telemetriert als
`deflation_inference_method` (`stationary_bootstrap` im Regelfall, `sharpe_formula_fallback` nur bei
< 5 Holdout-Perioden-Returns, ein dokumentierter Rest-Abweichungsfall statt eines stillen
Doppelstandards). Generalisierte Regel: zwei Inferenzmethoden im selben Selektionspfad sind KEIN
zusätzlicher Test — sie erzeugen einen unkontrollierten, undokumentierten Filter, dessen Ablehnungen
der falschen Stufe zugeschrieben werden.

### 🟢 Pitfall #225 — `None → 0.0` in der Parsing-Schicht vernichtet die Unterscheidung „nicht messbar" / „gemessen null" [BEHOBEN: GH-#759]
**Symptom:** `oos_win_rate` kollabierte fehlende Werte (kein Trial evaluiert, kein `win_rate`-Key)
auf `0.0` — dieselbe Zahl wie eine ECHT beobachtete Null-Win-Rate. `any_arm_unreachable_policy=
'recalibrate'` rekalibrierte die Schwelle dadurch teils/ausschliesslich aus Missing-Data-Sentinels
(empirisch: 305/459 `[#660]`-Warnungen mit `p99=0.0000`, davon 255 aus Studies mit 0 evaluierten
Trials).
**Root-Cause:** Die Parsing-Schicht (`parsing.TournamentMetrics`) setzte einen Default (`0.0`) VOR
jeder nachgelagerten Policy-Entscheidung — dieselbe Zahl bedeutete gleichzeitig "nicht messbar" UND
"gemessen null", und keine nachgelagerte Policy konnte die beiden Fälle mehr unterscheiden.
**Fix/Regel:** `TournamentMetrics.oos_win_rate` ist seit #759 `float | None` (kein `0.0`-Default im
Parser); Defaults gehören an die KONSUMSTELLE, nie in den Parser. `resolve_any_arm_policy` verlangt
zusätzlich einen Mindest-Stichprobenumfang ECHTER (None-gefilterter) Beobachtungen
(`any_arm_min_observations`) — eine kleine, aber ECHTE Stichprobe (3–4 Beobachtungen) dient sonst
fälschlich als belastbare Rekalibrierungs-Basis. Generalisierte Regel: `None → 0.0` in der
Parsing-Schicht vernichtet die Unterscheidung "nicht messbar" von "gemessen null" — Defaults werden
IMMER an der Konsumstelle gesetzt, nie im Parser, UND Diagnosen brauchen einen Mindest-
Stichprobenumfang echter Beobachtungen.

### 🟢 Pitfall #226 — Die aktive Gate-Menge ist Config, keine Code-Konstante [BEHOBEN: GH-#760]
**Symptom:** `_GATE_COLLINEARITY_KEYS`/`_GATE_COLLINEARITY_TO_CONJUNCTION_KEY` (`reward.py`) waren
ein eingefrorenes Tupel, das die zum Zeitpunkt seiner Einführung aktiven `eligible_requires_all`-Gates
hardcodierte. Spätere Katalog-Fixes (#676/#677/#697), die Gates aus dem DEFAULT entfernten, liessen
die Kollinearitäts-Warnung lautlos weiter Gates diagnostizieren, die es nicht mehr gab — während die
tatsächlich wirksamen Gates ungeprüft blieben.
**Root-Cause:** Eine Code-Konstante, die eine LIVE-Config-Menge (`eligible_requires_all`) spiegeln
soll, driftet lautlos von ihr weg, sobald sich die Config ändert, ohne dass der Code mitgezogen wird
— dieselbe Fehlerklasse wie #649 (Config-Key vs. Handler-Registry), nur eine Ebene tiefer
(Diagnose-Ebene statt Gate-Ebene selbst).
**Fix/Regel:** `_active_gate_collinearity_keys(tournament_cfg)` leitet die aktive Gate-Menge
JEDESMAL LIVE aus `tournament_cfg['eligible_requires_all']` ab (mit `oos_`-Normalisierung) statt aus
einer eingefrorenen Code-Konstante; die hartcodierten Tupel wurden VOLLSTÄNDIG entfernt
(regressionsgesichert über `test_no_hardcoded_gate_name_list_remains_in_reward_module`).
Generalisierte Regel: eine Menge, die eine Config-Struktur spiegeln soll, wird IMMER live aus der
Config abgeleitet — nie als Code-Konstante eingefroren, so bequem das zum Zeitpunkt der Einführung
auch scheint.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #753–#767)
- `optimizer.json.plateau_min_modelled_trials` (Default 48, Fallback `max(32, 2·n_startup_trials)`)
  — Pitfall #219, #753.
- `optimizer.json.tier_escalation_min_constraint_progress` (Default 0.05) — Pitfall #221, #754.
- `optimizer.json.sweep_max_workers` (Default `null` ⇒ `max(1, cpu−2)`) — #755 (Per-Study-Seed statt
  Sweep-Serialisierung; `seed_effective`/`n_trials_budget`/`n_startup_trials` neu als Study-User-Attrs).
- `optimizer.json.n_startup_trials_high_dim_threshold` (Default 8) /
  `optimizer.json.n_startup_trials_per_dim_high_dim` (Default 3) — #762 (Squeeze dim=9: 18→27
  Startpunkte, ComboTrendVwap dim=14: 28→42).
- `tournament.json.any_arm_min_observations` (Default 10) — Pitfall #225, #759.
- `tournament.json.psr_bootstrap_resamples` (Default 200) — Pitfall #223, #757.
- `optimizer.json.reward_semantics_version` 14 → 15 — #766 (auslösend #756/#757, siehe dortiger
  Changelog-Eintrag für die explizite #764-Präzisierung).

### 🔒 Watertight Invariants (Issue-Katalog #753–#767) — für künftige Agenten
- **`check_log_return_coherence`** (`invariants.py`) — `sign(oos_sortino_period) ==
  sign(oos_total_return)` gilt seit #756 PER KONSTRUKTION; ein verbleibender
  `oos_coherence_violation` ist ein echter Aggregationsdefekt, keine erwartete Restrate mehr
  (Pitfall #222).
- **`check_metric_sentinel_absence`** (`invariants.py`) — regressionsgesichert gegen ein erneutes
  `None → 0.0`-Sentinel-Collapse in der Parsing-Schicht (Pitfall #225).
- **`check_config_key_registry`** (`invariants.py`) prüft seit #765 ZUSÄTZLICH, dass kein
  `_schema.fields`-Text eine AKTUELLE `eligible_requires_all`/`eligible_requires_any`-Mitgliedschaft
  behauptet (via den deklarierten Markern `"in eligible_requires_all (HART)"`/`"in
  eligible_requires_any (aktiver OR-Arm)"`), die die tatsächliche Liste nicht widerspiegelt
  (Pitfall #226-Fehlerklasse auf der Dokumentations-Ebene statt der Code-Ebene).
- **`reward_term_variance_table`** (`invariants.py`, #764) liefert `var_contrib` je Reward-Term
  gegen den `[0.02, 0.30]`-Zielkorridor als First-Class-Feld im `#742`-Report — eine tatsächliche
  Gewichts-Rekalibrierung/Term-Entfernung bleibt bewusst zurückgestellt (braucht eine reale Kohorte
  ≥ 50 Studies NACH Kohorte A/B, siehe `optimizer.json`s v15-Changelog).
- **`bounds.theoretical_max_oos_trades`** (#762) — obere Schranke der an der schnellsten im
  Suchraum zulässigen Zyklus-Konfiguration erreichbaren OOS-Trades; ein Wert unter `oos_min_trades`
  ist mechanisch ein Bounds-Bug, kein Signalqualitäts-Befund.

## Issue-Katalog #768–#793 — Budget-Skalierung, Renditeserien-Kohärenz, DSR-Multiplizität & Denylist-Evidenz (GitHub-Issues #743/#742, Sitzung 2026-07-26)

Zwei aufeinanderfolgende Forensik-Audits (GitHub-Issue #743, danach #742) auf einem 44,2 %/13,1 %
Budgetausführungs-Lauf. Kernaussage: die Suche findet inzwischen tatsächlich statt (Kohorte A,
`#768`–`#770`), aber die dabei GEMESSENEN Grössen liefen auf zwei unterschiedlichen Renditeserien
(`#771`/`#772`), einer TSLA-kalibrierten Kostenkonstante (`#774`/`#775`), einer dreifach-kollinearen
Gate-Konjunktion (`#776`) und einer Multiple-Testing-Korrektur, die Überlebende statt Versuche zählte
(`#784`) — vier unabhängige Auslöser für `reward_semantics_version` 15 → 16 (`#781`), plus ein
fünfter (`#788`, Sentinel-Kollaps-Wiederkehr). Ergänzend: Report-Präzision (`#783`/`#785`/`#786`/
`#790`/`#791`), Bounds-/Denylist-Evidenz (`#777`/`#778`) und Log-Attribution im parallelen Sweep
(`#780`). `#779` (Reward-Term-Rekalibrierung, eigener Bump v17) bleibt EXPLIZIT zurückgestellt — sie
erfordert einen vollständigen Re-Run mit ≥ 50 Studies NACH allen Fixes dieses Katalogs, der in
dieser Sitzung nicht produziert wurde (kein Ersatz durch synthetische/erfundene Kalibrierwerte).

### 🟢 Pitfall #227 — Wird eine Schwelle dimensionsabhängig gemacht, müssen ALLE Schwellen desselben Budget-Pfads mitgezogen werden [BEHOBEN: GH-#768]
**Symptom:** `n_trials` und `n_startup_trials` skalieren mit der Suchraum-Dimension `dim`,
`plateau_min_modelled_trials` (#753) blieb eine FLACHE Konstante (48) — der vor dem
`ZERO_ELIGIBLE`-Urteil tatsächlich ausgeführte Budgetanteil fiel dadurch monoton von 64 % (dim=2)
auf 32 % (dim=14, ComboTrendVwap), exakt invers zur Anforderung: ein höher-dimensionaler Raum
braucht MEHR, nicht weniger modellierte Trials für ein belastbares Urteil.
**Root-Cause:** Eine neue Konstante NEBEN zwei bereits dimensionsskalierenden Grössen ist ein
latenter Skalenfehler — sie wird über `dim` implizit relativ IMMER kleiner, ohne dass sich ihr
Nominalwert je ändert.
**Fix/Regel:** `plateau_min_modelled_trials_per_dim` (Default 8) koppelt die Schwelle selbst an
`dim`: `min_for_zero_eligible = n_startup_trials + max(plateau_min_modelled_trials, ceil(k·dim))`,
gedeckelt auf `n_trials`. Generalisierte Regel: sobald EINE Schwelle eines Budget-Pfads
dimensionsabhängig gemacht wird, müssen ALLE Geschwister-Schwellen desselben Pfads auf dieselbe
Abhängigkeit geprüft werden — eine übersehene flache Konstante zwischen zwei skalierenden Grössen
ist kein Sonderfall, sondern der Regelfall.

### 🟢 Pitfall #228 — Ein „behobener" Pitfall ist nur in dem Zweig behoben, in dem der Fix landete [BEHOBEN: GH-#769]
**Symptom:** `#753` entkoppelte den `ZERO_ELIGIBLE`-Zweig von `n_startup_trials` (Pitfall #219),
liess aber den GESCHWISTER-Zweig `STRUCTURAL_ALL_UNEVALUABLE` bewusst an der alten Schwelle
(`n_startup_trials + floor_plateau_k`) — dort urteilte der Guard weiterhin nach NULL modellierten
Trials, obwohl Pitfall #219/#220 im Katalog als `[BEHOBEN]` markiert waren.
**Root-Cause:** „Behoben" bezieht sich auf den KONKRETEN Code-Pfad, den ein Fix durchlief — nicht
auf die Fehlerklasse als Ganzes. Ein Guard mit zwei parallelen Zweigen, die dieselbe strukturelle
Frage stellen, kann in einem Zweig repariert und im anderen unverändert defekt bleiben, ohne dass
irgendein Test das aufdeckt (beide Zweige feuern auf unterschiedlichen, sich gegenseitig
ausschliessenden Bedingungen).
**Fix/Regel:** Beim Markieren eines Pitfalls als `[BEHOBEN]` MUSS jeder Geschwister-Zweig derselben
Guard-Funktion explizit geprüft und der verbleibende Scope dokumentiert werden — „behoben in Zweig
A, Zweig B ungeprüft" ist eine zulässige, aber PFLICHT anzugebende Zwischenaussage, keine stille
Lücke.

### 🟢 Pitfall #229 — Eine Diagnose-Kategorie, die „null" und „zu wenig" zusammenfasst, verhindert genau die Unterscheidung, für die sie gebaut wurde [BEHOBEN: GH-#769]
**Symptom:** `binding_cause = 'signal_frequency'` deckte sowohl `median_is_trades = 0`
(parameterunabhängig — die Strategie feuert im GESAMTEN Suchraum nie) als auch `= 9`
(parameterabhängig — sie feuert, erreicht nur `oos_min_trades` nicht) ab — 133 vs. 119 Studies mit
GEGENSÄTZLICHER Handlungsempfehlung (Denylist vs. Bounds-Kalibrierung) unter demselben Label.
**Root-Cause:** Eine Diagnose-Kategorie, die zwei Ursachen mit entgegengesetzter Handlungskonsequenz
bündelt, ist keine Diagnose — sie verschiebt die eigentliche Unterscheidung auf den Menschen, der
den Bericht liest, GENAU die Arbeit, die die Kategorie automatisieren sollte.
**Fix/Regel:** `'signal_frequency'` wurde in `'signal_absent'` (0 evaluable, IS-Aktivität über
JEDEN Trial null — parameterunabhängig) und `'signal_sparse'` (0 evaluable, aber positive
IS-Aktivität — parameterabhängig, tunebar) aufgespalten. Generalisierte Regel: zwei Ursachen mit
unterschiedlicher Handlungskonsequenz gehören NIE unter dieselbe Diagnose-Kategorie, selbst wenn
ihr unmittelbares Symptom (hier: 0 evaluable Trials) identisch aussieht.

### 🟢 Pitfall #230 — Zwei Grössen, die „denselben Equity-Pfad" beschreiben sollen, müssen aus EINER Serie stammen — eine Identität im Docstring ist keine Invariante [BEHOBEN: GH-#771/#773]
**Symptom:** `total_return` (aus `mtm_frames`, PER FOLD-SEGMENT berechnet) und `period_rets` (aus
`mtm_series`, über die volle Kalenderspanne konkateniert) unterschieden sich exakt um die
Nahtstellen-Returns an jedem Fold-Übergang — die von `#756` behauptete `sign(oos_sortino_period) ==
sign(oos_total_return)`-Identität war dadurch NIE erfüllbar, obwohl der Docstring sie als gegeben
postulierte.
**Root-Cause:** Eine Kohärenz-Invariante, die nur als Kommentar/Docstring-Behauptung existiert,
wird nie verifiziert — der `#756`-Fix optimierte die BERECHNUNG (Log-Returns) einer Serie, während
die andere Serie strukturell aus einer ANDEREN Bar-Menge gebildet wurde; die Behauptung blieb falsch,
bis ein MASCHINELLER Test (`assert_return_series_identity`) sie prüfte.
**Fix/Regel:** `total_return` bevorzugt seither `mtm_series` (dieselbe Fold-Segment-Vereinigung wie
`period_rets`) über `mtm_frames`, mit einem Warnsignal (`NON_CONTIGUOUS_FOLD_SEGMENTS`) bei
Diskrepanz; `check_log_return_coherence` (seit #773 ein STUDY-ABSCHLUSS-Wächter, kein
Report-Nachtrag mehr) verifiziert die Identität als harten Test. Generalisierte Regel: eine
Identität zwischen zwei Grössen, die „denselben Pfad" beschreiben sollen, MUSS als ausführbarer Test
formuliert werden — ein Docstring-Kommentar ist eine Hoffnung, keine Invariante.

### 🟢 Pitfall #231 — Wird der Zähler auf eine andere Fensterung umgestellt, muss der Benchmark im selben PR mitgezogen werden [BEHOBEN: GH-#772]
**Symptom:** `#632` machte den Buy&Hold-Benchmark (`oos_buyhold_return`) PER FOLD kompoundiert —
WEIL die Strategie-Renditeserie (`total_return`) es zu diesem Zeitpunkt war. `#771` kehrte die
Strategie-Seite auf die Fold-Segment-UNION um (Pitfall #230) — der Benchmark blieb zunächst
per-Fold, wodurch Zähler (Strategie) und Nenner (Benchmark) unterschiedliche Bar-Mengen abdeckten.
**Root-Cause:** Zähler und Nenner einer relativen Kennzahl (hier: Excess-Return) müssen über
DENSELBEN Zeitraum/dieselbe Bar-Menge gebildet werden; eine einseitige Umstellung der Fensterung
reproduziert denselben Span-Bug (`#552`) mit umgekehrtem Vorzeichen.
**Fix/Regel:** `oos_buyhold_return`/`oos_excess_return` verwenden seit `#772` dieselben
`_fold_segments_half_open`/`_concat_half_open`-Helfer wie `oos_mtm` — EINE Bar-Menge für beide
Seiten der Kennzahl, mit einem `BENCHMARK_SPAN_MISMATCH`-Guard bei Index-Divergenz. Generalisierte
Regel: eine Fensterungs-Umstellung des Zählers erzwingt IM SELBEN PR eine Prüfung (und ggf.
Umstellung) jedes Nenners, der auf denselben Zeitraum referenziert.

### 🟢 Pitfall #232 — Eine aus einem Referenzsymbol hergeleitete Kostenkonstante wird beim Universumswechsel zur stillen Fehlkalibrierung [BEHOBEN: GH-#774/#775]
**Symptom:** `penalty_turnover_weight = 0.0003` (3 bps) war EXPLIZIT aus TSLA (commission 1 bps +
spread 2 bps) hergeleitet; das Universum enthält acht Krypto-Symbole mit 16 bps realen
Round-Trip-Kosten — die Turnover-Strafe unterschätzte hochfrequente Konfigurationen GENAU dort, wo
die realen Kosten am höchsten sind (Faktor 5,3). Parallel las
`_read_default_round_trip_cost_bps` den DEFAULT-Spread statt der (bereits existierenden)
Symbol→Asset-Class→DEFAULT-Auflösungskette.
**Root-Cause:** Wo eine Auflösungskette (Symbol → Asset-Class → DEFAULT) bereits existiert, darf
KEINE Parallelkonstante danebenstehen, die von einem einzelnen Referenzsymbol abgeleitet wurde — sie
verhält sich korrekt für das Referenzsymbol und driftet für jedes andere Symbol lautlos weg, ohne
dass ein Fehler oder eine Warnung entsteht.
**Fix/Regel:** Die Turnover-Strafe konsumiert `round_trip_cost_bps` (bereits pro Trial gestempelt,
asset-class-aufgelöst) statt `penalty_turnover_weight`; `penalty_turnover_weight` bleibt NUR als
Fallback für fehlende Kosten-Telemetrie. `_read_default_round_trip_cost_bps` nutzt seither dieselbe
Auflösungskette wie der Worker. Generalisierte Regel: eine Kostenkonstante, die für EIN Symbol
hergeleitet wurde, gehört nie neben eine bereits bestehende, generische Auflösungskette — sie MUSS
durch diese Kette ersetzt werden, sobald das Universum über das Referenzsymbol hinauswächst.

### 🟢 Pitfall #233 — Parallelisiert man den Ausführungspfad, muss die Log-Attribution im selben PR mitgezogen werden [BEHOBEN: GH-#780]
**Symptom:** Seit `#755` laufen mehrere Studies (`ThreadPoolExecutor`) parallel in EINEN Logger.
Von den analysierten Warnungsklassen trugen nur `[#565]`/`[#620]` Strategie/Symbol; `REWARD_TERM_
INERT` (983), `[#667]` (695), `[#660]` (232), Zero-Eligible-Plateau (705), Floor-Plateau (252) und
`[#597]` (32) trugen sie NICHT. Eine reihenfolgebasierte Heuristik ("aktuelle Study = letzter
`[#565]`-Marker") lieferte nachweislich falsche Zahlen (590 statt 705 Zero-Eligible-Zuordnungen) und
übersah `ComboTrendVwapStrategy` vollständig (die Strategie hat einen Champion, emittiert daher nie
`[#565]`).
**Root-Cause:** Eine Parallelisierungs-Änderung des Ausführungspfads (hier: `#755`s
`ThreadPoolExecutor`) betrifft implizit JEDE nachgelagerte Log-Zeile, die vorher unbeobachtet von
genau EINER Study ausging — eine Attribution, die vorher „kostenlos" (weil sequenziell) galt, muss
ab dem Parallelisierungs-PR EXPLIZIT hergestellt werden, sonst wird sie durch eine (nachweislich
falsche) Heuristik ersetzt.
**Fix/Regel:** `log_manager.bind_study_context` (contextvars, pro nativem Thread implizit isoliert)
bindet strategy/symbol/study_name EINMAL in `optimize_symbol` für die gesamte Study-Lebensdauer;
`StructuredFormatter` (Prosa-Präfix) UND `emit_execution_event` (JSONL-Pflichtfelder) lesen
denselben Kontext, OHNE dass die ~20 bestehenden Log-Call-Sites geändert werden mussten.
Generalisierte Regel: eine Parallelisierung des Ausführungspfads gehört NIE ohne eine begleitende
Prüfung/Herstellung der Log-Attribution in denselben PR.

### 🟢 Pitfall #234 — Ein Fallback-Ausgang, der dasselbe Statuslabel wie der validierte Pfad trägt, ist ein Deployment-Risiko [BEHOBEN: GH-#783]
**Symptom:** Die `#682`-Default-Route (0 symbol-eligible Trials, globaler Default besteht das
Symbol-Holdout-Gate) lieferte `READY_FOR_PR` mit `n_eligible=0`, `best_reward=null` und OHNE
Confirm-Stufe — im Report ununterscheidbar von einer holdout-VALIDIERTEN Promotion. Alle 37 im
`#742`-Katalog beobachteten `#682`-Promotionen stammten zudem aus einem vorzeitigen
`#768`-Plateau-Abbruch (45–64 % Budgetausführung).
**Root-Cause:** Zwei strukturell verschiedene Confirm-Ausgänge (Micro-Tuning-validiert vs.
ungetunter globaler Fallback), die dasselbe String-Label teilen, sind in JEDER nachgelagerten
Automatisierung, die auf dieses Label filtert, nicht unterscheidbar — der Fallback „verschwindet" im
validierten Pfad.
**Fix/Regel:** Die Default-Route liefert seither einen EIGENEN Status (`PROMOTE_GLOBAL_DEFAULT`)
UND ein `promotion_route`-Feld (`'global_default_on_symbol'`) im Proposal/Report; zusätzlich ein
Budget-Vorbedingungs-Gate (`global_default_promotion_min_budget_execution`, #770), das einen
Plateau-Abbruch von einer belastbaren Nullaussage über den Suchraum trennt. Generalisierte Regel:
jeder neue Confirm-/Entscheidungs-Ausgang braucht ein EIGENES Label UND ein Routen-Feld im Artefakt
— niemals dasselbe Label wie ein bereits bestehender, strukturell anderer Pfad.

### 🟢 Pitfall #235 — Eine Multiple-Testing-Korrektur muss die Zahl der VERSUCHE zählen, nicht die der ÜBERLEBENDEN [BEHOBEN: GH-#784]
**Symptom:** `sweep._family_n_from_studies` zählte `oos_eligible is True` (Trials, die den
Eligibility-Filter PASSIERT haben) statt der tatsächlich GEZOGENEN Kandidaten — 13,0 % Passrate
⇒ die familienweite Multiplizität wurde im Mittel um Faktor 7,7 unterschätzt. Zusätzlich koppelte
das die Deflationsschwelle INVERS an die Budgetausführung: Spearman(`n_family`,
Budgetausführung)=+0,220 — eine vorzeitig abgebrochene Study wurde MILDER deflatiert als eine
vollständig durchlaufene.
**Root-Cause:** Die Deflated Sharpe Ratio korrigiert für die Multiplizität der SUCHE (`N` gezogene
Kandidaten) — der Eligibility-Filter selbst ist ein Selektionsschritt und gehört IN die Korrektur,
nicht davor. Wer nur die Überlebenden zählt, misst die Multiplizität NACH der Selektion, die die
Korrektur gerade neutralisieren soll.
**Fix/Regel:** `_family_n_from_studies`/`_family_period_returns_from_studies` zählen seither
`oos_evaluated is True` (Versuche); `deflation_family_floor_mode='budgeted'` (Default) hebt die
Multiplizität zusätzlich auf das GEPLANTE Budget an, wenn ein Abbruch die tatsächlich gezogenen
Kandidaten reduziert hat. Generalisierte Regel: JEDE Multiple-Testing-Korrektur (DSR, Bonferroni,
FDR, …) muss über der Menge der GEZOGENEN Versuche gebildet werden — ein nachgelagerter
Selektionsfilter (Eligibility, Signifikanz-Schwelle, …) gehört strukturell IN die Korrektur, nie vor
sie.

### 🟢 Pitfall #236 — Ein Invarianten-Check, der den Erfolgsfall unbedingt bestehen lässt, prüft den einzigen Fall nicht, der zählt [BEHOBEN: GH-#785]
**Symptom:** `check_rejection_chain_completeness` war für `status == 'READY_FOR_PR'` PER
KONSTRUKTION `True` (`passed = True if status in (None, 'READY_FOR_PR') else ...`) — genau dort
fehlte in allen 37 beobachteten Fällen die `confirm_or_selection`-Stufe, und 1736/1736 Records
gingen trotzdem grün durch den Check.
**Root-Cause:** Ein Check, der als „vollständige Kettenprüfung" benannt ist, aber nur EINE
Implikation prüft (Ablehnung ⇒ konkreter Grund), ist auf dem PROMOTETEN Pfad vakuum — der Erfolgsfall
ist der einzige, bei dem eine fehlende Nachweiskette tatsächlich ein Deployment-Risiko ist, und
genau er wurde nie geprüft.
**Fix/Regel:** `rejection_chain` wurde zu `decision_chain` (jede Stufe mit `{stage, passed, detail}`,
auch bestandene); `check_rejection_chain_completeness` fordert für `promote=True` die ANWESENHEIT
aller obligatorischen Stufen mit `passed=True`. Generalisierte Regel: ein Invarianten-Check, der
für den Erfolgsfall unbedingt (`True` ohne Bedingung) besteht, prüft diesen Fall NICHT — die
Erfolgs-Seite jeder binären Entscheidung braucht eine ausdrückliche, positive Nachweiskette, nicht
nur eine leere Implikations-Lücke.

### 🟢 Pitfall #237 — Ein Fix ohne gemessenes Akzeptanzkriterium bleibt eine Hypothese; wird das Kriterium verfehlt, ist der Fix gescheitert, nicht „teilweise wirksam" [TEILWEISE BEHOBEN: GH-#787]
**Symptom:** `#762` hob `n_startup_trials_per_dim_high_dim` für SqueezeBreakout von 18 auf 27
Startpunkte, mit der dokumentierten Begründung „k=2 ist für multivariate=True,group=True bei dim≥8
knapp für die Kovarianzschätzung". Nach dem Merge: weiterhin 0/124 eligible Studies, bindendes Gate
in 119/124 Symbolen `oos_min_trades` (ein Trade-FREQUENZ-, kein Kovarianzschätzungs-Problem) — der
Fix verschob den Plateau-Stop zusätzlich von 64 auf 75 Trials (mehr Budget verbrannt, keine
Wirkung).
**Root-Cause:** Ein Fix, dessen Root-Cause-Hypothese nie gegen die tatsächlichen Trial-Daten
verifiziert wurde, kann eine PLAUSIBLE, aber falsche Ursache adressieren — ohne ein explizit
gemessenes Akzeptanzkriterium bleibt unklar, ob er wirkte, und ein späterer Agent markiert ihn
mangels Gegenbeweis stillschweigend als erledigt.
**Fix/Regel:** Die bindende Ursache wird aus `near_miss_deltas`/dem `binding_gate`-Histogramm je
Strategie ABGELEITET, nicht geraten, BEVOR ein Sampler-Parameter geändert wird
(`report._binding_gate_histogram_by_strategy`, seit #790 ausschliesslich über AKTIVE Gates). Der
`#762`-Schema-Text ist als widerlegt zu kennzeichnen, sobald der Re-Run die behauptete Wirkung
verfehlt. Generalisierte Regel: ein Fix ohne MESSBARES Akzeptanzkriterium ist eine Hypothese, keine
Behebung — verfehlt der Re-Run das Kriterium, ist der Fix nachweislich GESCHEITERT und so zu
dokumentieren, nicht als „teilweise wirksam" stehen zu lassen. *(Vollständig behoben ist dieser
Pitfall erst mit der im Issue-Katalog geforderten Such­raum-Kalibrierung/dem PR-dokumentierten
Deaktivierungsbeschluss für die vier betroffenen Strategien — siehe `#787`-Restarbeit.)*

### 🟢 Pitfall #238 — Eine Diagnose-Tabelle, die deaktivierte Gates mitführt, priorisiert falsch [BEHOBEN: GH-#790]
**Symptom:** `near_miss_deltas` enthielt zehn Gates; nur vier davon waren tatsächlich in
`eligible_requires_all`/`_any` aktiv — vier wurden per `#650`/`#676`/`#677`/`#697` explizit
deaktiviert. Über 321 Studies mit eligiblen Trials meldeten 283 (88 %) ein „bindendes" Gate, das
GAR KEINE Eligibility-Entscheidung mehr traf (`oos_min_profitable_folds_frac` 152×,
`oos_min_expectancy` 131×) — jede Priorisierung anhand dieser Tabelle führte in die Irre.
**Root-Cause:** Eine Diagnose-Tabelle, die WEICHE (deaktivierte, nur noch Distanz-Telemetrie) und
HARTE (aktive, Eligibility-wirksame) Gates ungetrennt nebeneinander führt, meldet als „bindend" oft
das Gate mit dem numerisch negativsten Delta — unabhängig davon, ob es je eine Entscheidung trifft.
Dieselbe Fehlerklasse wie Pitfall #226 (Kollinearitäts-Check gegen eine veraltete Key-Liste), hier
auf der Diagnose- statt der Alarm-Ebene.
**Fix/Regel:** `near_miss_deltas` wurde zu `{'binding': {...}, 'soft': {...}}` — die Trennung stammt
ZUR LAUFZEIT aus `tournament_cfg` (`reward._active_gate_collinearity_keys`, dieselbe Quelle wie
Pitfall #226), keine zweite gepflegte Liste; `binding_gate` ist `argmin` AUSSCHLIESSLICH über
`binding`. Generalisierte Regel: eine Diagnose-Kategorie, die aktive und deaktivierte Config-Zustände
mischt, muss die Trennung LIVE aus derselben Config-Quelle ableiten wie die Aktivierungs-Entscheidung
selbst — nie aus einer zweiten, separat gepflegten Liste.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #768–#793)
- `optimizer.json.plateau_min_modelled_trials_per_dim` (Default 8) — Pitfall #227, #768.
- `optimizer.json.floor_plateau_k` (jetzt EXPLIZIT dokumentiert, Default weiterhin 0) — #769.
- `optimizer.json.min_median_budget_execution` (Default 0.5) — #770.
- `optimizer.json.max_coherence_violation_rate` (Default 0.01, Übergangswert) — #773.
- `optimizer.json.global_default_promotion_min_budget_execution` (Default 0.9) — Pitfall #234, #783.
- `optimizer.json.bounds_widening_factor` (Default 1.5, Fallback 0.3) — Pitfall #232-Klasse, #777.
- `optimizer.json.max_gate_collinearity_affected_fraction` (Default 0.20) — #776.
- `tournament.json.gate_collinearity_threshold` (Default 0.90, EINE Schwelle für drei
  Einstiegspunkte, ersetzt zwei koexistierende Code-Defaults 0.90/0.95) — #792.
- `tournament.json.deflation_family_floor_mode` (Default `'budgeted'`, Alternative `'attempted'`)
  — Pitfall #235, #784.
- `optimizer.json.reward_semantics_version` 15 → 16 — #781 (fünf Auslöser: #771/#772, #774/#775,
  #776, #784, #788; siehe dortiger Changelog-Eintrag).

### 🔒 Watertight Invariants (Issue-Katalog #768–#793) — für künftige Agenten
- **`check_gate_collinearity_consolidation`** (`invariants.py`, #776) — konsumiert den seit `#679`
  unkonsumierten Redundanz-Alarm sweep-weit: FAIL ab `max_gate_collinearity_affected_fraction`
  (Default 20 %) betroffener Studies.
- **`check_budget_execution`** (`invariants.py`, #770) — FAIL, wenn der Median von
  `budget_executed_fraction` über alle Studies eines Laufs `min_median_budget_execution`
  unterschreitet.
- **`check_promotion_inference_coverage`** (`invariants.py`, #791) — jeder promotete Kandidat
  (`READY_FOR_PR`/`PROMOTE_GLOBAL_DEFAULT`) MUSS `inference_method.promotion.applied == True`
  tragen; `'not_applicable'` ist eine explizit dokumentierte Nichtanwendbarkeit, kein stiller Gap.
- **`check_metric_sentinel_absence`** (`invariants.py`, #788) — auf ALLE OOS-Metriken derselben
  Erzeugungsstelle erweitert (`oos_win_rate`, `oos_profit_factor`, `oos_expectancy`,
  `oos_total_return`, `oos_sortino`, `oos_psr`, `oos_sortino_period`), nicht mehr nur
  `oos_win_rate` (Pitfall #225-Fehlerklasse, hier sechsmal wiederholt).
- **`assert_eligible_requires_all_not_redundant`** (`reward.py`, #776/#792) — die EINE deklarative
  Kollinearitäts-Schwelle (`gate_collinearity_threshold`) gilt für ALLE DREI Einstiegspunkte
  (`assert_gate_collinearity_guard`, `gate_collinearity_redundancy_alarm`, diese Funktion selbst);
  kein Code-Default mehr.
- **`recommend_diagnosis_action`** (`sweep_diagnostics.py`, #778) — eskaliert NIE mehr auf
  `'denylist'` für `'signal_sparse'`/`'hold_duration'` (parameterabhängig); für `'signal_absent'`
  nur bei `budget_executed_fraction >= 0.9` UND `n_runs_confirmed >= 2` (Cache-Einträge tragen
  `first_seen_run_id`/`n_runs_confirmed`).
- **`bind_study_context`** (`log_manager.py`, #780) — jede Optimizer-Log-Zeile/jedes Event ist seit
  `optimize_symbol` (contextvars, pro nativem Thread isoliert) eindeutig strategy/symbol/study_name
  zuordenbar, ohne Reihenfolge-Heuristik.

## Issue-Katalog #794–#815 — Storage-Lebenszyklus, Inferenz-Korrektheit & Selektions-Integrität (GitHub-Issues #745/#746, Sitzung 2026-07-28)

Zwei gekoppelte Katalog-Audits auf demselben 21-Stunden-Absturz-Lauf (`ISSUES_optimizer_storage_
runtime_20260727.md`, #794–#800, UND der direkte Nachfolge-Katalog #801–#816): Storage/Runtime
zuerst (sonst läuft jeder Re-Run wieder in `ENOSPC`), danach Inferenz-Korrektheit (P0, blockiert
alles Nachgelagerte), Suchbudget-Kalibrierung und Selektions-Integrität. **Kohorte Storage (kein
Purge, #794–#800):** #796 (`freeze_study_config` erhält `copy_config=False` — die Study-Config wird
EINMAL eingefroren statt bei jedem Trial neu kopiert, Pitfall #243-Klasse); #797 (Subprozess-Logs
folgen derselben Policy wie der Elternprozess statt eigener, unkontrollierter Verbosity); #800
(`bind_study_context`, #780, leckte contextvars-Tokens über Study-Grenzen — jetzt symmetrisch
reset); #794/Pitfall #243 (kontinuierliche statt Sweep-Ende-Retention — der eigentliche Root-Cause
des Platten-Absturzes); #798 (`period_returns` wird nach dem Parsing aus `tournament_result.json`
gestrippt — kein Konsument liest sie mehr von der Platte, #813 macht das für `oos_period_returns`
in `user_attrs` erst recht relevant); #795 (`disk_guard` bricht VOR dem nächsten Symbol ab, wenn
freier Speicher `min_free_disk_gb` unterschreitet, statt mitten in einem Trial zu crashen); #799
(die Per-Symbol-Sweep-Schleife wird transaktional — ein Symbol-Fehler beendet nicht den gesamten
Lauf, sondern wird isoliert übersprungen und telemetriert). **Kohorte A — Inferenz-Korrektheit
(P0, Purge):** #801/#802/Pitfall #240/#241/#242 (`assert_return_series_identity`/`_calculate_stats`
erzwingen `skipna=False` bzw. prüfen Endlichkeit VOR jeder Aggregation; `assert_pandas_version_
supported` als Preflight-Guard gegen driftende Bibliotheks-NaN-Semantik); #803 (`REJECT_OOS_
INVALID_METRICS` — `oos_coherence_violation`/`equity_ruined` fliessen jetzt UNBEDINGT, nicht nur
über die alte studienweite Abbruchrate, in `_evaluate_oos_eligibility` ein); #804/Pitfall #239
(strukturierter `inference_diagnostics`-Rückkanal statt Subprozess-`logging`, das der
Elternprozess nie sieht — `run_optimization._reemit_inference_diagnostics` re-emittiert jede
Diagnose mit voller Study-Identität). **Kohorte B — Suchbudget (P0/P1, Purge nur #813/#814):**
#805/Pitfall #244 (`structural_min_modelled_trials_per_dim` ersetzt den stillschweigend auf `0`
degenerierten `floor_plateau_k` — `assert_structural_min_modelled_trials_valid` lehnt den
degenerierten Wert jetzt fail-loud ab); #806/Pitfall #245 (`plateau_stop_missed_probability`,
Dreierregel: 0 Erfolge in `m` Trials ⇒ 95%-Konfidenz-Obergrenze `3/m` auf die wahre Erfolgsrate,
statt eines unbegründeten festen Trial-Zählers); #807 (`load_diagnosed_pairs_cache`-Sekundärsignal
prüft symbolweite Daten-Degeneriertheit VOR einem Studien-Abbruch, damit `signal_absent` nicht mit
struktureller Datenarmut verwechselt wird); #808 (`gradient_signal_arm` — DREI gleichrangige Arme
`discovery`/`reward_variance`/`constraint_progress` statt eines einzigen Reward-Varianz-Kriteriums,
das bei winziger `n_eligible` gerade den stärksten Beleg für mehr Budget verwarf); #809
(`GapContinuationStrategy` deaktiviert — bewusste Abweichung von der im Issue vorgeschlagenen
Variante B, siehe eigener Commit-Begründungstext: die Codebase besitzt keinen funktionierenden
RTH-Session-Kalender). **Kohorte C — Selektions-Integrität (P0/P1, Purge #812/#813/#814):**
#810/Pitfall #246 (`gate_consolidation_priority`/`gate_consolidation_protected` als deklarative
Config statt der eingefrorenen `_GATE_CONSOLIDATION_PRIORITY`-Konstante; `assert_gate_priority_
coverage` fail-loud statt `priority.get(k, 99)`-Sentinel); #811/Pitfall #247 (Jaccard-Ähnlichkeit
der PASS-Mengen statt Spearman-Rangkorrelation der rohen Gate-Deltas für `gate_collinearity_
redundancy_alarm` — die Spearman-Matrix bleibt als reine `#742`-Report-Telemetrie erhalten);
#812/Pitfall #248 (`any_arm_unreachable_policy` Default `'recalibrate'` → `'drop_arm'`;
`reward.selection_rule_fingerprint` macht eine verbleibende Selektionsregel-Heterogenität
maschinell sichtbar statt sie in einer Zahl zu verstecken); #813 (`oos_period_returns` wird für
JEDEN `oos_evaluated`, nicht nur eligiblen, Trial gestempelt — schliesst die #784-Coverage-Lücke
zwischen Zähler und Decluster-Matrix, `deflation_cluster_coverage` als neuer Regressionswächter);
#814 (`deflation_family_floor_mode` Default `'budgeted'` → `'attempted'` — ein nie gezogener Trial
hat keinen Sharpe-Schätzer und darf `E[max_N]` nicht beeinflussen; die Suchraum-Kapazität wandert
in den separaten, additiven `deflation_search_space_penalty`-Term). **Governance:** #815
(`reward_semantics_version` 16→17, vier Auslöser: #801/#802, #803, #812, #813/#814); #816 (Pitfalls
#239–#248, dieser Eintrag). **Bewusst zurückgestellt:** #813-Umsetzungspunkt 2 (Autokorrelations-
Signatur statt voller Renditeserie, falls die Speicherkosten im Re-Run zu hoch ausfallen —
"Entscheidung nach Messung, nicht vorab", per Issue-Text); der H0-Kalibrierlauf (#814,
≥ 200 Studies, realisierte False-Positive-Rate bei `deflation_confidence=0.95` im Intervall
[0.03; 0.07]) sowie der reale Re-Run selbst (Akzeptanzkriterien für Spearman(n_family,
Budgetausführung), `deflation_cluster_coverage ≥ 0.95`, den `#815`-Purge-Nachweis) — alle drei
erfordern einen echten Sweep-Lauf mit Marktdaten, der in dieser Sandbox nicht existiert. 91 neue
Tests über acht neue Testdateien (`test_issue_801`/`803`/`804`/`805`/`806`/`807`/`808`/`809`/
`810`/`811`/`812`/`813`/`814`/`815`) + mehrere bestehende Fixtures korrigiert, die durch die
#810→#811-Algorithmus-Umstellung (Spearman→Jaccard) und die #812/#814-Default-Wechsel
unbeabsichtigt betroffen waren (`test_issue_668`, `test_issue_679`, `test_issue_680`,
`test_issue_792`, `test_issue_784`, `test_issue_781`, `test_issue_712`). Volle Suite: 20
vorbestehende, umgebungsbedingte Fehlschläge (identisch vor und nach jedem einzelnen Fix
reproduziert — Allocator-Präzision, Live-Execution-Defaults, NautilusTrader-ADX-Bug,
Sizing-Precedence — NICHT durch diesen Katalog verursacht), alle neuen/geänderten Tests grün.

### 🟢 Pitfall #239 — Ein Log-Ereignis aus einem Subprozess ist keine Diagnose [BEHOBEN: GH-#804]
**Symptom:** Die vier (inzwischen sieben) fail-loud-Diagnosen des Inferenzpfads
(`_calculate_stats`, `assert_return_series_identity`, `_assert_sortino_return_coherence`) liefen im
Backtest-SUBPROZESS und erreichten den Optimizer-Elternprozess-Log nie: der Grep über ein
vollständiges 5490-Zeilen-Lauf-Log lieferte 0 Treffer für `RETURN_SERIES_IDENTITY_VIOLATION`/
`NON_CONTIGUOUS_FOLD_SEGMENTS`/`COHERENCE_INVARIANT_VIOLATION`/`SORTINO_GUARD_TRIPPED`, obwohl der
Elternprozess 35 `STUDY_ABORTED_ON_INVARIANT`-Events sah — die Diagnosen landeten in
`trial_dir/logs/backtest_stdout.log`, einer Datei, die kein Aggregator liest und die #794 Sekunden
später löscht.
**Root-Cause:** `logging` jenseits einer Prozessgrenze erreicht den lesenden Prozess strukturell
nicht — nur was der Elternprozess über einen expliziten Rückkanal SIEHT, existiert für ihn. Eine
Fail-loud-Instrumentierung, die auf Subprozess-`logging` vertraut, ist aus Sicht des Aggregators
unsichtbar, unabhängig davon, wie laut sie im Subprozess selbst ist.
**Fix/Regel:** `_calculate_stats` sammelt jede Verletzung ZUSÄTZLICH in
`metrics['inference_diagnostics']` (strukturierter Rückkanal, additiv, kein Reward-/Gate-Einfluss)
— dasselbe Dict, das `tournament_result.json` ohnehin persistiert. `parsing.parse_tournament` hebt
sie nach `TournamentMetrics.inference_diagnostics`; `run_optimization._reemit_inference_
diagnostics` re-emittiert jeden Eintrag im ELTERNPROZESS als `INFERENCE_DIAGNOSTIC`-ERROR-Ereignis
mit voller Study-Identität. Generalisierte Regel: Fail-loud-Instrumentierung gehört in den
strukturierten Rückkanal (bereits serialisiertes Ergebnis-Dict), niemals in `logging` jenseits
einer Prozessgrenze.

### 🟢 Pitfall #240 — pandas-Aggregationen überspringen `NaN` per Default [BEHOBEN: GH-#801]
**Symptom:** `assert_return_series_identity` verglich zwei Renditeserien über pandas-Reduktionen
(`.sum()`/Vergleichsoperationen) ohne `skipna=False` — eine Serie mit `NaN`/`±inf` (aus einer
Null-Preis-Division oder einem Gap-Tag-Artefakt) liess diese Beobachtungen STILLSCHWEIGEND aus
jeder Aggregation herausfallen, statt die Prüfung fehlschlagen zu lassen.
**Root-Cause:** pandas-Serien-/DataFrame-Reduktionen überspringen `NaN` per Default — eine
Invariante, die über einer solchen Reduktion berechnet wird, gilt dann über einer STILLSCHWEIGEND
REDUZIERTEN Menge, nicht über der vollen Serie, die sie zu beschreiben behauptet.
**Fix/Regel:** Jede Invariante über eine Serie muss `skipna=False` erzwingen ODER die Endlichkeit
(`np.isfinite(...).all()`) VOR der Reduktion prüfen — ein pandas-Aggregat-„Erfolg" ist kein Beleg
dafür, dass die volle Serie wohlgeformt war.

### 🟢 Pitfall #241 — Ein `except ValueError` in einem Wächter darf nie „keine Verletzung" bedeuten [BEHOBEN: GH-#801]
**Symptom:** Ein früher Entwurf der Kohärenz-Prüfung fing `ValueError` (z. B. bei einer Serie mit
< 2 Punkten, ausserhalb des Definitionsbereichs der Prüfung) pauschal ab und behandelte „die Prüfung
konnte gar nicht laufen" als „die Prüfung ist bestanden" — dieselbe Fehlerklasse wie ein stiller
Sentinel, nur über eine Exception statt einen Default-Wert.
**Root-Cause:** Der Definitionsbereich einer Prüffunktion ist Teil ihrer Spezifikation. Eine
Exception aus einem Codepfad, den der Autor der Prüfung nie als „geprüft" vorgesehen hat, pauschal
auf „OK" abzubilden, löscht den Unterschied zwischen „sauber verifiziert" und „nie verifiziert".
**Fix/Regel:** Ein `except ValueError` in einem Wächter ist nur legitim, wenn es als EIGENER,
SICHTBARER Ausgang behandelt wird (z. B. eine WARNING mit explizitem „nicht bestimmbar"-Detail, wie
`assert_eligible_requires_all_not_redundant`s #810-Fail-open-Zweig es tut) — niemals still in den
Erfolgsfall gefaltet.

### 🟢 Pitfall #242 — Eine Bibliotheks-Abhängigkeit ohne obere Versionsgrenze macht numerische Semantik zur Eigenschaft der Installation [BEHOBEN: GH-#802]
**Symptom:** `Series.corr()`s NaN-Handling-/ddof-Defaults unterscheiden sich zwischen
pandas-Hauptversionen, ohne dass `requirements.txt` eine obere Grenze zog — das numerische Ergebnis
des Sweeps (Sortino, PSR, DSR, PBO) hing dadurch STILLSCHWEIGEND davon ab, WELCHE pandas-Version
zufällig auf der ausführenden Maschine installiert war.
**Root-Cause:** Eine Bibliotheks-Abhängigkeit ohne obere Versionsgrenze macht numerische Semantik
zu einer Eigenschaft der Installation, nicht des Codes — zwei Läufe „derselben" Pipeline auf zwei
Maschinen können lautlos divergieren.
**Fix/Regel:** Jede Invariante, die auf einem Bibliotheks-Default beruht, braucht ein EXPLIZITES
Argument (nie den impliziten Default nutzen) UND einen Versions-Pin/Preflight-Guard
(`assert_pandas_version_supported`, einmalig am Sweep-Start aufgerufen) — dieser bricht fail-loud
ab, sobald die installierte Version ausserhalb des getesteten Bereichs liegt.

### 🟢 Pitfall #243 — Ein Ressourcen-Lebenszyklus hinter einer globalen Barriere ist über die gesamte Laufzeit inaktiv [BEHOBEN: GH-#794]
**Symptom:** Die Trial-Verzeichnis-Retention lief bislang NUR am Ende eines vollständigen Sweeps
(einer globalen Barriere über alle Symbole/Strategien) — auf dem 21-Stunden-Absturz-Lauf feuerte
Retention KEIN EINZIGES MAL, unabhängig davon, wie konservativ das Retention-Fenster konfiguriert
war, und die Platte lief mitten im Lauf voll.
**Root-Cause:** Ein Ressourcen-Lebenszyklus, dessen Aufräumschritt hinter einer globalen Barriere
liegt, ist über die GESAMTE Laufzeit bis zu dieser Barriere inaktiv — eine „Retention-Policy", die
erst nach einem Checkpoint greift, den der Lauf unter realistischen Fehlermodi nie erreicht, ist
keine Retention.
**Fix/Regel:** Retention muss auf DERSELBEN Ebene laufen wie die Erzeugung (pro Trial/pro Study,
kontinuierlich), nicht auf einen Sweep-Abschluss-Checkpoint verschoben.

### 🟢 Pitfall #244 — Ein Konfigurationsschlüssel mit degeneriertem Wert ist nicht „gesetzt" [BEHOBEN: GH-#805]
**Symptom:** `floor_plateau_k` (die dimensionsskalierte Early-Stop-Schwelle, Pitfall #229) akzeptierte
`0` als „gültig gesetzten" Wert — `K=0` erfüllt „K aufeinanderfolgende komplett-unevaluierbare
Trials" bereits beim ERSTEN Trial trivial, wodurch der beabsichtigte Sicherheitsmechanismus genau
dann zum No-op wurde, wenn er gebraucht worden wäre.
**Root-Cause:** Ein Konfigurationsschlüssel, dessen degenerierter Wert (`0`, `null`, `[]`) syntaktisch
präsent erscheint, ist nicht semantisch „gesetzt" — Code, der nur `if key in config` (oder eine
falsch verstandene Truthy-Prüfung) testet, kann einen Wert stillschweigend akzeptieren, der das
gesamte Feature aushebelt.
**Fix/Regel:** Ein Fix, der einen Konfigurationsschlüssel mit einer bedeutsamen Schwelle einführt,
MUSS den degenerierten Wert fail-loud ablehnen (`assert_structural_min_modelled_trials_valid`,
#805) — nicht ihn stillschweigend als „bewusste Operator-Entscheidung" akzeptieren.

### 🟢 Pitfall #245 — Eine Abbruch-Schwelle in Trials ist eine Budgetentscheidung, keine statistische [BEHOBEN: GH-#806]
**Symptom:** Der ZERO_ELIGIBLE-Early-Stop feuerte nach einer festen Trial-Zahl ohne angehängte
Fehlerrate-Begründung — „nichts gefunden in N Trials" wurde behandelt, als wäre es äquivalent zu
„es gibt hier nichts zu finden", und verwechselte damit die Erschöpfung eines festen
Rechenbudgets mit einer statistischen Schlussfolgerung.
**Root-Cause:** Eine Abbruch-Schwelle, die ausschliesslich in Trial-Zahlen ausgedrückt ist, trägt
KEINE Fehler-Garantie — sie ist eine Budgetentscheidung im Gewand einer statistischen. „Es gibt hier
nichts zu finden" ist eine Hypothese und braucht ein explizites Fehlerniveau, um falsifizierbar zu
sein.
**Fix/Regel:** `plateau_stop_missed_probability` wendet die Dreierregel an (0 Erfolge in `m` Trials
⇒ 95%-Konfidenz-Obergrenze `3/m` auf die wahre Rate) und gibt der Abbruch-Entscheidung ein echtes,
konfigurierbares Fehlerniveau (`plateau_stop_max_missed_probability`) statt eines blossen
Trial-Zählers.

### 🟢 Pitfall #246 — Eine Prioritäts-/Zuordnungstabelle, die neben einer Config gepflegt wird, driftet [BEHOBEN: GH-#810]
**Symptom:** `_GATE_CONSOLIDATION_PRIORITY` war eine eingefrorene Code-Konstante, die bei der
`#776`-Konsolidierung nicht mitgezogen wurde — weder `min_trades` noch `max_drawdown` hatten einen
Eintrag, beide fielen über `priority.get(k, 99)` auf denselben Sentinel, und der Redundanz-Alarm
empfahl fälschlich die Entfernung von `max_drawdown` (der harten Risikogrenze, die `#776`
ausdrücklich behalten hat) bei einem Kollinearitäts-Treffer mit `oos_min_psr`.
**Root-Cause:** Eine Prioritäts-/Zuordnungstabelle, die NEBEN einer Config (statt AUS ihr abgeleitet)
gepflegt wird, driftet in dem Moment, in dem sich die Config ändert, ohne dass die Tabelle im
selben Schritt nachgezogen wird — nichts erzwingt die Synchronität der beiden. Ein Sentinel-Default
(`.get(k, 99)`) verwandelt diese Drift in ein PLAUSIBLES, FALSCHES Ergebnis statt in einen
sichtbaren Fehler.
**Fix/Regel:** Die eingefrorene Konstante wird durch eine deklarative Config-Tabelle
(`gate_consolidation_priority`) ersetzt, UND die Abdeckung wird verpflichtend —
`assert_gate_priority_coverage` bricht fail-loud ab, sobald ein aktives Gate keinen expliziten
Eintrag hat, statt still zu defaulten.

### 🟢 Pitfall #247 — Rangkorrelation zwischen Gate-Margen misst gemeinsame Skalierung, nicht gemeinsame Entscheidung [BEHOBEN: GH-#811]
**Symptom:** `gate_rank_correlation_matrix` markierte `ρ(oos_min_trades, oos_max_drawdown) > 0.9`
über 105 Studies als redundant — beide Grössen kovariieren jedoch lediglich mit der Handelsaktivität
(mehr Trades ⇒ engere Margen), während ihre tatsächlichen PASS/FAIL-Entscheidungen grösstenteils
DISJUNKT waren.
**Root-Cause:** Spearman-Rangkorrelation auf den rohen Gate-Delta-WERTEN beweist nur, dass zwei
Gates derselben latenten „Aktivitäts-Achse" folgen, NICHT dass sie dieselbe Zulassungs-ENTSCHEIDUNG
treffen. Eine hohe (oder niedrige) Rangkorrelation sagt nichts darüber aus, ob das Entfernen eines
Gates überhaupt ändern würde, welche Trials eligibel sind.
**Fix/Regel:** Redundanz zwischen Gates wird über PASS-MENGEN gemessen (Jaccard-Ähnlichkeit der
`delta >= 0`-Ergebnisse), nie über die Korrelation der rohen Deltas — und zusätzlich durch eine
Marginalbeitrags-Prüfung bestätigt (schliesst das niedriger priorisierte Gate tatsächlich noch
Trials aus, die die übrigen Gates ohnehin schon ausgeschlossen hätten?), nicht durch reine
Ähnlichkeit allein.

### 🟢 Pitfall #248 — Eine Multiplizitätskorrektur setzt eine über die Familie konstante Selektionsregel voraus [BEHOBEN: GH-#812]
**Symptom:** `any_arm_unreachable_policy='recalibrate'` rekalibrierte die OR-Arm-Schwelle STUDY-
SPEZIFISCH auf das jeweils beobachtete p99 — 230 Warnungen kollabierten einen `min_win_rate`-Filter
auf eine effektiv bedeutungslose 5%-Schwelle, und die „Selektionsregel", der ein Kandidat
unterworfen war, unterschied sich von Study zu Study INNERHALB DERSELBEN Familie.
**Root-Cause:** Die `E[max_N]`-Korrektur der Deflated Sharpe Ratio setzt voraus, dass alle `N`
Kandidaten DERSELBEN Selektionsprozedur unterworfen wurden. Eine study-adaptive Schwelle bricht
diese Voraussetzung — Kandidaten aus unterschiedlich strengen Verfahren in einer `n_family` zu
mischen macht `E[max_N]` aus `N` allein UNBERECHENBAR, auch wenn der Code anstandslos eine Zahl
ausspuckt.
**Fix/Regel:** Der Default wechselt auf eine über die Familie IDENTISCHE Selektionsregel
(`drop_arm` statt `recalibrate`); wird eine study-adaptive Schwelle bewusst dennoch verwendet (ein
expliziter Kalibrierlauf), MUSS die Familie anhand von `selection_rule_fingerprint` aufgespalten
werden, statt als eine `n_family` gepoolt zu werden.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #794–#815)
- `optimizer.json.structural_min_modelled_trials_per_dim` (Default 3, ersetzt `floor_plateau_k`)
  — Pitfall #244, #805.
- `optimizer.json.plateau_stop_max_missed_probability` (Default 0.05) — Pitfall #245, #806.
- `optimizer.json.plateau_min_modelled_trials_per_dim` (8 → 6) — #806.
- `optimizer.json.tier_escalation_min_eligible_for_variance` (Default 5) — #808.
- `optimizer.json.reward_semantics_version` 16 → 17 — #815 (vier Auslöser: #801/#802, #803, #812,
  #813/#814; siehe dortiger Changelog-Eintrag).
- `tournament.json.gate_consolidation_priority` / `gate_consolidation_protected` — Pitfall #246,
  #810.
- `tournament.json.gate_redundancy_jaccard` (Default 0.95) / `gate_redundancy_marginal`
  (Default 0.02) — Pitfall #247, #811 (ersetzen `gate_collinearity_threshold` NUR für den
  Redundanz-ALARM; die Spearman-Telemetrie `assert_gate_collinearity_guard` behält
  `gate_collinearity_threshold`).
- `tournament.json.any_arm_unreachable_policy` Default `'recalibrate'` → `'drop_arm'`;
  `min_win_rate_recalibration_floor` entfernt (toter Schlüssel) — Pitfall #248, #812.
- `tournament.json.deflation_family_floor_mode` Default `'budgeted'` → `'attempted'`;
  `deflation_search_space_penalty` (Default `null`) neu — #814.
- `strategies.json.GapContinuationStrategy.active` `true` → `false` — #809.

### 🔒 Watertight Invariants (Issue-Katalog #794–#815) — für künftige Agenten
- **`assert_pandas_version_supported`** (`backtest_runner.py`, #802) — Preflight am Sweep-Start:
  bricht fail-loud ab, wenn die installierte pandas-Version ausserhalb des getesteten Bereichs
  liegt (Pitfall #242).
- **`_evaluate_oos_eligibility`s `REJECT_OOS_INVALID_METRICS`** (`backtest_runner.py`, #803) —
  `oos_coherence_violation`/`equity_ruined` schliessen einen Trial UNBEDINGT von der Eligibility
  aus, unabhängig von der studienweiten Abbruchrate.
- **`assert_structural_min_modelled_trials_valid`** (`run_optimization.py`, #805) — bricht fail-loud
  ab, wenn `structural_min_modelled_trials_per_dim` auf den degenerierten Wert `0` gesetzt ist
  (Pitfall #244).
- **`assert_gate_priority_coverage`** (`reward.py`, #810) — jedes aktive Gate (`eligible_requires_
  all`/`_any`) MUSS einen Eintrag in `gate_consolidation_priority` haben; kein
  `priority.get(k, 99)`-Sentinel mehr (Pitfall #246).
- **`gate_collinearity_redundancy_alarm`** (`reward.py`, #811) — misst Redundanz über Jaccard-
  Pass-Set-Ähnlichkeit UND marginalen Eigenbeitrag, NICHT mehr über Spearman-Rangkorrelation
  (Pitfall #247); `assert_gate_collinearity_guard`/`gate_rank_correlation_matrix` bleiben
  UNVERÄNDERT als reine `#742`-Report-Telemetrie erhalten.
- **`resolve_any_arm_policy`** (`reward.py`, #812) — Default `'drop_arm'` hält die Selektionsregel
  über die Familie konstant (Pitfall #248); `'recalibrate'` bleibt als Option mit einer WARNING,
  dass in diesem Modus kein studienübergreifender DSR-Vergleich zulässig ist.
- **`selection_rule_fingerprint`** (`reward.py`, #812) — SHA-256 über die effektiv wirksame
  Gate-Konfiguration je Study; `report._selection_rule_families` macht eine innerhalb eines Symbols
  heterogene Selektionsregel im `#742`-Report sichtbar.
- **`check_deflation_cluster_coverage`** (`invariants.py`, #813) — FAIL, wenn
  `deflation_cluster_coverage` (Anteil der gezählten `oos_evaluated`-Kandidaten mit vorliegender
  Renditeserie) unter 0.9 liegt.
- **`sr0_multiple_testing_robust`s `search_space_penalty`** (`deflation.py`, #814) — additiver,
  expliziter SR₀-Term für Suchraum-Kapazität, verzerrt NIE `E[max_N]`/`n_trials` selbst.

## Issue-Katalog #817–#835 — Champion-Store-Härtung, Inferenz-Integrität & Durchsatz/Berichtswesen (GitHub-Issues #749/#750/#751, Sitzung 2026-07-30)

Drei aufeinander aufbauende Kataloge auf demselben 35-Stunden-Lauf (69 von 122 Symbolen,
`3836af54_20260728T174020944733`). **Kohorte A — Champion-Store (Purge-frei, #817–#821):** #817
(die Seed-Zulassung eines `REJECT_HOLDOUT_GATE`-Kandidaten braucht zusätzlich zur Allowlist eine
GEDECKELTE relative Holdout-Gate-Unterschreitung, `champion_max_holdout_gate_shortfall`); #820
(`champion_min_tuning_edge` — 21/76 gespeicherte Champions waren schlechter als der ungetunte
globale Default; Cross-Snapshot-Vergleiche vergleichen `R_symbol` nicht mehr roh über Snapshots
hinweg; `load_global_best` filtert auf tatsächlich tunbare Parameter); #819 (`params_schema_version`
von `reward_semantics_version` getrennt — ein Reward-Bump markiert seither nur `quality_stale`
statt Params + `corroboration_count` zu verwerfen; `champion_min_advance_days` explizit 45.0);
#818/Pitfall #237-Wiederkehr (`maybe_write_back` hatte KEINE Produktions-Call-Site —
`sweep._attempt_champion_writeback` läuft jetzt unmittelbar nach `store_champion`, achter
Invarianten-Check `check_champion_writeback_reachability`); #821 (`store_champion` verlangt jetzt
den Sweep-`run_id`; `corroboration_count` inkrementiert nur über DISTINKTE `run_id`s; ein
schema-inkompatibler, selbst nicht zulassungsfähiger Eintrag wird nach `_stale/` quarantiert statt
still fortzubestehen). **Kohorte B — Inferenz-Integrität (Purge, #822–#827):** #823/Pitfall #254/
#255 (Sortino-/PSR-Punktschätzer laufen auf der INFORMATIVEN Teilmenge — Bars mit Rendite ≠ 0 —
statt der vollen, ggf. 24/7-aufgefüllten Kalenderachse; 617 Guard-Trips im Quelllauf waren ein
fehlspezifizierter Schätzer, kein Datenfehler; `sortino_min_downside_observations` als
Vorbedingung, `STUDY_GUARD_DOMINATED`-Marker; `sortino_numeric_guard_min_periods` BEWUSST
dokumentiert, aber ungesetzt gelassen — eine Aktivierung ohne dedizierten Monte-Carlo-H0-Lauf wäre
geraten statt kalibriert); #822/Pitfall #253 (`n_family` zählt seither Trials MIT definierter
Selektions-Teststatistik, `oos_selection_statistic_available`, statt blosser `oos_evaluated`-
Aktivität); #824 (`bootstrap_psr_z`/`sample_skew_kurtosis` resampeln dieselbe informative
Teilmenge — ein Bootstrap-SE über eine grossteils gepaddete Serie unterschätzt sich um
`√(T/T_informativ)`); #826/Pitfall #256 (`promotion_family_scope='per_strategy'` — `confirm()`
erhält N1, die EIGENE Study-Zahl, statt der vorherigen symbolweiten Summe über alle Strategien
eines Symbols; Roster-Erweiterung erhöhte vorher die Promotion-Hürde JEDER bestehenden Strategie);
#827/Pitfall #257 (`selection_rule_homogeneity_policy` — Punkte 1/2 bereits strukturell durch #826
erledigt; `'fail'` bricht ein Symbol mit heterogener Selektionsregel fail-loud ab); #825 (die
Equity-Ruin-Ausschlussklausel — `REJECT_OOS_INVALID_METRICS` — existierte bereits seit v17/#801;
`liquidated_trials` ist ein reiner `#804`-Telemetrie-Alias; die eigentliche Wartungsmargin-/
Zwangsliquidations-Simulation bleibt zurückgestellt, siehe unten). **Kohorte C —
Durchsatz/Closed-Loop/Berichtswesen (Purge-frei, #828–#835):** #829/Pitfall #258 (`signal_absent`
verlangte 90 % Budgetausführung, aber `#805` kappt genau diese Studies strukturell bei 28 %/46 % —
ein Deadlock zwischen Abbruch- und Aktionsregel; die Evidenzbedingung akzeptiert jetzt AUCH ein
vollständig ausgeführtes strukturelles Kriterium); #830/Pitfall #258 (Kehrseite: `signal_quality`
deaktivierte bislang UNBEDINGT nach einer einzigen Beobachtung — Typ-II-Verstärker, der bevorzugt
regimebedingte Nicht-Ergebnisse entfernt; unterliegt seither demselben Evidenzregime wie
`signal_absent`, PLUS eine neue `deprioritized`-Zwischenklasse mit halbiertem statt vollem oder
null Budget); #831 (der `#763`/`#777`-Bounds-Vorschlag lief nur innerhalb `confirm()`, das eine
Study mit 0 eligiblen Trials nie erreicht — derselbe Vorschlags-Pfad läuft jetzt zusätzlich im
Post-Study-Pfad von `_emit_study_summary`; `WIRED_OVERRIDE_STRATEGIES`, eine seit `#681`
eingefrorene 3-von-14-Strategien-Allowlist, ist durch eine ABGELEITETE Prüfung ersetzt); #828
(die Worker-Deckelung `min(n_jobs, len(symbol_pairs))` verwarf bis zu 8 von 22 konfigurierten
Workern JEDEN Lauf, unabhängig von `n_jobs`; `sweep_max_wallclock_h` als Laufzeit-Guard analog
`disk_guard`); #833/Pitfall #237-Wiederkehr (der `#742`-Report entstand nur am Ende von `main()` —
JEDER Abbruch davor, `SIGINT`/`SIGTERM`/eine unerwartete Exception, lieferte null Artefakt;
`sweep.main()` erzeugt seither IMMER einen — ggf. `run_status != 'complete'` markierten — Report,
bevor der ursprüngliche Fehler weitergereicht wird; `--report-only` rekonstruiert nachträglich aus
den bereits exportierten Proposals); #832 (`summary_de.py` — deutschsprachige Abschlussberichte,
liest ausschliesslich das bereits erzeugte `#742`-Report-Dict, direkt nach dem Report-Aufruf
verdrahtet, erbt damit automatisch die `#833`-Abbruchfestigkeit); #834 (`reward_semantics_version`
17 → 18, vier Auslöser: #822, #823, #824, #826; siehe dortiger Changelog-Eintrag); #835 (Pitfalls
#249–#258, dieser Eintrag).

**Merge-Reihenfolge:** Kohorte A (#817/#820 → #819 → #818, #821 unabhängig) → Kohorte B
(#823/#825 → #822 → #824 → #826 → #827) → Kohorte C (#829/#830/#831/#828/#833 unabhängig
voneinander, #832 zuletzt weil es auf `#833`s Report-Artefakt aufsetzt) → `#834`
(`reward_semantics_version`-Bump, LETZTE Aktion) → Re-Run → `#835` (dieser Eintrag).

**Pitfall-#237-Wiederkehr, dritte und vierte Instanz:** `#818` (`maybe_write_back` seit seiner
Einführung ohne Produktions-Call-Site) und `#833` (der `#742`-Report existierte für JEDEN
Lauf-Abbruch nicht) sind beide GENAU die in Pitfall #237 beschriebene Fehlerklasse — ein Fix ohne
gemessenes Akzeptanzkriterium im REALEN Pfad bleibt eine Hypothese, unabhängig davon, wie sorgfältig
die Funktion selbst getestet ist. Zusammen mit den ursprünglichen `#794`/`#796`/`#797`-Instanzen ist
das die VIERTE Wiederkehr derselben Lektion in diesem Repository.

**Bewusst zurückgestellt (dokumentiert, nicht implementiert):** die eigentliche
Wartungsmargin-/Zwangsliquidations-Simulation innerhalb der `nautilus_trader`-`BacktestEngine`
(#825 Fix-Punkte 1/2/4 — ein Beobachter-`Actor` kann keine schliessenden Orders einreichen, ohne
den Fill-/PnL-Aggregationspfad an seiner riskantesten Stelle zu verändern, ohne realen
Marktdaten-Katalog zur Regressionsverifikation in dieser Sandbox); die zweistufige
`promotion_family_scope='per_symbol_best'`-Korrektur (#826 Fix-Punkt 1 — die Komposition aus N1
und N2 ist laut Katalogtext explizit NICHT `E[max_{N1·N2}]` und braucht einen eigenen
H0-Kalibrierlauf); die Streichung des `min_win_rate`-OR-Arms (#827 Fix-Punkt 4 — ändert reale
Eligibility-Semantik für jeden künftigen Lauf, verdient einen eigenen bewussten Durchgang); echtes
Cross-Symbol-Pipelining + Largest-First-Scheduling (#828 Fix-Punkte 1/2 — der eigentliche
Durchsatz-Fix, aber eine Umstrukturierung der Kern-Dispatch-Schleife, von der > 15 bestehende Tests
für die `#652`-Familien-Invariante/`#799`-Transaktionalität/`#755`-Determinismus abhängen, ohne
einen realen Mehrstunden-Lauf zur empirischen Verifikation der Akzeptanzkriterien); der
`SuccessiveHalvingPruner` (#828 Fix-Punkt 4 — architektonisch wirkungslos wie beschrieben: das
fold-weise Zwischenergebnis wird dem Elternprozess erst sichtbar, NACHDEM der Backtest-Subprozess
bereits ALLE Folds abgeschlossen hat; ein Pruning-Signal an dieser Stelle kann keine Rechenzeit
mehr einsparen); individuelle Einzel-Trades mit Entry-/Exit-Zeitstempel in Report-Abschnitt 4
(#832 Fix-Punkt 1 — würde eine neue State-Verfolgung in der FIFO-Match-Schleife von
`backtest_runner.extract_metrics` voraussetzen, der höchstriskantesten P&L-Aggregationsstelle des
Systems); literale Inkrementelle-Shard-Dateien (#833 Fix-Punkte 1/2 — die bereits bestehende
Proposal-Datei-plus-SQLite-Rekonstruktion, `generate_report_for_run`, leistet dieselbe
Abbruchfestigkeit bereits bit-identisch, ein paralleles Shard-System hätte kein reales Problem in
diesem Repository gelöst, das nicht schon gelöst ist); der gemeinsame H0-Kalibrierlauf (#824
Punkte 3/4 + #826 Punkt 4 + die seit `#667` (2026-07-17) in inzwischen FÜNF Katalogen angekündigte,
nie ausgeführte Kalibrierung) — alle erfordern einen echten Sweep-Lauf mit Marktdaten, der in
dieser Sandbox nicht existiert. 32 neue Testdateien
(`test_issue_817`…`test_issue_834_reward_semantics_bump.py`) + mehrere bestehende Fixtures
korrigiert, die durch die `#822`-Zähl-Umstellung, die `#826`-Scope-Umstellung und die
`#830`/`#831`-Default-Wechsel unbeabsichtigt betroffen waren. Volle Suite: 20 vorbestehende,
umgebungsbedingte Fehlschläge (identisch vor und nach jedem einzelnen Fix reproduziert — Allocator-
Präzision, Live-Execution-Defaults, NautilusTrader-ADX-/Squeeze-Bug, Sizing-Precedence, Storage-
DDL-Race — NICHT durch diesen Katalog verursacht), alle neuen/geänderten Tests grün.

### 🟢 Pitfall #249 — Eine Rejection-Allowlist nach ERREICHTER STUFE ist blind für den ABSTAND zur Schwelle [BEHOBEN: GH-#817]
**Symptom:** Ein `REJECT_HOLDOUT_GATE`-Kandidat (am Holdout-Gate selbst gescheitert) war für den
Champion-Warm-Start-Seed trivial zulassungsfähig, sobald sein Ablehnungsgrund in der Allowlist
stand — unabhängig davon, ob er die Schwelle um 0,1 % oder um das Zehnfache verfehlt hatte.
**Root-Cause:** Eine Allowlist, die nur prüft, WELCHE Ablehnungsstufe erreicht wurde, behandelt
„knapp verfehlt" und „krachend verfehlt" identisch — die Ablehnungs-URSACHE sagt nichts über den
ABSTAND zur Schwelle aus, den die Zulassungsentscheidung eigentlich braucht.
**Fix/Regel:** Eine Rejection-Allowlist für einen nachgelagerten, weniger strengen Verwendungszweck
(hier: Seed statt Promotion) braucht ZUSÄTZLICH eine gedeckelte relative Unterschreitungs-Grenze
(`champion_max_holdout_gate_shortfall`), nicht nur die Mitgliedschaft in der Liste.

### 🟢 Pitfall #250 — Relative Qualitätskriterien schlagen absolute, wo beide vorliegen [BEHOBEN: GH-#820]
**Symptom:** 21 von 76 gespeicherten Champions waren nachweislich SCHLECHTER als der ungetunte
globale Default (`R_symbol < R_global`) — dennoch bestanden sie jede absolute
Zulassungsschwelle und wurden als Warm-Start-Anker persistiert.
**Root-Cause:** Ein absolutes „gut genug"-Kriterium beantwortet nicht die Frage, die für einen
Warm-Start-Seed eigentlich zählt: ist das Ergebnis besser als eine bereits verfügbare, kostenlose
Baseline? Eine Grösse kann jede absolute Schwelle bestehen und trotzdem von einem einfacheren
Vergleichswert dominiert werden.
**Fix/Regel:** Wo ein relativer Vergleichswert (hier: der ungetunte globale Default) verfügbar ist,
MUSS die Zulassungsprüfung ihn explizit gegen den Kandidaten prüfen (`champion_min_tuning_edge`),
zusätzlich zu jeder absoluten Schwelle — ein rein absolutes Kriterium reicht nicht.

### 🟢 Pitfall #251 — Versionierung muss den Geltungsbereich abbilden: bewertend vs. beschreibend [BEHOBEN: GH-#819]
**Symptom:** JEDER `reward_semantics_version`-Bump verwarf gespeicherte Champion-Parameter UND
`corroboration_count` vollständig — auch dann, wenn sich nur die BEWERTUNG (Reward-Mathematik)
geändert hatte, nicht die STRUKTUR des Suchraums selbst.
**Root-Cause:** Eine einzige Versionsnummer trug zwei orthogonale Bedeutungen gleichzeitig: „ist
dieser Parametervektor noch ein gültiger Punkt im aktuellen Suchraum" (beschreibend/strukturell)
und „wurde dieser Punkt unter der aktuellen Reward-Mathematik bewertet" (bewertend/Qualität). Ein
Bump der zweiten Bedeutung zerstörte unnötig auch die erste.
**Fix/Regel:** Zwei GETRENNTE Versionszähler für zwei getrennte Fragen — `params_schema_version`
(strukturell, ein Mismatch verwirft) und `reward_semantics_version` (bewertend, ein Mismatch
markiert nur `quality_stale`, behält aber Parameter und Korroborations-Historie).

### 🟢 Pitfall #252 — `run_id` heisst nur so, wenn er über einen Lauf hinweg KONSTANT ist [BEHOBEN: GH-#821]
**Symptom:** `corroboration_count` (die Zahl unabhängiger Bestätigungen eines Champions) konnte
innerhalb EINES EINZIGEN Sweep-Laufs mehrfach inkrementieren, wenn `store_champion` mehrfach für
dasselbe Paar aufgerufen wurde — ein selbst gemünzter Schreib-Zeitstempel unterschied „zwei
unabhängige Läufe" nicht von „zweimal innerhalb desselben Laufs aufgerufen".
**Root-Cause:** Ein Zähler, der unabhängige BESTÄTIGUNGEN zählen soll, braucht eine von aussen
zugeführte, über den gesamten Lauf STABILE Identität als Vergleichsanker — ohne sie zählt er
stattdessen AUFRUFE, eine andere (und hier falsche) Grösse.
**Fix/Regel:** `store_champion` verlangt den Sweep-`run_id` als Pflichtparameter;
`corroboration_count` inkrementiert nur, wenn sich der `run_id` gegenüber dem gespeicherten
Eintrag UNTERSCHEIDET — ein `run_id`, der nicht pro Lauf konstant und eindeutig ist, ist für diesen
Zweck kein `run_id`.

### 🟢 Pitfall #253 — `N` einer Multiplizitätskorrektur zählt Kandidaten mit definierter Teststatistik, nicht mit blosser Aktivität [BEHOBEN: GH-#822]
**Symptom:** `n_family` (die Deflated-Sharpe-Ratio-Multiplizität) summierte JEDEN `oos_evaluated`
Trial — darunter 617 `SORTINO_GUARD_TRIPPED`- und 7 `EQUITY_NONPOSITIVE`-Trials, die nachweislich
KEINEN Sortino/PSR trugen.
**Root-Cause:** `E[max_N]` korrigiert für die Zahl der Kandidaten, deren Teststatistik unter H₀
tatsächlich zum beobachteten Maximum hätte beitragen KÖNNEN. Ein Trial ohne definierten
Schätzwert — sei es, weil er nie gezogen wurde (`#814`), sei es, weil seine Statistik VERWORFEN
wurde (hier) — kann das Maximum nicht beeinflusst haben und darf `N` nicht erhöhen.
**Fix/Regel:** `N` zählt `oos_selection_statistic_available` (ein definierter Wert der tatsächlich
selektionsrelevanten Grösse), nicht `oos_evaluated` (blosse Handelsaktivität) — dieselbe
Argumentationslogik wie `#814`, nur eine Ebene tiefer.

### 🟢 Pitfall #254 — Ein häufig auslösender Numerik-Guard zeigt einen fehlspezifizierten Schätzer an, keinen Datenfehler [BEHOBEN: GH-#823]
**Symptom:** Ein Sortino-Numerik-Guard löste 617-mal in einem einzigen Lauf aus (566 davon bei
einer einzigen Strategie) — und zensierte damit systematisch das OBERE Ende der Zielverteilung,
genau dort, wo der TPE-Sampler am meisten lernen könnte.
**Root-Cause:** Ein Guard, der „zu oft" auslöst, ist kein Filter gegen Ausreisser mehr — er ist ein
aktiver Eingriff in die Suche, der eine ganze Suchraumregion für den Sampler unsichtbar macht. Die
tatsächliche Ursache war ein Nenner (Downside-Deviation), der über eine strukturell zu grosse
Bar-Menge gebildet wurde, kein numerischer Ausreisser.
**Fix/Regel:** Eine ungewöhnlich HÄUFIGE Guard-Aktivierung ist ein Diagnosesignal für den
SCHÄTZER, nicht für die Daten — der Schätzer (hier: die Bar-Teilmenge, auf der er rechnet) gehört
korrigiert, nicht die Guard-Schwelle selbst hochgesetzt.

### 🟢 Pitfall #255 — Ein aufgefülltes Kalenderraster hat ZWEI Längen; jede Inferenz-Rechnung muss die INFORMATIVE verwenden [BEHOBEN: GH-#823/#824]
**Symptom:** Sortino-Punktschätzer UND PSR-Bootstrap-Standardfehler wurden über die VOLLE,
teilweise 24/7-aufgefüllte Kalender-Bar-Achse gebildet — bei einem RTH-Instrument auf einem
durchgehenden Stundenraster ein Nenner, der die tatsächliche Beobachtungszahl um ein Vielfaches
übersteigt.
**Root-Cause:** Eine Bar-Achse, die künstlich auf eine durchgehende Taktung aufgefüllt wurde
(Nicht-Handelszeit, keine offene Position), hat zwei verschiedene, gleichzeitig gültige Längen: die
RASTERLÄNGE (jeder Zeitschritt, inkl. informationsleerer Bars mit Rendite exakt 0) und die
INFORMATIVE Länge (nur Bars mit tatsächlicher Rendite ≠ 0). Jede Grösse, die von der
Beobachtungszahl abhängt (Standardabweichung, Standardfehler, Annualisierungsfaktor), muss GENAU
wissen, welche der beiden gemeint ist.
**Fix/Regel:** Sortino-/PSR-Punktschätzer UND der Bootstrap-Standardfehler laufen auf der
INFORMATIVEN Teilmenge (`_informative_period_returns`); die ökonomische Zielgrösse (`total_return`)
bleibt unverändert über die VOLLE Kurve — dieselbe #756/#801-Trennung „ökonomische Zielgrösse vs.
Inferenz-Renditedefinition", nur konsequent auf die Stichprobengrösse selbst angewandt.

### 🟢 Pitfall #256 — Der Geltungsbereich einer Multiplizitätskorrektur muss der TATSÄCHLICH getroffenen Entscheidung folgen [BEHOBEN: GH-#826]
**Symptom:** Die familienweite DSR-Multiplizität `n_family` war die Summe über ALLE Strategien
eines Symbols — floss aber in JEDE einzelne (Strategie, Symbol)-Promotion-Entscheidung ein, obwohl
diese Entscheidung nur über die Trials EINER Strategie getroffen wurde. Eine Roster-Erweiterung
(mehr Strategien) erhöhte dadurch die Promotion-Hürde JEDER bestehenden Strategie, ohne dass sich
deren eigene Evidenz geändert hätte.
**Root-Cause:** Wird über eine BREITERE Familie korrigiert, als tatsächlich selektiert wurde,
koppelt die Multiplizitäts-Hürde an Grössen (Roster-Umfang, Suchbudget anderer Strategien), die mit
der Evidenz des konkreten Kandidaten nichts zu tun haben.
**Fix/Regel:** Der Geltungsbereich der Korrektur (`promotion_family_scope`) muss explizit
DEKLARIERT werden und der tatsächlich getroffenen Entscheidung entsprechen — bei einer
Per-Strategie-Entscheidung ist `N1` (die eigene Study-Zahl) die korrekte Multiplizität, nicht eine
symbolweite Summe.

### 🟢 Pitfall #257 — Eine GEMESSENE Verletzung der Verfahrensvoraussetzung ist nicht „konservativ", sondern UNDEFINIERT [BEHOBEN: GH-#827]
**Symptom:** `selection_rule_fingerprint` erkannte korrekt, dass zwei Studies derselben Symbol-
Familie unterschiedliche effektive Selektionsregeln trugen (Pitfall #248) — die Multiplizitäts-
korrektur wurde trotzdem unverändert über beide hinweg als EINE Familie angewandt.
**Root-Cause:** Die `E[max_N]`-Korrektur setzt eine über die Familie KONSTANTE Selektionsprozedur
voraus. Ist diese Voraussetzung nachweislich verletzt, hat das daraus berechnete `SR₀` keine
kalibrierte Bedeutung mehr — weder eine strengere noch eine mildere. Es „konservativ trotzdem
anzuwenden" ist eine Kategorienverwechslung: Konservativität ist eine Eigenschaft einer gültigen
Berechnung, keine Rettung für eine ungültige.
**Fix/Regel:** Eine gemessene Voraussetzungsverletzung erfordert entweder, die Voraussetzung
HERZUSTELLEN (Partitionierung nach Fingerprint) oder das Verfahren AUSZUSETZEN (fail-loud) — niemals,
es unverändert auf der verletzten Grundlage weiterlaufen zu lassen.

### 🟢 Pitfall #258 — Zwei Evidenzschwellen DESSELBEN Mechanismus müssen gegeneinander geprüft werden [BEHOBEN: GH-#829/#830]
**Symptom:** Zwei gemergte Fixes blockierten sich gegenseitig: eine Abbruchregel (`#805`) kappte
strukturell tote Studies bei 28–46 % Budgetausführung, eine Aktionsregel (`#778`) verlangte für
dieselbe Deaktivierungsentscheidung mindestens 90 % Budgetausführung — die Ursache, für die der
Mechanismus gebaut wurde, konnte die Schwelle STRUKTURELL nie erreichen (138 Studies, `action ==
'none'`, dauerhaft). Symmetrisch dazu verlangte eine ANDERE Ursache (`signal_quality`) GAR KEINE
Evidenzschwelle und deaktivierte nach einer einzigen Beobachtung 10 Läufe lang.
**Root-Cause:** Zwei Schwellen, die auf DIESELBE zugrunde liegende Grösse (hier: Budgetausführung)
wirken, aber aus unabhängigen PRs stammen, können einen Deadlock bilden, ohne dass ein einzelner
Unit-Test ihn je sichtbar macht — jeder Fix ist für sich genommen korrekt und getestet. Symmetrisch
gilt: eine Ursache ohne JEDE Evidenzschwelle ist derselbe Fehler mit umgekehrtem Vorzeichen (ein
Typ-II-Verstärker statt eines Deadlocks).
**Fix/Regel:** Bei JEDER neuen Abbruch- oder Aktionsregel explizit prüfen, ob eine bereits
bestehende Schwelle DESSELBEN Mechanismus dieselbe Grösse von der ANDEREN Seite einschränkt — eine
Evidenzbedingung muss auf das GEMESSENE ERGEBNIS zielen (hier: ein vollständig ausgeführtes
strukturelles Kriterium ODER hohe Budgetausführung), nicht auf einen einzelnen Indikator, der von
einer anderen Regel bereits gekappt wird. Ein neuer Invarianten-Check
(`check_diagnosis_actionability`) macht einen solchen Deadlock künftig maschinell sichtbar (FAIL,
sobald ≥ 50 Studies dieselbe `(strategy, binding_cause)`-Kombination mit `action == 'none'`
melden).

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #817–#835)
- `optimizer.json.champion_admissible_reject_details` / `champion_max_holdout_gate_shortfall`
  (Default 0.0) — Pitfall #249, #817.
- `optimizer.json.champion_min_tuning_edge` (Default 0.0) — Pitfall #250, #820.
- `optimizer.json.champion_min_advance_days` (`null` → 45.0, jetzt explizit) — #819/#820.
- `optimizer.json.sortino_guard_trip_fraction_warn` (Default 0.10) — #823 Fix Punkt 4.
- `optimizer.json.deprioritized_budget_factor` (Default 0.5) — #830 Fix Punkt 2.
- `optimizer.json.report_longest_trades_k` (Default 10) — #832 (Scope-Hinweis: rankt Studies, nicht
  Einzel-Trades — siehe `summary_de.py`-Modul-Docstring).
- `optimizer.json.sweep_max_wallclock_h` (Default 24, `null` deaktiviert) — #828 Fix Punkt 5.
- `optimizer.json.reward_semantics_version` 17 → 18 — #834 (vier Auslöser: #822, #823, #824, #826;
  siehe dortiger Changelog-Eintrag).
- `tournament.json.sortino_min_downside_observations` (Default 30) — Pitfall #254, #823.
- `tournament.json.sortino_numeric_guard_min_periods` — SCHEMA dokumentiert, DATENWERT bewusst
  NICHT gesetzt (#823, wartet auf einen dedizierten Monte-Carlo-H0-Kalibrierlauf).
- `tournament.json.promotion_family_scope` (Default `'per_strategy'`) — Pitfall #256, #826.
  `'per_symbol_best'` ist deklariert, aber fail-loud (kein H0-Kalibrierlauf verfügbar).
- `tournament.json.selection_rule_homogeneity_policy` (Default `'partition'`) — Pitfall #257, #827.

### 🔒 Watertight Invariants (Issue-Katalog #817–#835) — für künftige Agenten
- **`champions.champion_is_admissible`** (`champions.py`, #817/#820) — prüft
  `champion_admissible_reject_details` UND `champion_max_holdout_gate_shortfall` UND
  `champion_min_tuning_edge` gemeinsam; keine der drei Bedingungen allein genügt.
- **`champions._bump_corroboration`** (`champions.py`, #821) — inkrementiert
  `corroboration_count` NUR bei einem gegenüber dem gespeicherten Eintrag unterschiedlichen
  `run_id` (Pitfall #252); ein fehlender `run_id` löst einen `ValueError` aus, statt still auf
  einen Zeitstempel zurückzufallen.
- **`sweep._attempt_champion_writeback`** (`sweep.py`, #818) — läuft in der Produktion
  unmittelbar nach jedem erfolgreichen `store_champion`; `invariants.check_champion_writeback_
  reachability` FAILt, wenn kein einziger `written_back`-Eintrag über den gesamten Champion-Store
  nachweisbar ist (Pitfall #237-Wiederkehr).
- **`invariants.check_family_n_statistic_coverage`** (`invariants.py`, #822) — FAIL, wenn
  `deflation_n_family_raw` grösser ist als die Zahl der Trials mit tatsächlich vorhandener
  Selektions-Teststatistik.
- **`backtest_runner._informative_period_returns`** (`backtest_runner.py`, #823) — die EINE
  Filterfunktion, die JEDE Sortino-/PSR-/Bootstrap-Berechnung auf die informative Teilmenge
  beschränkt (Pitfall #255); `SORTINO_INSUFFICIENT_DOWNSIDE` trennt einen degenerierten Nenner vom
  numerischen Ausreisser-Guard.
- **`sweep._resolve_promotion_family_scope`** (`sweep.py`, #826) — löst `promotion_family_scope`
  EINMAL, fail-fast, vor jeder Symbol-Optimierung auf; `'per_symbol_best'` bricht mit einer
  `ValueError` ab, statt eine unkalibrierte Formel still anzuwenden (Pitfall #256/#257-Klasse).
- **`invariants.check_diagnosis_actionability`** (`invariants.py`, #829) — FAIL, wenn ≥ 50 Studies
  dieselbe `(strategy, binding_cause)`-Kombination mit `action == 'none'` melden (Pitfall #258).
- **`invariants.check_holding_time_cap`** (`invariants.py`, #832) — FAIL, wenn eine Study eine
  Haltedauer über der `#714`/GR-01-Zeitbox-Obergrenze (24 Bars) meldet — ein Treffer ist ein Bug im
  Exit-Pfad, keine Dateneigenart.
- **`sweep.main()`s Abbruch-Pfad** (`sweep.py`, #833) — erzeugt IMMER ein `#742`-Report-Artefakt
  (mit `run_status ∈ {complete, aborted_disk, aborted_wallclock, aborted_signal, aborted_error}`),
  bevor ein SIGINT/SIGTERM/eine unerwartete Exception weitergereicht wird (Pitfall #237-Wiederkehr).

## Issue-Katalog #836–#855 — Zeitbox-Exit-Pfad, Symbol-Durchsatz, Inferenz-Integrität & Governance (GitHub-Issues #753/#754/#755/#756, Sitzung 2026-08-04)

Vier Kataloge auf demselben Katalog-Lauf, Basis-Commit `20fd81c`. **Kohorte A — Zeitbox & Exit-Pfad
(Purge, #836–#839):** #836/Pitfall #259 (`_close_position_base` füllte `_pending_cancels`, der
Aufrufer leerte es zwei Zeilen später — alle drei Callback-Rückwege waren auf diese Menge gated,
der Zeit-Exit erreichte `_execute_market_close` in ~80 % der Studies NIE; `_exit_pending`/
`_exit_pending_kind`/`_exit_close_watchdog` ersetzen den Mechanismus, `EXIT_CLOSE_STALLED` als
Fail-Loud-Diagnose nach `exit_close_max_bars`); #837/Pitfall #260 (`_in_position = False`/
`_bars_in_position = 0` standen im AUSLÖSER statt in `on_position_closed` — der Bar-Zähler startete
neu, der Trailing-Stop verankerte sich neu, BEVOR die Order bestätigt war; `_trailing_initialised`
entkoppelt die Trailing-Stop-Initialisierung von `_in_position`); #838/Pitfall #261/#262/#263
(`test_issue_714_bar_time_box.py` prüfte nur Config-Grenzen/Konstruktor-Klemmung, kein Order-
Lebenszyklus — blieb über mehrere Läufe grün, während der Zeit-Exit in 80 % der Studies nicht
griff; `max_trades_cap`-Early-Return in `_check_exits_and_update` sperrte NEBENBEI Trailing-Stop/
Zeit-Exit/ATR-Update — `_entry_allowed()` extrahiert den Cap auf den Entry-Pfad; `min_holding_time
> max_bars_in_trade` unterdrückte den Zeit-Exit strukturell — jetzt hart geklemmt mit WARNING);
#839 (`invariants.compute_trial_timebox_violations` + `tournament.json['timebox_violation_
tolerance']` — `confirm.confirm_per_symbol_promotion` verwirft eine Study mit
`REJECT_INVALID_TIMEBOX` VOR jedem statistischen Gate, wenn der `#714`/GR-01-Zeitbox-Vertrag
gebrochen wurde; `champions._configured_admissible_reject_details` schliesst diesen Grund
unbedingt aus der Champion-Allowlist aus). **Kohorte B — Symbol-Durchsatz (kein Purge,
#840–#843):** #840/Pitfall #264 (`sweep.main()` hatte KEIN `--resume`/`--run-id` — der bereits
vollständig implementierte und getestete `#799`-Checkpoint war ohne CLI-Einstieg unerreichbar,
sechste Wiederkehr von Pitfall #237 nach #794/#796/#797/#818/#831; `_strategies_fingerprint`
validiert, dass ein wiederaufgenommener Lauf dieselbe Strategien-/Semantik-Konfiguration trägt);
#841 (`symbol_coverage.py`, neuer Ledger — `order_symbols(policy='least_recently_covered')` löst
die stabile Symbolreihenfolge ab, die bei einem Wallclock-Abbruch denselben Schwanz nie
optimierte; `check_symbol_coverage` FAILt, wenn ein Symbol seit `symbol_coverage_max_age_runs`
Läufen unabgedeckt blieb); #842 (`sweep_max_wallclock_h` 24→72 + `_wallclock_forecast`-Telemetrie
nach `wallclock_forecast_after_symbols` Symbolen); #843 (echtes Cross-Symbol-Pipelining/
`SuccessiveHalvingPruner` als NICHT sicher umsetzbar analysiert und dokumentiert zurückgestellt —
kein reales Multi-Symbol-Katalog-Fixture in dieser Sandbox, um AK-1s Bit-Identitäts-Anforderung
für `n_family`/`deflation_n_effective`/`selection_rule_fingerprint` zu verifizieren; Rohmaterial
für eine künftige Umsetzung — `wallclock_by_strategy`/`symbol_barrier_wait_s`/`worker_utilisation`
— wurde in Kohorte D (#851) bereits gelegt). **Kohorte C — Inferenz & Selektion (Purge,
#844–#848):** #844/Pitfall #267 (`sortino_numeric_guard_min_periods` blieb trotz #823s
Dokumentation NIE tatsächlich gesetzt, FÜNFTE Wiederkehr nach #488/#753/#769/#805/#823 — Wert auf
1600 gesetzt, `_required_keys.json`-Registry-Preflight [`assert_required_config_keys_valid`]
verhindert ein künftiges stilles Fehlen für JEDEN registrierten Key, nicht nur diesen); #845
(`downside_obs` als Trial-Attribut [analog `oos_n_periods`] + `check_family_n_periods_
homogeneity`/`deflation_max_n_periods_ratio` [Default 4.0] gegen eine Faktor-45-`n_periods`-
Streuung innerhalb einer DSR-Kohorte, die die per-Trial-Sortinos inkommensurabel macht; der
Issue-Text-Vorschlag `oos_evaluated=False` für `SORTINO_INSUFFICIENT_DOWNSIDE`-Trials wurde
bewusst NICHT umgesetzt — er hätte keine Reward-Neutralität erreicht, sondern nur eine ANDERE
Penalty-Formung über `reward.calculate_reward_v2`s Unevaluable-Pfad, siehe Code-Kommentar bei
`SORTINO_INSUFFICIENT_DOWNSIDE`); #846 (`deflation_skipped_reason` erzwingt an der Export-Grenze
dieselbe SR0/DSR-Kohärenz-Garantie, die `#651` bereits forderte — VIERTE Wiederkehr; kein
`deflated_sr0` mehr ohne begleitendes `deflated_dsr`/`deflation_dsr_z`); #847 (`_inference_
method_block` erkannte bislang AUSSCHLIESSLICH die DSR-spezifische Inferenzmethode — eine Study,
die ausschliesslich am PBO-Gate scheiterte, hatte `promotion.applied=False`, obwohl eine
CSCV/PBO-Inferenz tatsächlich gelaufen war; erkennt jetzt `holdout_metrics['pbo'] is not None`
ebenfalls als dokumentierte Methode); #848/Pitfall #268-Vorstufe (`min_win_rate` aus
`eligible_requires_any` entfernt — FÜNFTE Wiederkehr #660→#668→#678→#812→#848, derselbe
strukturell unerreichbare OR-Arm; `check_selection_rule_homogeneity` FAILt jetzt [statt nur eine
`[#812]`-WARNUNG], wenn trotzdem mehr als ein `selection_rule_fingerprint` je Symbol auftritt).
**Kohorte D/E — Bericht, Reproduzierbarkeit & Governance (kein Purge ausser #854 selbst,
#849–#855):** #849 (`invariants.InvariantResult.to_dict()` schrieb nur den Schlüssel `name`,
`summary_de.py` las `check` — ein Schlüssel, den `to_dict()` nie geschrieben hatte, 519×
`**None**` in Berichts-Sektion 5; beide Schlüssel jetzt exportiert [Übergangs-Vertrag], Sektion 5
auf 5.1 Übersicht [Check/FAILs/betroffene Studies/Schweregrad] + 5.2 Details [begrenzt auf
`summary_max_details_per_check`] umgebaut, blockierende FAILs zusätzlich in Abschnitt 1); #850/
Pitfall #268 (`holdout_excess_return` maß im Bärenmarkt näherungsweise nur `−Buy&Hold`, eine
SYMBOL-Konstante — Varianzanteil Symbol 99,1 % gegen Strategie 0,9 % auf den Katalog-Daten, 14
strukturell verschiedene Strategien lagen auf demselben Symbol innerhalb 3,4 Prozentpunkten;
`exposure_fraction` [Anteil der Fenster-Zeit mit offener Position] + `excess_variance_
decomposition` machen das im Bericht explizit sichtbar statt als Strategie-Ranking auszugeben,
ein negativer Buy&Hold-Return unterdrückt jetzt die „schlägt Buy & Hold"-Wertung); #851
(`run_optimization._optimize_symbol_impl` persistiert `study_started_at_utc`/`study_ended_at_utc`/
`study_wallclock_s`/`worker_id` als Study-User-Attrs [auch bei vorzeitigem Abbruch, `#833`-Stil] —
vorher führte der `#742`-Report KEINE Wallclock-Zeit je Study, Abschnitt 3.2/3.4 mussten das
selbst eingestehen; `wallclock_by_strategy`/`symbol_barrier_wait_s`/`worker_utilisation`
abgeleitet; echte Einzel-Trade-Longest-Trades mit Entry-/Exit-Zeitstempel + `exit_reason` bewusst
zurückgestellt, dieselbe FIFO-Match-Scope-Grenze wie `#832`); #852 (`optuna` stand OHNE jede
Versionsangabe in `requirements.txt`, obwohl derselbe Kommentar dort für `pandas` [`#802`] erklärt,
warum das inakzeptabel war — jetzt `optuna`/`numpy`/`nautilus_trader` mit oberer Grenze gepinnt,
`optimizer.json['pinned_library_versions']` + `check_library_version_drift` als Preflight,
gemeinsam mit `#844`); #853 (826/826 Studies `[#565] shrinkage_inactive` — KEIN neuer Code-Defekt,
sondern eine Kopplung dreier korrekter Mechanismen zu einem Deadlock [Pitfall-#258-Klasse]: `#834`
entwertete den Vorlauf-Store, ohne `#840`/`#841` bricht jeder Lauf vorzeitig ab,
`corroboration_count >= 2` ist bei Trunkierung + Semantik-Bump je Sitzung strukturell
unerreichbar; `seed_source` unterscheidet jetzt `'champion'`/`'champion_quality_stale'` als
POSITIVE Telemetrie [vorher nur die `[#565]`-Negativ-WARNUNG], `check_champion_seed_coverage`
warnt bei > 90 % `strategy_defaults`; die volle `corroborating_snapshots`-Datenmodell-Umstellung
[Issue-Text-Punkt 1] bewusst zurückgestellt); #854 (`reward_semantics_version` wurde für ZWEI
semantisch verschiedene Ereignisse überladen — die Reward-FORMEL hat sich geändert vs. die
SIMULATION selbst hat sich geändert; `optimizer.json['simulation_semantics_version']`
[Startwert 1] jetzt orthogonal eingeführt; `champions.champion_is_admissible` schliesst einen
`simulation_semantics_version`-Mismatch VOLLSTÄNDIG aus [params + quality], anders als ein reiner
`reward_semantics_version`-Mismatch [nur quality, `#819` unverändert];
`run_optimization._check_simulation_semantics_version` derselbe fail-loud+Purge-Mechanismus wie
`_check_reward_semantics_version`, eigener Fehlercode; `reward_semantics_version` 18→19, EIN
Auslöser `#848` — die Changelog-Präzisierung dokumentiert ausführlich, warum `#845` in dieser
Sitzung KEIN Auslöser ist [der `oos_evaluated`-ändernde Teil wurde bewusst zurückgestellt];
`invariants.check_semantics_version_coherence`). **Governance:** #855 (Pitfalls #259–#268, dieser
Eintrag). **Bewusst zurückgestellt (vier Punkte, alle dokumentiert am jeweiligen Code-Ort):**
echtes Cross-Symbol-Pipelining (`#843`), Einzel-Trade-Longest-Trades mit Zeitstempel + `exit_
reason` (`#851` Punkt 3), die volle `oos_evaluated=False`-Reward-Neutralität für
`SORTINO_INSUFFICIENT_DOWNSIDE`-Trials (`#845` Punkt 2), die `corroborating_snapshots`-
Datenmodell-Umstellung des Champion-Stores (`#853` Punkt 1) — alle vier erfordern entweder einen
echten Multi-Symbol-Sweep-Lauf mit Marktdaten oder einen dedizierten H0-Kalibrierlauf, die in
dieser Sandbox nicht existieren. Zehn neue Testdateien (`test_issue_836_exit_close_continuation.py`
… `test_issue_854_semantics_versioning.py`) + mehrere bestehende Fixtures korrigiert
(`test_issue_743_invariant_checks.py`s `to_dict()`-Schlüsselmenge, neun Sortino-Guard-abhängige
Tests nach der `#844`-Aktivierung, acht `min_win_rate`-lesende Tests nach `#848`,
`test_issue_637`/`834_reward_semantics_bump.py` auf den etablierten Bump-Präzedenzfall [`>=` statt
`==`] aktualisiert). Volle Suite: 20 vorbestehende, umgebungsbedingte Fehlschläge (identisch
vor/nach jedem Fix reproduziert, NICHT durch diesen Katalog verursacht) plus eine bekannte
order-abhängige Pollution-Klasse (die neuen Kohorte-A-Tests zeigen dasselbe Symptom NUR im
Voll-Suite-Kontext, isoliert/in kleinen Batches durchgehend grün — verifiziert per `git stash`-
Vergleich gegen den Vorzustand), alle neuen/geänderten Tests grün.

### 🟢 Pitfall #259 — Ein asynchroner Fortsetzungs-Token darf nie in dem synchronen Block gelöscht werden, der ihn erzeugt hat [BEHOBEN: GH-#836]
**Symptom:** `HourlyStrategyBase._close_position_base` (`automation/strategies/hourly_strategy_
base.py`) füllte `self._pending_cancels` mit den IDs der zu stornierenden Resting-Orders, um den
Markt-Close NACH deren Bestätigung (`on_order_canceled`) fortzusetzen. Zwei Zeilen später leerte
derselbe synchrone Aufrufer dieselbe Menge — bevor auch nur eine Bestätigung eingetroffen war. Alle
DREI Callback-Rückwege (`on_order_canceled`/`on_order_filled`/`on_order_rejected`) waren auf ein
nicht-leeres `_pending_cancels` gated und feuerten daher nie; der 24-Bar-GR-01-Zeit-Exit wurde in
~80 % der Studies eines Referenzlaufs durch eine bestehende Restorder silently unterdrückt.
**Root-Cause:** Wer eine Aktion auf einen asynchronen Callback vertagt (hier: den Markt-Close NACH
Order-Stornierung), BESITZT den Fortsetzungs-Token (hier: `_pending_cancels`) bis exakt dieser
Callback ihn konsumiert — kein synchroner Code-Pfad, der ihn erzeugt hat, darf ihn vorher leeren,
auch nicht „zur Sicherheit" oder aus Analogie zu einem synchronen Zustands-Reset.
**Fix/Regel:** `_exit_pending`/`_exit_pending_kind`/`_exit_pending_bars` ersetzen den Mechanismus;
`_pending_cancels` wird ausschliesslich in den drei Callbacks selbst geleert, nie im
Auslöser-Block. `_exit_close_watchdog` erzwingt nach `exit_close_max_bars` Bars ohne Bestätigung
einen `_execute_market_close()` mit `EXIT_CLOSE_STALLED`-Diagnose als Sicherheitsnetz gegen einen
künftig erneut blockierten Rückweg.

### 🟢 Pitfall #260 — Zustand, der eine bestätigte Transaktion beschreibt, wird ausschliesslich im Bestätigungs-Callback gesetzt [BEHOBEN: GH-#837]
**Symptom:** `_in_position = False`/`_bars_in_position = 0` standen im EXIT-AUSLÖSER (dem
synchronen Code, der die Order absetzt), nicht in `on_position_closed` (der Bestätigung). Der
Bar-Zähler startete dadurch bei jedem Exit-Versuch sofort neu, unabhängig davon, ob die Order
tatsächlich gefüllt wurde — ein Trailing-Stop, dessen Order storniert und neu platziert werden
musste, verankerte sich auf dem NEUEN (fälschlich als „flach" behandelten) Zustand, nicht auf der
tatsächlich noch offenen Position.
**Root-Cause:** Optimistische Zustandsübernahme — „die Order wurde abgesetzt, also behandle ich die
Position schon als geschlossen" — ist bei JEDEM asynchronen Order-Pfad falsch. Ein Order-Absetzen
ist eine ABSICHT, keine bestätigte Transaktion; nur der Callback trägt die Bestätigung.
**Fix/Regel:** `_trailing_initialised` (statt `_in_position`) entkoppelt die Trailing-Stop-
(Re-)Initialisierung vom optimistischen Positions-Flag; `_in_position`/`_bars_in_position`/
`_trailing_initialised` werden AUSSCHLIESSLICH in `on_position_closed` zurückgesetzt.

### 🟢 Pitfall #261 — Ein Test, der nur Konfigurationsgrenzen prüft, testet keinen Mechanismus [BEHOBEN: GH-#836]
**Symptom:** `test_issue_714_bar_time_box.py` prüfte Config-Defaults, Bounds-Klemmung im
Konstruktor und Grenzwerte — und blieb über mehrere Läufe hinweg grün, während der eigentliche
Zeit-Exit-MECHANISMUS (Pitfall #259) in ~80 % der Studies eines Referenzlaufs nicht griff.
**Root-Cause:** Ein Test, der nur die STATISCHE Konfiguration eines Vertrags prüft (Defaults,
Klemmung, Typvalidierung), verifiziert nie, dass der Vertrag zur LAUFZEIT tatsächlich durchgesetzt
wird — insbesondere nicht bei einem Vertrag, der einen mehrstufigen, asynchronen Order-Lebenszyklus
umfasst.
**Fix/Regel:** `test_issue_836_exit_close_continuation.py` prüft den vollständigen Lebenszyklus
(Order absetzen → Callback → Fortsetzung) end-to-end mit einem Order-Double. Jeder Vertrag, der
einen Order-Lebenszyklus umfasst, braucht mindestens einen Test, der diesen Lebenszyklus
tatsächlich DURCHLÄUFT, nicht nur seine Konfigurationsgrenzen.

### 🟢 Pitfall #262 — Eine Vorbedingungs-Prüfung am Funktionsanfang sperrt alles, was die Funktion tut [BEHOBEN: GH-#838]
**Symptom:** Der `max_trades_cap`-Early-Return am Anfang von `_check_exits_and_update` sperrte nicht
nur neue Entries (die eigentlich beabsichtigte Wirkung), sondern NEBENBEI auch Trailing-Stop-
Updates, den Zeit-Exit und das ATR-Update für JEDE bereits offene Position — sobald der Trade-Cap
erreicht war, konnte eine bestehende Position nicht mehr geschützt oder zeitlich beendet werden.
**Root-Cause:** Eine Vorbedingung, die am Anfang einer Funktion mit mehreren, logisch unabhängigen
Verantwortlichkeiten früh zurückkehrt, sperrt ALLE davon — nicht nur die eine, für die sie gedacht
war. Caps/Quoten, die nur NEUE Aktionen begrenzen sollen, gehören strukturell in den ENTRY-Pfad,
nicht vor eine Funktion, die auch bestehende Positionen verwaltet.
**Fix/Regel:** `_entry_allowed()` extrahiert die `max_trades_cap`-Prüfung als eigene Funktion, nur
noch aus `_compute_quantity` (dem Entry-Pfad) aufgerufen; `_check_exits_and_update` prüft sie nicht
mehr. Nebeneffekt der ursprünglichen Platzierung: ein `if` vor dem Funktions-Docstring machte diesen
zu einem toten, nie ausgeführten String-Literal — ein zusätzliches Symptom derselben
Fehlplatzierung.

### 🟢 Pitfall #263 — Zwei unabhängig gesampelte Parameter mit impliziter Ordnungsbeziehung brauchen eine explizite Constraint [BEHOBEN: GH-#838]
**Symptom:** `min_holding_time` und `max_bars_in_trade` werden unabhängig voneinander gesampelt.
Sobald `min_holding_time` (in Sekunden) grösser als `max_bars_in_trade` (in Bars) ausfiel, wurde der
Zeit-Exit dauerhaft unterdrückt — die Mindesthaltezeit-Klausel verhinderte, dass die Position
jemals das Alter erreichte, an dem der Zeit-Exit greifen durfte.
**Root-Cause:** Zwei Suchraum-Dimensionen, die eine STILLSCHWEIGENDE Ordnungsbeziehung tragen (hier:
`min_holding_time < max_bars_in_trade` muss gelten, damit der Zeit-Exit überhaupt erreichbar ist),
aber unabhängig gesampelt werden, können jederzeit eine Kombination erzeugen, die einen Mechanismus
strukturell unerreichbar macht — ohne dass irgendein einzelner Parameter für sich genommen ungültig
wäre.
**Fix/Regel:** Der Konstruktor klemmt `min_holding_time` hart unter `max_bars_in_trade` (mit einer
WARNING-Log-Zeile bei einer Verletzung), statt die Kombination unbeanstandet zu akzeptieren. Jede
Suchraum-Dimension mit einer impliziten Ordnungsbeziehung zu einer anderen braucht entweder eine
`constraints_func`-Klausel (Sampler-Ebene) oder eine Konstruktor-Klemmung (Ausführungs-Ebene) —
sonst bleibt sie ein reiner Zufallstreffer, ob eine gezogene Kombination den Mechanismus überhaupt
erreichbar lässt.

### 🟢 Pitfall #264 — Eine Resume-Fähigkeit ohne CLI-Einstieg existiert nicht [BEHOBEN: GH-#840]
**Symptom:** Der `#799`-Checkpoint-Mechanismus (`_write_checkpoint`, `sweep_progress.json`) war
VOLLSTÄNDIG implementiert und getestet — aber `sweep.main()` besass kein `--resume`/`--run-id`-Flag
und erzeugte bei jedem Aufruf eine neue `run_id`. Ein abgebrochener 35-Stunden-Lauf konnte nicht
fortgesetzt werden, obwohl die Infrastruktur dafür längst existierte.
**Root-Cause:** SECHSTE Wiederkehr von Pitfall #237 („ein Mechanismus, der nie von seinem
vorgesehenen Aufrufer erreicht wird, existiert praktisch nicht") — nach #794 (Retention),
#796/#797 (Storage-Policy), #818 (Champion-Writeback), #831 (Bounds-Widening-Post-Study-Pfad). Ein
vollständig korrekt implementierter Mechanismus OHNE einen erreichbaren Aufruf-Pfad liefert exakt
denselben Nutzen wie gar keine Implementierung.
**Fix/Regel:** `--resume`/`--run-id` als sich gegenseitig ausschliessende CLI-Argumente ergänzt;
`--resume` validiert `_strategies_fingerprint` (SHA-256 über Strategien + Semantik-Versionen) gegen
den gespeicherten Checkpoint und bricht bei einem Mismatch mit Exit-Code 2 ab, statt einen
inkompatiblen Checkpoint stillschweigend zu übernehmen. Bei JEDER neuen Fähigkeit mit einem
internen Mechanismus: sofort prüfen, ob ein tatsächlich erreichbarer Aufruf-Pfad (CLI-Flag,
Produktions-Call-Site, Scheduler-Eintrag) existiert — nicht erst, wenn ein Operator sie zum ersten
Mal braucht und feststellt, dass sie unerreichbar ist.

### 🟢 Pitfall #265 — Ein Laufzeit-Budget ohne Fortschritts-Rotation erzeugt eine dauerhaft unbearbeitete Teilmenge [BEHOBEN: GH-#841]
**Symptom:** `sweep_max_wallclock_h` schnitt bei stabiler (z. B. alphabetischer) Symbolreihenfolge
IMMER denselben Schwanz des Universums ab — 84 von 143 Symbolen wurden über mehrere Läufe hinweg
NIE optimiert, während die ersten ~59 Symbole bei jedem Lauf erneut (redundant) bearbeitet wurden.
**Root-Cause:** Ein Wallclock-/Ressourcen-Budget, das eine Menge von Arbeitseinheiten in EINER
FESTEN Reihenfolge abarbeitet, ist funktional äquivalent zu einer harten Allowlist der ersten N
Einheiten — unabhängig davon, wie oft der Lauf wiederholt wird. `symbols_completed`-Telemetrie
(reine Zähl-Statistik) macht diese Verzerrung nicht sichtbar, weil sie nicht aufzeichnet, WELCHE
Symbole wiederholt VOR der Abbruchgrenze lagen.
**Fix/Regel:** `symbol_coverage.py` — ein persistenter Ledger, der `last_completed_at_utc` je Symbol
über Läufe hinweg trägt; `order_symbols(policy='least_recently_covered')` sortiert nie/am längsten
nicht abgedeckte Symbole an den ANFANG jedes neuen Laufs. Ein reines Laufzeit-Budget OHNE eine
solche Rotationslogik braucht IMMER einen expliziten Fortschritts-Ledger, sobald es wiederholt auf
derselben Menge angewendet wird — sonst ist „irgendwann abgedeckt" strukturell falsch, selbst wenn
jeder Einzellauf korrekt funktioniert.

### 🟢 Pitfall #266 — Ein Worker-Pool, dessen Task-Batch kleiner ist als max_workers, wird durch Anheben von max_workers nicht schneller [BEHOBEN: GH-#828, dokumentiert #843]
**Symptom:** Die Deckelung `min(n_jobs, len(symbol_pairs))` → `n_jobs` (Katalog #817–#835, #828)
änderte die Laufzeit nicht messbar — ein Batch mit 14 Strategien-Paaren je Symbol nutzt nie mehr als
14 der bis zu 22 konfigurierten Worker, unabhängig von der Deckelungs-Logik selbst.
**Root-Cause:** Der Engpass war die BARRIERE (jedes Symbol wartet auf seine langsamste Strategie,
bevor der nächste `ThreadPoolExecutor`-Batch startet), nicht die Worker-Deckelung. Eine
Kapazitätserhöhung an einer Stelle, die nie der tatsächliche Flaschenhals war, ändert nichts —
selbst wenn die Erhöhung selbst korrekt implementiert ist.
**Fix/Regel:** Vor JEDER Parallelisierungs-Kapazitätserhöhung (mehr Worker, grösserer Pool) explizit
messen, ob der tatsächliche Engpass die WORKER-ZAHL ist oder die BATCH-GRÖSSE/-Struktur (hier: die
Barriere zwischen Symbolen). #843 (dieser Katalog) hat echtes Cross-Symbol-Pipelining als
Gegenmassnahme analysiert, aber mangels eines realen Multi-Symbol-Katalog-Fixtures zur
Bit-Identitäts-Verifikation NICHT umgesetzt (siehe dortige Zurückstellung) — die
`wallclock_by_strategy`/`symbol_barrier_wait_s`/`worker_utilisation`-Telemetrie (#851) legt das
Rohmaterial für eine künftige, verifizierbare Umsetzung.

### 🟢 Pitfall #267 — Ein Config-Key, den ein Modul mit None-Fallback liest, ist ohne Registry-Preflight unsichtbar abwesend [BEHOBEN: GH-#844]
**Symptom:** `sortino_numeric_guard_min_periods` fehlte weiterhin in `tournament.json`, obwohl #823
das Problem bereits diagnostiziert UND der #823-Fix den Key im `_schema` dokumentiert hatte — der
tatsächliche WERT wurde nie gesetzt. Der T-bewusste Sortino-Numerik-Guard blieb über alle
`n_periods` hinweg flach bei 25.0.
**Root-Cause:** FÜNFTE Wiederkehr nach #488/#753/#769/#805/#823 — ein Config-Modul, das einen Key
mit `opt_data.get(key, default)` liest, macht dessen Abwesenheit UNSICHTBAR: das Verhalten bleibt
lauffähig (der Fallback greift), nur eben nicht das BEABSICHTIGTE Verhalten. Ein Fallback, der einen
Mechanismus komplett DEAKTIVIERT (hier: keine T-Skalierung), ist kein harmloser Default, sondern ein
stiller Semantikwechsel, den kein Test bemerkt, der nur die FUNKTION isoliert mit explizit
übergebenen Parametern prüft.
**Fix/Regel:** `automation/config/_required_keys.json` — eine deklarative Registry
(`{datei: {key: {required, reject_values}}}`); `invariants.check_required_config_keys` +
`sweep.assert_required_config_keys_valid()` brechen VOR dem ersten Symbol fail-loud (Exit-Code 2)
ab, wenn ein registrierter Key fehlt, `null` ist, oder einen verbotenen Wert trägt — GENERALISIERT
über JEDEN künftig registrierten Key, nicht nur diesen einen. Ein Config-Key, der einen Mechanismus
aktiviert/deaktiviert, MUSS entweder registriert sein oder sein Fehlen muss an einer anderen,
tatsächlich beobachteten Stelle (Log, Report-Check) sichtbar gemacht werden — ein blosser
Schema-Kommentar reicht nachweislich nicht (das war bereits der #823-Fix-Zustand).

### 🟢 Pitfall #268 — Eine Kennzahl, deren Varianz überwiegend aus einer Kontextdimension statt aus dem Prüfobjekt stammt, darf nicht als Leistungsvergleich des Prüfobjekts dargestellt werden [BEHOBEN: GH-#850]
**Symptom:** `holdout_excess_return` (Strategie-Return minus Buy&Hold) wurde in Berichts-Abschnitt
2.3 als „Vergleich gegen Buy & Hold je Symbol" mit einer „schlägt Buy & Hold"-Wertung dargestellt.
14 strukturell verschiedene Strategien (Trendfolge, Mean-Reversion, Squeeze-Breakout,
Opening-Range, RSI(2)) lieferten auf demselben Symbol Ergebnisse innerhalb von 3,4 Prozentpunkten,
bei einem absoluten Niveau von über 20 Prozentpunkten.
**Root-Cause:** Eine Ein-Weg-Varianzzerlegung (`report._excess_variance_decomposition`) zeigt: 99,1
% der Gesamtstreuung über alle 233 Zeilen entfiel auf das SYMBOL, nur 0,9 % auf die STRATEGIE.
`holdout_excess_return` ist im Bärenmarkt näherungsweise `−Buy&Hold` — eine Symbol-KONSTANTE, kein
strategiespezifisches Signal. Eine als Ranking präsentierte Kennzahl, deren Streuung überwiegend
aus einer Kontextdimension (hier: welches Symbol) statt aus dem eigentlichen Prüfobjekt (hier: die
Strategie) stammt, belohnt strukturell das FALSCHE Merkmal — hier: überwiegend in Cash gestandene
Strategien auf einem stark fallenden Symbol, nicht echtes Alpha.
**Fix/Regel:** VOR jeder Ranking-Darstellung einer abgeleiteten Kennzahl über mehrere Gruppierungs-
dimensionen (hier: Strategie × Symbol) eine Varianzzerlegung rechnen und ausweisen. `exposure_
fraction` (Anteil der Fenster-Zeit mit offener Position) + `excess_per_exposure` unterscheiden
echtes Alpha von vermiedenem Kursverlust; ein negativer Buy&Hold-Return unterdrückt jetzt die
„schlägt Buy & Hold"-Wertung (`summary_de.py` Abschnitt 2.3).

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #836–#855)
- `tournament.json.timebox_violation_tolerance` (Default 0.0) — #839.
- `tournament.json.sortino_numeric_guard_min_periods` (`null` → 1600, jetzt tatsächlich gesetzt,
  Pitfall #267) — #844.
- `tournament.json.eligible_requires_any` (`min_win_rate` entfernt, FÜNFTE Wiederkehr) — #848.
- `tournament.json.deflation_max_n_periods_ratio` (Default 4.0) — #845.
- `optimizer.json.fail_fast_invariants` / `fail_fast_min_symbols` — #839.
- `optimizer.json.sweep_max_wallclock_h` (24 → 72) / `wallclock_forecast_after_symbols` /
  `sweep_symbol_order_policy` (Default `'least_recently_covered'`) / `symbol_coverage_max_age_runs`
  (Default 3) — #841/#842.
- `optimizer.json.summary_max_details_per_check` (Default 5) — #849.
- `optimizer.json.pinned_library_versions` (`{pandas, optuna, numpy, nautilus_trader}`) — #852.
- `optimizer.json.reward_semantics_version` 18 → 19 — #854 (EIN Auslöser: #848; siehe dortiger
  Changelog-Eintrag, warum #845 KEIN Auslöser ist).
- `optimizer.json.simulation_semantics_version` (neu, Startwert 1) — #854, orthogonal zu
  `reward_semantics_version` (siehe dortiges Schema für die reward/simulation/params_schema-
  Abgrenzung).

### 🔒 Watertight Invariants (Issue-Katalog #836–#855) — für künftige Agenten
- **`HourlyStrategyBase._exit_pending`/`_exit_close_watchdog`** (`hourly_strategy_base.py`, #836) —
  der EINE Fortsetzungs-Token-Mechanismus für einen asynchronen Exit; `_pending_cancels` wird
  AUSSCHLIESSLICH in den drei Order-Callbacks geleert (Pitfall #259).
- **`invariants.compute_trial_timebox_violations`** (`invariants.py`, #839) — die EINE
  Zeitbox-Verletzungs-Zählfunktion; `confirm.confirm_per_symbol_promotion`s
  `REJECT_INVALID_TIMEBOX` und `report.py`s `timebox_*`-Felder konsumieren dieselbe Funktion.
- **`sweep._strategies_fingerprint`** (`sweep.py`, #840) — validiert `--resume` gegen einen
  inkompatiblen Checkpoint (andere Strategien/Semantik-Versionen); Exit-Code 2 bei Mismatch.
- **`symbol_coverage.order_symbols`** (`symbol_coverage.py`, #841) — die EINE Rotationsfunktion für
  die Sweep-Dispatch-Reihenfolge; `invariants.check_symbol_coverage` verifiziert, dass kein Symbol
  über `symbol_coverage_max_age_runs` Läufe unabgedeckt bleibt (Pitfall #265).
- **`invariants.check_required_config_keys`** (`invariants.py`, #844) — GENERALISIERTE
  Registry-Prüfung (`_required_keys.json`) für JEDEN künftig registrierten Config-Key, nicht nur
  `sortino_numeric_guard_min_periods` (Pitfall #267).
- **`invariants.check_family_n_periods_homogeneity`** (`invariants.py`, #845) — verifiziert, dass
  `confirm.py`s `deflation_n_periods_ratio`-Suppression bei überschrittener Schwelle tatsächlich
  greift (kein `deflated_dsr` trotz Heterogenität).
- **`invariants.check_sr0_coherence`** (`invariants.py`, seit #651, jetzt VIERTE Durchsetzung
  #846) — `deflated_sr0` NIE ohne begleitendes `deflated_dsr`/`deflation_dsr_z` (oder umgekehrt).
- **`invariants.check_selection_rule_homogeneity`** (`invariants.py`, #848) — FAIL (statt WARNUNG)
  bei mehr als einem `selection_rule_fingerprint` je Symbol.
- **`invariants.InvariantResult.to_dict()`** (`invariants.py`, #849) — exportiert `name` UND
  `check` (identischer Wert) als Übergangs-Vertrag; `summary_de._check_name` liest `name` zuerst.
- **`report._excess_variance_decomposition`** (`report.py`, #850) — die EINE
  Varianzzerlegungs-Funktion für `holdout_excess_return`; VOR jeder künftigen Ranking-Darstellung
  einer Kennzahl über Strategie × Symbol wiederverwenden (Pitfall #268).
- **`run_optimization._check_simulation_semantics_version`** (`run_optimization.py`, #854) —
  orthogonal zu `_check_reward_semantics_version`; `champions.champion_simulation_stale`
  entscheidet den VOLLSTÄNDIGEN Ausschluss eines Champion-Eintrags, nicht nur `quality_stale`.
- **`invariants.check_semantics_version_coherence`** (`invariants.py`, #854) — FAIL, wenn ein
  Champion-Store-Eintrag mit veralteter `simulation_semantics_version` trotzdem als
  `champion_is_admissible` gilt (verifiziert die #854-Hartausschluss-Garantie, statt sie nur zu
  behaupten).

## Issue-Katalog #897–#912 — Exit-Sperrklinke, Kostenmodell-Fallback, Governance-Rückstand (GitHub-Issues #771/#769/#770/#772, Sitzung 2026-08-06)

Vier Kataloge auf demselben Katalog-Lauf (Basis-Commit `353ff773`). **Katalog A — Exit-Pfad &
Kostenmodell (Simulations-Bump, #897–#900):** #897/Pitfall #285/#286 (der ATR-Trailing-Stop rastete
auf `max(alter_Stop, close − k·ATR)` — bei fallender Volatilität degenerierte die Preis-Ratsche zu
einer reinen ATR-Verfolgung, die sich nie erholte; `trailing_stop_anchor='price_extreme'` rastet
jetzt auf dem seit Entry erreichten Preis-Extremum, `close_ratchet` bleibt bit-identisch als
Opt-Out); #898/Pitfall #287 (`asset_class='Unknown'` fiel still auf `spread_bps_by_asset_class
['DEFAULT']` zurück — 69 Instrumente in `instrument_map.json` betroffen; `resolve_spread_bps` wirft
jetzt `InstrumentMetadataIncompleteError`, Policy `unknown_asset_class_policy` Default `'reject'`);
#899 (Exit-Reason-/ATR-bps-Telemetrie über `OrderFactory`-Tags — `EXIT_REASON`/`ATR_MEDIAN_BPS`/
`ATR_MIN_BPS`, ausgelesen über `_build_order_exit_meta`/`_aggregate_exit_telemetry` in
`backtest_runner.py`); #900 (Bar-Qualitäts-Preflight erhält einen True-Range-/ATR-Skalen-Check
[`frac_zero_true_range`, `atr_median_bps`] gegen degenerierte Bar-Daten, `max_frac_high_eq_low`
0.5→0.20). **Katalog B — Inferenz-Integrität (Reward-Bump, #901–#903):** #901
(`_effective_sortino_numeric_guard` lieferte bei `sortino_numeric_guard_reference='family_median'`
ohne verfügbaren Familienwert STILL `source='absolute'` statt ehrlich `None`/
`'family_median_unavailable'` — dieselbe Fehlerklasse wie #759/#788); #902 (`bar_seconds` ist für
`compute_trial_timebox_violations` jetzt Pflichtparameter, `_contracts.BAR_SECONDS_DEFAULT` löst
drei unabhängig gepflegte `3600.0`-Literale ab); #903 (die Zeitbox-Verletzungsrate wurde auf
TRIAL-Ebene gezählt, nicht auf ROUND-TRIP-Ebene — `timebox_round_trip_violation_fraction` ergänzt
die trade-genaue Zählung). **Katalog C — Familien-Multiplizität & Governance (kein Bump,
#904–#907):** #904/Pitfall #289/#290 (`promotion_family_scope='per_strategy'` war seit #826 Default,
wurde aber durch eine spätere `max(deflation_n_family_raw, len(family_rows))`-Zeile bei
vollständiger Cluster-Abdeckung wieder auf die symbolweite Summe angehoben — zwei einzeln korrekte
Fixes annullierten sich; `deflation_n_family_effective ≤ deflation_n_family_raw` ist jetzt eine
harte Invariante, unvollständige Abdeckung SETZT die Declusterung AUS statt sie zu erhöhen); #905
(`_family_period_returns_from_studies` filterte auf `oos_evaluated` statt `oos_selection_statistic_
available` — derselbe Zählfehler wie #822/v18, hier im Perioden-Returns-Matrix-Builder statt im
Zähler); #906 (Kollinearitäts-Konsolidierungsentscheidung bewusst auf den #897-Kalibrierlauf
vertagt — `gate_collinearity_policy`/`gate_collinearity_accepted_pairs` in `tournament.json`
dokumentieren den Vertagungs-Zustand, keine Gate-Änderung in dieser Sitzung); #907
(`check_gate_collinearity_decision_required`/`check_fail_fast_invariants_wired` erzwingen, dass ein
Kollinearitäts-Alarm über `threshold` eine EXPLIZITE, begründete Entscheidung trägt, statt folgenlos
zu bleiben). **Katalog D — Durchsatz & Governance (kein Bump ausser #912 selbst, #908–#912):** #908
/Pitfall #288 (`pipeline_depth` blieb über drei Kataloge [#843/#871/#893] dokumentiert, aber
unverdrahtet — DOKUMENTIERT-NICHT-VERDRAHTET bleibt der Zustand nach dieser Sitzung explizit, siehe
Pitfall #288; `AdxAtrMomentumStrategy`-Suchraum erhält `min_holding_time`-Sampling und
`cooldown_bars`-Untergrenze 2→6 gegen das neu aufgetretene 754-Trades-Regime; Wallclock-Forecast
kürzt jetzt nachweislich die Symbolliste statt nur zu warnen); #909/Pitfall #291
(`global_catalog_oldest_ns` maß über `min(latest_ts_by_symbol(...).values())` die Streuung der
SYMBOL-Enddaten, nicht die Katalog-Historienlänge — `earliest_ts_by_symbol`/`per_symbol_span_stats`
liefern die pro-Symbol-Spanne); #910 (Champion-Kette deadlocked: 14/14 Writebacks
`NO_ADMISSIBLE_ENTRY`, weil Korroboration an `advance_days` [ein Datenfenster-Fortschritt] gekoppelt
war — zwei Läufe derselben Datenbasis konnten per Konstruktion nie korroborieren;
`champion_corroboration_mode='either'` [Default] lässt Korroboration ODER Fenster-Fortschritt
genügen; `load_champion_entry_with_reason` unterscheidet `STORE_EMPTY` von echten
Inadmissibilitäts-Gründen); #911/Pitfall #292 (`max_consecutive_structural_runs` [Default 2] deckelt
strukturelle Sackgassen [`signal_absent`/`signal_sparse`]; `signal_quality` eskaliert NICHT MEHR auf
`denylist`, sondern auf `quarantined_pending_simulation_review` — der Rückschrieb war bis zum
#897-Fix gegen eine defekte Simulationsschicht gerichtet gewesen); #912 (dieser Eintrag — Doppel-Bump
`simulation_semantics_version` 1→2, `reward_semantics_version` 19→20, vollständige
Auslöser-Begründung im jeweiligen `_schema`-Feld, siehe dort).

### 🟢 Pitfall #285 — Eine Sperrklinke darf nur auf der Grösse rasten, die sie sichern soll, nicht auf ihrer Schätzgrösse [BEHOBEN: GH-#897]
**Symptom:** Der ATR-Trailing-Stop folgte dem Kurs bei fallender Volatilität nach einer
Preis-Rally NICHT nach, sondern blieb auf dem enger werdenden ATR-Abstand kleben — Median-
Bruttoverlust 5,43 bps bei einem konfigurierten Stop-Abstand von 30–80 bps.
**Root-Cause:** `hourly_strategy_base.py::_check_exits_and_update` aktualisierte den Stop als
`max(alter_Stop, close − k·ATR)`. Die Ratsche sicherte damit effektiv den ATR-ABSTAND (eine
Schätzgrösse, die mit sinkender Volatilität selbst schrumpft), nicht den erreichten PREIS (die
eigentlich zu sichernde Grösse) — bei fallendem ATR "erholte" sich der Stop scheinbar, tatsächlich
degenerierte er zu einer reinen ATR-Verfolgung ohne Bezug zum Kursverlauf.
**Fix/Regel:** `trailing_stop_anchor='price_extreme'` (Default) rastet auf
`max(bisheriges Preis-Extremum) − k·max(ATR, atr_floor_bps)`; das Preis-Extremum ist per Definition
monoton und kann sich nie "erholen". Vor jeder Ratschen-Implementierung explizit benennen, WELCHE
Grösse gesichert werden soll, und verifizieren, dass genau diese (nicht eine mit ihr korrelierte
Schätzgrösse) monoton in die Ratschen-Formel eingeht.

### 🟢 Pitfall #286 — Ein realisierter Effekt ohne Empfindlichkeit gegenüber seinem eigenen Konfigurationsparameter ist ein Artefakt, kein kalibrierter Mechanismus [BEHOBEN: GH-#897]
**Symptom:** `atr_trailing_multiplier` war konfigurierbar und dokumentiert, aber kein Test
verifizierte, dass eine Änderung des Werts den tatsächlich realisierten Stop-Abstand proportional
verschiebt — derselbe #897-Root-Cause (ATR-Ratsche statt Preis-Ratsche) hätte einen solchen Test
durchfallen lassen, lange bevor der Effekt im Produktivlauf auffiel.
**Root-Cause:** Bestehende Tests prüften analog Pitfall #261 nur Config-Grenzen/
Konstruktor-Klemmung, nie die End-zu-End-Wirkung eines Risikomodell-Parameters auf eine gemessene
Kennzahl.
**Fix/Regel:** `invariants.check_effective_stop_distance` verifiziert
`oos_gross_loss_mean_bps / (atr_trailing_multiplier_median · atr_median_bps) ≥ min_ratio` je Study.
Ein Risikomodell-Parameter ohne nachweisbare Wirkung auf die Grösse, die er kontrollieren soll, ist
keine Kalibrierung, sondern ein Blindgänger — jedes künftige Risikomodell-Feature braucht diese
Variations-Prüfung als Mindestabnahme, nicht nur einen Grenzwert-Test.

### 🟢 Pitfall #287 — Ein Enum-Wert aus der Datenquelle, der in der Nachschlagetabelle fehlt, darf nie still auf DEFAULT fallen [BEHOBEN: GH-#898]
**Symptom:** `instrument_map.json` enthielt 69 Instrumente mit `asset_class='Unknown'`;
`resolve_spread_bps` fiel dafür still auf `spread_bps_by_asset_class['DEFAULT']` (4,0 bps) zurück —
ein abweichender realer Spread wurde unbemerkt falsch simuliert, für fast die Hälfte des Universums.
**Root-Cause:** `_resolve_asset_class_for_symbol`/`resolve_spread_bps` behandelten eine unbekannte
`asset_class` wie eine gültige, nur nicht explizit gemappte Kategorie (Fail-Open) — bei einer
Kostenkonstante, die direkt in jeden simulierten Bid/Ask-Preis eingeht (`load_ticks_from_catalog`),
ist das die teuerste mögliche Voreinstellung.
**Fix/Regel:** Eine unbekannte/leere `asset_class` liefert jetzt `'UNKNOWN'` und wirft (Policy
`'reject'`, Default) `InstrumentMetadataIncompleteError` statt eines stillen DEFAULT-Fallbacks;
`unknown_asset_class_policy` erlaubt ein bewusstes, benanntes Opt-Out. Jeder Enum-Auflösungspfad mit
Datenquellen-Ursprung (Symbol → Kategorie, Kategorie → Kostenkonstante) braucht einen expliziten
"kein Mapping gefunden"-Zweig, der fail-loud statt fail-open reagiert.

### 🟢 Pitfall #288 — Ein existierender, dokumentierter, registrierter Config-Key kann trotzdem tot sein — nur ein Variations-Test findet das [BEHOBEN: GH-#908]
**Symptom:** `optimizer.json['pipeline_depth']` war über drei Kataloge (#843/#871/#893) als
geplant dokumentiert, aber `sweep.py` überbrückte die Symbol-Barriere nie; effektiver
Parallelitätsgrad blieb bei 5,6 trotz 22 konfigurierten Workern.
**Root-Cause:** Ein Key-Existenz-Test (`assert 'pipeline_depth' in CFG`) wäre grün geblieben,
obwohl der Key nirgends gelesen wurde — Existenz und Wirkung sind zwei verschiedene Eigenschaften,
die ein reiner Registry-Test (Pitfall #267-Klasse) nicht unterscheidet.
**Fix/Regel:** Jeder neu eingeführte Config-Key braucht zusätzlich zum Registry-Preflight (gegen
FEHLENDE Keys) einen Test, der den Wert VARIIERT und eine beobachtbare Telemetrie-/
Verhaltensänderung erwartet (gegen WIRKUNGSLOSE Keys). `pipeline_depth` bleibt für diesen Katalog
bewusst dokumentiert-aber-nicht-verdrahtet — genau der Zustand, den dieser Pitfall benennt, nicht
behebt; die echte Umsetzung bleibt für eine künftige Sitzung mit einem realen
Multi-Symbol-Katalog-Fixture offen (vgl. #843-Rückstellungsnotiz).

### 🟢 Pitfall #289 — Zwei einzeln korrekte Fixes an derselben Grösse können sich über ein max() gegenseitig annullieren [BEHOBEN: GH-#904]
**Symptom:** `promotion_family_scope='per_strategy'` war seit #826 Default und in `confirm.py`
korrekt auf N1 (die eigene Study-Zahl) gesetzt — bis eine spätere, isoliert korrekte Zeile
`deflation_n_family_raw = max(deflation_n_family_raw, len(family_rows))` denselben Wert bei
ausreichender Perioden-Returns-Abdeckung wieder auf die symbolweite Summe anhob.
**Root-Cause:** Zwei zu unterschiedlichen Zeitpunkten unabhängig korrekte Fixes definierten
denselben Namen (`deflation_n_family_raw`) über zwei verschiedene Berechnungswege, ohne
voneinander zu wissen; `max()` wählte strukturell den GRÖSSEREN — hier den scope-verletzenden —
der beiden Werte.
**Fix/Regel:** `deflation_n_family_raw` wird jetzt ausschliesslich aus `deflation_n_family` (dem
`per_strategy`-Scope) abgeleitet, ohne nachträgliche Rekonstruktion aus einer anderen Quelle. Wer
einen engeren Scope für eine Grösse einführt, muss JEDE spätere Stelle, die dieselbe Grösse aus
einer anderen Quelle rekonstruiert, explizit verbieten oder auf den neuen Scope umstellen — ein
`max()` zwischen zwei unterschiedlich skalierten Scopes derselben Grösse ist niemals harmlos.

### 🟢 Pitfall #290 — Eine Declusterung darf die Multiplizität nie erhöhen — unvollständige Abdeckung setzt sie aus, sie korrigiert nicht nach oben [BEHOBEN: GH-#904]
**Symptom:** Dieselbe #904-Stelle: bei unvollständiger Abdeckung der Perioden-Returns-Matrix
(weniger Studies mit gespeicherten Renditeserien als die Familie Trials hat) hob die alte
`max()`-Zeile die gemeldete Multiplizität über den Rohwert `deflation_n_family_raw` hinaus an.
**Root-Cause:** `cluster_effective_configs` reduziert eine VOLLSTÄNDIGE Renditeserien-Matrix auf
effektiv unabhängige Konfigurationen; bei unvollständiger Abdeckung ist das Ergebnis kein gültiger
Declusterungs-Output, sondern ein Artefakt der fehlenden Zeilen — ihn trotzdem als Multiplizität zu
verwenden, kehrt die Wirkrichtung der Deflationskorrektur um (eine Declusterung, die MEHR
Multiplizität meldet als ohne sie, ist ein Widerspruch in sich).
**Fix/Regel:** `deflation_n_family_effective = min(deflation_n_family_effective,
deflation_n_family_raw)` ist jetzt eine harte Invariante; bei `deflation_cluster_coverage < 1.0`
wird die Declusterung ausgesetzt (`deflation_n_family_effective = deflation_n_family_raw`) statt
einen unvollständigen Cluster-Output zu verwenden. Jede Declusterungs-/Verdichtungs-Funktion braucht
dieselbe Obergrenzen-Invariante gegen ihren eigenen Rohwert.

### 🟢 Pitfall #291 — Ein Aggregat über eine Menge von Endzeitpunkten heterogener Entitäten misst deren Streuung, nicht die Grösse jeder einzelnen Entität [BEHOBEN: GH-#909]
**Symptom:** Der `[#624]`-Preflight meldete "verfügbare Katalog-Spanne = 1,3 Tage" auf einem
gesunden Katalog mit 426+ Tagen Historie je Symbol — der Alarm wurde umso lauter, je mehr Symbole
zufällig auf ähnliche Enddaten konvergierten.
**Root-Cause:** `global_catalog_oldest_ns = min(latest_ts_by_symbol(...).values())` — das Minimum
über die JÜNGSTEN Zeitstempel je Symbol ist die früheste ENDDATIERUNG über alle Symbole, nicht der
älteste Datenpunkt irgendeines Symbols. Die Grösse maß die Streuung der Enddaten (eine
Kohärenz-Eigenschaft des gesamten Katalogs), wurde aber als Historienlänge (eine Eigenschaft jedes
einzelnen Symbols) interpretiert und gemeldet.
**Fix/Regel:** `earliest_ts_by_symbol` (symmetrisch zu `latest_ts_by_symbol`) + `per_symbol_span_
stats` liefern `min_span_days`/`median_span_days`/`n_symbols_below_required` PRO Symbol, erst dann
aggregiert — nie ein `min`/`max` direkt über eine Menge von Endzeitpunkten verschiedener Entitäten.
Jedes Aggregat über heterogene Entitäten braucht die Achse (hier: "Spanne je Symbol", nicht
"Endzeitpunkt über Symbole") explizit im Namen, sonst verschmelzen zwei verschiedene Fragen zu
einer falschen Antwort.

### 🟢 Pitfall #292 — Eine Suchraum-Diagnose ist nur gültig, solange die Simulationsschicht, die sie erzeugt hat, gültig ist [BEHOBEN: GH-#911]
**Symptom:** Sechs `ZERO_ELIGIBLE_PLATEAU`-Events mit `binding_cause='signal_quality'` hätten unter
der alten Eskalationsregel Denylist-Einträge erzeugt — für Strategien, deren einziges Problem der
#897-Trailing-Stop-Bug war (94 % aller Trades endeten an der defekten Sperrklinke nahe Breakeven).
Ein Denylist-Rückschrieb auf dieser Basis hätte funktionierende Strategien dauerhaft ausgeschlossen.
**Root-Cause:** `recommend_diagnosis_action` bewertete `binding_cause` ausschliesslich anhand der
TRADE-Statistik (Frequenz, Qualität), nie anhand der Frage, ob die Simulationsschicht, die diese
Statistik erzeugt hat, zum Diagnosezeitpunkt noch als korrekt gilt — eine Diagnose kann strukturell
korrekt gerechnet und trotzdem gegenstandslos sein, wenn ihre Datengrundlage seither als defekt
erkannt wurde.
**Fix/Regel:** `signal_quality` eskaliert jetzt auf `'quarantined_pending_simulation_review'` statt
`'denylist'` und stempelt `diagnosed_simulation_semantics_version`; ein Re-Test ist erst nach einem
`simulation_semantics_version`-Bump gegenüber dem Diagnosezeitpunkt fällig. Jeder
Closed-Loop-Rückschrieb, der aus SIMULIERTEN Kennzahlen eine Handlungsempfehlung ableitet, braucht
die Simulationsversion als Gültigkeitsstempel, nicht nur die Handlungsempfehlung selbst.

### ⚠️ Dokumentationsrückstand: Pitfalls #269–#284 (Kataloge #856–#896) weiterhin offen
`#912` (dieser Katalog) stellt explizit fest, dass die Pitfalls #269–#284 aus den dazwischenliegenden
Katalogen #856–#896 nie eingetragen wurden — zu dieser Sitzung bereits drei Sitzungen Rückstand,
nachweisbar an mehreren Wiederkehr-Fällen in diesem Katalog selbst (#901 = siebte Wiederkehr
Pitfall #267-Klasse, #903 = neunte Wiederkehr der Zählebenen-Verwechslung, #904 = neunte Wiederkehr
der Multiplizitätsklasse). Diese Sitzung trägt #285–#292 (die im Auslöse-Issue #912 vollständig
ausformulierten acht neuen Pitfalls) vollständig nach, verlängert den bestehenden Rückstand
#269–#284 aber NICHT durch Rekonstruktion — der Inhalt dieser 16 Nummern (Kataloge #856–#896) liegt
ausserhalb des in dieser Sitzung tatsächlich bearbeiteten Materials (#897–#912) und ausserhalb des
mit vertretbarem Aufwand nachprüfbaren Kontexts. Eine Rekonstruktion ohne die Original-Issues
(#856–#896) im Volltext würde 16 Pitfall-Einträge mit erfundenem statt belegtem Root-Cause
erzeugen — das wäre ein Dokumentationsfehler derselben Klasse, die dieser Abschnitt eigentlich
verhindern soll. Für eine künftige Sitzung: `#856`–`#896` (GitHub-Issues) im Volltext abrufen und
gegen die tatsächlich in `git log` dieser Spanne umgesetzten Fixes abgleichen, dann #269–#284
nachtragen.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #897–#912)
- `optimizer.json.trailing_stop_anchor` (Default `'price_extreme'`) / `atr_floor_bps` (Default 2.0)
  — #897, Pitfall #285.
- `optimizer.json.stop_distance_min_ratio` (Default 0.4) — #897, Pitfall #286.
- `backtest.json.unknown_asset_class_policy` (Default `'reject'`) — #898, Pitfall #287.
- `optimizer.json.bar_quality.max_frac_zero_true_range` (Default 0.25) /
  `bar_quality.min_atr_median_bps` (Default 5.0) / `bar_quality.max_frac_high_eq_low` (0.5→0.20)
  — #900.
- `optimizer.json.fail_fast_invariants` (`check_effective_stop_distance` ergänzt) — #897.
- `tournament.json.gate_collinearity_policy` (Default `'require_decision'`) /
  `gate_collinearity_accepted_pairs` — #906/#907 (Konsolidierungsentscheidung selbst vertagt).
- `optimizer.json.pipeline_depth` (Default 2, dokumentiert-nicht-verdrahtet, Pitfall #288) — #908.
- `optimizer.json.champion_corroboration_mode` (Default `'either'`, ∈
  {`window_advance`, `independent_search`, `either`}) — #910.
- `optimizer.json.max_consecutive_structural_runs` (Default 2) — #911, Pitfall #292.
- `optimizer.json.reward_semantics_version` 19 → 20 (ein Auslöser: #901) — #912.
- `optimizer.json.simulation_semantics_version` 1 → 2 (zwei Auslöser: #897, #898) — #912.

### 🔒 Watertight Invariants (Issue-Katalog #897–#912) — für künftige Agenten
- **`hourly_strategy_base._effective_atr_value`/`_check_exits_and_update`** (`hourly_strategy_
  base.py`, #897) — die EINE Trailing-Stop-Ratschen-Implementierung; `trailing_stop_anchor` ist die
  einzige Stelle, die zwischen `price_extreme` und `close_ratchet` entscheidet (Pitfall #285).
- **`invariants.check_effective_stop_distance`** (`invariants.py`, #897) — verifiziert, dass der
  realisierte Bruttoverlust proportional zum konfigurierten Stop-Abstand skaliert (Pitfall #286).
- **`backtest_runner._resolve_asset_class_for_symbol`/`resolve_spread_bps`** (`backtest_runner.py`,
  #898) — die EINE Kostenmodell-Auflösungskette; ein nicht gemapptes `asset_class` wirft fail-loud
  statt still auf `DEFAULT` zu fallen (Pitfall #287).
- **`backtest_runner._aggregate_exit_telemetry`** (`backtest_runner.py`, #899) — die EINE
  Exit-Reason-/ATR-bps-Aggregationsfunktion; `report._study_record` und `parsing.TournamentMetrics`
  konsumieren ausschliesslich ihre Ausgabe.
- **`sweep_diagnostics.check_bar_quality`** (`sweep_diagnostics.py`, #900) — die EINE
  Bar-Qualitäts-Preflight-Funktion; `frac_zero_true_range`/`atr_median_bps` ergänzen die
  bestehenden `frac_high_eq_low`/`frac_identical_consecutive_closes`-Kriterien additiv.
- **`invariants.compute_trial_timebox_violations`** (`invariants.py`, #902/#903) — `bar_seconds` ist
  PFLICHTPARAMETER (kein Default mehr); liefert Trial- UND Round-Trip-Ebene, `report.py`/
  `confirm.py` konsumieren beide über `_contracts.BAR_SECONDS_DEFAULT` als Single Source of Truth.
- **`confirm.py`s Deflations-Block** (`confirm.py`, #904/#905) — `deflation_n_family_effective ≤
  deflation_n_family_raw` ist eine harte Invariante (Pitfall #290); `deflation_n_family_raw` hat
  GENAU EINE Quelle (`deflation_n_family`, `per_strategy`-Scope, Pitfall #289).
- **`invariants.check_gate_collinearity_decision_required`/`check_fail_fast_invariants_wired`**
  (`invariants.py`, #907) — jeder Kollinearitäts-Alarm über `threshold` UND jeder in
  `fail_fast_invariants` registrierte Check-Name braucht einen nachweisbaren Konsequenz-Pfad.
- **`sweep.py`s Wallclock-Forecast** (`sweep.py`, #908) — `wallclock_forecast_after_symbols` kürzt
  die Symbolliste jetzt nachweislich (`_wallclock_truncation_limit`), statt nur zu warnen.
- **`sweep.earliest_ts_by_symbol`/`per_symbol_span_stats`** (`sweep.py`, #909) — die EINE
  Katalog-Spannen-Berechnung; PRO Symbol, nie als Aggregat über Endzeitpunkte (Pitfall #291).
- **`champions.load_champion_entry_with_reason`/`maybe_write_back`** (`champions.py`, #910) —
  `champion_corroboration_mode` entscheidet, ob Korroboration ODER Fenster-Fortschritt für einen
  Writeback genügt; `skipped_reason` unterscheidet `STORE_EMPTY` von echten
  Inadmissibilitäts-Gründen.
- **`sweep_diagnostics.recommend_diagnosis_action`** (`sweep_diagnostics.py`, #911) —
  `max_consecutive_structural_runs` deckelt `signal_absent`/`signal_sparse`;
  `signal_quality` eskaliert auf `quarantined_pending_simulation_review`, NIE mehr direkt auf
  `denylist` (Pitfall #292).
- **`optimizer.json['reward_semantics_version']`/`['simulation_semantics_version']`** (#912) — die
  vollständige, kumulative Auslöser-/Nicht-Auslöser-Begründung jedes Bumps steht im jeweiligen
  `_schema.fields`-Eintrag, nicht nur in diesem Dokument.

## Issue-Katalog #913–#936 — Inferenz-Blockade, Suchbudget, Simulations-Verifikation & Re-Run-Runbook (GitHub-Issues #774/#775/#776/#777, Sitzung 2026-08-06)

Vier Kataloge auf demselben Katalog-Lauf (`be341d57_20260806T113734093100`, Basis-Commit
`9ad6423e`, Vorgänger-Katalog #897–#912). Der Befund dieser Sitzung unterscheidet sich von jedem
Vorlauf: die 0-Promotions-Zahl war **weder echt noch falsch — sie war leer**. Es hatte **keine
Selektionsmathematik stattgefunden** (#913), während die #897/#898-Simulationsfixes nachweislich
gewirkt hatten (Median-Bruttoverlust 5,43→34,86 bps, Faktor 6,42, exakt im vorhergesagten Band) —
462 Trials bestanden jedes Gate ausser einem, und dieses eine scheiterte nicht an der Strategie,
sondern an einem fehlenden Keyword-Argument.

**Katalog A — Inferenz-Blockade (#913–#918, Pitfalls #293–#296):** #913 (der kritische Fund dieser
Sitzung — `sortino_numeric_guard_reference='family_median'` war seit #901 konfiguriert, aber KEINE
Call-Site übergab `family_median_n_periods`; jeder Aufruf lief in den ehrlichen, aber unbenutzbaren
dritten Zustand `'family_median_unavailable'`. Fix: `run_optimization.py` berechnet den Median von
`oos_n_periods` über die bereits abgeschlossenen Sibling-Trials desselben Symbols, reicht ihn über
das self-describing Manifest an den Backtest-Subprozess durch, `_calculate_stats` konsumiert ihn als
`family_median_n_periods=`; neuer Bootstrap-Modus `sortino_numeric_guard_reference_bootstrap`
für die ersten `sortino_guard_family_median_min_siblings` Trials einer Familie, für die noch kein
Median existiert; `assert_guard_reference_injectable()` bricht beim Sweep-Start fail-loud ab, falls
eine künftige Call-Site die Injektion wieder verliert — per AST über ALLE Aufrufer von
`_effective_sortino_numeric_guard` geprüft); #914 (`SORTINO_GUARD_REFERENCE_UNAVAILABLE` fehlte in
`_inference_failure_codes` — der `inference_failure_policy='prune'`-Pfad lief seit #901 leer, 1767
Trials trugen stattdessen einen regulären Failure-Branch-Reward und primten den TPE-Sampler mit
Rauschen); #915 (`check_guard_reference_coherence` prüfte nur, OB die konfigurierte Referenz
verwendet wurde, nicht OB sie eine benutzbare Schwelle lieferte, und PASSte bei 0,00 definiertem
`oos_psr` — neue Wirkungs-Invariante `check_selection_statistic_availability`, severity `blocking`,
hätte den vorliegenden Lauf nach ~26 Minuten statt nach hochgerechnet 170 Stunden gestoppt);
#916 (`sortino_numeric_guard_min_periods` blieb bei 1600, gegen den realen Familien-Median 305–331
um Faktor 5,25 zu gross — auf 320 rekalibriert, jetzt nur noch für den Bootstrap-Fall relevant);
#917 (`REJECT_OOS_STATISTIC_UNAVAILABLE` unterscheidet "nicht messbar" von "gemessen und
abgelehnt" jetzt über JEDEN Rejection-Grund, nicht nur eine feste Teilmenge — vorher liefen
`oos_min_psr`/`oos_min_excess_return`-Ablehnungen und die generische `None (insufficient`-Markierung
unter allen anderen Gründen weiterhin in `REJECT_OOS_OTHER`); #918 (zentrale
`InferenceDiagnosticCode`-Registry in `_contracts.py`, AST-Vertragstest verlangt, dass jeder in
`backtest_runner.py` gestempelte Diagnose-Code dort registriert ist — Verallgemeinerung von #914).

**Katalog B — Simulations-Verifikation (#919–#924, Pitfalls #297–#299):** #919 (die #899-Exit-
Telemetrie lag pro Trial vollständig vor, wurde aber nie zu einem Study-Aggregat zusammengefasst —
`report._sum_exit_reason_histograms`/`_time_box_exit_fraction` speisen endlich
`invariants.check_exit_reason_coverage` und `check_effective_stop_distance`); #920/Pitfall #297/
#298 (12 Krypto-Symbole trugen `asset_class='equity'` seit dem #898-Flächen-Backfill — Round-Trip-
Kosten um Faktor 4 zu niedrig; `size_precision=8` widerlegt `asset_class='equity'` bereits aus dem
Datensatz selbst, keine externe Quelle nötig; neue `check_instrument_metadata_coherence`); #921
(SqueezeBreakout: `bb_std_dev`/`keltner_multiplier` unabhängig gesampelt trafen die für `squeeze_on`
strukturell enge Verhältnis-Zone selten — nur 19/178 Trials auswertbar; `squeeze_ratio` wird jetzt
direkt gesampelt und `keltner_multiplier` bleibt der absolute Faktor, dasselbe fast+gap-Muster wie
ComboTrendVwaps `macd_slow`; die `binding_cause`-Korrektur für `median_oos_trades<=2` war bereits
über #926 abgedeckt); #922 (OpeningRangeBreakout verankerte den "Handelstag" ausschliesslich auf
`pd.Timestamp(bar.ts_init).day` — ein Kalendertag-Wechsel um Mitternacht UTC, ohne Bezug zur
tatsächlichen RTH-Session eines Equity-Instruments auf dem 24/7-Stundenraster; neuer
`opening_range_session_anchor∈{calendar_day,session_open_hour}`, Default bit-identisch,
`opening_range_session_open_hour` asset-class-aufgelöst); #923 (der #900-Bar-Qualitäts-Preflight
berechnete `bar_coverage_ratio` bereits, wertete ihn aber nie als Ablehnungskriterium — ein Symbol
mit grosser Datenspanne, aber überwiegend Lücken im Bar-Raster bestand Gate 1 unbemerkt; neuer
`min_bar_coverage_ratio`; `check_n_periods_homogeneity` gegen die beobachtete Faktor-11,3-Streuung
von `n_periods` innerhalb desselben Symbols, die die #865-Heterogenitäts-Suppression sonst für
praktisch jede Familie auslöst); #924/Pitfall #299 (der `atr_floor_bps`-Floor war entgegen der
Issue-Prämisse bereits über `hourly_strategy_base._effective_atr_value` an JEDER Stop-Preis-
Berechnung angewandt — die tatsächliche Lücke war ein flacher, in `HourlyStrategyConfig` hart
kodierter 2.0-bps-Default ohne Konfigurationsschlüssel und ohne Asset-Class-Auflösung, für Krypto
strukturell zu eng; `resolve_atr_floor_bps`, `backtest.json['atr_floor_bps_by_asset_class']`).

**Katalog C — Suchbudget & Diagnose-Attribution (#925–#930, Pitfalls #300–#303):** #925/Pitfall
#300 (der Plateau-Frühstopp konnte GESCHLOSSEN bewiesen frühestens bei 98,6 % des Trial-Budgets
feuern — `missed_probability` schreibt das gesparte Restbudget in den NENNER des Risikoterms, ein
Kriterium kann nicht feuern, solange noch etwas zu sparen wäre; neuer `plateau_stop_mode` Default
`'expected_yield'`: Abbruch, wenn `p_hi(m)·r < plateau_stop_min_expected_eligible`, alte
`missed_probability`-Logik bleibt als Opt-Out erhalten); #926/Pitfall #301 (`binding_cause=
'signal_quality'` bei 10 Studies, deren wahre Ursache die #913-Inferenzblockade war — ein zweiter
Lauf unter demselben Defekt hätte funktionierende Strategien dauerhaft in die Denylist geschrieben;
`diagnostic_writeback_enabled`-Notausschalter, dritter `binding_cause`-Wert `inference_unavailable`
ohne Denylist-/Bounds-Konsequenz); #927/#928/Pitfall #302 (`reward_terms_aggregates.terms` und
`gate_collinearity` liefen auf der ELIGIBLEN statt der EVALUIERTEN Kohorte und waren bei 0
eligiblen Trials in 14/14 Studies leer — Selection-on-the-dependent-variable: auf der Menge der
Überlebenden sind per Definition alle Kriterien erfüllt, die Varianz, die man messen will, ist dort
weggeschnitten; beide auf `oos_evaluated=True` umgestellt, Kollinearität zusätzlich pairwise-
complete statt listwise-complete, Jaccard-Mass der Pass-Mengen ergänzt); #929 (`best_value=null`
in 14/14 Studies, weil der Report-Layer den Study-Best aus der Optuna-CONSTRAINT-gefilterten
`study.best_value` statt aus der Menge der abgeschlossenen Trials zog — `_best_completed_value`,
getrenntes `best_eligible_reward`-Feld, neue `check_search_made_progress`); #930/Pitfall #303
(die `[#640]`-Eskalationsmeldung prüfte `stop_reason != BUDGET_EXHAUSTED` als Proxy für "Budget
übrig" — nach #925s Verschiebung des Plateau-Abbruchpunkts ist der Proxy falsch geworden, ohne dass
jemand ihn geändert hätte; auf die direkte Grösse `budget_executed_fraction <
min_median_budget_execution` umgestellt).

**Katalog D — Durchsatz & Re-Run-Runbook (#931–#936, Pitfalls #304–#306):** #931/Pitfall #304 (der
Disk-Preflight prüfte Plattenplatz — 786 GB frei gegen 8,3 GB erwartet, unauffällig — während die
tatsächlich knappe Ressource die Zeit war: 143 Symbole × 71,5 min/Symbol ≈ 170 h gegen ein 72-h-
Budget, Faktor 1,97–2,37; neues `WALLCLOCK_BUDGET_PREFLIGHT` VOR dem ersten Backtest, Policy
`wallclock_budget_policy∈{degrade,abort,warn}`, Default `degrade` kürzt das Trial-Budget global
proportional); #932/Pitfall #305 (`pipeline_depth` existierte, war dokumentiert, in der `_required_
keys.json`-Registry geführt — und hatte NULL ausführende Referenzen ausser zwei Docstrings, von
denen einer selbst sagt "bleibt bewusst NICHT implementiert"; dieselbe #913-Fehlerklasse, eine
Konfigurationsdatei weiter, im selben Lauf. Key entfernt; Longest-Processing-Time-Dispatch statt
einer Restrukturierung: die Studies eines Symbols werden absteigend nach dem Wallclock-
Erfahrungswert des letzten Reports dispatcht, `barrier_wait_s`/`SYMBOL_DISPATCH_COMPLETED` machen
die Barriere-Wartezeit je Symbol erstmals sichtbar); #933/Pitfall #306 (ein 5,9-MB-Log mit 4318
Zeilen — XOM vollständig abgeschlossen, 14/14 Studies — enthielt NULL `INVARIANT_*`-Events und
keinen `SWEEP_COMPLETED`/`SWEEP_ABORTED`-Abschluss; `report._build_report` lief ausschliesslich am
Sweep-Ende, bei 170 h Laufzeit ist das der erste Befund nach einer Woche; neu: `INVARIANT_RESULT`
je Symbol [symbol-lokal beschränkt auf dessen eigene Proposals, O(1) statt O(n)], `SWEEP_PROGRESS`
mit kumulativen Zählern, der Sweep-Report wird nach JEDEM Symbol atomar mit
`run_status='in_progress'` neu geschrieben, `sweep.main()` emittiert `SWEEP_COMPLETED`/
`SWEEP_ABORTED` als allerletztes strukturiertes Ereignis jedes Laufs); #934 (`logs/filter.sh` trug
den Log-Dateinamen hart kodiert und zeigte im Vorlauf nachweislich auf die falsche Datei — optionales
Argument, `ls -t`-Fallback auf die neueste `optimizer_*.log`, vom Eingabenamen abgeleiteter statt
fixer Ausgabename); #935 (reine Verifikation, kein Defekt — der aus #897–#912 dokumentierte
Pitfall-Rückstand #269–#284 ist unverändert offen, siehe dortiger Hinweis; #897/#901/#902/#912 sind
verifiziert gemergt und wirksam); #936 (dieser Eintrag — Doppel-Bump `reward_semantics_version`
20→21 [drei unabhängig hinreichende Auslöser: #913/#914/#917, alle ändern gestempelte
`oos_sortino_period`/`oos_psr`/`is_rejection_detail`-Werte bereits abgeschlossener Trials] und
`simulation_semantics_version` 2→3 [zwei unabhängig hinreichende Auslöser: #920/#924, beide ändern
simulierte Fill-Preise]; vollständige Auslöser-/Nicht-Auslöser-Begründung im jeweiligen
`_schema.fields`-Eintrag, Pitfall #299 verlangt genau diese Code-Gegenprobe statt einer
Absichtserklärung; AGENTS.md-Pitfalls #293–#306 nachgetragen).

### 🟢 Pitfall #293 — Wird ein stiller Fallback als Lüge erkannt und entfernt, muss im selben Commit ein funktionierender Pfad existieren [BEHOBEN: GH-#913]
**Symptom:** #901 identifizierte korrekt, dass `_effective_sortino_numeric_guard` unter
`sortino_numeric_guard_reference='family_median'` ohne verfügbaren Familienwert still auf
`source='absolute'` zurückfiel, und entfernte den Fallback zugunsten eines ehrlichen dritten
Zustands (`None, None, 'family_median_unavailable'`). Der Injektionspfad, der `family_median_
n_periods` tatsächlich befüllt, wurde nie gebaut — die Konfiguration verlangte `'family_median'`
weiter. Ergebnis: 100 % aller handelnden Trials verloren Sortino und PSR, 0 eligible Trials über
2187 Trials, obwohl 462 davon jedes andere Gate bestanden.
**Root-Cause:** Der ehrliche dritte Zustand ist ehrlicher als die Lüge und gleichzeitig
destruktiver. Die Lüge lieferte einen falsch begründeten, aber brauchbaren Guard; der ehrliche
Zustand liefert gar keinen. Eine Korrektur, die eine Falschaussage durch eine korrekte
Nicht-Aussage ersetzt, hat die Funktionsfähigkeit nicht wiederhergestellt, nur den Fehlermodus
verändert.
**Fix/Regel:** Entfernen eines stillen Fallbacks und Bauen des Pfads, der ihn überflüssig macht,
sind EIN Vorgang, nicht zwei aufeinanderfolgende PRs. Wird ein Fallback als Lüge entlarvt, ist die
Frage "wodurch wird der jetzt fehlende Wert tatsächlich geliefert?" Teil desselben Commits — nicht
eine für später vertagte Restarbeit, die eine grüne Registry unsichtbar macht.

### 🟢 Pitfall #294 — Ein Fix, der einen neuen Zustand einführt, ist erst vollständig, wenn jede Menge, die Zustände dieser Art aufzählt, nachgezogen ist [BEHOBEN: GH-#914/#918]
**Symptom:** `SORTINO_GUARD_REFERENCE_UNAVAILABLE` — der von #901 neu eingeführte Diagnose-Code —
fehlte in `run_optimization.py::_inference_failure_codes`. `inference_failure_policy='prune'` war
gesetzt, der Prune-Pfad konnte für diesen Code aber nicht auslösen: 1767 Trials erhielten einen
regulären Failure-Branch-Reward statt geprunt zu werden, obwohl ihre Basis-Komponente uniform
degeneriert war — der TPE-Sampler lernte 1767 Trials lang gegen Straf-Terme, nicht gegen ein reales
Signal.
**Root-Cause:** Mindestens fünf Stellen führten unabhängig gepflegte Listen von Diagnose-Codes
(`_inference_failure_codes`, `parsing.py`, `report.py`, `invariants.check_inference_diagnostics_
absent`, Test-Fixtures). Ein neuer Code war erst vollständig, wenn alle fünf nachgezogen waren; dass
eine davon vergessen wurde, war der Normalfall.
**Fix/Regel:** Jeder Zustand, den ein Fix neu einführt (Enum-Wert, Diagnose-Code, Rückgabevariante),
gehört in EINE Registry mit AST-Vertragstest (`_contracts.InferenceDiagnosticCode`/
`INFERENCE_DIAGNOSTIC_CODES`) — nicht in fünf über die Codebasis verstreute Literale, deren
Synchronität niemand prüft.

### 🟢 Pitfall #295 — Eine Invariante über die Quelle eines Werts ersetzt nicht die Invariante über seine Wirkung [BEHOBEN: GH-#915]
**Symptom:** `check_guard_reference_coherence` — die Invariante, die #901 ausdrücklich gegen die
tautologische Immer-Pass-Variante gehärtet hatte — meldete in diesem Lauf PASS, bei 0 eligiblen
Trials und 0 definierten Sortinos.
**Root-Cause:** Die Invariante prüfte "wird die konfigurierte Referenz auch verwendet?" (eine
Quellen-Frage) statt "liefert der Guard eine benutzbare Schwelle?" (eine Wirkungs-Frage). Der
ehrliche dritte Zustand `'family_median_unavailable'` (Pitfall #293) lässt den Quellen-Check PASSen,
weil er nicht über die verwendete Referenz lügt — er beantwortet aber nicht, ob überhaupt etwas
Verwendbares dabei herauskam.
**Fix/Regel:** Für jede Entscheidungsgrösse mit einer konfigurierbaren Quelle braucht es MINDESTENS
zwei Invarianten: eine über die Quelle (wurde die konfigurierte Referenz verwendet?) und eine über
die Wirkung (liefert sie einen benutzbaren, definierten Wert in ausreichendem Anteil der Fälle?).
`check_selection_statistic_availability` ist die Wirkungs-Invariante zu `check_guard_reference_
coherence` und hätte den vorliegenden Lauf nach ~26 Minuten statt nach 170 Stunden gestoppt.

### 🟢 Pitfall #296 — Eine Konfiguration, deren Aktivierung einen Codepfad verlangt, der nicht existiert, muss beim Start fail-loud abbrechen [BEHOBEN: GH-#913]
**Symptom:** `tournament.json['sortino_numeric_guard_reference'] = 'family_median'` war gesetzt,
ohne dass irgendeine Call-Site den dafür nötigen `family_median_n_periods`-Wert lieferte. Der Sweep
lief 1,33 Stunden lang informationsfrei, bevor der Defekt manuell entdeckt wurde — bei 143 Symbolen
und einer Hochrechnung von 170 Stunden wäre der Defekt erst nach einer Woche aufgefallen.
**Root-Cause:** Es gab keine Startup-Prüfung, die verifiziert, dass eine aktivierte Konfigurations-
Option tatsächlich verdrahtet ist. Die Diskrepanz zwischen "Modus konfiguriert" und "Modus
implementiert" wurde erst im Trial-Loop sichtbar, Trial für Trial, nie beim Start.
**Fix/Regel:** `assert_guard_reference_injectable()` prüft per AST über ALLE Aufrufer von
`_effective_sortino_numeric_guard`, dass `family_median_n_periods` als Keyword geführt wird, und
bricht VOR dem ersten Backtest fail-loud ab, wenn nicht. Jede Config-Option, deren Aktivierung einen
bestimmten Codepfad voraussetzt, braucht eine Startup-Probe dieser Art — die Frage "ist der Modus
verdrahtet?" gehört vor den ersten Trial, nicht in ihn hinein.

### 🟢 Pitfall #297 — Eine fail-loud-Policy für fehlende Metadaten plus ein flächendeckender Backfill ergibt einen Wächter, der nie feuert [BEHOBEN: GH-#920]
**Symptom:** #898 führte `unknown_asset_class_policy='reject'` ein und beseitigte damit
nachweislich alle `asset_class='Unknown'`-Einträge (69 von 146 Instrumenten). Der Preis: 12
Krypto-Symbole wurden bei der Bereinigung auf `asset_class='equity'` zurückgesetzt statt korrekt
aufgelöst — Round-Trip-Kosten für diese Symbole um Faktor 4 zu niedrig (4,0 statt 16,0 bps), und die
`'reject'`-Policy kann für diese 12 Symbole nie mehr feuern, weil kein Instrument mehr `'Unknown'`
ist.
**Root-Cause:** Ein Backfill, der eine erkennbare Lücke (fehlende asset_class) durch einen positiv
behaupteten falschen Wert ersetzt, verschlechtert die Fehlerklasse: vorher gab es einen konservativ
falschen, aber SICHTBAR falschen Fallback (`DEFAULT`); jetzt gibt es einen falschen Wert, den keine
Prüfung mehr hinterfragt.
**Fix/Regel:** Ein Backfill muss seine Provenienz mitschreiben (`asset_class_source∈{explicit,
derived_from_precision, backfill_default}`); ein `backfill_default`-Eintrag darf einen Sweep nicht
ohne Warnung passieren. "Kein Unknown mehr" ist kein Nachweis von Korrektheit — eine fail-loud-Policy
für einen Zustand, der durch denselben Fix flächendeckend beseitigt wird, ist keine Absicherung mehr,
sondern eine Beruhigung.

### 🟢 Pitfall #298 — Metadaten sind gegen sich selbst prüfbar, ohne externe Quelle [BEHOBEN: GH-#920]
**Symptom:** Alle 12 falsch als `'equity'` klassifizierten Symbole (und nur sie) trugen
`size_precision=8` bei `price_precision=2` — für eToro-Bruchteilshandel bei Aktien technisch
unmöglich (acht Nachkommastellen Stückzahl gibt es bei keiner Aktie im Universum).
**Root-Cause:** Die Klassifikation `asset_class='equity'` wurde nie gegen die BEGLEITENDEN
Metadaten desselben Instrumenteneintrags geprüft, obwohl der Widerspruch aus dem Datensatz selbst
ableitbar war — keine externe Quelle nötig.
**Fix/Regel:** Für jede Klassifikation, die eine Entscheidung trägt (Kostenmodell, Simulation,
Routing), ist mindestens eine aus dem Datensatz selbst ableitbare Kohärenzregel zu formulieren
(`check_instrument_metadata_coherence`: `size_precision>=6 ⇒ asset_class MUSS crypto sein`). Eine
solche Regel ist billiger und zuverlässiger als eine von Hand gepflegte externe Liste und fängt
Klassen von Fehlern, die eine reine Existenzprüfung nie sieht.

### 🟢 Pitfall #299 — Ein `_schema`-Kommentar, der Verhalten dokumentiert, das im Code nicht existiert, ist schlimmer als keine Dokumentation [BEHOBEN: GH-#924]
**Symptom:** `optimizer.json['simulation_semantics_version']`s v2-Beschreibung nannte
`max(ATR, atr_floor_bps)` als umgesetztes Verhalten des #897-Trailing-Stops. Die #924-Analyse
zeigte: das war zum Analysezeitpunkt korrekt, ABER die tatsächliche Lücke — kein Konfigurations-
schlüssel für `atr_floor_bps`, kein Asset-Class-Bezug — blieb unentdeckt, weil die Versions-
Beschreibung suggerierte, das Thema sei vollständig erledigt.
**Root-Cause:** Eine Versions-Beschreibung, die Verhalten NENNT, ohne gegen den tatsächlichen Code
verifiziert zu werden, beendet die Suche vorzeitig — ein Leser (Mensch oder Agent) vertraut der
Beschreibung statt nachzusehen, ob sie noch vollständig zutrifft.
**Fix/Regel:** Versions-Beschreibungen gehören gegen den Code getestet, nicht gegen die Absicht
geschrieben — `test_issue_936_version_bumps.py::test_simulation_schema_v3_describes_only_
implemented_behaviour` verifiziert exemplarisch, dass ein im `_schema`-Text genannter Config-Key
(`atr_floor_bps_by_asset_class`) tatsächlich in der ausgelieferten Config existiert. Jede künftige
Versions-Bump-Beschreibung sollte mindestens einen solchen Code-Gegenprobe-Test tragen.

### 🟢 Pitfall #300 — Ein Abbruchkriterium, dessen Risikoterm das noch verfügbare Restbudget enthält, kann per Konstruktion erst feuern, wenn das Restbudget klein ist [BEHOBEN: GH-#925]
**Symptom:** Der Plateau-Frühstopp (`missed_probability`) sparte über 14 Studies im Median 1,1 %
des Trial-Budgets ein, obwohl die Rule-of-Three-Evidenz für mindestens 40 % der Trials Entbehrlich-
keit signalisierte. Geschlossen hergeleitet: der Stopp kann PER KONSTRUKTION höchstens 1,43 % des
Budgets sparen, unabhängig von jeder Empirie.
**Root-Cause:** `missed_probability` ist monoton fallend in der Evidenz UND monoton steigend im
Restbudget — der NUTZEN des Abbruchs (das gesparte Restbudget) steht im NENNER des gemessenen
Risikos. Früh in einer Study ist das Restbudget gross, also ist die Miss-Wahrscheinlichkeit gross
und der Stopp feuert nicht; er kann erst feuern, wenn nichts mehr zu sparen ist.
**Fix/Regel:** Für jedes Frühstopp-Kriterium ist die erreichbare Ersparnis GESCHLOSSEN herzuleiten
und als Regressionstest zu fixieren, bevor man sich auf die Empirie eines einzelnen Laufs verlässt —
sie zeigt den Defekt nur, wenn man ihn schon vermutet. Ein Kriterium, dessen Nutzen im Nenner seines
eigenen Risikoterms steht, ist gegen sich selbst blockiert; die richtige Form ist ein
Erwartungswert-Vergleich (`p_hi(m)·r` gegen eine Mindest-Ertragsschwelle), nicht eine
Risiko-Obergrenze allein.

### 🟢 Pitfall #301 — Eine Diagnose, die eine nicht durchgeführte Messung als negatives Messergebnis ausgibt, wird gefährlich, sobald ein Closed-Loop sie konsumiert [BEHOBEN: GH-#926]
**Symptom:** 10 `ZERO_ELIGIBLE_PLATEAU`-Events trugen `binding_cause='signal_quality'` — obwohl die
betroffenen Studies 62–171 OOS-Trades im Median erzeugten (unauffällige Signalfrequenz) und fünf
davon in der 462-Kandidatenliste mit Profit-Faktoren bis 4,46 standen. `p_eligible=0` war
ausschliesslich Folge der #913-Inferenzblockade, keine Aussage über die Strategiequalität.
**Root-Cause:** Die `binding_cause`-Ableitung nahm stillschweigend an, dass die
Eligibility-Prüfung STATTGEFUNDEN hat. Es gab keinen dritten Zweig für "die Prüfung war nicht
durchführbar" — eine nicht durchgeführte Messung wurde als negatives Messergebnis interpretiert,
exakt eine Ebene über Pitfall #277 (Missing-Data-vs-negatives-Ergebnis).
**Fix/Regel:** Jede automatische Diagnose braucht einen dritten Ausgang (`unavailable`/
`inference_unavailable`) mit der Konsequenz KEINE — weder Denylist noch Bounds-Override. Jeder
PERSISTIERENDE Rückschrieb eines Closed-Loops ist zusätzlich an eine Verfügbarkeits-Invariante zu
koppeln (`diagnostic_writeback_enabled`, gekoppelt an `check_selection_statistic_availability`),
nicht nur an eine manuell umschaltbare Config-Option — sonst entscheidet die Disziplin eines
Operators statt einer verifizierten Bedingung.

### 🟢 Pitfall #302 — Telemetrie über die Eigenschaften einer Auswahlregel darf nicht auf der ausgewählten Kohorte rechnen [BEHOBEN: GH-#927/#928]
**Symptom:** `reward_terms_aggregates.terms` und `gate_collinearity` waren in 14 von 14 Studies leer
bzw. `null`, obwohl jeder der 2187 Trials eine vollständige `reward_terms`-Zerlegung trug — die
Aggregation lief auf der ELIGIBLEN Kohorte, die bei `p_eligible=0` leer war.
**Root-Cause:** Auf der Menge der Überlebenden (eligible) sind per Definition alle Gates erfüllt —
die Varianz, die eine Kollinearitäts- oder Termvarianz-Analyse messen will, ist genau dort
weggeschnitten (Selection-on-the-dependent-variable). Das ist zusätzlich inkonsistent zum
Reward-Design seit #629, das ausdrücklich auf der EVALUIERTEN Kohorte definiert ist.
**Fix/Regel:** Telemetrie über die Eigenschaften einer Auswahlregel (Kollinearität, Redundanz,
Termvarianz, jede Analyse "wie unterscheiden sich Kandidaten voneinander") rechnet auf der Menge,
über die entschieden wurde (`oos_evaluated=True`), nicht auf der Menge, die die Entscheidung bereits
bestanden hat. Eine engere Kohorten-Wahl mag für andere Zwecke plausibel sein — sie macht die
Diagnose aber genau dann blind, wenn man sie am dringendsten braucht.

### 🟢 Pitfall #303 — Eine Alarmmeldung, deren Auslösebedingung ein Proxy für eine Grösse ist, die inzwischen direkt vorliegt, wird nach dem nächsten Fix falsch, ohne dass jemand sie ändert [BEHOBEN: GH-#930]
**Symptom:** Die `[#640]`-Eskalationsmeldung feuerte 10× mit einem in sich widersprüchlichen Text —
"kein Basisbudget ausgeschoepft" bei einer gemessenen `budget_executed_fraction` von 0,9857–0,99
(praktisch vollständig ausgeschöpft).
**Root-Cause:** `[#640]` prüfte `stop_reason != BUDGET_EXHAUSTED` als Proxy für "Budget übrig" — ein
zum Entstehungszeitpunkt gültiger Proxy (der damalige Frühstopp bei `n_startup+3·dim` liess
tatsächlich Budget übrig). Nach #925 verschob sich der Plateau-Abbruchpunkt strukturell, der Proxy
wurde falsch, ohne dass der Code, der ihn nutzt, geändert wurde.
**Fix/Regel:** Ein Proxy, der zum Zeitpunkt seiner Einführung eine echte Grösse approximierte, ist
bei jeder späteren Änderung der approximierten Grösse neu zu bewerten — sobald die direkte Grösse
(hier `budget_executed_fraction`) ohnehin vorliegt, ersetzt sie den Proxy. Eine Alarmbedingung sollte
gegen die direkte Grösse geprüft werden, sobald diese existiert, nicht gegen einen historisch
gewachsenen Stellvertreter.

### 🟢 Pitfall #304 — Ein Preflight, der eine Ressource prüft und die eigentlich knappe ignoriert, ist eine Beruhigung, keine Absicherung [BEHOBEN: GH-#931]
**Symptom:** Der Disk-Preflight (`DISK_BUDGET_PREFLIGHT`) lief eine Sekunde nach Laufbeginn,
prüfte Plattenplatz (786 GB frei gegen 8,3 GB erwartet, komfortabel) und liess den Lauf passieren.
Dieselbe Sekunde kannte bereits `expected_trials=277420` — genug, um mit einem Erfahrungswert für
`backtest_ms` eine Zeitprognose zu stellen: 143 Symbole × 71,5 min ≈ 170 h gegen ein 72-h-Budget.
**Root-Cause:** Ein Preflight, der VOR der ersten Arbeitseinheit läuft und alle nötigen
Eingangsgrössen für eine Prognose bereits hält, aber nur eine UNKRITISCHE Ressource prüft, erzeugt
falsche Sicherheit — er sieht aus wie eine Absicherung, ist aber keine, solange die tatsächlich
knappe Ressource (hier: Zeit) aussen vor bleibt.
**Fix/Regel:** Für jede Ressource mit einem harten Budget gehört die Prognose VOR die erste
Arbeitseinheit, mit einer POLICY statt einer Warnung (`WALLCLOCK_BUDGET_PREFLIGHT`,
`wallclock_budget_policy∈{degrade,abort,warn}`). Ein Preflight, der misst, aber keine Konsequenz
trägt, ist der Vorläufer dieses Pitfalls, kein Schutz dagegen.

### 🟢 Pitfall #305 — Eine Barriere am Ende einer Verarbeitungsstufe kostet die Differenz zwischen längster und medianer Teilaufgabe [BEHOBEN: GH-#932]
**Symptom:** Die Symbolende-Barriere (alle 14 Studies eines Symbols warten aufeinander, bevor
Confirm/Export starten) kostete für XOM 1614 s = 26,9 min — 37,6 % des gesamten Symbol-Takts —, die
Differenz zwischen der längsten Study (ComboTrendVwap, 2858 s) und der Median-Study (1244 s).
**Root-Cause:** `pipeline_depth` sollte diese Barriere durch Look-Ahead über Symbolgrenzen hinweg
auflösen, war aber dieselbe #913-Fehlerklasse eine Konfigurationsdatei weiter: existent,
dokumentiert, Registry-grün, NULL ausführende Referenzen. Eine echte Pipelining-Restrukturierung
war als grössere Massnahme zurückgestellt worden, ohne dass eine billigere Zwischenstufe geprüft
wurde.
**Fix/Regel:** Liegen die Laufzeiten der Teilaufgaben aus einem Vorlauf vor, ist absteigender
Dispatch (Longest-Processing-Time) eine Sortierzeile mit zweistelligem Prozentgewinn — und immer
die ERSTE Massnahme, bevor eine strukturelle Restrukturierung (Pipelining) erwogen wird. Ein
dokumentierter, aber unverdrahteter Config-Schlüssel ist keine Rechtfertigung, die billige
Zwischenstufe zu überspringen.

### 🟢 Pitfall #306 — Invarianten, die erst am Ende eines mehrtägigen Laufs auswerten, sind keine Wächter, sondern Obduktionen [BEHOBEN: GH-#933]
**Symptom:** Ein 5,9-MB-Log mit 4318 Zeilen — XOM vollständig abgeschlossen, 14/14 Studies, 14/14
Confirms, 14/14 Champion-Writebacks — enthielt NULL `INVARIANT_*`-Events, obwohl
`check_guard_reference_coherence` (die #913 hätte melden müssen) und mehrere andere Invarianten für
das abgeschlossene Symbol bereits berechenbar gewesen wären.
**Root-Cause:** `report._build_report` lief ausschliesslich am SWEEP-Ende. Bei 143 Symbolen und
(nach #931) 142–170 h Laufzeit bedeutet das: die erste Invarianten-Auswertung fände nach einer Woche
statt. Der bestehende Fail-Fast-Pfad prüfte nur eine Teilmenge der Invarianten und emittierte seine
Ergebnisse nicht als strukturiertes Event.
**Fix/Regel:** Jede symbol- oder studienlokal auswertbare Invariante gehört NACH der jeweiligen
Einheit emittiert, als strukturiertes Event (`INVARIANT_RESULT`), mit `blocking` als Spezialfall des
bestehenden Fail-Fast-Mechanismus statt als getrenntem System. Ein Lauf, der 170 h braucht, darf
seinen ersten Befund nicht erst nach 170 h liefern — dasselbe gilt für den Laufstatus selbst
(`SWEEP_PROGRESS` je Einheit, `SWEEP_COMPLETED`/`SWEEP_ABORTED` als garantierte letzte Zeile).

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #913–#936)
- `tournament.json.sortino_numeric_guard_reference_bootstrap` (Default `'absolute'`,
  ∈ {`absolute`, `defer`}) / `sortino_guard_family_median_min_siblings` (Default 32) /
  `sortino_guard_family_scope` (Default `'symbol_strategy'`) — #913/#916, Pitfall #293/#295/#296.
- `tournament.json.sortino_numeric_guard_min_periods` 1600 → 320 — #916.
- `optimizer.json.selection_statistic_min_available_fraction` (Default 0.80) — #915, Pitfall #295.
- `optimizer.json.fail_fast_invariants` (`check_selection_statistic_availability` ergänzt) — #915.
- `optimizer.json.diagnostic_writeback_enabled` (Default `true`, Notausschalter) — #926, Pitfall
  #301.
- `optimizer.json.wallclock_budget_policy` (Default `'degrade'`, ∈ {`degrade`, `abort`, `warn`}) —
  #931, Pitfall #304.
- `optimizer.json.plateau_stop_mode` (Default `'expected_yield'`, ∈ {`expected_yield`,
  `missed_probability`}) / `plateau_stop_min_expected_eligible` (Default 0.5) — #925, Pitfall #300.
- `optimizer.json.bar_quality.min_bar_coverage_ratio` (Default 0.6) — #923.
- `optimizer.json.pipeline_depth` — ENTFERNT (statt implementiert, siehe Fix-Entscheidung) — #932,
  Pitfall #305.
- `instrument_map.json` — 12 Krypto-Symbole `asset_class` `'equity'` → `'crypto'` korrigiert — #920,
  Pitfall #297/#298.
- `backtest.json.atr_floor_bps_by_asset_class` (CRYPTO 10.0, EQUITY/DEFAULT 2.0, FOREX 1.0,
  COMMODITY 3.0) — #924, Pitfall #299.
- `backtest.json.opening_range_session_open_hour_by_asset_class` (EQUITY/COMMODITY 13 UTC,
  FOREX/CRYPTO/DEFAULT 0) — #922.
- `OpeningRangeBreakoutConfig.opening_range_session_anchor` (Default `'calendar_day'`) /
  `opening_range_session_open_hour` (Default 13) — #922.
- `search_space_overrides.json` — SqueezeBreakoutStrategy[`squeeze_ratio`, `min_squeeze_bars`,
  `cooldown_bars`, `max_bars_in_trade`] und OpeningRangeBreakoutStrategy[`or_bars`,
  `cooldown_bars`, `max_bars_in_trade`] neu verdrahtet — #921/#922.
- `optimizer.json.reward_semantics_version` 20 → 21 (drei Auslöser: #913, #914, #917) — #936.
- `optimizer.json.simulation_semantics_version` 2 → 3 (zwei Auslöser: #920, #924) — #936.

### 🔒 Watertight Invariants (Issue-Katalog #913–#936) — für künftige Agenten
- **`run_optimization.assert_guard_reference_injectable`** (`run_optimization.py`, #913) — der EINE
  Startup-Preflight, der per AST verifiziert, dass JEDER Aufrufer von `_effective_sortino_numeric_
  guard` `family_median_n_periods` als Keyword führt (Pitfall #293/#296).
- **`_contracts.INFERENCE_DIAGNOSTIC_CODES`** (`_contracts.py`, #914/#918) — die EINE Registry für
  Inferenz-Diagnose-Codes; ein AST-Vertragstest verlangt, dass jeder in `backtest_runner.py`
  gestempelte Code dort registriert ist (Pitfall #294).
- **`invariants.check_selection_statistic_availability`** (`invariants.py`, #915) — die
  WIRKUNGS-Invariante zu `check_guard_reference_coherence`; FAILt, wenn der Anteil `oos_evaluated`-
  Trials mit definiertem `oos_psr` unter `selection_statistic_min_available_fraction` fällt
  (Pitfall #295).
- **`sweep_diagnostics.resolve_ineligible_binding_cause`** (`sweep_diagnostics.py`, #917/#921/#926)
  — die EINE `binding_cause`-Ableitungsfunktion; Priorität `inference_unavailable` >
  `signal_sparse` > `signal_quality` (Pitfall #301).
- **`sweep.write_symbol_bar_quality_cache`/`read_symbol_bar_quality_cache`** (`sweep.py`, #923) —
  der EINE Kanal, über den `report._study_record` die #900-Bar-Qualitäts-Kennzahlen (u. a.
  `median_delta_t_s` für `bar_seconds`) symbol-scoped ohne eigenen Katalog-Zugriff erreicht.
- **`backtest_runner.resolve_atr_floor_bps`/`resolve_opening_range_session_open_hour`**
  (`backtest_runner.py`, #924/#922) — Single Source of Truth für die asset-class-aufgelösten
  Werte, analog `resolve_spread_bps` (#566/#898); jeder Aufrufer von `run_single_backtest_worker`
  muss beide Tabellen durchreichen (AST-Vertragstest in `test_issue_924_*`/`test_issue_922_*`).
- **`run_optimization.plateau_stop_expected_yield`** (`run_optimization.py`, #925) — der Default-
  Kriterium-Pfad des Plateau-Frühstopps; `plateau_stop_mode` entscheidet, welche der beiden
  geschlossenen Formeln greift (Pitfall #300).
- **`invariants.check_search_made_progress`** (`invariants.py`, #929) — FAILt, wenn
  `constraint_improvement_rate<=0` UND `p_eligible==0` UND ausreichend modelliert wurde; die
  einzige Study-Ebene-Aussage über TPE-Fortschritt, die nicht an Eligibility hängt.
- **`sweep._read_last_study_wallclock_by_strategy`/LPT-Sortierung** (`sweep.py`, #932) — die EINE
  Quelle für den Wallclock-Erfahrungswert je Strategie; die Symbol-Dispatch-Reihenfolge sortiert
  absteigend danach, `barrier_wait_s` macht die Wirkung messbar (Pitfall #305).
- **`wallclock_guard.write_degrade_factor`/`read_degrade_factor`** (`wallclock_guard.py`, #931) —
  der EINE Kanal für den globalen Trial-Budget-Degradationsfaktor über unabhängig geladene
  Study-Configs hinweg (Pitfall #304).
- **`report.build_probe_report`** (`report.py`, #915/#933) — die EINE Funktion, die sowohl der
  Fail-Fast-Preflight als auch die neue Per-Symbol-`INVARIANT_RESULT`-Emission konsumieren; der
  Fail-Fast-Pfad ist ein `blocking`-Spezialfall auf demselben `invariant_checks`, kein getrenntes
  System (Pitfall #306).
- **`sweep.main`s `SWEEP_COMPLETED`/`SWEEP_ABORTED`-Emission** (`sweep.py`, #933) — die
  garantierte letzte strukturierte Zeile jedes Laufs, VOR dem Weiterreichen einer eingefangenen
  Exception (Pitfall #306).
- **`optimizer.json['reward_semantics_version']`/`['simulation_semantics_version']`** (#936) — die
  vollständige, kumulative Auslöser-/Nicht-Auslöser-Begründung jedes Bumps steht im jeweiligen
  `_schema.fields`-Eintrag, gegen den Code getestet (`test_issue_936_version_bumps.py`), nicht nur
  in diesem Dokument (Pitfall #299).

## Katalog #937–#964 (GitHub-Issues #779–#783, Sitzung 2026-08-07)

**Lauf:** `07d5aef0_20260806T195848096997`. Fünf Meta-Issues (Katalog A–E) mit 28 Einzelbefunden
(#937–#964) aus demselben Referenzlauf wie Katalog #913–#936. Kernlinie: ein Telemetrie-Sink, der
selbst zur Abbruchursache wird (Katalog A); Inferenz-Wächter, deren harte Verwerfungsschwelle bei
kleinen Stichproben eine Anti-Selektion gegen genau die Strategien erzeugt, die sie schützen sollen
(Katalog B); eine Reward-Varianz, die vom Failure-Zweig statt von der Rangordnung innerhalb der
zulässigen Region getragen wird (Katalog C); eine Simulation, die über den wirtschaftlichen Ruin
hinaus weiterrechnet und einen Kostenmodell-Floor, der die physikalische Tick-Granularität ignoriert
(Katalog D); und eine Promotion-Entscheidung, die einen nie OOS-evaluierten Trial zum
Studienbesten machen konnte (Katalog E). Sechs Befunde (#940/#941/#945/#946/#955/#959) waren beim
Start dieser Sitzung bereits durch vorangegangene Sitzungen behoben. Vier Befunde wurden bewusst
zurückgestellt, in derselben Kategorie wie #843/#845/#954/#962 (grössere Restrukturierung ohne
Möglichkeit einer empirischen Validierung durch einen echten Mehrstunden-Lauf in dieser Umgebung):
#951 (skalenfreie Straf-Terme — reward.py-weite Neukalibrierung), #952 (Signalfrequenz-Preflight —
tief in die Backtest-Ausführung verdrahtet, analog zur Komplexität von #956), #954 (Warm-Start-
Kaskade — neue `champion_store.py`-Funktionalität, von #781 selbst bereits als P2 zurückgestellt
angelegt), #963 (gestuftes Abnahmeprotokoll — ein komplett neues Modul `acceptance.py`, das vor
dieser Sitzung nicht existierte). #962 (globale Arbeitswarteschlange) ist die dritte Zurückstellung
desselben Befunds nach zwei vorangegangenen Sitzungen.

### 🟢 Pitfall #307 — Ein Telemetrie-Sink, der selbst wirft, macht aus einem lokalen Diagnosewert einen globalen Abbruchgrund [BEHOBEN: GH-#937/#938]
**Symptom:** `emit_execution_event`/`_append_jsonl_sidecar` riefen `json.dumps(event, ...)` direkt
auf. Sobald ein Event-Payload einen Nicht-String-Dict-Key enthielt (u. a. `(strategy, symbol)`-
Tupel als Schlüssel in Diagnose-Dicts von `sweep.py`), warf `json.dumps` eine `TypeError` — nicht
im aufrufenden Code, sondern in der Logging-Funktion selbst, die von praktisch jedem Codepfad
aufgerufen wird.
**Root-Cause:** Ein Sink, dessen Aufgabe reine Beobachtung ist, hatte keinen eigenen Fehlerpfad und
erbte damit die Abbruchsemantik des beobachteten Codes — ein Logging-Bug wurde zum Sweep-Abbruch.
Dieselbe Tupel-Key-Annahme steckte zusätzlich in den produktiven Dict-Strukturen selbst
(`_offending_pairs_for_fail_fast_check` u. a.), nicht nur im Telemetrie-Pfad.
**Fix/Regel:** Ein Telemetrie-Sink darf NIE werfen — `_sanitize_for_json`/`_canonical_key`
normalisieren jeden Key auf einen String, ein äusseres try/except mit `TELEMETRY_SERIALIZATION_
FAILED`-Fallback-Event fängt jeden verbleibenden Fehler ab, `logger.log()` selbst ist ebenfalls
try/except-umschlossen. Zusätzlich: `(strategy, symbol)`-Tupel als Dict-Key sind über den ganzen
Optimizer hinweg verboten — `_contracts.pair_key`/`split_pair_key` sind der EINE Kanal für
serialisierbare Paar-Schlüssel (analog zum String-Key-Zwang aus Pitfall #234).

### 🟢 Pitfall #308 — Eine Symbolschleife ohne Fehlerisolation macht aus 1 von 143 Symbolausfällen einen Totalabbruch nach Tagen Laufzeit [BEHOBEN: GH-#939]
**Symptom:** `run_per_symbol_sweep` hatte keine try/except-Grenze um die Family-Aggregation/
Confirm/Export-Schritte eines einzelnen Symbols. Eine unbehandelte Exception in Symbol 87 von 143
beendete den gesamten mehrtägigen Lauf, inklusive der bereits erfolgreich abgeschlossenen 86
Symbole, deren Ergebnisse dadurch nie exportiert wurden.
**Root-Cause:** Bei einer angenommenen Ausfallwahrscheinlichkeit von 1 % pro Symbol liegt die
Erfolgswahrscheinlichkeit eines ungeschützten 143-Symbol-Laufs bei `0.99^143 ≈ 23.6 %` — eine
einzelne Randbedingung (fehlende Daten, ein Parsing-Fehler) genügt, um Tage an bereits geleisteter
Rechenarbeit zu verwerfen.
**Fix/Regel:** Jede Einheit einer mehrtägigen Batch-Schleife (hier: Symbol) wird einzeln
try/except-isoliert; ein Ausfall wird als `SYMBOL_FAILED` telemetriert und in `sweep_failed_
symbols` gesammelt, der Lauf läuft mit den verbleibenden Symbolen weiter. Ein globaler Abbruch
bleibt als bewusste Politik-Entscheidung möglich, aber nur wenn BEIDE Schwellen
(`max_failed_symbols_abs` UND `max_failed_symbols_frac`) gleichzeitig überschritten sind — eine
einzelne UND-verknüpfte Schwelle allein hätte bei kleinen Läufen (wenige Symbole) bereits beim
ersten Ausfall abgebrochen.

### 🟢 Pitfall #309 — Eine Gate-1-Ablehnung, die pro (Strategie, Symbol) statt pro Symbol emittiert wird, vervielfacht dieselbe Information bis zu 14-fach [BEHOBEN: GH-#942]
**Symptom:** `enumerate_tunable_pairs` rief `emit_gate1_rejection()` für `INSUFFICIENT_HISTORY`
einmal je (Strategie, Symbol)-Kombination auf — bei 14 Strategien je Symbol bis zu 14 identische
Events für denselben, rein symbolabhängigen Befund (die verfügbare Historie hängt nicht von der
Strategie ab).
**Root-Cause:** Die Ablehnungsursache ist eine Eigenschaft des Symbols (Datenverfügbarkeit), wurde
aber an einer Stelle emittiert, die über beide Achsen (Strategie × Symbol) iteriert — dieselbe
Verwechslung von Iterationsebene und Informationsebene wie in Pitfall #302, nur auf der
Emissions- statt der Berechnungsseite.
**Fix/Regel:** Ein `_gate1_history_rejection_emitted`-Cache-Set (schlüssel: Symbol) sorgt dafür,
dass `INSUFFICIENT_HISTORY` genau einmal je Symbol emittiert wird. Allgemeiner: bevor ein Event in
einer verschachtelten Schleife emittiert wird, ist zu prüfen, auf welcher der beiden Achsen der
Befund tatsächlich variiert — Emissionsrate und Informationsgehalt müssen übereinstimmen.

### 🟢 Pitfall #310 — Eine Verwerfungsschwelle auf einer Stichprobengrösse ist ein Anti-Selektions-Filter, wenn die Stichprobengrösse mit der Strategiequalität korreliert [BEHOBEN: GH-#944]
**Symptom:** `SORTINO_INSUFFICIENT_DOWNSIDE` verwarf Trials, deren Anzahl negativer (Downside-)
Perioden unter `sortino_min_downside_observations` lag. Für hochselektive, profitable Strategien
(z. B. SqueezeBreakout, Median ~27 informative Perioden) ist eine niedrige Downside-Beobachtungszahl
aber KEIN Rauschsignal, sondern die direkte Konsequenz einer guten Trefferquote.
**Root-Cause:** Die Verwerfungswahrscheinlichkeit stieg dadurch MONOTON mit der Strategiequalität
(weniger Verlustperioden ⇒ höhere Ablehnungsrate) — eine Schwelle, die eigentlich vor einem
degenerierten Schätzer schützen sollte (Pitfall #296 selbst), wurde zu einem Filter, der
systematisch die besten Kandidaten aus dem Pool entfernt.
**Fix/Regel:** Ein dünn besetzter Schätzer wird James-Stein-artig Richtung eines Referenzwerts
GESCHRUMPFT (`lambda = downside_obs/(downside_obs+m0)`, geschrumpft Richtung der Gesamtstreuung
aller informativen Perioden), nicht VERWORFEN — der Trial bleibt bewertbar
(`SORTINO_DOWNSIDE_SHRUNK`-Diagnostik statt Prune). Eine Verwerfungsschwelle auf einer
Stichprobengrösse ist grundsätzlich daraufhin zu prüfen, ob die Stichprobengrösse selbst mit der
gesuchten Eigenschaft korreliert — wenn ja, ist Schrumpfung fast immer die richtigere Antwort als
Verwerfung.

### 🟢 Pitfall #311 — Eine Simulation, die über den wirtschaftlichen Ruin hinaus weiterrechnet, produziert Trades ohne Aussagekraft und lässt die Equity-Kurve unrealistisch weiterlaufen [BEHOBEN: GH-#947]
**Symptom:** `HourlyStrategyBase` hatte keine Kill-Switch-Logik für den Fall, dass das simulierte
Konto-Equity unter eine Margin-Stop-out-Schwelle fiel — die Strategie handelte in der Simulation
unbegrenzt weiter, auch nachdem ein realer Broker die Position zwangsliquidiert und den Handel
gesperrt hätte.
**Root-Cause:** CFD-artige Backtests ohne expliziten Margin-Stop-out-Mechanismus modellieren
implizit ein unbegrenztes Nachschusskonto — eine Annahme, die in keinem realen Ausführungskontext
gilt und die abgeleiteten Kennzahlen (Sortino, PSR, Total Return) für ruinierte Trials verzerrt statt
sie als das auszuweisen, was sie sind: ungültig.
**Fix/Regel:** Sobald `current_equity <= stop_out_equity_frac * initial_equity`, schliesst die
Strategie sofort alle offenen Positionen (`ExitReason.EQUITY_STOPOUT`) und stoppt jeden weiteren
Handel für den Rest des Trials (`self._ruined`-Flag). `backtest_runner.py` erkennt
`EQUITY_STOPOUT` im Exit-Reason-Histogramm (IS wie OOS) und stempelt `TRIAL_RUINED_STOPOUT`
(`failure_policy="prune"`, `nullifies_metrics=("oos_sortino_period","oos_psr","oos_total_return")`)
— ein ruinierter Trial darf keine der von ihm abgeleiteten Kennzahlen in die Selektion einspeisen.

### 🟢 Pitfall #312 — Eine Diagnosemeldung, deren Text die entgegengesetzte Aussage ihrer eigenen Auslösebedingung trifft, führt jeden Leser in die falsche Richtung [BEHOBEN: GH-#948]
**Symptom:** `check_inference_diagnostics_concentration` beschrieb die betroffenen Trials als
Trials "mit einem regulären Inferenzpfad-Ausgang" — exakt das Gegenteil dessen, was die Funktion
tatsächlich misst: eine Konzentration von Trials, die vom Inferenz-Wächter ZENSIERT wurden
(`SORTINO_GUARD_TRIPPED`/`SORTINO_INSUFFICIENT_DOWNSIDE`), also gerade KEINEN regulären Ausgang
hatten.
**Root-Cause:** Der Meldungstext wurde vermutlich für eine frühere Version der Funktion formuliert
und nicht mitgeändert, als sich die tatsächliche Bedingung weiterentwickelte — dieselbe
Fehlerklasse wie Pitfall #303 (Proxy überlebt die Änderung seiner Grundlage), hier auf der
Text- statt der Code-Ebene.
**Fix/Regel:** Der Meldungstext wurde auf die tatsächliche Bedingung korrigiert ("wurden vom
Inferenz-Wächter zensiert … kein regulärer Ausgang"). Jede Diagnosemeldung, die eine Bedingung in
Prosa umschreibt, gehört bei jeder Änderung der Bedingung selbst auf Wortlaut-Konsistenz geprüft —
ein invertierter Text ist schlimmer als gar keine Meldung, weil er aktiv in die falsche Richtung
weist.

### 🟢 Pitfall #313 — Report-Diagnostik, die Reward-Terme über ALLE Trials mittelt, misst die Varianz des Failure-Zweigs statt der Qualitätsordnung [BEHOBEN: GH-#949]
**Symptom:** `reward_std` lag über 28 Studies zwischen 0,018 und 53,99 (Faktor 2951) für dieselbe
Zielfunktionsformel. Die beiden grössten Werte gehörten exakt den beiden Studies mit
`EQUITY_NONPOSITIVE`-Ereignissen — eine geschlossene Schranke (`|Δ| ≤ σ/√(p(1-p))`) zeigte, dass
schon 1–3 Failure-Trials von ~120 die gesamte beobachtete Streuung erklären können, drei
Grössenordnungen über der Skala, auf der die Shaping-Terme wirken.
**Root-Cause:** Die Report-Ebene berechnete Varianz-Kennzahlen über ALLE Trials inklusive später
geprunter — ein Trial mit `trial_pruned_inference_codes` liefert einen `reward_terms`-User-Attr-
Snapshot, der zum Bewertungszeitpunkt real war, dessen Trial aber danach als ungültig markiert
wurde; die Diagnostik konsumierte diesen Snapshot trotzdem als gültige Beobachtung.
**Fix/Regel:** `_feasible_reward_terms`/`_reward_std_total_and_feasible` trennen `reward_std_
total` (alle Trials) von `reward_std_feasible` (nur nicht-geprunte). `check_reward_dynamic_range`
verlangt `reward_std_feasible >= 4·max_j std(term_j)` UND `reward_std_feasible >= 0.05` UND
`reward_std_total/reward_std_feasible <= 3.0` — die Basis-Dominanz-Berechnung MUSS den Basis-Term
selbst aus `max_j std(term_j)` ausschliessen, sonst ist die Klausel bei `reward≈base` tautologisch
fast unerfüllbar (Selbstvergleich `1× statt 4×`).

### 🟢 Pitfall #314 — Ein Fix, der nur auf dem produktiven Codepfad landet, lässt den Nicht-Default-Pfad mit dem alten Bug zurück [BEHOBEN: GH-#950]
**Symptom:** `reward_mode='pareto'` (ein seit früheren Sitzungen existierender, nicht-produktiver
Modus) definierte seine eigene `constraints_func`, die `trial.user_attrs.get("constraints", (0.0,
0.0))` zurückgab — ein Platzhalter, der nie mit den echten, normierten OOS-Constraint-Distanzen
gefüllt wurde, während der produktive `reward_mode`-Pfad längst die reale `_oos_constraints_func`
verwendete.
**Root-Cause:** Als `_oos_constraints_func` für den Default-Pfad eingeführt wurde, blieb der
`pareto`-Zweig unverändert — zwei Implementierungen derselben Zuständigkeit (Constraint-
Übergabe an den Sampler) drifteten auseinander, weil nur eine davon regelmässig getestet/genutzt
wurde.
**Fix/Regel:** Beide Stellen (`make_objective` und `_optimize_symbol_impl`) instanziieren den
`NSGAIISampler` jetzt mit `constraints_func=_oos_constraints_func` — derselben Funktion wie der
Default-Pfad. Bei mehreren Implementierungen derselben Zuständigkeit (hier: Constraint-Distanz für
den Sampler) ist eine gemeinsame Funktion einer Duplikation immer vorzuziehen, auch wenn einer der
beiden Pfade selten läuft — "selten genutzt" ist kein Grund, einen Bug dort unkorrigiert zu lassen.

### 🟢 Pitfall #315 — Eine genäherte statistische Schranke sollte durch ihre exakte Form ersetzbar sein, ohne den validierten Default anzufassen [BEHOBEN: GH-#953]
**Symptom:** Der Plateau-Frühstopp (`plateau_stop_mode='expected_yield'`, #925) verwendet für die
Obergrenze der Trefferwahrscheinlichkeit bei `t` Trials ohne einen einzigen eligiblen Kandidaten
implizit die Rule-of-Three-Näherung `p_hi ≈ 3/m` (α≈0,05), keine exakte Formel.
**Root-Cause:** Rule-of-Three ist eine asymptotische Näherung der einseitigen Clopper-Pearson-
Obergrenze — für die bereits validierte, produktive Standardkonfiguration ausreichend genau, aber
kein geschlossen hergeleiteter Wert, und für kleine `t` (wie sie am Anfang jeder Study auftreten)
messbar ungenau.
**Fix/Regel:** `plateau_stop_clopper_pearson(m, remaining_budget, alpha)` implementiert die exakte
Formel `p_hi(t) = 1 - α^(1/t)`, additiv erreichbar über `plateau_stop_mode='clopper_pearson'` +
`plateau_stop_alpha` — der bereits empirisch validierte Default (`expected_yield`, 43 % Budget-
Ersparnis laut #925) bleibt bit-identisch unverändert. Eine Näherungsformel im validierten Pfad zu
ERSETZEN ist ein grösseres Risiko als eine exakte Alternative daneben additiv verfügbar zu machen.

### 🟢 Pitfall #316 — Ein Kostenmodell-Floor in Basispunkten ignoriert, dass die tatsächlich erreichbare Spanne durch die Tick-Grösse selbst nach unten begrenzt ist [BEHOBEN: GH-#956]
**Symptom:** `resolve_spread_bps` gab für Symbole mit grober Preis-Präzision (wenige Nachkomma-
stellen relativ zum Kurs) einen Spread zurück, der unter einem einzigen Tick lag — ein Fill-Preis,
den kein reale Orderbuch je hätte anbieten können, weil der minimale Preisschritt selbst schon
grösser als der modellierte Spread war.
**Root-Cause:** Der Asset-Klassen-/Symbol-Override-Spread ist eine STATISTISCHE Schätzung
(historischer Median), aber ohne einen PHYSIKALISCHEN Floor kann diese Schätzung unterhalb dessen
liegen, was die Tick-Grösse des Instruments überhaupt zulässt.
**Fix/Regel:** `tick_floor_spread_bps(median_price, tick_size) = 1e4 · tick_size / median_price`
liefert die physikalische Untergrenze; `resolve_spread_bps(..., tick_floor_bps=...)` wendet
`max(resolved, tick_floor_bps)` sowohl am Symbol-Override- als auch am Asset-Klassen-Rückgabepunkt
an. Default `tick_floor_bps=0.0` hält den Aufruf ohne den neuen Parameter bit-identisch zum
Alt-Verhalten (Pitfall-Muster wie #566/#898: ein Kostenmodell-Floor gehört an die EINE
`resolve_spread_bps`-Quelle, nicht an einzelne Aufrufer).

### 🟢 Pitfall #317 — Eine Ursachen-Diagnose, die nur zwischen "kein Signal" und "schlechte Qualität" unterscheidet, verwechselt eine ungültige Simulation mit einem echten Qualitätsbefund [BEHOBEN: GH-#957]
**Symptom:** `resolve_ineligible_binding_cause` konnte einer Study `binding_cause=signal_quality`
zuweisen und daraus `action=quarantined_pending_simulation_review` ableiten, selbst wenn eine
blockierende Invariante (z. B. #947s `TRIAL_RUINED_STOPOUT`) bereits belegte, dass die zugrunde
liegende Simulation selbst ungültig war — die Diagnose bewertete dann die Qualität eines Ergebnisses,
dessen Erhebung bereits als fehlerhaft feststand.
**Root-Cause:** Die Ursachen-Priorität (`inference_unavailable > signal_sparse > signal_quality`)
hatte keinen vierten Fall für "die Messung selbst ist ungültig" — eine Kategorie, die logisch VOR
jeder Qualitätsaussage stehen muss, weil eine ungültige Messung keine Qualitätsaussage zulässt.
**Fix/Regel:** `recommend_diagnosis_action(..., blocking_invariants_failing=...)` routet
`cause="signal_quality"` bei `blocking_invariants_failing=True` auf `cause="simulation_invalid"`,
`action="none"`, `writeback_suppressed_reason="blocking_invariant"` — eine Empfehlung wird
unterdrückt, solange die Simulation selbst als ungültig belegt ist. Bei `blocking_invariants_
failing=False` (explizit widerlegt) darf die #911-Suspendierung sogar aktiv gelöst werden
(`action="denylist"`). Default `None` hält bestehende Aufrufer bit-identisch zum Alt-Verhalten.

### 🟢 Pitfall #318 — Eine Promotion-Logik, die den Studienbesten nach Rang wählt, kann einen Trial promovieren, dessen Selektionsstatistik nie tatsächlich berechnet wurde [BEHOBEN: GH-#958]
**Symptom:** `confirm_per_symbol_promotion` wählte `promoted_m_symbol` über eine Median-Rang-
Berechnung aus dem Top-k-Holdout-Pool — ohne zu prüfen, ob für den gewählten Trial `oos_n_periods
>= 1` überhaupt zutraf. Ein Trial ohne auswertbare OOS-Perioden konnte so unbemerkt zum
promovierten Studienbesten werden.
**Root-Cause:** Die Selektionslogik verwechselte "hat den besten RANG innerhalb der Kandidatenmenge"
mit "wurde tatsächlich OOS bewertet" — ein degenerierter Kandidat mit einem zufällig günstigen
Rang-Wert (z. B. durch fehlende Vergleichsdaten) besteht die Rang-Auswahl trotzdem.
**Fix/Regel:** Ein enger Guard NACH der Median-Rang-Auswahl (nicht in der `eligible_trials`-
Filterung selbst — letztere hätte 28 Tests gebrochen, weil die meisten Fixtures `oos_n_periods`
nie setzen und der reale Default `0` ist) verwirft die finale Promotion mit `REJECT_PROMOTED_
TRIAL_INADMISSIBLE`, wenn `oos_n_periods < 1`. Eine Eligibility-Prüfung, die auf einem Feld ohne
realistischen Default basiert, gehört so spät wie möglich in der Entscheidungskette platziert —
am Punkt der tatsächlichen Konsequenz, nicht in der breiten Kandidatenmenge davor.

### 🟢 Pitfall #319 — Dieselbe Kollinearitäts-Redundanz taucht ein sechstes Mal auf, weil jede vorherige Behebung nur EIN betroffenes Gate korrigierte [BEHOBEN: GH-#960]
**Symptom:** `tournament.json['eligible_requires_all']` enthielt weiterhin `'min_profit_factor'`
neben `oos_min_psr` — Jaccard-Ähnlichkeit 0,964–0,979, marginaler Eigenbeitrag exakt 0,000 — dieselbe
Kollinearitätsklasse wie bereits #677/#697/#776, jeweils an einer ANDEREN Gate-Liste behoben.
**Root-Cause:** Jede vorherige Behebung entfernte die Redundanz aus genau der Liste, in der sie
gerade beobachtet wurde, ohne zu prüfen, ob dieselbe redundante Kennzahlenpaarung auch in anderen
Gate-Listen (`eligible_requires_all` vs. `gate_consolidation_protected`) wiederkehrt.
**Fix/Regel:** `'min_profit_factor'` wurde aus `eligible_requires_all` entfernt (verbleibt:
`['min_trades', 'max_drawdown', 'oos_min_psr']`). `gate_consolidation_protected` bleibt bewusst
UNVERÄNDERT (`['min_trades', 'max_drawdown']`) — diese Liste kodiert strukturelle Vorbedingungen,
keine Qualitätsgates, `oos_min_psr` gehört dort NICHT hinein (ein erster Versuch, es dort
hinzuzufügen, wurde nach Lektüre der Feld-Dokumentation zurückgenommen). Eine gefundene
Kollinearitätsredundanz ist grundsätzlich gegen ALLE Gate-Listen zu prüfen, nicht nur gegen die,
in der sie gerade auffiel.

### 🟢 Pitfall #320 — Eine Semantik-Versions-Bump-Begründung, die die Issue-Liste der ganzen Sitzung statt der tatsächlichen Auslöser nennt, ist bei der nächsten Purge-Prüfung nicht mehr nachvollziehbar [BEHOBEN: GH-#961]
**Symptom:** Von 14 in dieser Sitzung behandelten Befunden (#937–#960) ändern nur vier tatsächlich
eine gestempelte Simulations- oder Reward-/Eligibility-Entscheidung: #944/#947/#956 (simulation,
v3→4) und #958/#960 (reward/eligibility, v21→22). Die übrigen sind additive Diagnostik,
nicht-produktive Pfade oder opt-in mit sicherem Default.
**Root-Cause:** Ohne eine explizite Trigger-/Nicht-Trigger-Unterscheidung pro Bump (Pitfall #299)
würde ein künftiger Agent entweder ALLE 14 Befunde als Bump-Rechtfertigung lesen (und die Purge-
Notwendigkeit überschätzen) oder — schlimmer — bei einer künftigen Änderung an einem der
"NICHT Ausloeser"-Issues fälschlich annehmen, ein Bump sei schon erfolgt.
**Fix/Regel:** Beide `_schema.fields`-Einträge benennen im `v22 =`/`v4 =`-Absatz explizit sowohl
die hinreichenden Auslöser als auch die geprüften Nicht-Auslöser mit Issue-Nummer und
Ein-Satz-Begründung, gegen den Code getestet statt nur behauptet
(`test_issue_961_version_bumps.py`, Pitfall #299). Die Pflicht-Purge (`purge_stale_studies.py`)
bleibt die LETZTE Aktion vor jedem Re-Run, der #944/#947/#956/#958/#960 produktiv aktivieren soll.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #937–#964)
- `optimizer.json.max_failed_symbols_abs` (Default 5) / `max_failed_symbols_frac` (Default 0.05)
  — #939, Pitfall #308.
- `optimizer.json.plateau_stop_alpha` (Default 0.05, nur wirksam bei `plateau_stop_mode=
  'clopper_pearson'`) — #953, Pitfall #315.
- `tournament.json.sortino_downside_shrinkage_m0` (Default 30) — #944, Pitfall #310.
- `tournament.json.eligible_requires_all` — `'min_profit_factor'` entfernt (verbleibt
  `['min_trades', 'max_drawdown', 'oos_min_psr']`) — #960, Pitfall #319.
- `HourlyStrategyConfig.stop_out_equity_frac` (Default 0.20) — #947, Pitfall #311.
- `optimizer.json.reward_semantics_version` 21 → 22 (zwei Auslöser: #958, #960) — #961, Pitfall
  #320.
- `optimizer.json.simulation_semantics_version` 3 → 4 (drei Auslöser: #944, #947, #956) — #961,
  Pitfall #320.

### 🔒 Watertight Invariants (Issue-Katalog #937–#964) — für künftige Agenten
- **`log_manager._sanitize_for_json`/`_canonical_key`** (`log_manager.py`, #937) — der EINE
  Sanitisierungs-Pfad, den JEDER Telemetrie-Emissionsaufruf durchläuft, bevor `json.dumps`
  aufgerufen wird; niemals umgehen, auch nicht für "garantiert saubere" Payloads (Pitfall #307).
- **`_contracts.pair_key`/`split_pair_key`** (`_contracts.py`, #938) — der EINE Kanal für
  (Strategie, Symbol)-Paar-Schlüssel in Dicts; ein rohes Tupel als Dict-Key ist verboten
  (Pitfall #307).
- **`sweep.sweep_failed_symbols`/`max_failed_symbols_abs`+`max_failed_symbols_frac`** (`sweep.py`,
  #939) — die EINE Stelle, an der ein Symbol-Ausfall isoliert UND (bei doppelter
  Schwellenüberschreitung) zum globalen Abbruch eskaliert wird (Pitfall #308).
- **`backtest_runner._read_sortino_downside_shrinkage_m0`** (`backtest_runner.py`, #944) — liest
  `sortino_downside_shrinkage_m0` gecacht; jede Schrumpfungsberechnung der Downside-Deviation MUSS
  über diese Funktion laufen, nicht über einen lokal hartkodierten `m0` (Pitfall #310).
- **`HourlyStrategyBase._ruined`/`ExitReason.EQUITY_STOPOUT`** (`hourly_strategy_base.py`, #947) —
  sobald gesetzt, liefert `_check_exits_and_update` sofort `True` OHNE weitere Exit-Prüfung; kein
  Trade-Code darf nach einem Stop-out noch eine neue Position eröffnen (Pitfall #311).
- **`backtest_runner.tick_floor_spread_bps`** (`backtest_runner.py`, #956) — die EINE Quelle der
  physikalischen Spread-Untergrenze; jeder Aufrufer von `resolve_spread_bps` mit einem bekannten
  `tick_size`/`median_price` reicht sie als `tick_floor_bps` durch (Pitfall #316, analog
  `resolve_atr_floor_bps`/`resolve_opening_range_session_open_hour` aus #924).
- **`invariants.check_reward_dynamic_range`/`_reward_std_total_and_feasible`** (`invariants.py`,
  #949) — der EINE Kanal für Basis-Dominanz-/Streuungs-Kennzahlen; `max_j std(term_j)` MUSS den
  Basis-Term selbst ausschliessen (Pitfall #313).
- **`sweep_diagnostics.recommend_diagnosis_action`s `blocking_invariants_failing`-Parameter**
  (`sweep_diagnostics.py`, #957) — `cause="simulation_invalid"` hat Vorrang vor
  `cause="signal_quality"`, sobald eine blockierende Invariante eine ungültige Simulation belegt
  (Pitfall #317).
- **`confirm.confirm_per_symbol_promotion`s `REJECT_PROMOTED_TRIAL_INADMISSIBLE`-Guard**
  (`confirm.py`, #958) — die letzte Prüfung vor jeder Promotion; `oos_n_periods < 1` verhindert
  die Promotion unabhängig vom Rang-Ergebnis (Pitfall #318).
- **`optimizer.json['reward_semantics_version']`/`['simulation_semantics_version']`** (#961) — wie
  #936, jetzt mit v22-/v4-Einträgen; die vollständige Auslöser-/Nicht-Auslöser-Begründung steht im
  `_schema.fields`-Eintrag, gegen den Code getestet (`test_issue_961_version_bumps.py`), nicht nur
  in diesem Dokument (Pitfall #299/#320).

## Issue-Katalog #965–#990 (GitHub-Issues #785–#788, Sitzung 2026-08-07, Referenzlauf `46cf5070`)

**Nummerierungs-Hinweis:** die ursprünglichen Issue-Texte (#992) schlugen die Pitfall-Nummern
`#303`–`#312` für diese Runde vor — zum Zeitpunkt ihrer Formulierung war das der nächste freie
Block. Der Katalog #937–#964 (siehe oben) hat diesen Block inzwischen selbst belegt (`#303`–`#320`).
Code-Kommentare, die in DIESER Session geschrieben wurden, zitieren daher stellenweise `#303`–`#312`
im Sinne der Issue-Texte, NICHT im Sinne der hier tatsächlich registrierten Pitfalls — die
verbindliche Registrierung für diese Runde beginnt bei `#321` (nächster freier Wert). Bei einem
künftigen Cross-Referenzieren gilt: ein Code-Kommentar mit `Pitfall #30x`/`#31x` OHNE Bezug auf
Issue #965–#990 im selben Kommentar meint den `#937–#964`-Katalog oben, NICHT diesen Abschnitt.

- **#321 (war Pitfall-Vorschlag "#303")** — Ein blockierender Wächter, dessen Zahl sich aus der
  eigenen Telemetrie nicht nachrechnen lässt, darf keinen Lauf abbrechen. `check_holding_time_cap`
  (#971) maß TRIAL- statt TRADE-Anteile und reproduzierte dabei bit-genau `(n_trials -
  evaluable_trials) / n_trials` — eine Größe ohne jeden Bezug zur Haltedauer. Fix:
  `InvariantResult.provenance` (numerator/denominator/numerator_definition/source_field) ist jetzt
  für `severity='blocking'`-Checks Pflicht.
- **#322 (war "#304")** — Ein Zähler, dessen Grundgesamtheit durch genau das Kriterium vorgefiltert
  ist, das er messen soll, liefert immer dasselbe (tautologische) Ergebnis. Der Zero-Eligible-
  Plateau-Zähler (#972) lief über die bereits überlebenden `oos_evaluated`-Trials, konnte also nie
  einen der bereits herausgefilterten Fälle "sehen" — "0/N trafen die Grenze" war strukturell nicht
  widerlegbar. Fix: über `n_trials` (die tatsächliche Grundgesamtheit) zählen, mit Zerlegung;
  `check_counter_partition_consistency` als Regressionswächter.
- **#323 (war "#305")** — Ein Sentinel, der die Signatur eines Messwerts trägt (`0.0` statt `None`
  bei fehlender Messung), wird von JEDEM nachgelagerten Konsumenten als Messwert behandelt — nicht
  nur vom offensichtlichen. `oos_expectancy` (#966) kollabierte in der Parsing-Schicht auf `0.0` und
  fütterte darüber sowohl die Gate-Distanz als auch den TPE-Sampler-Constraint mit einem
  fabrizierten Wert. Dieselbe Klasse wie #759 (`oos_win_rate`), hier an einer vierten Metrik
  nachgezogen — Konsumenten sollten `None` explizit AN DER KONSUMSTELLE behandeln, nicht die
  Parsing-Schicht raten lassen.
- **#324 (war "#306")** — Eine fehlende Statistik ist nie "neutral": prüfen, ob die Ausfallmenge
  ökonomisch verschieden von der Erfolgsmenge ist. Im Referenzlauf war die Kohorte OHNE definierten
  `oos_psr` (#965) profitabler (92,2 % positive Rendite) als die Kohorte MIT PSR (7,4 % positiv) —
  eine reine Anteilsschwelle (`check_selection_statistic_availability`) fängt diese Klasse nicht.
  Fix: `check_selection_statistic_economic_bias`, ein einseitiger Mann-Whitney-Test ohne
  scipy-Abhängigkeit (`invariants._mann_whitney_u_one_sided`).
- **#325 (war "#307")** — Ein laufzeitabhängiger, aus der eigenen Suchpopulation gebildeter Anker
  macht Urteile reihenfolgeabhängig und den Lauf trotz festem Seed nicht reproduzierbar. Der
  Sortino-Guard (#968) mit `sortino_numeric_guard_reference='family_median'` wanderte über acht
  verschiedene Werte innerhalb EINES Laufs und wechselte mitten in einer Study die Quelle
  (`absolute_bootstrap` → `family_median`) — derselbe Parametervektor konnte je nach
  Ankunftsreihenfolge im Scheduler ein umgekehrtes Guard-Urteil erhalten. Fix (dieser Katalog):
  `check_guard_reference_stability` als Regressionswächter; die vollständige Entfernung des
  `family_median`-Modus zugunsten einer vor dem ersten Trial eingefrorenen H0-Bootstrap-Tabelle
  bleibt für einen Folge-Katalog zurückgestellt (Restrukturierungsrisiko ohne Live-Validierung).
- **#326 (war "#308")** — Ein Strafterm, der nur im verworfenen Zweig streut, ordnet dort, wo
  Ordnung folgenlos ist — `std(term|eligible) / std(term|failure)` messen, nicht nur `std(term)`
  absolut. `dd_penalty` (#977) dominierte die Zielfunktion um Faktor 2,7–8,5, aber ausschliesslich
  im `failure`-Zweig (im `eligible`-Zweig 1426× kleiner). Fix: `penalty_dd_weight` auf `0.0`
  (Risikokontrolle bleibt über das `oos_max_drawdown`-Gate bestehen — Doppelzählung entfernt,
  siehe Pitfall #327/#309).
- **#327 (war "#309")** — Dieselbe Grösse zweimal zu kodieren (hart als Gate, weich als Strafe) ist
  Doppelzählung und verzerrt die Suche — siehe #326/#977 (`dd_penalty` vs. `oos_max_drawdown`-Gate)
  und #979 (Reward-Zweig-Klippe vs. #612-Sampler-Constraint als zwei Feasibility-Kodierungen).
- **#328 (war "#310")** — Eine metrische Skala, die je Fold empirisch bestimmt wird, ist über Folds
  NICHT kommensurabel; Mitteln/Median darüber ist eine Kategorienverwechslung. Der annualisierte
  Sortino (#978) nutzt einen ereignisgetriebenen Annualisierungsfaktor F, der innerhalb EINES
  Trials um Faktor bis 3,59 variierte (vier Folds derselben Kalenderlänge, vier verschiedene √F).
  Fix: `check_annualization_commensurability` (`max(√F)/min(√F) <= 1.05` je Trial).
- **#329 (war "#311")** — Ein uniformer Budgetschnitt trifft die modellgeführte Suche
  überproportional, solange Fixkosten (Startup, Mindestzahlen) nicht mitskalieren — 26 % Wallclock-
  Kürzung (#983/#984) vernichtete rechnerisch ~31 % der TPE-modellierten Trials, weil `startup_
  limit` konstant blieb. Der Preflight-Schätzfehler (Faktor ~1,90, Median statt Mittelwert +
  fehlende Scheduling-/Overhead-Korrektur) wurde in diesem Katalog behoben
  (`wallclock_guard.estimate_expected_wallclock_h`, `backtest_share`/`scheduling_efficiency`); die
  NICHT-uniforme Studies-übergreifende Neuallokation selbst bleibt zurückgestellt.
- **#330 (war "#312")** — Ein Wächter braucht einen dritten Zustand `INCONCLUSIVE`. Ohne ihn wird
  "nicht messbar" als "defekt" berichtet — und daraus werden ggf. Denylist-Entscheidungen
  abgeleitet. `check_search_made_progress` (#981) interpretierte eine dreiwertige
  Treppenfunktion (`min_constraint_violation_first/last ∈ {0.0, 0.5, 1.0}`, Folge von #966s
  Sentinel-Bug) als "der TPE-Sampler hat nachweislich keinen Gradienten gefunden" — eine Aussage,
  die aus dieser Auflösung nicht folgt. Fix: `InvariantResult.inconclusive`, gesetzt wenn
  `len(set(constraint_violations_observed)) < 10`.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #965–#990)
- `optimizer.json.penalty_dd_weight` 1.0 → 0.0 — #977, Pitfall #326/#327.
- `optimizer.json.reward_semantics_version` 22 → 23 (zwei Auslöser: #977, #966) — Pitfall #326/#323.
- `optimizer.json.inference_semantics_version` — NEU, Startwert 1 — #968, Pitfall #325. Geprüft/
  gestempelt über `_check_inference_semantics_version` (`run_optimization.py`, Mechanik identisch
  zu `_check_reward_semantics_version`/`_check_simulation_semantics_version`,
  `REJECT_STALE_INFERENCE_SEMANTICS` als eigener Fehlercode).
- `optimizer.json.wallclock_preflight_backtest_share` (Default 0.85) / `.wallclock_preflight_
  scheduling_efficiency` (Default 0.80) — #983, Pitfall #329.
- `optimizer.json.fail_fast_policy` (Default `'quarantine'`) — #975.
- `_CONFIGURED_INACTIVE_REWARD_TERMS` (`invariants.py`) — `"dd_penalty"` ergänzt — #977, Pitfall
  #326.

### 🔒 Watertight Invariants (Issue-Katalog #965–#990) — für künftige Agenten
- **`invariants.check_holding_time_cap`** (`invariants.py`, #971) — konsumiert seit diesem Fix
  `timebox_violating_trades_frac` (TRADE-Ebene), NIE mehr `timebox_violation_fraction`
  (TRIAL-Ebene) für eine `severity='blocking'`-Entscheidung (Pitfall #321).
- **`run_optimization._classify_is_rejection_detail`** (`run_optimization.py`, #971) — der
  `timebox_violated`-Parameter MUSS vor der IS-Gate-Heuristik geprüft werden, sonst ist ein
  nachträglich (#857) invalidierter Trial vom echten IS-Gate-Drop nicht mehr unterscheidbar
  (Pitfall #321).
- **`invariants._CENSORING_DIAGNOSTIC_CODES`/`_ADAPTIVE_DIAGNOSTIC_CODES`** (`invariants.py`,
  #967) — jeder neue `inference_diagnostics`-Code MUSS hier klassifiziert werden (CENSORING: Trial
  verschwindet aus der Zielverteilung; ADAPTIVE: Trial bleibt mit korrigierter Statistik), sonst
  bestraft `check_inference_diagnostics_absent` einen funktionierenden Korrekturmechanismus.
- **`_contracts.INFERENCE_DIAGNOSTIC_CODES`** (`_contracts.py`, #965/#967) — jeder Rückgabepfad in
  `backtest_runner._compute_sortino`, der `sortino`/`psr` auf `None` setzt, MUSS hier registriert
  sein (`test_issue_918_inference_diagnostic_registry.py`s AST-Vollständigkeitstest erzwingt das) —
  vier vorher stumme Pfade wurden in diesem Katalog nachgezogen (Pitfall #323).
- **`invariants.gate_inventory_table`/`check_gate_marginal_contribution`** (`invariants.py`, #970)
  — vor dem Hinzufügen eines neuen `eligible_requires_all`-Gates: gegen eine reale Kohorte prüfen,
  ob es je solo oder marginal beiträgt, statt es auf Verdacht zu ergänzen.

### 🟢 Pitfall #330 — Ein Wächter, der zu oft feuert, ist zuerst als korrekt anzunehmen [Katalog #993–#1002]
**Symptom:** Ein vorgeschlagener Fix wollte die Schwelle eines Invarianten-Checks aufweichen, weil er im Bestand ungewöhnlich oft anschlug.
**Root-Cause:** Eine hohe FAIL-Zahl ist ein Mass für die Verbreitung des zugrunde liegenden Defekts, nicht für die Fehlerhaftigkeit des Wächters selbst.
**Fix/Regel:** Bevor die Schwelle einer Invariante angepasst wird, ist zu belegen, dass sie das Falsche misst — nicht, dass sie unbequem oft anschlägt. Gegenprobe: Wäre die vorgeschlagene neue Schwelle auch dann gewählt worden, wenn sie im Bestand null Treffer hätte?

### 🟢 Pitfall #331 — Ein numerischer Zielwert für den Ausgang eines Signifikanztests ist kein Akzeptanzkriterium [Katalog #993–#1002]
**Symptom:** Ein Vorschlag formulierte als Erfolgskriterium „die Ablehnungsquote eines Gates sinkt um mindestens X %".
**Root-Cause:** Das macht die Grösse, die vor Überanpassung schützen soll, zur abhängigen Variablen der gewünschten Promotionsrate — eine Zielquote für einen Signifikanztest ist per Konstruktion zirkulär.
**Fix/Regel:** Zulässig ist ausschliesslich ein Kalibrierlauf mit OFFENEM Ausgang (z. B. ein H₀-Bootstrap, der die Schwelle empirisch herleitet). Ergebnis wird berichtet, nicht vorab als Zielzahl festgelegt.

### 🟢 Pitfall #332 — Optimierung auf dem Holdout hebt den Holdout auf [Katalog #993–#1002]
**Symptom:** Ein Vorschlag wollte eine Holdout-Kennzahl direkt als Zielfunktion oder Tie-Breaker in die Suche einspeisen.
**Root-Cause:** Eine Zielfunktion, die eine Holdout-Kennzahl maximiert, macht jede nachgelagerte DSR-/PSR-/PBO-Zahl bedeutungslos, weil die Selektionsbreite dann nicht mehr messbar ist.
**Fix/Regel:** Gilt auch dann, wenn der Holdout nur als Tie-Breaker oder Frühstopp-Kriterium gelesen wird — der Holdout bleibt strikt ausserhalb jeder Optimierungsschleife.

### 🟢 Pitfall #333 — Jeder Issue-/Katalog-Vorschlag ist vor der Umsetzung gegen `main` zu verifizieren [Katalog #993–#1002]
**Symptom:** Diese Session begann die Implementierung von Issue #846/#847 auf einem `claude/issue-846-agents-md-kbrdld`-Branch, dessen lokaler Checkout von `main` abwich (fremde, nicht-verwandte Git-Historie, kein Remote-Gegenstück) — die erste Verifikationsrunde gegen diesen falschen `main`-Stand ergab fälschlich, dass ein Dutzend im Katalog referenzierter Invarianten-Funktionen (`check_annualization_commensurability`, `check_objective_branch_coverage`, `check_selection_statistic_availability`, `check_cost_model_floor`/`tick_floor_bps` u. a.) sowie `automation/AGENTS.md`'s Zeilenzahl/höchste Pitfall-Nummer NICHT existierten. Nach dem Feststellen, dass der Ziel-`main` zwischenzeitlich (Force-Push) auf einen anderen, weit fortgeschritteneren Stand aktualisiert worden war, verifizierten sich SÄMTLICHE dieser Funktionen als real vorhanden — die Diskrepanz lag am falschen Vergleichspunkt, nicht am Katalog.
**Root-Cause:** Ein lokal vorhandener Branch ohne Remote-Gegenstück und ohne gemeinsamen Vorfahren mit dem aktuellen `main` ist kein zuverlässiger Verifikations-Ausgangspunkt — er kann veralteter Container-/Session-Zustand sein oder (wie hier) ein Signal, dass `main` selbst seither überschrieben wurde.
**Fix/Regel:** Prüfreihenfolge vor jeder Umsetzung: (1) `git fetch origin <default-branch>` unmittelbar vor der Verifikation, NICHT nur den vom Environment vorgegebenen lokalen Checkout vertrauen; (2) `git merge-base <branch> origin/<default-branch>` — kein gemeinsamer Vorfahre ist ein Alarmsignal, kein Freifahrtschein zum Verwerfen; (3) existiert die behauptete Konstante/Funktion überhaupt (`grep`) GEGEN DEN FRISCH GEHOLTEN Stand? Diese Session implementierte die beiden Stufe-0-Punkte (#993, #999) vollständig — der Rest des Katalogs (#994–#998, #1000–#1002) blieb unimplementiert, weil er einen Purge/Re-Run voraussetzt, der ausserhalb des Zeitbudgets dieser Session lag (nicht, weil seine Prämissen falsch wären — sie sind es nicht).

### 🟢 Pitfall #334 — Kelly-Formeln sind dimensionsbehaftet [Katalog #993–#1002]
**Symptom:** Eine vorgeschlagene Positionsgrössen-Formel lieferte für plausible Eingaben `f*` deutlich ausserhalb `[0, 1]`.
**Root-Cause:** `f* = p/a − q/b` gilt für Gewinn/Verlust JE EINGESETZTER EINHEIT. Werden `a`/`b` stattdessen auf das Gesamtkapital bezogen, wächst `f*` um den Kehrwert der relativen Positionsgrösse — der Exposure-Cap bindet dann immer, und die Risikosteuerung ist dekorativ.
**Fix/Regel:** Für jede Kelly-artige Formel prüfen: liefert sie für plausible Eingaben ein `f*` innerhalb `[0, 1]`? Falls nicht, ist die Formel dimensional falsch instrumentiert, kein Kalibrierungsproblem.

### 🟢 Pitfall #335 — Ein Divisor, der mit dem eingegangenen Risiko schrumpft, ist ein Hebel [Katalog #993–#1002, siehe §11.2]
**Symptom:** `MomentumLSAllocator.get_allocation` teilte `account_balance` durch die Zahl der Universumssymbole OHNE offene Position — der Nenner schrumpft mit jeder eröffneten Position, die LETZTE Position erhielt das gesamte Restkapital.
**Root-Cause:** `kapital / freie_slots` weist der letzten Position das gesamte Kapital zu, statt es über ein Budget (`Σ w_i <= W_max`) zu verteilen — eine Division durch die Zahl offener Möglichkeiten ist kein Erhaltungsprinzip.
**Fix/Regel:** Kapitalallokation braucht ein Budget mit Erhaltungsbedingung, keine Division durch freie Slots. Testfall: die Allokationsfolge muss in der Zahl bereits offener Positionen monoton FALLEND sein (siehe `test_issue_999_live_risk_boundary.py`).

### 🟢 Pitfall #336 — Eine Ablehnungsursache auf Study-Ebene ist keine Ursache, sondern eine Zusammenfassung [Katalog #993–#1002]
**Symptom:** Eine dominante Study-Level-Ablehnungsursache wurde im Vorschlag als der alleinige Treiber einer Nullpromotion benannt.
**Root-Cause:** Eine aggregierte Ursache auf Study-Ebene beantwortet nicht, WARUM auf Trial-Ebene tatsächlich abgelehnt wurde — ohne Aufschlüsselung nach der feineren, tatsächlichen Ablehnungsdetail-Verteilung ist die genannte Ursache eine Vermutung, kein Beleg.
**Fix/Regel:** Zwei Felder verschiedener Aggregationsebenen dürfen nie als gemeinsamer Beleg für dieselbe Kausalaussage geführt werden — jede Ursachenbehauptung muss auf der Ebene belegt werden, auf der sie gilt.

### 🟢 Pitfall #337 — Ein Split, dessen Grenze von den Suchparametern abhängt, ist kein Out-of-Sample [Katalog #993–#1002]
**Symptom:** Ein ereignisbasierter IS/OOS-Schnitt (Grenze abhängig von einer während der Suche variierenden Grösse) wurde als Alternative zu einer kalendarischen Grenze vorgeschlagen.
**Root-Cause:** Legt die IS/OOS-Grenze für zwei Trials derselben Study auf zwei verschiedene Kalenderzeitpunkte, vergleicht die Trials nicht mehr auf demselben Zeitraum — der Optimizer kann die Testmenge dadurch faktisch verschieben.
**Fix/Regel:** Die IS/OOS-Grenze muss kalendarisch und parameterunabhängig bleiben.

### 🟢 Pitfall #338 — Ein Artefakt hat genau eine Identität [Katalog #993–#1002]
**Symptom:** Ein Issue-Bestand enthielt Titel der Form „Issue #NNN: …" unter einer ANDEREN, tatsächlichen GitHub-Nummer — eine zweite Nummerierung mit konstantem Offset entstand.
**Root-Cause:** Jede Referenz „siehe #NNN" wird dadurch mehrdeutig, sobald zwei verschiedene Nummerierungssysteme parallel existieren.
**Fix/Regel:** Eine Nummer, ein Ort, eine Bedeutung — gilt für Issue-Nummern ebenso wie für `run_id` gegen Log-Dateinamen und für Feldnamen in Invarianten-Serialisierung.

### 🟢 Pitfall #339 — Eine blockierende Invariante muss ihren Zähler offenlegen [Katalog #993–#1002]
**Symptom:** Ein vorgeschlagener `severity=blocking`-Check ohne rekonstruierbare Definition von Zähler und Nenner wäre nicht prüfbar gewesen, warum er einen Lauf abbricht.
**Root-Cause:** Ohne die Rohgrössen (`n_violating`, `n_total`, Verteilungsstatistik) ist eine blockierende Meldung nur ein Quotient — die Abbruchentscheidung selbst bleibt unprüfbar.
**Fix/Regel:** Jede blockierende Invariante trägt die Rohgrössen, nicht nur den daraus abgeleiteten Quotienten — umgesetzt in `invariants.check_deployment_gate_completeness`/`check_live_exposure_budget` (Issue #993/#999): beide melden die vollständige `clause_results`/Snapshot-Liste der Verletzung, nicht nur ein Pass/Fail.

---

## Issue-Katalog #993–#1002 — Deployment-Grenze, Live-Kapitalallokation & Circuit-Breaker (GitHub-Issues #846/#847, Sitzung 2026-08-10)

**Ausgangslage.** GitHub-Issue #846 legte einen zehnteiligen, in Stufen (0–5) gegliederten Konsolidierungskatalog #993–#1002 vor (Basis: der 54-Issue-Bestand #965–#992 dieser Datei). Diese Session implementierte die beiden **Stufe-0-Punkte** (#993, #999) — die der Katalog selbst als „ohne Purge, ohne Re-Run" und mit dem grössten sofortigen Kapitalrisiko-Abbau kennzeichnet — vollständig inklusive Tests und Dokumentation. Die übrigen acht Punkte (Stufen 1–4) erfordern einen Trial-Purge und/oder einen vollständigen Symbolmatrix-Re-Run und blieben ausserhalb des Zeitbudgets dieser Session unimplementiert (siehe Pitfall #333 für eine Verifikations-Lektion aus dieser Session, NICHT weil die Katalog-Prämissen sich als falsch erwiesen — sie verifizierten sich vollständig gegen den tatsächlichen `main`).

### Umgesetzt in dieser Session

**#993 (P0, HEADLINE) — Die Deployment-Grenze.** Siehe §11.1 oben. Neues Modul `automation/optimizer/deployment_gate.py` (`evaluate_deployment_eligibility`, `DeploymentDecision`, acht Klauseln, fail-closed bei fehlenden Werten). `daily_orchestrator.phase5_live_deployment` liest Promotionsrecords jetzt aus `data/optimizer/proposal_{strategy}_{symbol}.json` statt aus `tournament_result`; die alte `oos_eligible ∧ oos_evaluated`-Bedingung ist ersatzlos entfernt. Additive Persistenz in `confirm.confirm_per_symbol_promotion` (`oos_psr`, `holdout_ci_lower_sortino`, `boundary_hit_fraction`, `data_snapshot_sha256` — vorher transient, jetzt in `metrics_symbol`/dem Proposal-Export gestempelt, keine bestehende Entscheidung geändert). Neue blockierende Invariante `invariants.check_deployment_gate_completeness`. Tests: `automation/tests/test_issue_993_deployment_gate.py` (35 Fälle).

**#999 (P0, HEADLINE) — Kapitalallokation und Circuit-Breaker.** Siehe §11.2 oben. `MomentumLSAllocator.get_allocation` auf eine Budget-Formel mit Erhaltungsbedingung umgestellt (ersetzt den monoton steigenden Ruin-Pfad `account_balance / pending_signals`). Neues Modul `automation/live_risk.py` (`evaluate_circuit_breaker`, `drawdown_damper`, `LiveCircuitBreakerWatchdog`) — Live-Equity-Überwachung mit zwei ODER-verknüpften Auslösern, Positions-Flatten via `Trader.market_exit_strategy` (verifiziert gegen die installierte `nautilus_trader`-API), Exit-Code 3. Neue Config-Sektion `backtest.json["live_risk"]`. Neue Invariante `invariants.check_live_exposure_budget`. Tests: `automation/tests/test_issue_999_live_risk_boundary.py` (22 Fälle). **Nicht umgesetzt** (siehe §11.2-Schlussabsatz): die Reconciliation-Vereinheitlichung (Symptom 3, GH #800 verworfen — bereits vorhandene Reconciliation-Infrastruktur in `etoro_execution.py`/`HourlyStrategyBase._reconcile_after_reconnect` unverändert) und die kostenabgeleitete Spread-Untergrenze `S_max = max(tick_floor_bps, α·ATR₁₄)` (der `tick_floor_bps`-Mechanismus aus #956 existiert real — die Live-Pfad-Verdrahtung selbst ist schlicht Zeitbudget-bedingt zurückgestellt, gehört inhaltlich zu #998).

### Offen — nicht in dieser Session bearbeitet

#994 (Selektionsstatistik/PSR-Verfügbarkeit), #995 (Reward-Zweig-Geometrie), #996 (Annualisierungs-/Periodenzahl-Kommensurabilität), #997 (Zeitbox-Exit-Vertrag), #998 (Kostenrealismus/Stresstest/CVaR), #1000 (Lookback-Bounds), #1001 (Matrix-Backtest-Durchsatz), #1002 (Bootstrap-Blocklänge) — alle acht sind laut Katalog Stufe 1–4 (Purge und/oder Re-Run erforderlich) und wurden nicht begonnen. Ihre referenzierten Mechanismen (`check_annualization_commensurability`, `check_objective_branch_coverage`, `check_selection_statistic_availability`, `check_guard_reference_stability`, `check_search_made_progress`, `check_gate_marginal_contribution`, `check_cost_model_floor`/`tick_floor_bps`) existieren SÄMTLICH bereits auf `main` — wer diese Punkte aufgreift, startet auf einer soliden, bereits verifizierten Grundlage (siehe Merge-Reihenfolge im ursprünglichen Issue #846).

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #993–#1002)
- `backtest.json.live_risk.max_total_exposure_fraction` (Default 0.60) — Pitfall #335, #999.
- `backtest.json.live_risk.max_symbol_exposure_fraction` (Default 0.10) — Pitfall #335, #999.
- `backtest.json.live_risk.dd_halt_fraction` (Default 0.10) — Circuit-Breaker Auslöser A, #999.
- `backtest.json.live_risk.psi_min` (Default 0.2) — Drawdown-Dämpfer-Untergrenze, #999.
- `backtest.json.live_risk.distribution_z_halt` (Default 2.5) — Circuit-Breaker Auslöser B, #999.
- `backtest.json.live_risk.circuit_breaker_n_min_periods` (Default 30) — Auslöser B fail-open-Schwelle, #999.
- `backtest.json.live_risk.poll_interval_s` (Default 30.0) — `LiveCircuitBreakerWatchdog`-Pollintervall, #999.

### 🔒 Watertight Invariants (Issue-Katalog #993–#1002) — für künftige Agenten
- **`invariants.check_deployment_gate_completeness`** (`invariants.py`, #993) — blockierend (Exit-Code 1 vor Bot-Start): jeder `whitelist_tournament.json`-Eintrag muss ein vollständiges `deployment_gate.clause_results`-Dict tragen, alle acht Klauseln `True`. Bei 0 Promotionen (Ausgangszustand) trivial erfüllt (Pitfall #330: kein Wächter, der noch nie feuert, ist deshalb falsch).
- **`invariants.check_live_exposure_budget`** (`invariants.py`, #999) — `Σ w_i <= max_total_exposure_fraction + 1e-9` über aufgezeichnete `LIVE_EXPOSURE_SNAPSHOT`-Telemetrie (`momentum_ls_allocator.get_allocation`). Eine Verletzung ist ein Bug in der Budget-Formel selbst, keine Dateneigenart (Pitfall #335).
- **`deployment_gate.evaluate_deployment_eligibility`** — fail-closed bei jeder fehlenden Grösse (`None` zählt nicht als erfüllt, Pitfall #237-Wiederkehr); kein Frühausstieg — `clause_results` trägt immer alle acht Klauseln, auch wenn eine früh entscheidende bereits fehlschlägt.
- **`live_risk.evaluate_circuit_breaker`** — Auslöser A (Drawdown) ist fail-closed und feuert unabhängig von der Stichprobengrösse; Auslöser B (Verteilung) ist fail-open, solange `n_live < circuit_breaker_n_min_periods` — beide Grenzwerte tragen eine `1e-9`-Toleranz gegen Float-Rundung an der exakten Schwelle.

### 🟢 Pitfall #340 — Ein Mittelwert von Quotienten ohne Nenner-Floor ist keine Kennzahl [Katalog #1003–#1022]
**Symptom:** `expectancy = mean(pnl_i / notional_i)` erzeugte bei einem Round-Trip mit Mikro-Notional (FIFO-Restmenge, Teil-Fill, `size_precision`-Rundung) einen beliebig grossen Quotienten, den das arithmetische Mittel ungedämpft übernahm — vier promotete Kandidaten implizierten bei identisch konfiguriertem `trade_amount_pct=15.0` vier verschiedene Positionsgrössen zwischen 0.96 % und 7.82 %.
**Root-Cause:** `mean(pnl/notional) ≠ Σpnl/Σnotional`; die zweite Form ist die ökonomisch richtige und robuste (kapitalgewichtete) Grösse.
**Fix/Regel:** Ökonomische Verhältnisse werden als Verhältnis der Summen berechnet, nicht als Mittel der Quotienten; das Mittel der Quotienten ist bestenfalls Telemetrie und nie ein Gate-/Reward-Eingang. **Nicht in dieser Session umgesetzt** (#1003, Stufe 2 — erfordert einen Trial-Purge, siehe unten).

### 🟢 Pitfall #341 — Zwei Kennzahlen desselben Trades müssen sich ineinander umrechnen lassen [Katalog #1003–#1022]
**Symptom:** `expectancy·n·f` (implizite Positionsgrösse aus Expectancy und Trade-Zahl) und `ln(1+total_return)` (tatsächlicher Portfolio-Return) widersprachen sich für denselben promoteten Kandidaten um Faktor 15,6.
**Root-Cause:** Mindestens eine der drei Grössen (`expectancy`, `total_return`, `notional_list`) misst nicht, was ihr Name behauptet — ohne eine Kohärenzprüfung bleibt das unsichtbar.
**Fix/Regel:** Widersprechen sich zwei Kennzahlen desselben Trades um mehr als eine Grössenordnung, ist der Bericht falsch — unabhängig davon, welche der beiden Zahlen stimmt. **Nicht in dieser Session umgesetzt** (`check_expectancy_return_coherence`, #1003, Stufe 2).

### 🟢 Pitfall #342 — Ein Cap ist eine Zensur, kein Wert [Katalog #1003–#1022, BEHOBEN #1004]
**Symptom:** `profit_factor = min(gross_profit / gross_loss, profit_factor_cap)` meldete für drei von vier Promotionen exakt `15.00` — der Bericht präsentierte einen Zensurwert als Messwert, ohne das kenntlich zu machen.
**Root-Cause:** Ein Cap schützt vor numerischer Explosion (rechtsschiefe Kennzahl), aber der geklemmte Wert wurde identisch wie ein echter Messwert weitergereicht — an Gate, Reward UND Bericht.
**Fix/Regel:** Jede geklemmte Grösse trägt ein `*_censored`-Flag (hier `profit_factor_censored`/`profit_factor_raw`, `backtest_runner._calculate_stats`), und keine Entscheidung darf auf einer zensierten Grösse beruhen — durchgesetzt von der neuen blockierenden Invariante `invariants.check_censored_statistic_in_decision`. `summary_de.py` zeigt einen zensierten Wert mit `≥`-Präfix und Fussnote, nie als glatte Zahl. Ein numerisch degenerierter Nenner (`0 < gross_loss < DENOMINATOR_FLOOR`) erzeugt zusätzlich `PROFIT_FACTOR_DENOMINATOR_DEGENERATE` (`_contracts.py`, `failure_policy='prune'`) — derselbe "nicht messbar, nicht schlecht"-Mechanismus wie bei den Sortino-Guard-Codes.

### 🟢 Pitfall #343 — In einer disjunktiven Bestätigung ist `None` immer `False` [Katalog #1003–#1022, BEHOBEN #1005 HEADLINE]
**Symptom:** `pbo_overfit = bool(study_pbo is not None and study_pbo > 0.5)` konflationierte „PBO ≤ 0.5" mit „PBO nicht schätzbar" — `not pbo_overfit` wurde in der `dsr_or_robust_pair`-Ersatzbestätigung fälschlich als „bestanden" gelesen. Analog `_holdout_bootstrap_ci_passes` (`ok=True` bei < 5 Returns). Ein Lauf mit `study_pbo=None` UND leeren `oos_period_returns` konnte dadurch trotz gescheiterter DSR promoten — der Kernfehler, der einen Lauf von 0 auf 4 Promotionen springen liess.
**Root-Cause:** Eine nicht schätzbare Prüfung ist keine bestandene Prüfung — die Regel gilt in `confirm.py` genauso wie in `deployment_gate.py` (Pitfall #237-Wiederkehr, jetzt in einem zweiten Modul).
**Fix/Regel:** `confirm.py` — `pbo_ok = (study_pbo is not None and study_pbo <= 0.5)` (eigene, fail-closed Variable statt der Wiederverwendung von `not pbo_overfit`); `_holdout_bootstrap_ci_passes` liefert bei zu wenigen Returns `(None, None)` statt `(True, None)`, jeder Aufrufer behandelt `None` als nicht bestanden (`if not ci_ok:` — funktioniert automatisch, weil `bool(None) is False`). `promotion_correction_mode` steht wieder auf dem per Kalibrierlauf (#667/#678) begründeten Default `'conjunction'` (die Config trug entgegen der eigenen dokumentierten Kalibrierentscheidung `'dsr_or_robust_pair'`).

### 🟢 Pitfall #344 — Ein Ersatz für eine Multiplizitätskorrektur muss selbst multiplizitätskorrigiert sein [Katalog #1003–#1022, BEHOBEN #1005]
**Symptom:** `dsr_or_robust_pair` ersetzte die (multiplizitätskorrigierte) DSR durch PBO + Bootstrap-CI — zwei NICHT multiplizitätskorrigierte Tests — bei 7280 durchsuchten Trials.
**Root-Cause:** Zwei unkorrigierte Tests ersetzen keinen korrigierten Test; sie ersetzen ihn durch nichts.
**Fix/Regel:** Die Bootstrap-CI im `robust_pair`-Zweig wird jetzt auf `1 − α/N_eff` statt `1 − α` gezogen (Bonferroni über `pbo_telemetry['pbo_n_configs']`, dieselbe deklusterte Familiengrösse, die `_study_pbo` bereits über `cpcv.cluster_effective_configs` berechnet) — `confirm.py`, `promotion_correction_alpha_effective`-Telemetrie macht die tatsächlich verwendete Konfidenz je Promotion nachvollziehbar.

### 🟢 Pitfall #345 — Eine Invariante, die eine Politik-Konstante ignoriert, ist unter der falschen Politik invertiert [Katalog #1003–#1022, BEHOBEN #1013]
**Symptom:** `check_family_n_periods_homogeneity` prüfte hart die Semantik von `deflation_heterogeneity_policy='suppress_dsr'` (heterogene Kohorte ⇒ nie ein DSR-Signal), unabhängig von der TATSÄCHLICH konfigurierten Politik. Unter dem seit #865 aktiven `'per_stratum'`-Default (der DSR ABSICHTLICH auf einem kommensurablen Stratum neu berechnet und behält) FAILte der Wächter GENAU DANN, wenn die Politik korrekt arbeitete — vier garantierte False Positives in jedem Lauf.
**Root-Cause:** Ein Wächter, der ein politik-gesteuertes Verhalten prüft, aber die Politik-Konstante selbst nicht liest, prüft nur EINE der möglichen Politiken korrekt — unter jeder anderen ist er invertiert.
**Fix/Regel:** Jeder Wächter, der ein konfigurierbares Verhalten prüft, liest den Konfigurationsschlüssel, der dieses Verhalten steuert, und verzweigt auf dessen tatsächlichen Wert. `check_family_n_periods_homogeneity` konsumiert jetzt `deflation_heterogeneity_policy` und `deflation_stratum_id`/`deflation_stratum_n`/`deflation_stratum_n_periods_ratio` (letzteres neu in `confirm.py` exportiert — ohne die Ratio INNERHALB des gewählten Stratums ist `'per_stratum'` nicht prüfbar).

### 🟢 Pitfall #346 — Eine harte Grenze bekommt einen harten Wächter [Katalog #1003–#1022]
**Symptom:** `MAX_BARS_IN_TRADE_HARD_CAP` deckelt jede Suchraum-Bound als HARTE Obergrenze (21–27 h zulässig), aber `check_holding_time_cap` prüft nur `timebox_violating_trades_frac > 0.25` — eine 25-%-QUOTE. Eine Study mit 3991h Max-Haltedauer (190× über der Grenze) meldete keinen FAIL, weil die Verletzung nicht in der Systematik-Quote lag.
**Root-Cause:** Eine Grenze, deren Verletzung bis 25 % toleriert wird, ist keine Grenze — Quote (systematischer Modus) und Grenzverletzung selbst (hängende-Position-Modus) sind zwei verschiedene Fragen; keine ersetzt die andere.
**Fix/Regel:** Eine harte Grenze bekommt einen harten Wächter, unabhängig von jeder Quote. Zusätzlich gilt: ein Statuswort wie `run_status='complete'` ist ebenfalls eine Art Grenze zwischen "sauber" und "nicht sauber" — dieselbe Zensur-Logik (Pitfall #342) angewendet auf ein Statuswort statt eine Zahl: `sweep.py` unterscheidet seit #1016 `'complete'` von `'complete_with_blocking_invariants'`, statt beide Zustände unter demselben Wort zu verstecken. **`check_holding_time_cap`-Härtung selbst nicht in dieser Session umgesetzt** (#1009, Stufe 3 — erfordert Simulationsschicht-Änderungen und einen Kalibrierlauf).

### 🟡 Pitfall #347 — Ein Zensurmechanismus, dessen Anker von der Ankunftsreihenfolge abhängt, macht den Lauf trotz festem Seed nicht reproduzierbar [Katalog #1003–#1022]
**Symptom:** `sortino_numeric_guard_reference='family_median'` skaliert den Numerik-Guard mit dem LAUFENDEN Median bereits abgeschlossener Geschwister-Trials — bei `n_jobs=22` bestimmt der Scheduler die Zusammensetzung, ein identischer Parametervektor erhält je nach Startzeitpunkt ein gegenteiliges Urteil (Ankerdrift 320 → 1328.5 im Verlauf einer Study, 99.8 % einer Study zensiert).
**Root-Cause:** Ein Anker, der aus bereits abgeschlossenen Trials abgeleitet wird, ist selbst eine Funktion der (nicht-deterministischen) Ausführungsreihenfolge, nicht der Daten.
**Fix/Regel:** Schwellen werden VOR Trial 1 berechnet, in `study.user_attrs` eingefroren und versiegelt; alle Trials messen gegen denselben eingefrorenen Wert. **Nicht in dieser Session umgesetzt** (#1011, Stufe 2 — erfordert einen Trial-Purge und ist mit #1003/#1012 in zwingender Reihenfolge verzahnt).

### 🟡 Pitfall #348 — Fehlende Werte, deren Fehlen mit dem Ergebnis korreliert, sind kein Rauschen [Katalog #1003–#1022]
**Symptom:** `check_selection_statistic_availability` (blocking) lag bei den vier promoteten Kandidaten zwischen 0.26 und 0.74 (Schwelle 0.8); `check_selection_statistic_economic_bias` zeigte für dieselben Studies eine dramatische Effektstärke zwischen der Kohorte MIT und OHNE Statistik (`z` bis 9.5). Die Selektion ist damit faktisch eine Ein-Statistik-Entscheidung auf `oos_psr`, dessen Fehlen positiv mit der Rendite korreliert.
**Root-Cause:** DSR/PSR modellieren den Gewinner als Maximum über N AUSTAUSCHBARE Ziehungen — schliesst die Kohorte, aus der das Maximum gezogen wird, systematisch die profitablen Trials aus, ist die Austauschbarkeitsannahme verletzt und der SR₀-Anker weder konservativ noch antikonservativ, sondern nicht interpretierbar.
**Fix/Regel:** Vor jeder Selektion auf einer Statistik mit Ausfällen: ein Verteilungsvergleich verfügbar/fehlend, und bei Signifikanz kein Urteil über die Strategie. **Nicht in dieser Session umgesetzt** (#1010, Stufe 2).

### 🟢 Pitfall #349 — Eine Schwelle, die für einen Vollauf kalibriert wurde, muss für den Ein-Entitäten-Fall eine eigene Formulierung haben [Katalog #1003–#1022, BEHOBEN #1016]
**Symptom:** `fail_fast_min_symbols=2`/`fail_fast_min_offending_symbols=2` (gegen Vollläufe über 143 Symbole kalibriert, #877) machten die GESAMTE Fail-Fast-Probe bei einem Ein-Symbol-Lauf strukturell unerreichbar (`len(completed_symbols)` kann nie `>= 2` werden) — genau der Modus, in dem ein einzelnes Paar für den Kapitaleinsatz feingetunt wird, lief ohne jeden Schutz.
**Root-Cause:** `min_offending_symbols = 2` bei einem Symbol heisst „Schutz aus", nicht „Schutz mild" — eine Schwelle, die eine STREUUNG über mehrere Entitäten misst, ist bei genau einer Entität keine abgeschwächte, sondern eine unerreichbare Bedingung.
**Fix/Regel:** `sweep.py`: bei `n_symbols_planned == 1` wird `fail_fast_min_symbols` auf 1 erzwungen, und `sweep._fail_fast_systemic_verdict` ersetzt die Symbol-Streuungs-Schwelle durch eine STUDY-Quote (`fail_fast_min_offending_studies`/`fail_fast_min_offending_studies_frac`, neue `optimizer.json`-Keys, Default 3 bzw. 25 %) — bei Erreichen UNBEDINGT `policy='abort'` (kein Ausweichen auf „übrige Symbole", die es bei einem Ein-Symbol-Lauf nicht gibt). Zusätzlich: `sweep._downgrade_run_status_for_blocking_invariants` patcht `run_status` von `'complete'` auf `'complete_with_blocking_invariants'`, sobald der geschriebene Report mindestens eine `severity='blocking'`-FAIL trägt — kein Lauf mit blockierenden FAILs trägt mehr dasselbe Statuswort wie ein sauberer.

---

## Issue-Katalog #1003–#1022 — Promotionsintegrität, Simulationsschicht, Selektionsinferenz (GitHub-Issue #858, Sitzung 2026-08-11)

**Ausgangslage.** GitHub-Issue #858 legte einen zwanzigteiligen, in sechs Stufen gegliederten Konsolidierungskatalog #1003–#1022 vor: der referenzierte Lauf `bad826d1` meldete erstmals seit Beginn der Kampagne vier Promotionen, von denen laut Katalog KEINE auf Basis der vorliegenden Zahlen mathematisch tragfähig ist (Kennzahlen bis Faktor 15,6 inkonsistent, drei von vier Profit-Faktoren Zensurwerte am Cap, alle vier aus Studies, die eine blockierende Invariante oder `check_selection_statistic_economic_bias` verletzten). Ursache: `promotion_correction_mode='dsr_or_robust_pair'`, dessen Ersatzpfad fail-open war. Diese Session implementierte **Stufe 0** (vier Punkte, „ändern keine Entscheidung, sondern beenden die Fehldarstellung") **vollständig** sowie **Stufe 1** (drei Punkte, „confirm-only, kein Backtest nötig") **vollständig als Mechanismus** — sieben von zwanzig Katalogpunkten insgesamt. Die übrigen 13 Punkte (Stufen 2–6) erfordern einen Trial-Purge und/oder einen vollständigen Re-Run über die Symbolmatrix und blieben ausserhalb des Zeitbudgets dieser Session unimplementiert — nicht, weil ihre Prämissen falsch wären, sondern weil sie ohne einen echten Katalog-Sweep (dieses Environment hat keinen) nicht empirisch verifizierbar sind (dieselbe Zurückstellungs-Lektion wie #843/#932, siehe Pitfall #333).

### Umgesetzt in dieser Session

**Stufe 0 (kein Purge, kein Re-Run):**

**#1004 — `profit_factor_cap` zensiert Anzeige UND Entscheidung.** Siehe Pitfall #342. `profit_factor` bleibt bewusst UNVERÄNDERT (weiterhin gecappt — Zero-Regression auf jede kalibrierte Gate-/Reward-Schwelle, die diesen Wert konsumiert); die Zensur wird stattdessen additiv sichtbar gemacht (`profit_factor_censored`/`profit_factor_raw`, `PROFIT_FACTOR_DENOMINATOR_DEGENERATE`) und über eine neue blockierende Invariante (`check_censored_statistic_in_decision`) durchgesetzt, statt den Reward-/Gate-Massstab selbst zu verschieben. Diese Session weicht damit bewusst von der Issue-Formulierung „`profit_factor` bleibt unbegrenzt" ab — siehe Kommentar an der Berechnungsstelle (`backtest_runner.py`) für die Begründung.

**#1006 — Sweep-Promotion und Deployment-Gate verwenden verschiedene Kriterien; „Deploybar" ist irreführend.** `summary_de.py` Abschnitt 2.1 heisst jetzt „Promotionskandidaten … — noch NICHT deploybar" und zeigt je Zeile das tatsächliche Urteil von `deployment_gate.evaluate_deployment_eligibility` (`report._study_record` ruft dieselbe Funktion, die auch Phase 5 aufruft, für jeden Promotionskandidaten auf und persistiert `deployment_decision`). Neue Invariante `check_promotion_deployment_coherence` (severity `high`).

**#1013 — `check_family_n_periods_homogeneity` ist unter `per_stratum` invertiert.** Siehe Pitfall #345.

**#1016 — `fail_fast_min_symbols=2` deaktiviert alle Fail-Fast-Invarianten bei Ein-Symbol-Läufen.** Siehe Pitfall #349.

**Stufe 1 (confirm-only, kein Backtest nötig):**

**#1005 (P0, HEADLINE) — `dsr_or_robust_pair` ist fail-open.** Siehe Pitfall #343/#344. Der Modus-Default in `tournament.json` steht wieder auf `'conjunction'`.

**#1007 — Die Promotionsentscheidung kennt die Invariantenlage nicht.** `confirm.confirm_per_symbol_promotion` erhält ein neues optionales Argument `study_invariant_results`; ein `severity='blocking'`-FAIL setzt `holdout_passed=False` mit `REJECT_STUDY_INVARIANT_BLOCKING` — ein unbedingter Hard-Stop wie `REJECT_HOLDOUT_GATE`, den auch `dsr_or_robust_pair` nicht unterlaufen kann. `deployment_gate.DEPLOYMENT_CLAUSES` bekommt eine neunte Klausel `study_invariants_clean` (fail-closed: fehlt `blocking_invariant_names` im Promotion-Record, gilt die Klausel als nicht überprüft, NICHT als sauber). **Bewusst NICHT umgesetzt:** die Live-Verdrahtung in `sweep.run_per_symbol_sweep` (tatsächliche Invarianten-Vorberechnung VOR dem Confirm-Aufruf jeder Study) — Invarianten sind heute weiterhin ein Report-Nachlauf über bereits exportierte Proposals (`build_probe_report`), sodass eine Vorberechnung VOR `confirm()` eine Restrukturierung der Dispatch-Reihenfolge in `sweep.py` erfordert hätte (dieselbe Klasse Risiko wie die in Pitfall #333/#932 dokumentierte Zurückstellung). Der Mechanismus ist vollständig implementiert und getestet; ein künftiger Aufrufer (Live-Sweep ODER ein manueller „confirm-only"-Nachlauf auf archivierten Studies, wie im Katalog als Stufe-1-Erkenntnisgewinn beschrieben) kann ihn sofort nutzen.

**#1015 Punkt 1 — Budgetausführung 362 %: die Studypopulation ist nicht die Laufpopulation.** `make_symbol_objective` stempelt seit diesem Fix jeden neu erzeugten Trial mit `user_attrs['run_id']` (durchgereicht von `sweep.run_per_symbol_sweep` über `optimize_symbol`/`_optimize_symbol_impl`); `run_optimization.compute_budget_execution` erhält ein neues optionales `run_id`-Argument und zählt bei GESETZTEM Wert ausschliesslich Trials mit passendem Stempel, während `n_trials_total_study` unverändert die volle SQLite-Historie bleibt (`None`/fehlender Stempel ⇒ bit-identisches Alt-Verhalten). **Bewusst NICHT umgesetzt:** die beiden `floor_plateau_callback`-internen Diagnose-Aufrufe (`run_optimization.py`, Zeilen um #778/#822 herum) reichen `run_id` noch nicht durch — diese speisen die Plateau-Eskalationsdiagnose, nicht die persistierte Budget-Telemetrie, und blieben aus Zeitbudget-Gründen ausserhalb des engeren #1015-Punkt-1-Scopes. Wie bei #1011/#1013 gilt: die Rückwirkung auf BEREITS archivierte Läufe (z. B. `bad826d1`) ist strukturell unmöglich, da deren Trials nie mit `run_id` gestempelt wurden — der Fix wirkt erst für künftige Läufe.

### Offen — nicht in dieser Session bearbeitet

**Stufe 2 (Metrik-Integrität, Purge erforderlich):** #1003 (Expectancy-Neudefinition als Verhältnis der Summen, Pitfall #340/#341), #1012 (Perioden-Skala statt Annualisierung als Inferenz-Grundlage), #1010 (PSR-Verfügbarkeit/MCAR-Verletzung, Pitfall #348), #1011 (Guard-Referenz-Einfrierung vor Trial 1, Pitfall #347). Reihenfolge laut Katalog zwingend (#1003 vor #1012 vor #1010/#1011).

**Stufe 3 (Simulationsschicht, Purge + Kalibrierlauf erforderlich):** #1008 (`exit_reason`-Aufschlüsselung für `check_effective_stop_distance`), #1009 (Zeitbox als harter statt weicher Wächter, Pitfall #346 — muss #1008 folgen).

**Stufe 4 (Selektion/Ertrag/Risiko):** #1014 (De-facto-Ein-Gate-Selektion), #1018 (Sizing-Parität Backtest vs. Live-Allocator — grösster Hebel ohne Optimizer-Re-Run), #1019 (Information Ratio statt Excess/Exposure), #1020 (CVaR/Expected-Shortfall-Nebenbedingung), #1021 (Suchraum-Bounds gegen Datenfenster deckeln).

**Stufe 5 (Governance):** #1017 (Champion-Closed-Loop, zehnte Wiederkehr), #1015 Punkte 2–4 (Semantik-Versions-Assertion beim Study-Laden, Kohortenfilterung nach Semantikstand, `check_counter_partition_consistency` auf `blocking`).

**Stufe 6 (letzte Aktion):** #1022 (Dreifach-Semantik-Bump + symmetrischer Purge + Vollauf) — setzt voraus, dass mindestens Stufe 2/3 abgenommen sind.

Alle referenzierten Mechanismen dieser offenen Punkte (`invariants.check_selection_statistic_availability`, `check_annualization_commensurability`, `check_holding_time_cap`, `deflation.py`, `bootstrap.py`, `champions.py`) existieren bereits auf `main`; wer diese Punkte aufgreift, startet auf derselben, in dieser Session bereits verifizierten Grundlage.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #1003–#1022)
- `tournament.json.promotion_correction_mode` — zurück auf `'conjunction'` (Pitfall #343, #1005); der Eintrag stand entgegen der eigenen dokumentierten Kalibrierentscheidung (#667/#678) auf `'dsr_or_robust_pair'`.
- `optimizer.json.fail_fast_min_offending_studies` (Default 3) — Ein-Symbol-Ersatz für `fail_fast_min_offending_symbols`, Pitfall #349, #1016.
- `optimizer.json.fail_fast_min_offending_studies_frac` (Default 0.25) — Geschwister-Schwelle als Anteil, #1016.

### 🔒 Watertight Invariants (Issue-Katalog #1003–#1022) — für künftige Agenten
- **`invariants.check_censored_statistic_in_decision`** (`invariants.py`, #1004) — blockierend: kein `promote=True`-Kandidat darf ein `*_censored`-Flag in seinen `holdout`-Metriken tragen (generisch über JEDES `*_censored`-Suffix-Feld, nicht nur `profit_factor_censored`).
- **`invariants.check_promotion_deployment_coherence`** (`invariants.py`, #1006) — severity `high`: ein `READY_FOR_PR`/`PROMOTE_GLOBAL_DEFAULT`-Kandidat, dessen `deployment_decision.admitted` nicht `True` ist, muss sichtbar sein, nicht implizit unter „Deploybar" verschwinden.
- **`confirm.confirm_per_symbol_promotion`** (`confirm.py`, #1005) — `promotion_correction_route`/`promotion_correction_pbo_ok`/`promotion_correction_ci_lower`/`promotion_correction_alpha_effective` werden IMMER exportiert, sobald der `dsr_or_robust_pair`-Ersatzpfad betreten wurde (auch wenn er am Ende nicht reinstated) — eine über diesen Pfad reinstatete Promotion ist sonst nicht auditierbar.
- **`deployment_gate.DEPLOYMENT_CLAUSES`** (`deployment_gate.py`, #1007) — jetzt NEUN Klauseln (vorher acht, #993); `study_invariants_clean` ist fail-closed bei fehlendem `blocking_invariant_names`-Feld im Promotion-Record — „nicht überprüft" ist keine bestandene Klausel.
- **`sweep._fail_fast_systemic_verdict`/`sweep._downgrade_run_status_for_blocking_invariants`** (`sweep.py`, #1016) — reine, unit-getestete Entscheidungsfunktionen statt Inline-Logik in `main()`; jede künftige Änderung an der Ein-Symbol-Fail-Fast-Semantik oder der `run_status`-Korrektur sollte hier ansetzen, nicht die Dispatch-Schleife selbst anfassen.
- **`run_optimization.compute_budget_execution`** (`run_optimization.py`, #1015) — `n_trials_total_study` UND `n_trials_completed` sind IMMER beide im Rückgabedict vorhanden; eine grosse Lücke zwischen beiden ist das direkte Signal für eine ungepurgte Study (Pitfall #349-Verwandtschaft: ein Zähler ohne Lauf-Scope zählt strukturell zu viel).

---

### 🟢 Pitfall #369 — Ein Verhältnis-Check braucht beide Schranken [Katalog #866-2]
**Symptom:** `check_effective_stop_distance` prüfte nur `ratio >= min_ratio` (Stop zu nah an der ATR) — eine Stopdistanz, die um Faktor 10+ über der ATR lag (praktisch kein Stop mehr), blieb unentdeckt PASS. Zweite Wiederkehr derselben Fehlerklasse wie `check_reward_dynamic_range` (#1055, dort bereits mit drei Klauseln inkl. Ratio-Obergrenze gehärtet).
**Root-Cause:** Eine einseitige Schwelle macht die nicht geprüfte Richtung zur blinden Flanke — sie wird nie durch einen Test widerlegt, weil kein Test sie je verletzt.
**Fix/Regel:** `check_effective_stop_distance` bekommt einen neuen Parameter `max_ratio` (`optimizer.json["stop_distance_max_ratio"]`, Default 10.0) zusätzlich zu `min_ratio`; `actual` trägt immer ALLE gemessenen Ratios (nicht nur die Offender einer Richtung), damit beide Flanken aus demselben Report rekonstruierbar sind (#1070).

### 🟢 Pitfall #370 — Ein Fail-Fast-Check muss die actual-Pair-Konvention erfüllen [Katalog #866-2]
**Symptom:** `check_holding_time_cap` FAILte mit Offendern ausschliesslich im `detail`-Fliesstext bzw. in `provenance`; `_offending_pairs_for_fail_fast_check` (die Fail-Fast-Mechanik) kann diese Struktur nicht parsen und fällt auf den konservativen globalen Abbruch zurück — genau der Breitenschwellen-Schutz (#877/#975), den die feingranulare Quarantäne ersetzen sollte, greift dann nicht.
**Root-Cause:** Ein Check, der in `fail_fast_invariants` eingetragen werden kann, ohne dass irgendetwas seine `actual`-Struktur gegen die `{"strategy/symbol": value}`-Konvention prüft, kann diese Konvention unbemerkt verletzen.
**Fix/Regel:** `check_holding_time_cap` merged Magnituden- und Anteils-Offender in EIN pair-keyed `actual`-Dict; `_offending_pairs_for_fail_fast_check` bekommt einen `provenance.magnitude_offenders`-Fallback; neue Invariante `check_fail_fast_actual_convention` prüft die Konvention selbst für jeden in `fail_fast_invariants` gelisteten Check (#1063).

### 🟢 Pitfall #371 — Automatische Suchraum-Weitung ohne Domänenklammer divergiert [Katalog #866-2]
**Symptom:** `_widen_bounds_toward` verschob eine Untergrenze bei jedem Lauf um denselben Betrag ohne Bodenprüfung — nach mehreren Läufen entstanden negative Perioden/Bar-Anzahlen im `search_space_overrides`-Cache (Beweis: `ema_period: [-325.0, 300]`).
**Root-Cause:** Ein Rückschrieb-Mechanismus, der bei jedem Lauf denselben Betrag addiert, erreicht nach `k` Läufen `lo₀ − k·Δ` — ohne eine Domänenklammer gibt es keine Untergrenze für die Untergrenze.
**Fix/Regel:** Neues `spaces._PARAM_DOMAIN_REGISTRY` (min/max/dtype je Parametername) plus `spaces.clamp_param_bounds`, das NUR ausserhalb der Domäne liegende Werte klammert (In-Domain-Werte bleiben bit-identisch); `sweep_diagnostics._widen_bounds_toward` klammert jeden Vorschlag; zusätzlich ein `_MAX_WIDEN_APPLICATIONS`-Zähler (Default 2) je (Strategie, Symbol, Parameter), der weitere Weitungen über den zuletzt übernommenen Wert hinaus verweigert; `migrate_search_space_override_cache` migriert bereits bestehende Cache-Einträge einmalig (#1066).

### 🟢 Pitfall #372 — Ein Hard-Cap ist keine Bandgrenze [Katalog #866-2]
**Symptom:** `MAX_BARS_IN_TRADE_HARD_CAP` schützt nur nach oben; ein Automatismus, der auch Untergrenzen bewegen kann (#1066), hatte keine symmetrische Schranke gegen eine zu klein gewordene Haltedauer-Untergrenze.
**Root-Cause:** Eine Konstante, die nur eine Richtung eines Wertebereichs deckelt, wird stillschweigend zur EINSEITIGEN Bandgrenze, sobald derselbe Mechanismus beide Richtungen bewegen kann.
**Fix/Regel:** Neue, symmetrische Konstante `_contracts.MIN_BARS_IN_TRADE_FLOOR = 4` neben `MAX_BARS_IN_TRADE_HARD_CAP = 24`, konsumiert von `spaces.clamp_param_bounds`/`_PARAM_DOMAIN_REGISTRY` (#1067).

### 🟢 Pitfall #373 — Eine Invariante, die ihren eigenen Lauf-Output liest, ist selbstreferenziell [Katalog #866-2]
**Symptom:** `check_coverage_ledger_continuity`s `has_prior_reports` scannte ein Report-Verzeichnis, in das der AKTUELLE Lauf bereits schreibt — je nach Auswertungszeitpunkt (vor/nach dem eigenen Report-Schreiben, siehe Pitfall #379) lieferte derselbe Messwert PASS oder FAIL.
**Root-Cause:** Eine Prüfung, die ihre eigene Vollständigkeitsaussage aus einem Verzeichnis ableitet, das sie selbst gerade befüllt, misst den eigenen Fortschritt, nicht den Zustand VOR diesem Lauf.
**Fix/Regel:** `check_coverage_ledger_continuity` bekommt einen expliziten `coverage_bootstrap_phase`-Parameter (vom Aufrufer VOR dem eigenen Report-Schreiben gesetzt) statt eine Selbstreferenz zu erraten; `total_runs_started`/`has_prior_reports` werden dadurch für JEDE Auswertungswelle desselben Laufs identisch (#1064).

### 🟢 Pitfall #374 — Ein Zähler, der numerisch mit n_eligible übereinstimmt, zählt vermutlich die Bestandenen [Katalog #866-2]
**Symptom:** Ein Gate-Marginal-Delta von `0` wurde als „Gate wirkungslos, kann entfernt werden" gelesen, ohne gegen die tatsächliche Rejection-Aufschlüsselung (`is_rejection_detail_counts`) kreuzgeprüft zu sein — ein deaktiviertes ODER nie greifendes Gate sieht in der reinen Delta-Zahl identisch aus.
**Root-Cause:** `marginal_delta == 0` ist mit MEHREREN Ursachen kompatibel (Gate deaktiviert, Gate nie bindend, Gate redundant zu einem anderen); ohne Kreuzprüfung gegen die Rejection-Rohzahlen entscheidet die Auswertungsreihenfolge, welche Ursache angenommen wird.
**Fix/Regel:** Neue Invariante `check_gate_inventory_coherence` (kein deaktiviertes Gate darf als `binding_gate` erscheinen); `check_gate_marginal_contribution` bekommt einen `gate_consolidation_protected`-Parameter, der eine Gate-Entfernungsempfehlung explizit sperrt, bis das Inventar kohärent ist (#1076).

### 🟢 Pitfall #375 — Argmin über rohe, dimensionsbehaftete Deltas ist ein Einheiten-Artefakt [Katalog #866-2]
**Symptom:** `_holdout_binding_gate` wählte das „bindende Gate" per Argmin über rohe Gate-Deltas verschiedener natürlicher Skalen (z. B. `oos_min_excess_return` in Prozentpunkten gegen `oos_min_psr` in Sortino-Einheiten) — das Gate mit der kleinsten natürlichen Skala gewann strukturell, unabhängig davon, wie knapp es tatsächlich war. Wiederkehr von Pitfall #631 in neuer Umgebung.
**Root-Cause:** Ein Argmin über dimensionsbehaftete Grössen ist keine Attribution, sondern ein Einheiten-Vergleich — „das bindende Gate" ist nur eine sinnvolle Aussage über NORMIERTE (skalenfreie) Distanzen.
**Fix/Regel:** Neues `reward.normalize_gate_deltas_for_binding` normiert jedes Delta gegen seine konfigurierte Schwellen-Skala (Fallback-Skala `1.0`, NICHT `abs(delta)` — letzteres kollabiert jede Magnitude auf `±1` und zerstört genau die Rangordnung, die normiert werden soll); `confirm._holdout_binding_gate`/`_holdout_tightest_margin` konsumieren die normierten Deltas, gefiltert auf die AKTIVEN Gates (`_active_holdout_gate_deltas`, permissiver Fallback ohne `eligible_requires_all`-Konfiguration, #1074/#1075).

### 🟢 Pitfall #376 — Ein Ersatznenner darf nie ungekennzeichnet in eine Ergebnistabelle [Katalog #866-2]
**Symptom:** `summary_de._EXPOSURE_EPSILON` setzte `max(exposure, 0.01)` ein, sobald `exposure` fehlte oder winzig war — die Strategie mit 0 Trades erzeugte dadurch die GRÖSSTE Zahl der Tabelle (`excess / 0.01`).
**Root-Cause:** Ein erfundener Ersatznenner ist mit einem echten, kleinen Nenner numerisch ununterscheidbar, sobald er in dieselbe Spalte geschrieben wird — der Leser kann „real, aber klein" nicht von „Artefakt" trennen.
**Fix/Regel:** `_EXPOSURE_EPSILON` entfernt; §2.3 splittet in `normal_rows` (echter, ausreichender Nenner) und einen eigenen „Nicht bewertbar"-Block (zu geringe/fehlende Exposition) — kein Epsilon-Ersatz, sondern eine explizite Nichtaussage (#1077).

### 🟢 Pitfall #377 — Geprunte Trials gehören in keinen Verfügbarkeits-Nenner [Katalog #866-2]
**Symptom:** `n_evaluable` zählte Trials mit `oos_evaluated=True` im (VOR dem Prune geschriebenen) `user_attrs`-Snapshot mit, obwohl deren Optuna-`TrialState` `PRUNED` war — ein Trial, der den Reward-Pfad nie vollständig durchlief, füllte trotzdem den Verfügbarkeits-Nenner eines blockierenden, fail-fast-verdrahteten Checks.
**Root-Cause:** Ein Trial, der per Konstruktion (Pruning VOR der Auswertung) keine Selektionsstatistik tragen kann, erzeugt in jedem Nenner, der ihn trotzdem zählt, einen garantierten Fehlalarm — unabhängig von der tatsächlichen Datenlage.
**Fix/Regel:** `n_evaluable` (`report._study_record`) schliesst `TrialState.PRUNED` explizit aus; `check_denominator_coherence` bekommt eine zweite, `n_evaluable`-basierte Identität zusätzlich zur bestehenden, damit beide Nenner-Definitionen gegeneinander geprüft werden (#1079).

### 🟢 Pitfall #379 — Eine mehrfach ausgewertete Invariantensuite macht die Reihenfolge zum Verdikt [Katalog #866-2]
**Symptom:** Dieselbe Invariantensuite lief pro Lauf VIERMAL (Symbol-Fortschritts-Probe, Zwischenreport, Fail-Fast-Probe, finaler Artefakt-Schreiber) — vier zeitlich getrennte `INVARIANT_CHECK_FAILED`-Wellen, aber nur EINE davon entsprach dem tatsächlich persistierten `run.json`; sobald eine Eingabe vom eigenen Lauf abhing (Pitfall #373), trugen verschiedene Wellen verschiedene Verdikte für denselben Check-Namen.
**Root-Cause:** Eine Suite, die mehrfach je Lauf ausgewertet wird, OHNE dass jede Auswertung ihre eigene Rolle (Zwischenstand vs. persistiertes Ergebnis) trägt, macht das Verdikt von der Auswertungsreihenfolge abhängig statt vom Lauf-Zustand.
**Fix/Regel:** Neuer `report_source`-Parameter (Default `'final'`) durchgereicht durch `_build_report`/`build_probe_report`/`generate_sweep_report`; jede Auswertung markiert sich selbst im `INVARIANT_CHECK_FAILED`-Event UND im Report (`invariant_evaluation_source`) — ein Log-Konsument kann die Wellen einander zuordnen, statt scheinbar widersprüchliche Ergebnisse ohne Herkunft zu sehen. Kombiniert mit Pitfall #373s Fix tragen alle Wellen inzwischen ohnehin dasselbe Verdikt; `report_source` bleibt die zusätzliche Diagnose-Spur für eine künftige, andere lauf-abhängige Eingabe (#1083, ZUSAMMEN mit #1064 derselbe Fix, dieselbe Abnahme).

### 🟢 Pitfall #380 — Aus written_back == 0 folgt nicht „keine Call-Site" [Katalog #866-2]
**Symptom:** `check_champion_writeback_reachability` behauptete UNBEDINGT „`maybe_write_back` ohne Produktions-Call-Site" (#818-Diagnose), sobald `stored > 0` und `written_back == 0` — obwohl das Sweep-Log 14 `CHAMPION_WRITEBACK`-Events zeigte (die Call-Site feuert nachweislich): 12× `STORE_EMPTY` (tatsächlich pair-skopiert falsch benannt, siehe unten), 2× `NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED` bei `corroboration_count = 1` gegen eine Schwelle von 2 — der wahre Blocker war ein Korroborations-Deadlock (Ledger `total_runs_started = 1`, Pitfall #373), keine fehlende Call-Site. Dritte Wiederkehr dieser Fehlerklasse (nach #1052, #1071).
**Root-Cause:** Eine Meldung, die aus `written_back == 0` eine SPEZIFISCHE Ursache ableitet, obwohl eine Telemetrie existiert, die die tatsächliche Ursache bereits trägt (`CHAMPION_WRITEBACK`-Events mit `skipped_reason`), rät statt zu messen — und die pair-skopierte `_champion_path`-Datei-pro-Paar-Struktur wurde zusätzlich store-weit benannt (`'STORE_EMPTY'` für „kein Eintrag für DIESES Paar", nicht „der Store ist leer").
**Fix/Regel:** `champions.load_champion_entry_with_reason` trennt `'STORE_EMPTY'` (Store insgesamt leer) von `'NO_ENTRY_FOR_PAIR'` (Store gefüllt, kein Eintrag für dieses Paar); `report._champions_summary(opt_data, studies_out=...)` zählt `attempts` (Versuche, hier 14) statt nur der Store-Einträge (hier 2) und ersetzt das tautologische `'NOT_WRITTEN_BACK'` durch den granularen Grund; `check_champion_writeback_reachability` meldet die BEOBACHTETE `skipped_by_reason`-Verteilung, „keine Call-Site" gilt nur noch bei explizit `attempts == 0`; neue Invariante `check_champion_corroboration_reachable` benennt den Korroborations-Deadlock direkt (FAIL bei `max(corroboration_count) < Schwelle` UND `total_runs_started == 1`, #1084).

### 🟡 Pitfall #381 — Ein Filter, der nur an einer Konsumstelle sitzt, schützt nur diese [Katalog #866-2]
**Symptom:** Dust-Round-Trips (Notional zwischen 4,26e-14 und 9,90e-13, Fliesskomma-Residuen aus Scale-in/Scale-out) füllten jeden gepoolten Zähler ausser der Expectancy — bis 11,80 % einer Study (Donchian), 9,59 % (Rsi2). Der 5-%-Median-Notional-Boden (#1031) schützt ausschliesslich `_expectancy_winsorized`/`_expectancy_capital_weighted`.
**Root-Cause:** Ein Filter, der an genau EINER Konsumstelle angewendet wird, verhindert nicht, dass dieselben degenerierten Einheiten JEDEN ANDEREN gepoolten Nenner erreichen (`oos_total_trades_with_exit_telemetry`, `exit_reason_histogram`, `timebox_violating_trades_denominator`) — degenerierte Einheiten gehören an die QUELLE, nicht an jeden einzelnen Verbraucher.
**Fix/Regel:** Neue Invariante `check_dust_round_trip_share` (severity high, Anteil je Study <= 1 %) macht den Anteil sichtbar und stempelt `dust_round_trips_filtered`/`oos_expectancy_notional_degenerate_count` als Telemetrie (#1085). **Der eigentliche Quellfix (5-%-Boden direkt in der Round-Trip-Extraktion, `backtest_runner.extract_metrics`/die FIFO-Positions-Matching-Logik) ist in dieser Session bewusst NICHT umgesetzt** — das ist die höchstriskante P&L-Aggregationsstelle des Systems, eine Änderung daran erfordert einen `simulation_semantics_version`-Bump mit Pflicht-Purge und eine Verifikation gegen echte Marktdaten (in dieser Sandbox ohne installierbares `nautilus_trader` nicht möglich). Diese Session liefert ausschliesslich die additive Sichtbarkeit; der Quellfix bleibt als eigener, sorgfältig gegen Backtest-Regressionen zu verifizierender Folgeauftrag offen.

### 🟢 Pitfall #378 — Ein Kostenstress muss die vollen Round-Trip-Kosten stressen [Katalog #866-2]
**Symptom:** `_expectancy_cost_stress` skalierte nur die Kommission (1,0 bps) mit dem Stress-Faktor, während der Spread (3,0 bps) fest blieb — ein nominell „2×"-Stress war bei einem Kosten-Split von 1:3 faktisch nur ein `+25 %`-Stress; die darauf gestützte `expectancy_round_trip_cost_stress_2x`-Deployment-Klausel mass damit nicht, was ihr Name behauptet.
**Root-Cause:** Ein Stress-Multiplikator, der nur den KLEINEREN Kostenbestandteil skaliert, verwässert sich selbst proportional zum Anteil des NICHT skalierten Bestandteils an den Gesamtkosten.
**Fix/Regel:** `_expectancy_cost_stress`s Parameter `commission_bps` wird zu `round_trip_cost_bps` (skaliert die VOLLEN Round-Trip-Kosten, Kommission + Spread); `deployment_gate._clause_cost_stress` bevorzugt die neuen `expectancy_round_trip_cost_stress_{1_5x,2x}`-Schlüssel mit Fallback auf die alten (ein Fallback-Kalenderjahr, danach entfernbar); `parsing.py` liest dieselben neuen Schlüssel bevorzugt (#1081).

---

## Issue-Katalog #1023–#1042 — TSLA-Preisreihenanomalie, Report-Kohortenmischung, Messapparat-Härtung (Katalog #866/#873, GitHub-Issues #874–#893, Sitzung 2026-08-12)

**Ausgangslage.** Der Abweichungs-Katalog #866 (inhaltsgleich dupliziert unter #873) prüfte vier Läufe (`34b99e6e`, dreimal `8a59f96d`) gegen den damaligen Stand (`AGENTS.md` bis Pitfall #349, höchste Issue-Referenz #1022) und fand: der Report des Laufs `34b99e6e` mischte 98 Studies vom Vortag in die Ergebnistabellen (#1023); die menschenlesbare Zusammenfassung liess sich aus dem beigelegten Report nicht bit-identisch regenerieren, inklusive Vorzeichenwechsel beim TSLA-Buy&Hold (#1024); die dominante gemeldete Abbruchursache `EXCEPTION` (66 % der Studies) erwies sich als Zählfehler ohne echten Absturz (#1026); und drei Kandidaten mit dreistelligem Holdout-Ertrag (VwapExhaustion/TSLA 162,2 %, FlashCrashReversal/TSLA 136,6 %, ComboTrendVwap/TSLA 98,0 %) stellten sich als arithmetisch unmöglich heraus — Symptom einer TSLA-Preisreihenanomalie (#1028, HEADLINE). Über alle 82 nicht-TSLA-Holdout-Studies blieb die reale Ökonomie schwach (Median-Holdout-Return 0,17 % über 45 Tage, Median-Expectancy 3,63 bps gegen 1 bps Kommission, 43/82 = 52,4 % unter Buy & Hold). Der Katalog gliederte 20 Punkte in fünf Kohorten (A: Report-/Artefakt-Integrität, B: TSLA-Datenintegrität, C: Messapparat, D: Governance/Suchhaushalt, E: Ertrag/Risiko) mit verbindlicher Merge-Reihenfolge und Sperrvermerk gegen Kapitaleinsatz auf einem TSLA-Kandidaten. Zwei Pull-Requests setzten den Katalog um: **PR #867** implementierte Kohorten A–C vollständig (17 der 20 Punkte); **PR #894** verifizierte diese Arbeit erstmals gegen eine vollständige `nautilus_trader`-Umgebung (zuvor nie gegen eine komplette Installation getestet — Ergebnis: alle 17 Punkte bestätigt korrekt) und schloss die verbliebenen Lücken (#1024, #1028, #1038, #1040, #1041, #1042 teilweise).

### Umgesetzt (PR #867, PR #894 — Sitzungen 2026-08-12/13)

**Kohorte A — Report- und Artefaktintegrität:**

**#1023 (P0, HEADLINE) — Lauf-Report enthielt Studies fremder Läufe.** `report.generate_sweep_report` enumerierte alle Studies des SQLite-Stores statt der Studies dieses Laufs (der `run_id`-Filter aus #1015 wirkte nur innerhalb von `compute_budget_execution`, nicht bei der Study-Auswahl). Fix: Studies werden auf `user_attrs['run_id'] == run_id` gefiltert, ausgeschlossene Fremd-Studies landen in einem eigenen Report-Feld `studies_excluded_foreign_run`; eine leere gefilterte Menge bei nicht-leerem Store ist fail-loud. Neue blockierende Invariante `check_report_cohort_coherence`. Dies ist die unmittelbare Vorstufe zu #1086/#1087/#1088 dieser Sitzung — dort erwies sich derselbe Mechanismus als zeitfenster- statt identitätsbasiert und damit bei echter Nebenläufigkeit weiterhin lückenhaft (Pitfall #382/#383).

**#1024 (P0) — Zusammenfassung und Report desselben `run_id` widersprachen sich** (219 Diff-Zeilen bei Regeneration, inkl. TSLA-Buy&Hold-Vorzeichenwechsel). Root-Cause blieb zum Zeitpunkt von PR #867 offen (zwei Kandidaten: divergierende Regenerationsläufe vs. degradierter Teilschrieb mit `started_at_utc=None`). **PR #894 schloss die Lücke:** die betroffenen committeten `.md`-Dateien stammten von einer Pipeline-Ausführung vor bzw. ohne den `report_sha256`-Fix und wurden über `write_german_summary_for_report_path` neu regeneriert; ein CI-Gate prüft seither die bit-identische Regenerierbarkeit jeder committeten `logs/run_*.json`.

**#1025 (P0) — `run_id`-Stempel erreichte die Trials nicht** (`n_trials_completed=0` bei `n_trials=n_trials_budgeted`). `make_symbol_objective` setzte den Stempel nur bei `run_id is not None`, der Wert wurde auf dem betroffenen Pfad nicht durchgereicht. Fix: lückenlose Durchreichung `sweep.run_per_symbol_sweep → _optimize_symbol_impl → make_symbol_objective`, fehlender Wert wird zum `ValueError` statt zum stillen `None`-Default; neue Invariante `check_run_id_stamp_coverage`.

**#1026 (P0) — `stop_reason=EXCEPTION` benannte eine nie gemessene Ursache** (74/112 Studies, keine davon mit Stacktrace oder Exception-Zähler; eine Study mit exakt ausgeführtem Budget 280/280 wurde trotzdem als abgestürzt gemeldet). `compute_budget_execution` leitete `EXCEPTION` als `else`-Zweig einer Budget-Fallunterscheidung ab. Fix: `study.optimize(..., catch=...)` zählt tatsächlich gefangene Exceptions in `n_trials_exception`/`exception_types`; `EXCEPTION` nur noch bei `n_trials_exception > 0`, sonst `UNKNOWN_INCOMPLETE`.

**#1027 (P0) — `compute_budget_execution` an 2 von 5 Aufrufstellen mit `run_id` gefixt, 3 nicht** — Promotionsroute (`confirm.py`) und Denylist-/Override-Rückschrieb (`run_optimization.py`, zwei Stellen) entschieden auf der ungefilterten Mehr-Lauf-Zahl (Beispiel: ComboTrendVwap/TSLA `0,2179` im Study-Record gegen `0,875` in `diagnosed_pairs`). Fix: `run_id` wird zum Pflichtparameter (keyword-only, kein Default) an allen fünf Aufrufstellen; neue Invariante `check_budget_metric_single_source`.

**#1029 (P0) — `snapshot_drift`-blockierte Kandidaten erschienen in der Promotionstabelle trotzdem als „Promotion".** `_section_1_result_in_one_sentence` zählte `promotion_outcome_counts` ohne Rücksicht auf `deployment_decision.admitted`. **In PR #894 gefixt** (ursprünglich in PR #867 als offener Punkt dokumentiert): Abschnitt 1 der deutschen Zusammenfassung meldet seither getrennt `n_promotions_sweep` und `n_deployable`; bei `snapshot_drift=false` erscheint der Kandidat nicht mehr in Abschnitt 2.1, sondern in einer neuen Sektion „2.1b Quarantäne — Datenintegrität".

**#1030 (P1) — Zensiertes Profit-Faktor-Rohmass erreichte den Bericht nie**, weil `holdout_metrics` für Proposals ohne `symbol`-Route ein leeres Dict war und die Kette (`backtest_runner.py` → `parsing.py` → `confirm.py` → `report.py`) still abbrach; zusätzlich verglich der Zensur-Test `profit_factor_raw == profit_factor_cap` mit `>` statt `>=`. Fix: `>=`-Vergleich, Fallback auf die `global`-Route mit gestempelter `holdout_route`, zusätzliche Konsistenzprüfung in `check_censored_statistic_in_decision`.

**#1038 (P2) — `worker_utilisation` war keine Auslastung** (151,8 %/246,5 %/332,9 % über drei Läufe) — Zähler enthielt Studies fremder Läufe (#1023) und sich überlappende, verschachtelte Worker-Pools je Study. **In PR #894 gefixt:** umbenannt in eine Grösse mit Docstring, der die Überlappung benennt; neue Invariante `check_worker_utilisation_plausible`.

**Kohorte B — TSLA-Datenintegrität:**

**#1028 (P0, HEADLINE) — Die drei TSLA-Kandidaten waren arithmetisch unmöglich.** Drei unabhängige Rechnungen (gemeldete Expectancy vs. aus Equity implizierte vs. aus PF/WR/Bruttoverlust implizierte) widersprachen sich um ein bis zwei Grössenordnungen; der implizite Sizing-Anteil lag bei 0,93–1,02 % gegen konfigurierte 15 %. Führende Hypothese: eine Sprungstelle/Snapshot-Naht in der TSLA-Preisreihe (`atr_median_bps` 366/308 gegen 19/24 auf demselben Symbol; alle drei Kandidaten mit `snapshot_drift=false`; einziges Symbol mit Buy&Hold-Vorzeichenwechsel zwischen `.md` und `.json`). Sofortmassnahme statt Ursachenbehebung: zwei neue Invarianten, **`check_sizing_identity_coherence`** (blocking — `|ln(1+TR)/(n·expectancy) − trade_amount_pct|` muss innerhalb 35 % relativer Toleranz bleiben) und **`check_atr_scale_homogeneity`** (high — ATR-Spannweite je Symbol über Strategien ≤ 6,0×), plus Quarantäne betroffener Kandidaten aus der Promotionstabelle (siehe #1029). Die eigentliche Datenkorrektur/Symbolsperre blieb offen — kein Kapitaleinsatz auf TSLA, solange ungeklärt.

**Kohorte C — Messapparat:**

**#1031 (P1) — Expectancy war ein Mittel von Quotienten ohne Nennerboden/Winsorisierung** (`statistics.mean(pnl_i/notional_i)`, unbeschränkt empfindlich gegen einen einzelnen degenerierten Nenner). Fix: kapitalgewichtete Variante `expectancy_capital_weighted = Σpnl/Σnotional`, zusätzlich `expectancy_winsorized` (5/95-Perzentil) und ein Nennerboden bei 5 % des Median-Notionals mit `EXPECTANCY_NOTIONAL_DEGENERATE`-Diagnose für verworfene Trades; neue Invariante `check_expectancy_definition_coherence`. Nachträgliche Registrierung des Diagnose-Codes in Commit `6bfb918` (eigener Follow-up-Fix).

**#1032 (P2) — `rt_notional` summierte wiederverwendetes Kapital** bei pyramidisierten Round-Trips (Teilausstiege mit Nachkauf zählten mehrfach). Fix: zusätzliches Feld `rt_notional_peak` (Spitzenbestand statt Summe), von `expectancy_capital_weighted` konsumiert.

**#1033 (P1) — Diagnose-Raten konnten 1 überschreiten** (bis 120,2 %) — Kategorienfehler: Zähler zählte Diagnose-**Ereignisse** (mehrere je Trial), Nenner zählte **Trials**. Fix: zusätzliche trial-distinkte Variante `inference_diagnostics_trials_by_code`, Assertion `actual <= 1.0` in beiden konsumierenden Checks.

**#1034 (P1) — 54–69 % der Exits waren `UNKNOWN`; drei `ExitReason`-Enum-Werte hatten keine Setzstelle** (`SIGNAL_REVERSAL`, `PROFIT_TARGET`, `EQUITY_STOPOUT` deklariert, nie gesetzt). Fix: alle drei Werte an ihren jeweiligen Ausstiegspfaden gesetzt, neuer Wert `DATA_END` für am Datenende erzwungen geschlossene Positionen (siehe #1037), neue Invariante `check_exit_reason_coverage` (UNKNOWN-Anteil ≤ 10 %).

**#1035 (P1) — `check_effective_stop_distance` mass über die falsche Grundgesamtheit** — Zähler mittelte über ALLE Verlust-Trades, obwohl bei 68,5 % UNKNOWN-Exits rund 80 % der Trades den Stop nie berührten. Fix (setzt #1034 voraus): Zähler auf `exit_reason == TRAILING_STOP` beschränkt, `INCONCLUSIVE`-Verdikt unter 30 Stop-Exits statt FAIL gegen eine falsche Population.

**#1036 (P1) — `check_holding_time_cap` liess 3991 h passieren** (Zeitbox 24 Bars + 3 Slack, Faktor 148) — die Anteilsschwelle (`timebox_violating_trades_frac` gegen 128 347 gepoolte Round-Trips) verdünnte jede Einzelverletzung. Fix: zweiter, magnitudenbasierter Ast — jeder Round-Trip mit `holding_s > k · cap_s` (k=3, `tournament.json['timebox_violation_hard_multiple']`) ist blockierend unabhängig vom Anteil.

**#1037 (P2) — `median_bars_held=3` und `max_holding=412 h` in derselben Study** — bimodale Haltedauerverteilung; `_finalize_round_trip` schliesst eine nie flat gewordene Position erst am Datenende und finalisiert sie als einen Round-Trip mit der vollen Zeitspanne. Fix: `exit_reason=DATA_END`-Markierung (#1034), Telemetrie `n_round_trips_data_end`, neue Invariante `check_open_position_at_data_end` (Anteil ≤ 2 %).

**#1039 (P1) — `n_family`-Multiplizität über gemischte Kohorten** — direkter Folgefehler aus #1023 (98 Fremd-Studies in der Multiplizitätsbasis); zusätzlich FAILte `check_family_n_periods_homogeneity` mit `stratum_n_periods_ratio=None`. Fix: Neuerhebung nach dem #1023-Kohortenfilter; `per_stratum`-Policy fail-closed, wenn `stratum_n_periods_ratio` nicht berechnet werden kann.

**Kohorte D — Governance und Suchhaushalt:**

**#1040 (P2) — Symbolrotation deckte 133 Symbole seit vier Läufen nicht ab** — bei ~14,6 Symbolen/Lauf und einem Universum von über 140 Symbolen greift die `least_recently_covered`-Rotation nachweislich nicht (dieselben 8–10 Symbole in vier Läufen in Folge, TSLA in jedem). **In PR #894 root-caused und gefixt:** die eigentliche Ursache war nicht die Rotationssortierung selbst, sondern dass `tier='deployable'` (CLI-Default) `candidate_syms` auf existierende Tier-A-Gewinner beschränkte — ein Symbol ohne Vorlauf-Sieg konnte den Pool nie erreichen, unabhängig von seinem Staleness-Rang. `enumerate_tunable_pairs` lässt seither zusätzlich Symbole aus dem stalen Anteil von `symbol_coverage` zu.

**#1041 (P2) — Champion-Closed-Loop weiterhin unwirksam** (`seed_source_distribution` 96,4–97,1 % `strategy_defaults`; vier TSLA-Strategien mit `n_evaluable=0`, ~12 % des Suchhaushalts strukturell informationsfrei). **In PR #894 root-caused:** der Diagnose→Cache→Skip-Loop für `STRUCTURAL_ALL_UNEVALUABLE` war bereits vollständig verdrahtet (`compute_budget_execution → recommend_diagnosis_action → record_diagnosed_pair`); er wurde durch die (inzwischen behobenen) #1026/#1027-Bugs in `stop_reason`/`budget_executed_fraction` verhungert, nicht durch eine fehlende Verdrahtung. Ende-zu-Ende-Regressionstest ergänzt.

**Kohorte E — Ertrag und Risiko:**

**#1042 (P1) — Ökonomische Abnahmekriterien: CVaR, Kosten-Stress, Sizing-Parität, Information Ratio.** Vier Massnahmen vorgeschlagen (E-1 Kostenstress-Band als Promotionsbedingung, E-2 Sizing-Parität Backtest/Live-Allocator, E-3 CVaR/ES statt Max-Drawdown, E-4 Information Ratio statt `Excess/Exposure`). **In PR #894 teilweise umgesetzt:** E-1 (`expectancy_cost_stress_1_5x`/`_2x` additiv aus vorhandenen Round-Trip-Daten, neue `deployment_gate`-Klausel `cost_stress`) und E-3 (`cvar_95`/`es_99` aus `oos_period_returns`) vollständig implementiert; eine nicht-blockierende Sichtbarkeits-Invariante `check_sizing_parity_backtest_vs_allocator` für E-2 ergänzt. **E-2s vollständige Sizing-Neufassung und E-4s echte Information Ratio bewusst zurückgestellt** — beide benötigen Eingaben, die diese Umgebung nicht produzieren kann (ein Live-Rekalibrierungslauf gegen Marktdaten bzw. eine holdout-skopierte periodische Benchmark-Renditereihe; die Pipeline führt heute nur skalare Aggregate).

### Offen — nicht in diesen Sitzungen bearbeitet
**E-2 (vollständig)** — die Backtest-Sizing-Logik bildet `MomentumLSAllocator`s Portfolio-Cap (#999) nach wie vor nicht ab; nur die Sichtbarkeits-Invariante existiert. **E-4** — `Excess/Exposure` mit Epsilon-Ersatznenner ist weiterhin die einzige Vergleichsgrösse in Abschnitt 2.3 (siehe auch Pitfall #376/#1077, behoben erst im #1063–#1085-Katalog). **#1028s eigentliche Ursachenklärung** (Datenkorrektur der TSLA-Preisreihe oder dokumentierte Symbolsperre) — die Sofortmassnahmen (Quarantäne, zwei neue Invarianten) stehen, die Preisreihen-Forensik selbst wurde nicht abgeschlossen.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #1023–#1042)
- `tournament.json.timebox_violation_hard_multiple` (Vorschlag 3) — Magnitudenast für `check_holding_time_cap`, #1036.
- `optimizer.json.symbol_coverage_max_stale_runs` — Rotationsvorrang für stale Symbole, #1040.
- `deployment_gate`-Klausel `cost_stress` (`expectancy_cost_stress_1_5x`/`_2x`) — zehnte Promotionsbedingung, #1042/E-1.

### 🔒 Watertight Invariants (Issue-Katalog #1023–#1042) — für künftige Agenten
- **`invariants.check_report_cohort_coherence`** (`invariants.py`, #1023) — blockierend: Vorstufe des in dieser Sitzung (#1086/#1087/#1088) auf `run_id`-Identität statt Zeitfenster gehärteten Kohortenfilters; ein Report, der diesen Check besteht, kann trotzdem Fremd-Studies enthalten, solange die Zeitfenster überlappen (Pitfall #382/#383).
- **`invariants.check_sizing_identity_coherence`** (`invariants.py`, #1028) — blockierend: `ln(1+TR)/(n·expectancy)` muss die konfigurierte `trade_amount_pct` innerhalb 35 % relativer Toleranz reproduzieren; die einzige Invariante, die eine TSLA-artige Preisreihenanomalie ohne OHLC-Rohdatenzugriff aufdeckt.
- **`invariants.check_budget_metric_single_source`** (`invariants.py`, #1027) — jede der fünf `compute_budget_execution`-Aufrufstellen muss denselben `run_id`-Pflichtparameter erhalten; ein Study-Record- und ein `diagnosed_pairs`-Wert für dieselbe Study, die auseinanderlaufen, sind das direkte Symptom eines Rückfalls.
- **`invariants.check_exit_reason_coverage`/`check_open_position_at_data_end`** (`invariants.py`, #1034/#1037) — der `UNKNOWN`-Anteil im Exit-Histogramm ist die Voraussetzung dafür, dass `check_effective_stop_distance` (#1035) über die richtige Population misst; wer eine dieser Invarianten lockert, muss die andere neu kalibrieren.

---

## Issue-Katalog #1043–#1062 — Lauf-Governance-Vorstufe (Katalogdokument nicht im Repository überliefert, zweite Sitzung 2026-08-12)

**Ausgangslage.** Issue #1105 (GitHub-Issue #938) stellte fest, dass `AGENTS.md` bis zu dieser Nachtragung 40 Issues (#1023–#1062) und die Pitfalls #350–#368 vermisste, mit der Root-Cause „der Katalog vom 12.08. (zwei Sitzungen an einem Tag) wurde übersprungen". Für die erste dieser beiden Sitzungen (#1023–#1042) existiert das Katalogdokument bis heute als GitHub-Issue #866/#873 mit 20 individuell nachgefilten Einzel-Issues (#874–#893, siehe voriger Abschnitt) — für die **zweite** Sitzung desselben Tages (#1043–#1062) existiert **kein** vergleichbares Artefakt: keine individuellen GitHub-Issues, kein Katalog-Cover-Issue, keine überlebende `logs/*.md`-Datei in der Git-Historie. Die Commit-Historie springt in den Katalog-Bezeichnungen direkt von „Katalog #1023-1042" (Commits `1dd12c8` … `b091440`) zu „Katalog #866-2" (`#1063–#1085`, Commit `3f25d45`, dokumentiert im vorigen bzw. folgenden Abschnitt). `logs/todo.md` — das Katalogdokument der #1063–#1085-Sitzung — bestätigt in seiner eigenen Kopfzeile, dass #1023–#1062 zu diesem Zeitpunkt „im Code umgesetzt, aber nicht in AGENTS.md nachgetragen" waren (40 Issues Rückstand), enthält aber selbst nur vereinzelte Rückwärtsverweise auf #1043–#1062 als bereits abgeschlossene Befunde — keine vollständige Symptom-/Root-Cause-/Fix-Beschreibung. Diese Sektion dokumentiert das Ehrlichste, was aus diesen Rückwärtsverweisen rekonstruierbar ist, und macht die Lücke selbst sichtbar, statt sie mit erfundenem Inhalt zu füllen.

### Aus Rückwärtsverweisen rekonstruierbar

**#1043 — Kohortenkohärenz-Check mit Fail-Open-Pfad.** Einziger Beleg: `logs/todo.md` (B-2, Referenzlauf `815455db`) vermerkt „`check_report_cohort_coherence`: Spannweite 2865 s < 5495 s ⇒ PASS mit echter Messung (nicht fail-open). Der #1043-Fail-Open-Pfad wird in diesem Lauf nicht betreten." — #1043 identifizierte demnach einen Fail-Open-Zweig in der durch #1023 neu eingeführten `check_report_cohort_coherence` (vermutlich: ein PASS-Verdikt bei fehlenden/unvollständigen `study_started_at_utc`-Werten statt einer inkonklusiven Meldung). Dieselbe Fehlerklasse — ein Kohortenfilter, der bei Nebenläufigkeit fail-open wird — trat in dieser Sitzung erneut auf und wurde in #1088 (`INVARIANT_SCOPE_CONTAMINATED`) sowie strukturell in #1086/#1087 endgültig durch einen identitätsbasierten (`run_id`) statt zeitfensterbasierten Filter ersetzt (Pitfall #382/#383/#384).

**#1050 / #1051 — Stopdistanz unter den Round-Trip-Kosten (erste Instanz).** Beleg: GitHub-Issue #908 (#1072, Sitzung 2026-08-13) trägt den Titel „Stopdistanz unter den Round-Trip-Kosten (**Wiederkehr #1050/#1051**)" und dokumentiert dieselbe Kennzahl (`d < min_stop_to_cost_ratio · c_rt`) wie ihre eigene Root-Cause. #1050/#1051 identifizierten demnach erstmals, dass für mehrere Strategien die nominale Stopdistanz kleiner als die Round-Trip-Kosten war (`E[MFE] > d + c_rt` strukturell verletzt, unabhängig vom Signal) — ohne dass zu diesem frühen Zeitpunkt bereits ein Sampler-Preflight oder eine dedizierte Invariante existierte. Die vollständige Behebung (`min_stop_to_cost_ratio`, `check_stop_cost_ratio`) erfolgte erst mit #1072.

**#1052 — Ein Check, der eine Ursache behauptet, die er nicht gemessen hat (erste Instanz).** Beleg: GitHub-Issue #909 (#1071) trägt den Titel „`check_atr_scale_homogeneity` meldet eine Ursache, die es nicht gemessen hat (**Wiederkehr #1052**)" mit dem Zusatz „dort mit drei Symbolen, zwei Mechanismen und einem dritten in der Meldung". #1052 identifizierte demnach erstmals dasselbe Muster — eine Invariante, die aus einer gemessenen Spannweite/Grösse eine SPEZIFISCHE, nicht selbst gemessene Ursache ableitet — an einer damals noch nicht identifizierten Stelle. Dieses Muster kehrte danach zweimal weiter zurück (#1071, dann #1084 „dritte Wiederkehr dieser Fehlerklasse (nach #1052, #1071)", siehe Pitfall #380) und wurde erst mit #1084s `champions.load_champion_entry_with_reason` strukturell behoben (der Check konsumiert seither beobachtete Telemetrie statt eine Ursache zu raten).

**#1055 — `check_reward_dynamic_range` prüfte nur eine Richtung.** Beleg: `logs/todo.md`s eigene Root-Cause zu #1070 (B-4) nennt es namentlich: „Dieselbe Fehlerklasse wie #1055 (`check_reward_dynamic_range` prüfte nur „std zu klein" und war deshalb auf der BTC-Explosion blind)"; Pitfall #369 in dieser Datei bestätigt zusätzlich, dass `check_reward_dynamic_range` „bereits mit drei Klauseln inkl. Ratio-Obergrenze gehärtet" wurde. #1055 identifizierte demnach, dass die Reward-Streuungsprüfung nur eine untere Schranke („Reward-Varianz zu klein, Suche liefert kein Signal") kannte und eine BTC-bedingte Reward-Explosion (obere Richtung) unentdeckt liess. Die Härtung (dritte Klausel, Ratio-Obergrenze) erfolgte im Rahmen dieser #1043–#1062-Sitzung selbst; dieselbe Fehlerklasse kehrte danach ein zweites Mal in `check_effective_stop_distance` zurück (#1070, siehe Pitfall #369) — #1055 ist damit die chronologisch früheste bekannte Instanz eines Musters, das dieser Datei bereits zweimal als Pitfall dokumentiert ist.

### Nicht rekonstruierbar
Für die verbleibenden 15 Nummern dieses Bereichs — **#1044, #1045, #1046, #1047, #1048, #1049, #1053, #1054, #1056, #1057, #1058, #1059, #1060, #1061, #1062** — enthält weder die Git-Historie noch eine der beiden nachfolgenden Katalog-Sitzungen (#1063–#1085 in dieser Datei, #1086–#1104 im nächsten Abschnitt) einen Rückwärtsverweis, aus dem sich Symptom, Root-Cause oder Fix rekonstruieren liesse. Diese Nummern werden hier ausschliesslich benannt, damit der Rückstand als solcher sichtbar bleibt (Akzeptanzkriterium aus #1105) — **nicht** mit erfundenem Inhalt gefüllt. Sollte das ursprüngliche Katalogdokument der zweiten 12.08.-Sitzung wiederauftauchen (lokale Kopie, Chat-Historie einer früheren Session), ersetzt dessen Inhalt diesen Absatz vollständig.

### 🟢 Pitfall #359 — Ein Kohortenfilter kann selbst einen Fail-Open-Zweig tragen [Katalog #1043–#1062, vermutlich #1043]
**Symptom:** Die durch #1023 neu eingeführte `check_report_cohort_coherence` besass einen Zweig, der bei fehlenden/unvollständigen Zeitstempeln PASS statt eines inkonklusiven Verdikts lieferte (rekonstruiert aus `logs/todo.md`s „#1043-Fail-Open-Pfad").
**Root-Cause:** Eine frisch eingeführte Invariante, die eine neue Fehlerklasse verhindern soll, kann an ihrer eigenen Rand-Kondition (fehlende Eingabedaten) denselben Fail-Open-Fehler reproduzieren, den sie beheben sollte.
**Fix/Regel:** Jede neue Kohärenz-/Kohorten-Invariante braucht einen expliziten Test für den Fall fehlender Eingabedaten (kein Zeitstempel, keine `run_id`) — das Ergebnis muss `inconclusive`, nie `passed=True`, sein. Endgültig gelöst erst durch den identitätsbasierten Filter dieser Sitzung (#1086, Pitfall #382).

### 🟢 Pitfall #360 — Eine notwendige Bedingung ohne Preflight wird erst nach dem Backtest sichtbar [Katalog #1043–#1062, #1050/#1051]
**Symptom:** Mehrere Strategien liefen mit einer nominalen Stopdistanz unter den Round-Trip-Kosten (`d < c_rt`) — eine Position kann den Stop strukturell nicht überleben, unabhängig vom Signal —, ohne dass der Sampler daran gehindert wurde, genau solche Parametervektoren zu ziehen.
**Root-Cause:** Eine notwendige ökonomische Bedingung (`E[MFE] > d + c_rt`), die erst NACH der vollständigen Simulation geprüft wird, verschwendet Suchbudget auf von vornherein aussichtslose Vektoren und macht den Fehler erst im aggregierten Ergebnis sichtbar, nie am Ort seiner Entstehung.
**Fix/Regel:** Eine notwendige Bedingung, die allein aus der Parameterkombination (ohne Simulation) berechenbar ist, gehört als Sampler-Constraint vor den Backtest, nicht als Reward-Strafe danach. Vollständig umgesetzt erst mit `min_stop_to_cost_ratio`/`check_stop_cost_ratio` (#1072).

### 🟢 Pitfall #361 — Eine Ursachenmeldung ohne eigene Messung ist eine Vermutung mit Verfallsdatum [Katalog #1043–#1062, #1052]
**Symptom:** Ein Homogenitäts-Check meldete eine Preisreihen-Sprungstelle als Ursache einer beobachteten Spannweite, obwohl der Check nur die Spannweite selbst gemessen hatte — mindestens zwei weitere Mechanismen (Floor-Bindung im Nenner, Fremdkohorte) hätten dieselbe Zahl erzeugt.
**Root-Cause:** Ein Check, der einen Messwert UND eine Ursachenzuschreibung im selben Meldungstext ausgibt, verschweigt, dass nur der Messwert tatsächlich gemessen wurde — die Zuschreibung ist eine Hypothese, die als Tatsache formuliert ist.
**Fix/Regel:** Ein Check nennt ausschliesslich den Mechanismus, den er tatsächlich misst; jede weitere mögliche Ursache gehört, wenn überhaupt, als unmarkierte Hypothese in den `detail`-Text. Dasselbe Muster kehrte danach zweimal zurück (#1071, #1084) — siehe Pitfall #380 für die endgültige Behebung.

### 🟢 Pitfall #362 — Eine Ratio-Schwelle ohne Obergrenze ist blind für die Explosionsrichtung [Katalog #1043–#1062, #1055]
**Symptom:** `check_reward_dynamic_range` prüfte ausschliesslich „Reward-Streuung zu klein" (Suche liefert kein Signal) und war dadurch strukturell blind für eine BTC-bedingte Reward-**Explosion** (die Streuung war nicht zu klein, sondern pathologisch gross).
**Root-Cause:** Eine Schwelle, die für eine Grösse nur eine Richtung (zu klein ODER zu gross) prüft, macht die nicht geprüfte Richtung zu einer blinden Flanke, die kein Test je verletzen kann, solange kein Testfall genau diese Richtung erzeugt.
**Fix/Regel:** `check_reward_dynamic_range` erhielt eine dritte Klausel mit Ratio-Obergrenze. Dieselbe Regel — ein Verhältnis-Check braucht immer beide Schranken — kehrte als Pitfall #369 dieser Datei ein zweites Mal zurück (`check_effective_stop_distance`, #1070); wer einen neuen Ratio-Check schreibt, prüft ab sofort beide Richtungen von vornherein.

---

## Issue-Katalog #1063–#1085 — Lauf-Governance, Suchraum-Rückschrieb, Risikoschicht, Entscheidungs-/Berichtssemantik, Inferenz-Haushalt (Katalog #866-2, `logs/todo.md`, Sitzung 2026-08-13)

**Ausgangslage.** `logs/todo.md` (Abweichungs-Katalog #866-2, eine Folgesitzung des #866-Katalogs) dokumentierte 23 Issues (#1063–#1085) über fünf Kohorten (A: Lauf-Governance, B: Suchraum-Rückschrieb, C: Risikoschicht, D: Bericht-/Entscheidungs-Semantik, E: Inferenz/Suchhaushalt) plus einen Nachtrag aus dem vollständigen Optimizer-Log (§3b, drei zusätzliche Befunde #1083/#1084/#1085) mit einer vorgeschriebenen Merge-Reihenfolge (Stufe 0–5) und einem SPERRVERMERK: kein weiterer Sweep, bevor die Stufe-0-Fixes gemergt und der Suchraum-Diagnose-Cache migriert sind.

### Umgesetzt in dieser Session

**Stufe 0 (Suchraum-Bounds klammern):** #1066 (Domänenregister + `clamp_param_bounds`, Cache-Migration), #1067 (`MIN_BARS_IN_TRADE_FLOOR`), #1068 (Rückschrieb-Kontext) — siehe Pitfall #371/#372.

**Stufe 1 (Lauf-Governance):** #1063 (actual-Pair-Konvention), #1064 (Ledger-Selbstreferenz), #1065 (Vollständigkeit ≠ Gültigkeit — `run_status` unterscheidet seither `'aborted_invariant'` von `'completed_invalid'`, wenn `symbols_completed >= symbols_planned`), #1079 (geprunte Trials aus dem Nenner), #1083 (Suite genau einmal auswerten, ZUSAMMEN mit #1064) — siehe Pitfall #370/#373/#377/#379.

**Stufe 2 (Risikoschicht):** #1070 (zweiseitiger `check_effective_stop_distance`, `stop_distance_max_ratio`), #1072 (`min_stop_to_cost_ratio`, neue Invariante `check_stop_cost_ratio`), #1071 (`check_atr_scale_homogeneity`/`check_n_periods_homogeneity` melden den Mechanismus, nicht eine geratene Ursache — `atr_floor_binding_studies`/`denominator_studies`-Provenance) — siehe Pitfall #369. **#1069 Punkte 2–4 (ATR-Floor an Kosten/Bar-Spanne koppeln, Preflight, tatsächliche Fill-Simulation ändern) bewusst NICHT umgesetzt** — diese Punkte ändern simulierte Fills und erfordern den in der Merge-Reihenfolge vorgeschriebenen `simulation_semantics_version`-Bump (4 → 5) mit Pflicht-Purge UND einen echten Re-Run zur Abnahme; beides ist in dieser Sandbox (kein installierbares `nautilus_trader`, Python 3.11 statt der geforderten ≥3.12) nicht verifizierbar. `realized_stop_loss_ratio` (Telemetrie-Hälfte von #1069) ist implementiert.

**Stufe 3 (Entscheidungs-Semantik):** #1076 (`check_gate_inventory_coherence`, `gate_consolidation_protected`), #1074/#1075 (`normalize_gate_deltas_for_binding`, `_active_holdout_gate_deltas`), #1073 (winsorisierte Expectancy rankt + gatet, `expectancy_outlier_robust`-Deployment-Klausel, `check_expectancy_outlier_dependence`), #1077 (kein Epsilon-Ersatznenner, „Nicht bewertbar"-Block) — siehe Pitfall #374/#375/#376.

**Stufe 4 (Inferenz und Haushalt):** #1085 (`check_dust_round_trip_share`, Telemetrie — Quellfix bewusst zurückgestellt, siehe Pitfall #381), #1078 (Nenner `n_trials` statt der disjunkten `n_trials_informative`-Teilmenge), #1080 (`n_family_stage1`-Fallback auf `n_selection_statistic_available`, neue Invariante `check_n_family_partition`), #1081 (voller Round-Trip-Kostenstress statt nur Kommission, siehe Pitfall #378), #1082 (`reward_std_constraint_feasible` + `REWARD_FEASIBLE_PARTITION_DEGENERATE`-Diagnosecode gegen die #612-Sampler-Constraint-Kohorte statt der fast immer identischen Prune-Kohorte; `cross_study['search_budget_proposal']` macht Studies unter der `check_objective_branch_coverage`-Schwelle sichtbar und `sweep._apply_search_budget_proposal` deprioritisiert sie automatisch im nächsten Lauf über den bestehenden #830-Pfad, ohne eine stärkere bestehende Konsequenz zu überschreiben).

**Stufe 3b (Champion-Diagnose, NACH #1064):** #1084 (`load_champion_entry_with_reason` trennt `STORE_EMPTY`/`NO_ENTRY_FOR_PAIR`, `_champions_summary(studies_out=...)` zählt Versuche statt Store-Einträge, `check_champion_writeback_reachability` meldet die beobachtete Ursache, neue Invariante `check_champion_corroboration_reachable`) — siehe Pitfall #380.

### Offen — nicht in dieser Session bearbeitet

**#1069 Punkte 2–4** (siehe Stufe-2-Absatz oben — Simulationsschicht-Änderung, Purge + Re-Run erforderlich). **Der Quellfix von #1085** (5-%-Notional-Boden direkt in `backtest_runner.extract_metrics`, siehe Pitfall #381 — dieselbe Kategorie Risiko wie #1069). Beide erfordern denselben `simulation_semantics_version`-Bump und dieselbe Re-Run-Abnahme und sind bewusst ALS EIN GEMEINSAMER Folgeauftrag zurückgestellt (kein Doppel-Bump). Alle referenzierten Mechanismen existieren bereits auf `main`; wer diese Punkte aufgreift, startet auf einer in dieser Session bereits erweiterten Telemetrie-Grundlage (`realized_stop_loss_ratio`, `dust_round_trips_filtered`, `atr_floor_binding_studies`).

**Kein Sweep-Re-Run in dieser Session** — die Sandbox hat kein installierbares `nautilus_trader` (Python 3.11.15 statt der geforderten ≥3.12), das Abnahmeprotokoll (`logs/todo.md` §6) ist daher NICHT gegen einen echten Lauf verifiziert; jede neue/geänderte Invariante ist stattdessen gegen dedizierte Unit-Tests (`automation/tests/test_issue_1063_*` bis `test_issue_1084_*`) und, wo möglich, gegen bestehende Regressionstests verifiziert. `simulation_semantics_version` bleibt bei 4, `reward_semantics_version` bei 23 — kein Issue dieser Session änderte einen gestempelten Reward-Wert oder simulierte Fills.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #1063–#1085)
- `optimizer.json.stop_distance_max_ratio` (Default 10.0) — obere Ratio-Schranke, Pitfall #369, #1070.
- `tournament.json.min_stop_to_cost_ratio` (Default 3.0) — Mindestvielfaches Stopdistanz/Round-Trip-Kosten, #1072.

### 🔒 Watertight Invariants (Issue-Katalog #1063–#1085) — für künftige Agenten
- **`invariants.check_fail_fast_actual_convention`** (`invariants.py`, #1063) — jeder in `fail_fast_invariants` gelistete Check muss die `{"strategy/symbol": value}`-Pair-Konvention in `actual` erfüllen, sonst greift der Breitenschwellen-Schutz nicht (Pitfall #370).
- **`invariants.check_champion_corroboration_reachable`** (`invariants.py`, #1084) — FAIL bei `max(corroboration_count) < champion_promote_after_runs` UND `symbol_coverage.total_runs_started == 1`: benennt den Korroborations-Deadlock direkt, statt ihn unter `check_champion_writeback_reachability`s generischer Diagnose zu verstecken (Pitfall #380).
- **`invariants.check_n_family_partition`** (`invariants.py`, #1080) — `n_family[symbol] == Σ n_family_stage1[symbol]`; eine Study mit 0 Holdout-Trades muss über `n_selection_statistic_available` trotzdem zur familienweiten Multiplizität beitragen, sonst ist die Deflationsreferenz SR* zu niedrig angesetzt.
- **`spaces.clamp_param_bounds`/`_PARAM_DOMAIN_REGISTRY`** (`spaces.py`, #1066/#1067) — die EINE Stelle, die jeden automatischen Bounds-Vorschlag (Weitung UND Verengung) gegen eine dokumentierte Domäne klammert; ein neuer Parameter ohne Registry-Eintrag bleibt ungeklammert (fail-open, kein stiller Blocker für neue Strategien) — siehe Pitfall #371.
- **`champions.load_champion_entry_with_reason`** (`champions.py`, #1084) — die EINE Stelle, die `STORE_EMPTY` (Store insgesamt leer) von `NO_ENTRY_FOR_PAIR` (Store gefüllt, kein Eintrag für dieses Paar) unterscheidet; jeder Konsument, der den pair-skopierten Champion-Store liest, sollte diese Funktion statt einer eigenen `entry is None`-Prüfung verwenden.

---

## Issue-Katalog #1086–#1104 — Nebenläufigkeits-Isolation, Risikoschicht-Abschluss, Messapparat-Härtung, Selektions-Provenienz (Kohorte E — Governance, GitHub-Issues #919–#937, Sitzung 2026-08-14)

**Ausgangslage.** GitHub-Issue #938 (intern #1105) legte einen 19-teiligen Katalog #1086–#1104 in fünf Stufen vor, ausgelöst durch den Referenzbefund, dass drei gleichzeitige Sweeps auf einem geteilten SQLite-Store liefen: jeder der drei Reports enthielt Studies aller vier Läufe (der zeitfensterbasierte Kohortenfilter aus #1023/#1043 erkennt Nebenläufigkeit strukturell nicht, siehe voriger Abschnitt), der Trailing-Stop erwies sich über 52 Studies und vier Symbole als mechanistisch wirkungslos (Anker auf `bar.high`, Auslöser auf `close` — der realisierte Verlust ist die Bar-Spanne, nicht die konfigurierte Stopdistanz), und der Suchraum-Rückschrieb hatte über mehrere Läufe kompoundiert. Diese Sitzung implementierte alle 19 Punkte (#1086–#1104) vollständig, in der von #1105 vorgeschriebenen Stufenreihenfolge; die Abnahme erfolgte ausschliesslich gegen dedizierte Unit-Tests (`automation/tests/test_issue_1086_*` bis `test_issue_1104_*`) sowie wiederholte volle Regressionsläufe (`comm -13` gegen eine vor Sitzungsbeginn erfasste Baseline-Fehlerliste) — kein echter Sweep-Re-Run, aus denselben Sandbox-Gründen wie in den beiden Vorkatalogen (kein installierbares `nautilus_trader` mit exakt gepinnter Version zum Zeitpunkt jedes Einzelschritts).

### Umgesetzt in dieser Session

**Stufe 0 (SOFORT, blockierte jeden weiteren Sweep):**

**#1090 — Diagnose-Rückschrieb bestätigte Paare auf verdreifachter Evidenz.** `sweep_diagnostics.confirm_pair`/`record_diagnosed_pair` wurde von den drei gleichzeitigen Läufen je Paar bis zu dreimal aufgerufen und zählte jeden Aufruf als unabhängige Bestätigung. Fix: Deduplikation auf einem Beobachtungs-Fingerprint statt auf Aufrufzahl; automatischer Rückschrieb (`writeback`) standardmässig deaktiviert (`diagnosis_auto_writeback_enabled=False`), muss explizit aktiviert werden — siehe Pitfall #385.

**#1086 — Gleichzeitige Sweeps teilten einen Study-Store; jeder Report enthielt alle Symbole.** `report._collect_studies` filterte auf ein Zeitfenster (Lauf-Spannweite), nicht auf eine Identität. Fix: `run_id`-basierter Kohortenfilter (jede Study trägt seit #1025 einen `run_id`-Stempel; die Filterung erfolgt jetzt darauf, nicht auf `study_started_at_utc`-Zeitfenstern) plus eine **store-weite** Lock-Datei (`{WORK}/sweep/.run.lock`, EINE Datei je `OPTIMIZER_WORK_DIR`, NICHT je Symbol oder je (Strategie, Symbol)-Paar — Korrektur #944/#1110, siehe Pitfall #382 und Abschnitt „Paralleler Mehr-Symbol-Betrieb" unten) gegen zwei unabhängige Sweep-Prozesse auf demselben Store.

**#1087 — `check_report_cohort_coherence` mass die Spannweite statt des Versatzes zum Laufbeginn.** `max(study_started_at_utc) − min(...) < wallclock_s` (aus #1023) erkennt drei um Stunden versetzte, aber je für sich kurze Läufe nicht als fremd, solange ihre jeweiligen Spannweiten unter der Schwelle bleiben. Fix: Der Check misst seither den Versatz jeder Study zum **Laufbeginn** (`min(x) >= run_started_at − slack`), nicht die interne Streuung der Kohorte. Siehe Pitfall #383.

**#1088 — Blockierende Invarianten urteilten über Fremd-Studies.** Die Invariantensuite konsumierte weiterhin die ungefilterte (oder unzureichend gefilterte) Study-Menge. Fix: neue Guard-Funktion `assert_invariant_scope_uncontaminated` plus Diagnosecode `INVARIANT_SCOPE_CONTAMINATED` — jede Invariantenauswertung deklariert explizit, auf welcher Kohorte sie lief, und ein Kontaminationsfund ist selbst ein blockierender Befund.

**#1089 — `check_champion_corroboration_reachable` war durch einen ODER-Ast fail-open.** Der zweite Operand des ODER-Asts las `symbol_coverage.total_runs_started` — einen globalen, prozessübergreifenden Zähler, den jeder Nebenprozess unabhängig erhöhen konnte, sodass ein einzelner Nebenlauf die Bedingung unabhängig vom tatsächlichen Korroborationsstand erfüllte. Fix: der ODER-Ast wurde entfernt; die Invariante urteilt ausschliesslich über lauf-skopierte Evidenz. Siehe Pitfall #384 (dieser Fund kippte vier Checks, davon einen von ehrlich-FAIL auf falsch-PASS).

**#1091 — `n_family` driftete über gleichzeitige Reports** (nicht-deterministische Deflationsschwelle, abhängig davon, welcher Nebenprozess zuerst schrieb — zur Berichtszeit aus veränderlichem globalem Zustand gelesen). Fix: `n_family`/`n_family_stage1` werden **vor dem ersten Trial** eingefroren und im Study-User-Attr gestempelt (`deflation_n_family_frozen`), statt bei jeder Report-Erzeugung neu aus dem aktuellen Store-Zustand erhoben zu werden. Siehe Pitfall #386.

**Stufe 1 (Risikoschicht — `simulation_semantics_version` 4 → 5, Pflicht-Purge am Ende):**

**#1092 — Anker und Auslöser des Trailing-Stops lagen auf verschiedenen Preis-Auflösungen** (P0, HEADLINE). Der Anker wurde gegen `bar.high`/`bar.low` nachgezogen, der Auslöser aber gegen `bar.close` geprüft — der realisierte Verlust bei Auslösung ist dadurch die Bar-Spanne, nicht die konfigurierte `k · ATR`-Distanz, unabhängig vom Multiplikator. Fix: `trailing_stop_anchor_resolution="close"` — Anker und Auslöser konsumieren dieselbe Preis-Auflösung. Siehe Pitfall #387. Die tiefergehende Engine-Änderung (eine echte `StopMarketOrder` statt eines Bar-Schluss-Signals, #1092B) blieb bewusst ausserhalb dieser Sitzung (siehe „Offen" unten).

**#1093 — Neue Invariante `check_trailing_stop_loss_share`** (blocking, kalibriert) — macht den Anteil verlustreicher Trailing-Stop-Exits an allen Exits einer Study direkt sichtbar und blockierend, statt ihn nur als Telemetrie zu führen.

**#1094 — Die Ratsche des Trailing-Stops konnte zurückweichen** (Neuberechnung ohne `max()` gegen den Vorwert). Fix: monotone Ratsche wiederhergestellt, mit einer Untergrenze für den Volatilitätsanteil als Sicherung gegen die ursprüngliche Klemm-Gefahr, die zur Aufweichung der Monotonie geführt hatte. Siehe Pitfall #388.

**#1095 — Stop-Exit-Fill-Lag telemetriert** (`stop_exit_lag_bars`) — macht sichtbar, dass ein als Bar-Schluss-Signal implementierter Exit-Mechanismus prinzipiell nicht schneller als der Bar-Takt sein kann. Siehe Pitfall #389.

**#1096 — Der ATR-Floor band in 18 von 56 Studies und machte den Stop zur Konstante.** Fix: ATR-Floor kostengekoppelt (`atr_floor_bps_by_asset_class` an `c_rt` gebunden statt frei gepflegt) plus ein informativer-Bars-basierter ATR-Schätzer, der bei dünner Handelsaktivität nicht kollabiert.

**Stufe 2 (Messapparat — parallel, kein Purge):**

**#1097 — Verlust-Mittelwerte (Median über Trials) und Zähler (Summe über Trials) waren nicht kommensurabel** — dritte Instanz derselben Fehlerklasse nach #304 und #1033. Fix: gepoolte, trade-gewichtete Verlust-Kennzahlen ergänzt, die auf derselben Grundgesamtheit wie ihr Nenner operieren. Siehe Pitfall #390.

**#1098 — `events.jsonl` verlor Trial-Ereignisse unter Diagnose-Last.** Der Ereignisstrom war weder zeilenatomar geschrieben noch beim Abschluss geflusht — unter hoher Last (viele gleichzeitige Diagnose-Schreiber) gingen genau die Ereignisse verloren, die am meisten gebraucht werden. Fix: `os.open()`+`os.write()`+`os.close()` für POSIX-garantiert atomare Einzelschreibvorgänge je Zeile, plus ein Vollständigkeits-Manifest (`_count_jsonl_events`/`_read_jsonl_events`, neue Invariante für Ereignisstrom-Vollständigkeit). Siehe Pitfall #391.

**#1099 — `champions.attempts` zählte Fremd-Studies.** Der Champion-Summary-Zähler war nicht auf die eigene Kohorte beschränkt. Fix: `_champions_summary` liest `attempts`/`skipped_by_reason` bevorzugt aus dem ereignisbasierten Sidecar (`events.jsonl`, sofern auflösbar), mit Fallback auf die `studies_out`-Rekonstruktion.

**#1100 — `holdout_buyhold_return` kollabierte bei 0 Trades auf `0,0`** statt `None` — ein weiterer Fall der Sentinel-Kollaps-Fehlerklasse (#759/#788/#966). Fix: `oos_buyhold_return` zu `invariants._SENTINEL_GUARDED_METRIC_KEYS` hinzugefügt, plus eine neue Kohärenz-Invariante zwischen `holdout_buyhold_return` und der zugrundeliegenden Trade-Zahl. Die tiefere Arithmetik in `backtest_runner.py` wurde bewusst NICHT verändert (bereits als `None`-sicher statisch verifiziert) — diese Sitzung schliesst ausschliesslich die Sentinel-Guard-Lücke.

**Stufe 3 (Selektion — nach Stufe 0):**

**#1101 — Das Randlösungs-Veto war der bindende Ausgang der ökonomisch besten Kandidaten.** Ein Randlösungs-Veto wird zum bindenden Constraint, sobald der Suchraum-Rückschrieb (#1066) die Grenzen bereits geweitet hat — dann ist das Veto richtig und die ursprüngliche Diagnose falsch adressiert (die Ursache liegt im Suchraum, nicht in der Selektion). Fix: neuer Status `REJECT_BOUNDARY_SOLUTION_PERSISTENT`, der greift, sobald `widen_applications` für den bindenden Parameter die `_MAX_WIDEN_APPLICATIONS`-Grenze erreicht hat (erkannt über den bestehenden Diagnose-Cache, keine neue synchrone Re-Optimierung). Siehe Pitfall #392.

**#1102 — `n_family` widersprach `Σ n_family_stage1` um Faktor 2,8–5,1.** Beide Grössen lasen unterschiedliche zugrundeliegende Proposal-Felder (`deflation_n_eligible` vs. `deflation_n_family`). Fix: der Report leitet `n_family` je Symbol seither als Summe von `n_family_stage1[symbol]` her — aus derselben, bereits durch #1080 gehärteten granularen Quelle — statt aus einer separat gepflegten Aggregatfunktion; `check_n_family_partition` wurde dadurch tautologisch und ihre Severity von `high` auf `blocking` angehoben.

**#1103 — Champion-Closed-Loop blieb bei 0 von 56 angewandten Rückschrieben.** `load_champion_entry_with_reason` meldete `NO_ENTRY_FOR_PAIR`/`STORE_EMPTY` ohne Herkunftsangabe — nicht unterscheidbar, ob ein Schlüssel-Mismatch oder ein echtes Persistenzproblem vorlag. Fix: neue Provenienz-Funktion `_no_entry_provenance` (`looked_up_key`, `available_keys`, `available_keys_total`), im `CHAMPION_WRITEBACK`-Event als `skipped_provenance` mitgeführt.

**#1104 — `run_id`-Präfix und `git_commit` divergierten im `3910e12b`-Referenzlauf.** Eine einzige `git_commit()`-Lesung zur Berichtszeit trug bislang zwei Bedeutungen („worauf lief die Simulation" vs. „worauf wurde DIESER Report gebaut") unter demselben Feldnamen. Fix: zwei getrennte Pflichtfelder — `git_commit_simulation` (Study-User-Attr, vor dem ersten Trial in `run_optimization.py` gestempelt) und `git_commit_report` (zur Berichtszeit in `report.py` gelesen); `log_manager.default_run_id()` trägt seither keinen Commit-Bezug mehr (ein kollisionsfreier Zufallstoken statt eines Commit-Präfix); neue Invariante `check_commit_coherence` (FAIL, wenn beide Commits bekannt sind UND divergieren; `inconclusive`, wenn einer fehlt).

### Offen — nicht in dieser Session bearbeitet
**#1092B** — eine echte `StopMarketOrder` in der Engine statt eines Bar-Schluss-Exit-Signals (eigener Folge-Issue laut #1105s Merge-Reihenfolge; ändert simulierte Fills und erfordert eine Engine-seitige Abnahme, die diese Sitzung nicht leisten kann). **#1101s Quellfix** bleibt asynchron über den bestehenden Diagnose-Cache-Mechanismus gelöst — eine synchrone, sofortige Re-Optimierung mit neu geweiteten Grenzen wurde bewusst nicht implementiert (grösseres Risiko, keine Verifikationsgrundlage in dieser Sandbox). **Kein Sweep-Re-Run** — dieselben Sandbox-Gründe wie in den beiden Vorkatalogen; das in #1105 Abschnitt 5 vorgeschriebene Abnahmeprotokoll (drei gleichzeitige Sweeps, Spearman-Korrelation `k·ATR` gegen realisierten Verlust, etc.) ist NICHT gegen einen echten Lauf verifiziert, sondern ausschliesslich gegen Unit-Tests und wiederholte volle Regressionsläufe.

### 📋 Neue/geänderte Config-Keys (Issue-Katalog #1086–#1104)
- `optimizer.json.diagnosis_auto_writeback_enabled` (Default `False`) — automatischer Diagnose-Rückschrieb muss seither explizit aktiviert werden, #1090.
- `optimizer.json.trailing_stop_anchor_resolution` (Default `"close"`) — Anker und Auslöser des Trailing-Stops auf derselben Preis-Auflösung, #1092.

### 🔒 Watertight Invariants (Issue-Katalog #1086–#1104) — für künftige Agenten
- **`report._collect_studies` / Lock-Datei je (Strategie, Symbol)** (`report.py`, `sweep.py`, #1086) — die EINE Stelle, die Kohortenzugehörigkeit über `run_id`-Identität statt über ein Zeitfenster entscheidet; ersetzt endgültig den in #1023/#1043 eingeführten, nachweislich nebenläufigkeitsunsicheren Zeitfenster-Filter.
- **`invariants.assert_invariant_scope_uncontaminated`** (`invariants.py`, #1088) — jede Invariantenauswertung deklariert ihre Kohorte explizit; `INVARIANT_SCOPE_CONTAMINATED` ist selbst ein blockierender Befund, kein Warnhinweis.
- **`invariants.check_commit_coherence`** (`invariants.py`, #1104) — `git_commit_simulation` und `git_commit_report` sind zwei Pflichtfelder mit unterschiedlicher Bedeutung; ein Konsument, der nur `git_commit` erwartet, liest seit dieser Sitzung nichts (das Feld existiert nicht mehr, siehe Test `test_report_carries_both_commit_fields_and_they_can_diverge`).
- **`invariants._SENTINEL_GUARDED_METRIC_KEYS`** (`invariants.py`, #1100) — jetzt inklusive `oos_buyhold_return`; ein neues Metrikfeld, das bei fehlender Datenlage auf `0.0` statt `None` defaulten könnte, gehört grundsätzlich in dieses Set (vierte dokumentierte Instanz der Sentinel-Kollaps-Klasse nach #759/#788/#966).
- **`sweep_diagnostics.record_diagnosed_pair`** (`sweep_diagnostics.py`, #1090) — Bestätigungen werden auf einem Beobachtungs-Fingerprint dedupliziert, nicht auf Aufrufzahl; drei gleichzeitige Läufe erzeugen keine dreifache Evidenz mehr für dasselbe Paar.

### 🟢 Pitfall #382 — Ein zeitfensterbasierter Kohortenfilter ist bei Nebenläufigkeit wirkungslos [Katalog #1086–#1104]
**Symptom:** Drei gleichzeitige Sweeps auf einem geteilten Study-Store erzeugten drei Reports, die jeweils Studies aller vier Läufe enthielten — der Filter aus #1023 prüfte eine Zeitspannen-Bedingung, keine Identität.
**Root-Cause:** Kohortenzugehörigkeit wird über eine **Identität** (`run_id`) entschieden, nie über eine **Zeitspanne** — ein Zeitfenster-Filter ist bei Nebenläufigkeit strukturell wirkungslos, weil sich die Zeitfenster mehrerer Läufe überlappen können, ohne dass die Läufe zusammengehören.
**Fix/Regel:** `report._collect_studies` filtert auf `study.user_attrs['run_id'] == run_id`; zusätzlich eine **store-weite** Lock-Datei (`{WORK}/sweep/.run.lock`) gegen zwei gleichzeitige, unabhängige Sweep-Prozesse auf demselben Store (#1086). Korrektur #944/#1110: die ursprüngliche Beschreibung dieses Pitfalls („Lock-Datei je (Strategie, Symbol)") war unzutreffend und lud einen Bediener dazu ein, mehrere Sweeps auf verschiedenen Symbolen desselben `OPTIMIZER_WORK_DIR` parallel zu starten — siehe Abschnitt „Paralleler Mehr-Symbol-Betrieb" in `manuals/run_optimizer.md`.

### 🟢 Pitfall #383 — Ein Spannweiten-Check prüft nicht die Lage [Katalog #1086–#1104]
**Symptom:** `check_report_cohort_coherence` mass `max(x) − min(x) < wallclock_s` — drei um Stunden versetzte, aber je für sich kurze Läufe blieben unter dieser Schwelle unentdeckt.
**Root-Cause:** Ein Spannweiten-Check (`max − min`) prüft die Streuung, nicht die Lage. Wer Zugehörigkeit zu einem Lauf prüfen will, braucht den Versatz zu einem Anker (`min(x) >= anchor − slack`), nicht die interne Streuung der Kohorte.
**Fix/Regel:** Der Check misst seither den Versatz jeder Study zum Laufbeginn, nicht die Spannweite der Kohorte selbst (#1087).

### 🔴 Pitfall #384 — Ein ODER-Ast auf einem globalen Zähler macht eine Invariante fail-open [Katalog #1086–#1104]
**Symptom:** `check_champion_corroboration_reachable` hatte einen ODER-Ast, dessen zweiter Operand `symbol_coverage.total_runs_started` las — ein globaler, prozessübergreifender Zähler. Ein einzelner Nebenprozess konnte ihn erhöhen und damit die Bedingung erfüllen, unabhängig vom tatsächlichen Korroborationsstand. Kippte vier Checks, davon einen von ehrlich-FAIL auf falsch-PASS.
**Root-Cause:** Ein ODER-Ast in einer Invariante, dessen zweiter Operand ein globaler, prozessübergreifender Zähler ist, macht den Check fail-open, sobald irgendein Nebenprozess das Ledger anfasst — ein ehrlicher FAIL wird dadurch zu einem falschen PASS, die gefährlichste Richtung.
**Fix/Regel:** Der ODER-Ast wurde entfernt; die Invariante urteilt ausschliesslich über lauf-skopierte Evidenz (#1089).

### 🟢 Pitfall #385 — Ein Bestätigungszähler muss auf einem Beobachtungs-Fingerprint dedupliziert werden [Katalog #1086–#1104]
**Symptom:** Der Diagnose-Rückschrieb bestätigte Paare auf verdreifachter Evidenz — jeder der drei gleichzeitigen Läufe zählte als unabhängige Bestätigung desselben Paares.
**Root-Cause:** Ein Bestätigungszähler für einen Rückschrieb muss auf einem Beobachtungs-Fingerprint dedupliziert werden, nicht auf Aufrufen — sonst zählt Nebenläufigkeit Evidenz, die nicht existiert.
**Fix/Regel:** `sweep_diagnostics.record_diagnosed_pair` dedupliziert auf einem Beobachtungs-Fingerprint; automatischer Rückschrieb ist standardmässig deaktiviert (#1090).

### 🟢 Pitfall #386 — Eine zur Berichtszeit gelesene Multiplizität ist keine Konstante [Katalog #1086–#1104]
**Symptom:** `n_family` driftete über gleichzeitige Reports — die Deflationsschwelle hing davon ab, welcher Nebenprozess zuerst schrieb.
**Root-Cause:** Eine Multiplizität, die zur Berichtszeit aus veränderlichem globalem Zustand gelesen wird, ist keine Konstante.
**Fix/Regel:** Multiplizitätskorrekturen werden vor dem ersten Trial eingefroren und gestempelt, nicht bei jeder Report-Erzeugung neu erhoben (#1091).

### 🔴 Pitfall #387 — Anker und Auslöser eines Stops müssen auf derselben Preis-Auflösung liegen [Katalog #1086–#1104]
**Symptom:** Der Trailing-Stop-Anker wurde gegen `bar.high`/`bar.low` nachgezogen, der Auslöser aber gegen `bar.close` geprüft — über 52 Studies und vier Symbole war der Stop nachweislich keine Risikogrösse (Spearman(`k·ATR`, realisierter Verlust) = −0,46 vor dem Fix).
**Root-Cause:** Ein Anker auf `bar.high` mit einem Auslöser auf `close` erzeugt einen Stop, dessen realisierter Verlust die Bar-Spanne ist und nicht die konfigurierte Distanz — unabhängig vom Multiplikator.
**Fix/Regel:** `trailing_stop_anchor_resolution="close"` — Anker und Auslöser konsumieren dieselbe Preis-Auflösung (#1092).

### 🟢 Pitfall #388 — Ein Stop, der zurückweichen kann, ist keine Risikogrösse [Katalog #1086–#1104]
**Symptom:** Die Ratsche des Trailing-Stops konnte durch eine Neuberechnung ohne `max()` gegen den Vorwert zurückweichen.
**Root-Cause:** Ein Stop, der zurückweichen kann, ist keine Risikogrösse — die ursprüngliche Klemm-Gefahr, die zur Aufweichung der Monotonie geführt hatte, wird über eine Untergrenze für den Volatilitätsanteil gelöst, nicht über den Verzicht auf Monotonie.
**Fix/Regel:** Monotone Ratsche wiederhergestellt, mit ATR-Floor-Untergrenze als Sicherung (#1094).

### 🟡 Pitfall #389 — Ein Bar-Schluss-Exit kann nicht schneller als der Bar-Takt sein [Katalog #1086–#1104]
**Symptom:** Der Stop-Ausstieg ist als Markt-Close nach der Auslöser-Bar implementiert — ein Exit-Mechanismus, der prinzipiell nicht schneller als der Bar-Takt sein kann.
**Root-Cause:** Ein Exit-Mechanismus, der als Bar-Schluss-Signal implementiert ist, kann prinzipiell nicht schneller als der Bar-Takt sein. Verlustbegrenzung gehört als ruhende Order in die Engine, nicht in die Strategie-Logik.
**Fix/Regel:** `stop_exit_lag_bars` telemetriert den Effekt (#1095). **Die eigentliche Engine-Änderung (echte `StopMarketOrder`, #1092B) ist bewusst NICHT Teil dieser Sitzung** — sie ändert simulierte Fills und erfordert eine eigene, sorgfältig gegen Backtest-Regressionen zu verifizierende Folgearbeit.

### 🟢 Pitfall #390 — Median über Trials und Summe über Trials sind nicht kommensurabel [Katalog #1086–#1104]
**Symptom:** Verlust-Mittelwerte (Median über Trials) und Zähler (Summe über Trials) wurden im selben Quotienten verwendet — dritte Instanz dieser Fehlerklasse nach #304 und #1033.
**Root-Cause:** Median über Trials und Summe über Trials sind nicht kommensurabel. Sobald das eine Zähler und das andere Nenner wird, ist der Quotient bedeutungslos.
**Fix/Regel:** Gepoolte, trade-gewichtete Verlust-Kennzahlen ergänzt, die auf derselben Grundgesamtheit wie ihr Nenner operieren (#1097).

### 🟢 Pitfall #391 — Ein forensischer Ereignisstrom muss zeilenatomar geschrieben und geflusht werden [Katalog #1086–#1104]
**Symptom:** `events.jsonl` verlor Trial-Ereignisse unter Diagnose-Last — genau dann lückenhaft, wenn er am meisten gebraucht wird (hohe Last).
**Root-Cause:** Ein Ereignisstrom, der als forensische Referenz dient, muss zeilenatomar geschrieben und beim Abschluss geflusht werden, sonst ist er unvollständig, wenn er am meisten gebraucht wird.
**Fix/Regel:** `os.open()`+`os.write()`+`os.close()` für POSIX-garantiert atomare Einzelschreibvorgänge je Zeile; ein Vollständigkeits-Manifest ist Pflicht (#1098).

### 🟢 Pitfall #392 — Ein Randlösungs-Veto nach Suchraum-Weitung ist richtig, aber falsch adressiert [Katalog #1086–#1104]
**Symptom:** Das Randlösungs-Veto war der bindende Ausgang der ökonomisch besten Kandidaten, obwohl der Suchraum-Rückschrieb (#1066) die Grenzen bereits geweitet hatte.
**Root-Cause:** Ein Randlösungs-Veto wird zum bindenden Constraint, sobald der Suchraum-Rückschrieb die Grenzen weitet. Das Veto ist dann richtig und die Diagnose falsch adressiert — die Ursache liegt im Suchraum, nicht in der Selektion.
**Fix/Regel:** Neuer Status `REJECT_BOUNDARY_SOLUTION_PERSISTENT`, sobald `widen_applications` die `_MAX_WIDEN_APPLICATIONS`-Grenze erreicht hat — über den bestehenden Diagnose-Cache erkannt, keine neue synchrone Re-Optimierung (#1101).

### 🟢 Pitfall #393 — Ein Nachtragungs-Rückstand ist selbst eine Fehlerquelle [Katalog #1086–#1104, GitHub-Issue #938/#1105]
**Symptom:** `AGENTS.md` enthielt bis zu dieser Nachtragung #1063–#1085 vollständig, aber von #1023–#1062 nur drei beiläufige Erwähnungen (#1031, #1052, #1055) — 40 Issues und Pitfalls #350–#368 fehlten. #1097 (Zähler/Nenner auf verschiedenen Grundgesamtheiten) und #1100 (Sentinel-Kollaps) in diesem Katalog sind beide Wiederholungen von Fehlerklassen, die in genau dieser Lücke dokumentiert worden wären.
**Root-Cause:** Der Nachtrag erfolgte katalogweise; ein Katalog, dessen Dokument nicht ins Repository committet wird (siehe voriger Abschnitt, #1043–#1062), hinterlässt keine Spur, aus der ein späterer Agent lernen kann — der Pitfall-Katalog ist die einzige Stelle, an der ein künftiger Agent von wiederkehrenden Fehlerklassen erfährt.
**Fix/Regel:** Katalog-Dokumente (`logs/*.md`) werden vor der nächsten Sitzung committet, nicht nur lokal erzeugt; die Nachtragung in `AGENTS.md` erfolgt in derselben Sitzung wie die Implementierung, nicht als aufgeschobene Sammelaktion über mehrere Sitzungen hinweg.

---

## Issue-Katalog #1106–#1125 — Kohorten-Identität, Mess-Kommensurabilität, Risikoschicht-Nachweis, Diagnose-Bereinigung (GitHub-Issues #940–#960, Sitzung 2026-08-15)

**Ausgangslage.** Diese Sitzung bearbeitete den 20-teiligen Katalog #1106–#1125 (GitHub-Issues #940–#959, plus #960 für diese Nachtragung), ausgelöst durch einen gestaffelten Batch-Start dreier gleichzeitiger Sweeps am 14.08. auf einem geteilten Store. Der für #1086 gebaute zeitfensterbasierte Kohortenfilter erwies sich als strukturell blind für genau den häufigsten realen Betriebsfall: einen Nachbarprozess, der nach dem Referenzlauf startet und vor ihm endet, liegt vollständig INNERHALB jedes Zeitfensters — eine zeitliche Enthaltung kann Identität nicht entscheiden, unabhängig von der gewählten Schwelle (Pitfall #394). Zweiter Schwerpunkt: `per_fold_oos_sortino`/`per_fold_oos_sortino_period` standen seit dieser Sitzung erstmals BEIDE im Ereignisstrom — die seit #665 vermutete Annualisierungs-Inkommensurabilität (√F-Spannweite Median 1,709, in 99,15 % der Trials über der alten Schwelle) wurde damit von einer Hypothese zu einer Zahl, die erklärt, warum PBO mit 16 von 42 eigenen Studies (38 %) zum dominanten Ablehnungsgrund wurde. Ökonomischer Befund des zugrundeliegenden Referenzlaufs: 0 von 42 Studies promotet ist korrekt — von acht wirtschaftlich sauberen Kandidaten (positive Expectancy nach vollem Kostenstress, n ≥ 20 Trades) übersteht genau einer den Buy&Hold-Vergleich, und der nur wegen negativen Betas auf einem fallenden Symbol; der begrenzende Faktor bleibt die Risikoschicht (Stufe 3 unten), nicht die Signalqualität.

Die Abnahme erfolgte gegen dedizierte Unit-Tests (`automation/tests/test_issue_940_1106_*.py` bis `test_issue_959_1125_*.py` — Dateinamen tragen bewusst BEIDE Nummern, GitHub-Issue UND internes Katalog-Issue, da die interne Nummerierung über Sitzungen hinweg wiederverwendet wird und sonst mit älteren `test_issue_9XX_*.py`-Dateien kollidiert) sowie wiederholte volle Regressionsläufe (`comm -23`/`comm -13` gegen eine vor Sitzungsbeginn erfasste Baseline-Fehlerliste, 33 umgebungsbedingte Fehlschläge identisch vor/nach jedem Einzelschritt, keine neuen Regressionen über den gesamten Katalog) — kein echter Sweep-Re-Run, aus denselben Sandbox-Gründen wie die vorangegangenen Kataloge (kein installierbares `nautilus_trader` mit exakt gepinnter Version, Python 3.11 statt der geforderten ≥3.12).

### Umgesetzt in dieser Session

**Stufe 1 — Lauf-Identität (P0):**

**#940/#1106 — Zeitfensterbasierter Kohortenfilter erkennt eine vollständig enthaltene Fremdkohorte nicht.** `check_report_cohort_coherence` mass bislang eine Zeitspannweite/einen Versatz zum Laufbeginn — beides blind für einen Nachbarprozess, der zeitlich VOLLSTÄNDIG im Referenzfenster liegt. Fix: der Check urteilt seither ausschliesslich über Kohorten-IDENTITÄT (`record['run_id'] == run_id`); die drei ehemaligen Zeitklauseln laufen separat als reine Uhr-Drift-Diagnose weiter (`check_cohort_clock_drift`, severity `low`). Siehe Pitfall #394.

**#941/#1107 — Fail-Fast- und Report-Pfad meldeten unterschiedliche Offender-Zahlen für dieselbe Prüfung, im selben Lauf.** Fix: `InvariantResult` trägt seither ein `cohort`-Feld (`run_id`/`n_studies`/`symbols`/`source`), das jede Auswertung explizit deklariert; neue `check_cohort_declaration_consistency` blockiert bei einer Teilmengen-Verletzung oder einer zwischen Wellen schrumpfenden `n_studies`. Siehe Pitfall #396.

**#949/#1115 — Sweep-weite Aggregate liefen über eine potenziell kontaminierte Kohorte.** Fix: jede sweep-weite Aggregatgrösse (Budgetausführung, Worker-Occupancy, Expectancy-Kohärenz etc.) wird ausschliesslich über die run_id-verifizierte Kohorte berechnet, nicht über eine separat/unzureichend gefilterte Study-Menge. Siehe Pitfall #400.

**#942/#1108 — `run_status` überlud „echter Arbeitsabbruch" und „zulässig abgelehnt" in einem einzigen Wert.** Fix: drei orthogonale, additive Achsen (`work_completed`/`decision_admissible`/`fail_fast_triggered`) neben dem unverändert erhaltenen `run_status` (Rückwärtskompatibilität für elf bestehende Konsumenten); `summary_de._run_status_label_de` korrigiert den angezeigten Text, wenn `work_completed=True ∧ decision_admissible=False` (kein Widerspruch mehr zwischen „abgebrochen" und einem vollständig durchlaufenen Lauf).

**#943/#1109 — `check_champion_corroboration_reachable`-Abnahme.** Der fail-open ODER-Ast (Root-Cause eines FALSCH-PASS in vier Checks) war bereits durch #1089 (Vorsitzung) entfernt; diese Sitzung ergänzt die fehlende Regressionsabdeckung und korrigiert eine seit #1086 stehen gebliebene Falschbehauptung in AGENTS.md („Lock-Datei je Symbol" statt store-weit, siehe #944 unten).

**#944/#1110 — Store-Lock war ein exists-check+write-Muster (TOCTOU-Fenster zwischen Prüfung und Schreibvorgang).** Fix: `fcntl.flock`-basiertes Locking (`_sweep_run_lock_state`), atomar gegen zwei gleichzeitige Sweep-Prozesse auf demselben Store; `manuals/run_optimizer.md` um Abschnitt „5.4 Paralleler Mehr-Symbol-Betrieb" (`OPTIMIZER_WORK_DIR`, `--n-jobs`-Dimensionierung) ergänzt.

**Stufe 2 — Mess-Kommensurabilität (P0):**

**#945/#1111 — Kostenstress-Leiter und die berichtete Expectancy liefen auf verschiedenen Basen.** Ein 2×-Kostenstress erschien dadurch bei SqueezeBreakout/PLTR als Verbesserung um +145,76 bps. Fix: beide Grössen laufen seither auf `holdout_expectancy_capital_weighted`; neue blockierende `check_cost_stress_monotonicity` (Monotonie UND gleich grosse Schritte). Siehe Pitfall #397.

**#946/#1112 — Dust-Round-Trips (Notional ~1e-13, Fliesskomma-Residuen) verzerrten die berichtete Expectancy.** Fix: `backtest_runner._filter_dust_round_trips` verwirft sie AN DER ROUND-TRIP-QUELLE (`extract_metrics`, unmittelbar nach dem FIFO-Matching), vor jedem nachgelagerten Konsumenten (Kostenstress, Portfolio-/Fold-Metriken).

**#947/#1113 — `check_holding_time_cap` nannte eine andere Referenz als die tatsächlich gerechnete.** Der gemeldete Faktor war gegen `cap_s` gerechnet, der Text nannte `hard_threshold_s` (das 3-Fache) — jeder gemeldete Wert erschien dadurch exakt Faktor 3 zu gross. Fix: der Text nennt beide Grössen explizit UND benennt den tatsächlich verwendeten Nenner; `max_holding_time_s` zusätzlich in `provenance`. Siehe Pitfall #398.

**#948/#1114 — Fold-Annualisierung war innerhalb desselben Trials inkommensurabel.** Root-Cause: `_get_annualization_factor` leitet den Faktor empirisch aus der Beobachtungszahl JE FOLD ab — vier Folds desselben Trials (dieselbe Kalenderlänge) annualisieren mit vier verschiedenen Faktoren. Entscheidungspfade (`confirm._study_pbo`, `reward.fold_dispersion`) konsumierten bereits die annualisierungsfreie, period-scale Serie (frühere #665/#589/#683-Fixes) — `check_annualization_commensurability` mass jedoch weiterhin die (erwartete, triviale) Intra-Trial-Fold-Streuung. Fix: neu definiert auf die Streuung des EINEN studienweiten, gepoolten Faktors über Studies DESSELBEN Symbols; severity `high` → `low`. Siehe Pitfall #399.

**#959/#1125 — PBO als dominanter Ausgang (16 von 42 Studies).** Bereits vollständig durch #663/#683 (frühere Sitzung) gelöst: `confirm._study_pbo` rechnet auf einer eigenen CSCV-Partition (S ≥ 8 Gruppen, Default 12) der gepoolten `oos_period_returns`, mit einer annualisierungsfreien `group_sortino`-Split-Metrik — nicht auf den 4 Walk-Forward-Folds und nicht auf `per_fold_oos_sortino`/`oos_fold_sortinos`. `pbo_n_groups`/`pbo_n_configs` sind unbedingt im Artefakt geführt; bei < 8 Gruppen liefert PBO `None`. Diese Sitzung ergänzt die explizite #959/#1125-Regressionsabdeckung. Die geforderte empirische Neu-Auswertung der 16 PBO-Ablehnungen (confirm-only, ohne Re-Optimierung) erfordert einen echten, gespeicherten Study-Store und bleibt der realen Entwicklungsumgebung vorbehalten.

**Stufe 3 — Risikoschicht (P0, `simulation_semantics_version` 4 → 5):**

**#952/#1118 — `simulation_semantics_version` blieb bei 4, obwohl drei simulationsverändernde Fixes (#1092/#1094/#1096) bereits in AGENTS.md als umgesetzt dokumentiert waren.** Fix: Version 4 → 5; `_schema.fields.simulation_semantics_version` um einen v5-Changelog-Abschnitt (drei Auslöser + zwei explizit benannte Nicht-Auslöser #1093/#1095, Katalog #1086–#1104/#919–#937) ergänzt — derselbe Bump, der bereits als „Stufe 1" der Vorsitzung angekündigt, aber nie tatsächlich vollzogen worden war.

**#950/#1116 — Der Trailing-Stop ist keine Risikogrösse: fehlende Abnahmemessung mit klarem Zielwert.** Fix: neue `invariants.check_trailing_stop_risk_calibration_acceptance` — drei verbindliche Kriterien (Spearman(k·ATR, realisierter Verlust) ≥ 0,3, `realized_stop_loss_ratio` im Band [0,8; 3,0] für ≥ 80 % der Studies, gepoolter TRAILING_STOP-Anteil < 35 %). Erfordert einen echten Re-Run zur Abnahme (Stufen 11/12 des #960-Abnahmeprotokolls); der Code-Fix selbst (#1092/#1094) ist bereits im HEAD.

**#951/#1117 — Der ATR-Floor ist seit #1096 kostengekoppelt, aber im Report weiterhin nur als statische Konstante sichtbar.** Fix: `report._stamp_atr_floor_bps_derived` macht den tatsächlich SIMULIERTEN, kostengekoppelten (und damit i. d. R. höheren) Floor je Study sichtbar (`atr_floor_bps_derived`); `check_atr_scale_homogeneity`s Floor-Bindungs-Diagnose bevorzugt ihn gegenüber der groben, rein asset-class-aufgelösten Konstante. Fix-Punkte 2/3 des Ursprungs-Issues (ATR-Schätzer auf informativen Bars, `spaces.py`-Preflight über `constraints_func`) bleiben bewusst zurückgestellt — dieselbe Risikoabwägung wie bereits bei #1069 Punkte 2–4 dokumentiert (Änderung am Kern-ATR-Schätzer-/Sampling-Pfad ohne echten Mehrsymbol-Referenzlauf verifizierbar).

**#953/#1119 — Ein-Bar-Ausführungslatenz als eigentliche Verlustuntergrenze.** Symptom: innerhalb eines Symbols ist der realisierte Stop-Verlust nahezu konstant, während die Stopdistanz um Faktor 20 variiert — vereinbar mit „Verlust = adverse Bewegung EINER Bar" (der Exit ist ein Bar-Schluss-Signal, kein echter `StopMarketOrder`, #1092B bewusst zurückgestellt), nicht mit „Verlust = Stopdistanz + Überschiessen". Fix: `bar_range_median_bps`-Telemetrie end-to-end (`hourly_strategy_base`-Bar-Spannen-Ablesung → `BAR_RANGE_MEDIAN_BPS`-Exit-Tag → `backtest_runner._aggregate_exit_telemetry` → `parsing`/`report`), neue blockierende `check_stop_loss_vs_bar_range` (FAIL, wenn Verlust GLEICHZEITIG in der Grössenordnung einer Bar-Spanne UND ein grosses Vielfaches der konfigurierten Stopdistanz ist — latenz- statt stopgetrieben).

**Stufe 4 — Diagnose und Selektion (P1/P2):**

**#954/#1120 — `check_selection_statistic_economic_bias` behauptete eine Ursache, die sie nicht gemessen hatte.** Gemessen wurde ein Median-Return-Unterschied; der tatsächliche Diskriminator war die Trade-Zahl (Median 2 gegen 130, beide Kohorten-Mediane negativ — „profitabel" traf auf keine zu). Fix: Vergleich auf Expectancy JE TRADE umgestellt, zusätzlich konditioniert auf `n_trades ≥ min_trades` (Default 20); Trials darunter bilden eine separat ausgewiesene dritte Kategorie statt stillschweigend in eine Kohorte gemischt zu werden. Siehe Pitfall #401.

**#955/#1121 — `check_objective_branch_coverage` war eine blosse Umbenennung von `p_eligible`.** `branch == 'per_symbol'` ist per Konstruktion identisch zu `oos_eligible` (1804 von 1804 Trials über drei Läufe) — zwei Namen für dieselbe Beobachtung überzeichneten die Fehlerlage im Report. Fix: misst seither den Anteil INELIGIBLER Trials mit definierter Selektionsstatistik — eine Grösse, die `p_eligible` strukturell nicht duplizieren kann; derselbe Check-NAME bleibt erhalten, damit der bestehende #1082-Suchbudget-Deprioritisierungsmechanismus unverändert funktionsfähig bleibt. Siehe Pitfall #402.

**#956/#1122 — `gate_inventory.n_rejections` blieb invertiert (0 statt 140 für das Gate, das jeden Trial verwarf).** Root-Cause: der Inventur-Zähler (`oos_gate_deltas[gate] > 0`) und der Detail-Zähler (`is_rejection_detail_counts`) zielten auf verschiedene Grundgesamtheiten — `oos_gate_deltas` trägt den Key für ein Gate wie `oos_min_psr` nur, wenn die zugrundeliegende Statistik selbst definiert ist. Fix: `n_rejections` wird direkt aus `is_rejection_detail_counts` abgeleitet statt parallel gepflegt; die vormalige Kreuzprüfung `check_gate_inventory_coherence` wird dadurch zur Tautologie und entfällt ersatzlos.

**#957/#1123 — Multiplizität driftete zwischen gleichzeitigen Reports und widersprach ihrer eigenen Zerlegung.** Die Drift war bereits durch #1091 (eingefrorene, budget-basierte Sicht) adressiert, die Partitionslücke bereits durch #1102 (`n_family[symbol]` wird direkt aus `Σ n_family_stage1` abgeleitet, `check_n_family_partition` bereits `severity='blocking'`). Diese Sitzung ergänzt das fehlende Stück: `deflation_n_family_source` im Artefakt (`confirm.py`/`sweep.py`/`report.py`), benennt explizit, welche der beiden strukturell möglichen Quellen (per-Study-Stage1-N vs. symbolweite Summe) die Deflationsschwelle tatsächlich gespeist hat.

**#958/#1124 — Das Randlösungs-Veto feuerte in 5 von 6 Fällen ohne ausgewiesenen Grund.** Root-Cause: die einzige zuvor öffentlich sichtbare „Beweis"-Grösse (`winner_outside_default_bounds`) verlangte eine STRIKTE Bounds-Verletzung, während das Veto selbst bereits auf blosser Nähe (≤ 2 % vom Rand) feuert. Fix: `boundary_veto_evidence` (`sampled_value`/`active_bounds`/`default_bounds`/`distance_to_edge` je Parameter) EXAKT aus derselben Quelle exportiert, die auch `boundary_frac`/`boundary_directions` speist (`run_optimization._boundary_hit_analysis`, konsolidiert); neue blockierende `check_boundary_veto_has_evidence`.

### 🔒 Watertight Invariants (Issue-Katalog #1106–#1125) — für künftige Agenten

- **`invariants.check_report_cohort_coherence`/`check_cohort_clock_drift`** (`invariants.py`, #940/#1106) — Kohortenzugehörigkeit wird NUR über `run_id`-Identität entschieden; jede zeitfensterbasierte Kohortenprüfung ist strukturell blind für eine vollständig enthaltene Fremdkohorte (Pitfall #394).
- **`InvariantResult.cohort`** (`invariants.py`, #941/#1107) — jede neue Invariante, die über eine mehrfach ausgewertete Kohorte urteilt (Fail-Fast-Probe UND finaler Report), sollte dieses Feld setzen, sonst ist eine Divergenz zwischen den Wellen unsichtbar (Pitfall #396).
- **`invariants.check_annualization_commensurability`** (`invariants.py`, #948/#1114) — misst seit diesem Fix NICHT mehr die (erwartete, triviale) Intra-Trial-Fold-Streuung des annualisierten Sortino; kein Entscheidungspfad darf `per_fold_oos_sortino`/`oos_fold_sortinos` konsumieren (Grep-Test in `test_issue_948_1114_*`), nur `per_fold_oos_sortino_period`/gepoolte `oos_period_returns` (Pitfall #399).
- **`report.gate_inventory_table`** (`report.py`, #956/#1122) — `n_rejections` kommt aus `is_rejection_detail_counts`, nicht aus `oos_gate_deltas`; ein Gate, dessen zugrundeliegende Statistik oft undefiniert ist, hat sonst einen strukturell blinden Fleck in der Inventur.
- **`run_optimization._boundary_hit_analysis`** (`run_optimization.py`, #958/#1124) — die EINE Quelle für `boundary_frac`/`boundary_directions`/`boundary_veto_evidence`; ein künftiger vierter Konsument sollte dieselbe Funktion wiederverwenden statt die 2 %-Toleranzschwelle erneut zu implementieren.

**Sperrvermerke (aus dem #960-Katalogdokument, weiterhin gültig):** kein Sweep ohne getrenntes `--work-dir`/`OPTIMIZER_WORK_DIR` je Symbol (der Store-Lock verhindert es seit #944 technisch, die Doku lud vorher zum Gegenteil ein); kein Kapitaleinsatz auf einem Kandidaten dieses Referenzlaufs; kein weiterer Bounds-Rückschrieb, solange die produktiv gewordenen #1066-Overrides nicht zurückgenommen sind; keine weitere Gate-Entfernung aus `eligible_requires_all` (der #1076-Fix, jetzt zusätzlich durch #956/#1122 gehärtet, verhindert das korrekt); der Purge (`purge_stale_studies`) bleibt zwingend die LETZTE Aktion vor jedem Re-Run und setzt den #952/#1118-Bump voraus.

---

## Neue Pitfalls #394–#402 (Issue-Katalog #1106–#1125)

### 🔴 Pitfall #394 — Zeitliche Enthaltung kann Identität nicht entscheiden [Katalog #1106–#1125, GitHub-Issue #940]
**Symptom:** Ein Nachbarprozess, der nach dem Referenzlauf startet und vor ihm endet, liegt vollständig innerhalb jedes Zeitfensters. Kohortenzugehörigkeit wird über einen Identitätsstempel entschieden, nie über Zeitstempel.
**Root-Cause:** Ein zeitfensterbasierter Kohortenfilter (Spannweite, Versatz zum Laufbeginn, jede Kombination aus beidem) kann eine vollständig ENTHALTENE Fremdkohorte per Konstruktion nicht erkennen — Enthaltung ist keine Verletzung der Zeitbedingung, egal wie eng die Schwelle gewählt ist.
**Fix/Regel:** Kohortenzugehörigkeit wird ausschliesslich über einen bei Studienstart gestempelten Identitäts-Wert (`run_id`) entschieden; Zeitfenster-Heuristiken bleiben höchstens als NACHGELAGERTE, rein diagnostische Uhr-Drift-Prüfung erhalten, nie als primäre Verteidigungslinie (#1106).

### 🟢 Pitfall #395 — Eine „zweite Verteidigungslinie", die auf der Ausgabe der ersten arbeitet, ist keine [Katalog #1106–#1125, GitHub-Issue #940]
**Symptom:** Wenn die Kontroll-Invariante die bereits gefilterte Liste liest, ist sie mit dem Filter perfekt korreliert und kann dessen Versagen nicht entdecken. Dritte Instanz nach #1043 und #1070.
**Root-Cause:** Eine als unabhängig gedachte zweite Prüfung, die dieselbe (bereits durch die erste Prüfung gefilterte/transformierte) Datengrundlage konsumiert, ist strukturell nicht unabhängig — sie kann per Definition nur bestätigen, was die erste bereits durchgelassen hat.
**Fix/Regel:** Eine echte zweite Verteidigungslinie prüft auf einer ANDEREN Evidenzachse (z. B. ein unabhängig geschriebener Ereignisstrom statt derselben Trial-`user_attrs`, die der Primärfilter bereits konsumiert hat) — `check_report_cohort_event_stream_coherence` gegen `optimizer_study_completed`-Ereignisse statt gegen die bereits gefilterten `studies_out` (#1106).

### 🟢 Pitfall #396 — Zwei Auswertungen derselben Invariante im selben Prozess müssen ihre Kohorte deklarieren [Katalog #1106–#1125, GitHub-Issue #941]
**Symptom:** Fail-Fast-Pfad und Report-Pfad haben in diesem Lauf 12 gegen 25 Offender gemeldet — für dieselbe Prüfung, im selben Lauf. Jede `InvariantResult` trägt, worüber sie geurteilt hat.
**Root-Cause:** Eine Invariante, die zu zwei verschiedenen Zeitpunkten im selben Prozess ausgewertet wird (Zwischen-Probe vs. finaler Report), kann strukturell unterschiedliche Kohorten sehen (die Study-Menge wächst zwischen den Wellen) — ohne eine explizite Deklaration ist diese Divergenz von einem echten Messfehler nicht unterscheidbar.
**Fix/Regel:** Jede `InvariantResult` trägt ein `cohort`-Feld (Identität/Grösse/Quelle der geprüften Menge); eine dedizierte Konsistenzprüfung blockiert, wenn eine spätere Welle eine ECHTE Teilmengen-Verletzung oder eine schrumpfende Kohorte gegenüber einer früheren zeigt (#1107).

### 🟢 Pitfall #397 — Eine Stress-Kennzahl wird aus derselben Grösse abgeleitet, die berichtet und sortiert wird [Katalog #1106–#1125, GitHub-Issue #945]
**Symptom:** Sonst erscheint eine Verschärfung als Verbesserung, sobald die beiden Basen auseinanderlaufen — sichtbar in 1 von 42 Studies, unsichtbar in 41.
**Root-Cause:** Eine Stress-/Sensitivitätsleiter, die aus einer ANDEREN Basis abgeleitet wird als der Wert, gegen den sie berichtet/sortiert wird, kann bei divergierenden Populationen (z. B. unterschiedliche Ausreisser-Behandlung) eine schärfere Stressstufe als scheinbare Verbesserung ausweisen — ein stilles, seltenes, aber ökonomisch verzerrendes Artefakt.
**Fix/Regel:** Die Stress-Leiter wird aus GENAU der Basis abgeleitet, gegen die sie auch berichtet wird; ein Regressionswächter prüft Monotonie UND gleich grosse Schritte auf dieser einen Basis (#1111).

### 🟢 Pitfall #398 — Der Meldungstext nennt die Referenz, gegen die tatsächlich gerechnet wurde [Katalog #1106–#1125, GitHub-Issue #947]
**Symptom:** `check_holding_time_cap` dividiert durch `cap_s` und druckt `hard_threshold_s`: jeder gemeldete Wert ist gegen die genannte Referenz exakt Faktor 3 zu gross.
**Root-Cause:** Ein Meldungstext, der eine ANDERE Grösse benennt als die tatsächlich im Nenner/als Referenz verwendete, führt jeden Leser, der zurückrechnet, zu einem falschen Ergebnis — der Fehler liegt nicht in der Berechnung, sondern in der Beschriftung.
**Fix/Regel:** Der Text nennt IMMER explizit die tatsächlich verwendete Referenzgrösse (nicht nur eine plausibel klingende verwandte Konstante); wo mehrere verwandte Grössen existieren, werden alle benannt UND die tatsächlich verwendete markiert (#1113).

### 🟡 Pitfall #399 — Ein Median über verschieden skalierte Fold-Werte ist kein Median [Katalog #1106–#1125, GitHub-Issue #948]
**Symptom:** Die √F-Spannweite innerhalb eines Trials liegt bei Median 1,709 und max 3,651; in 90 Trials wechselt der Fold-Median das Vorzeichen mit der Skala. Fold-übergreifend wird auf der Perioden-Skala aggregiert, annualisiert wird danach, einmal.
**Root-Cause:** Ein empirisch aus der Beobachtungszahl abgeleiteter Annualisierungsfaktor ist eine Funktion der tatsächlich gehandelten Perioden (ereignisgetrieben) — und variiert dadurch selbst INNERHALB eines Trials zwischen Folds derselben Kalenderlänge. Jede fold-übergreifende Mittelung/Median-Bildung auf der annualisierten Serie mittelt dadurch inkommensurable Grössen.
**Fix/Regel:** Jede fold-übergreifende Aggregation (PBO/CSCV, Fold-Median-Diagnose) konsumiert ausschliesslich die period-scale Serie; annualisiert wird NACH der Aggregation, einmal, mit einem einzigen studienweiten Faktor. Eine Kommensurabilitäts-Prüfung auf der Intra-Trial-Fold-Streuung ist bei einem ereignisgetriebenen Faktor strukturell erwartet (trivial) und gehört nicht auf `severity=high` — die eigentliche Diagnose ist die Streuung des EINEN studienweiten Faktors über Studies desselben Symbols (#1114).

### 🟡 Pitfall #400 — Aggregat-Kennzahlen über eine kontaminierte Kohorte sind keine Lauf-Kennzahlen [Katalog #1106–#1125, GitHub-Issue #949]
**Symptom:** „Median Budgetausführung 0,0 %" bei tatsächlich 100 % ist keine Nebensache: sie speist `check_budget_execution`, den Denylist-Rückschrieb und die Zusammenfassung.
**Root-Cause:** Eine sweep-weite Aggregatgrösse, die über eine unzureichend/separat gefilterte Study-Menge berechnet wird, erbt jede Kontamination dieser Menge — und trägt sie in JEDE nachgelagerte Entscheidung (Gate, Rückschrieb, Zusammenfassung), nicht nur in die Anzeige.
**Fix/Regel:** Jede sweep-weite Aggregatgrösse wird über GENAU dieselbe, bereits identitätsgeprüfte Kohorte berechnet wie der restliche Report — eine zweite, separat gefilterte oder ungefilterte Studien-Liste für Aggregate ist ein struktureller Kontaminationskanal (#1115).

### 🟢 Pitfall #401 — Eine Invariante darf keine Ursache behaupten, die sie nicht gemessen hat [Katalog #1106–#1125, GitHub-Issue #954]
**Symptom:** „Die Selektion verwirft bevorzugt die profitable Kohorte" — gemessen wurde ein Return-Unterschied zwischen einer Kohorte mit Median 2 Trades und einer mit 130. Wiederkehr #1052.
**Root-Cause:** Ein Return-/Performance-Vergleich ohne Konditionierung auf Trade-Zahl/Exposure belohnt Nicht-Handeln monoton — der numerische Unterschied zwischen zwei Kohorten kann vollständig durch einen Konfund (hier: Stichprobengrösse) erklärt sein, ohne dass die behauptete ökonomische Kausalität geprüft wurde.
**Fix/Regel:** Der Vergleich läuft auf einer trade-normierten Grösse (Expectancy je Trade, nicht kumulativer Return) UND wird auf eine Mindest-Trade-Zahl konditioniert; Beobachtungen darunter bilden eine eigene, separat ausgewiesene Kategorie statt in eine der Vergleichskohorten gemischt zu werden; der Meldungstext nennt nur das Gemessene, keine unbelegte Kausalaussage (#1120).

### 🟢 Pitfall #402 — Ein Diagnose-Check, der zu einer bestehenden Kennzahl äquivalent ist, erzeugt Scheinbelege [Katalog #1106–#1125, GitHub-Issue #955]
**Symptom:** `branch=='per_symbol'` ⟺ `oos_eligible` in 1804 von 1804 Trials: zwei Befunde für eine Beobachtung überzeichnen die Fehlerlage.
**Root-Cause:** Zwei unterschiedlich benannte Prüfungen, die (per Konstruktion oder empirisch nachgewiesen) dieselbe zugrundeliegende Beobachtung messen, erzeugen bei einem einzigen Defekt ZWEI Meldungen im Report — das überzeichnet die tatsächliche Fehlerlage, ohne zusätzliche Information zu liefern.
**Fix/Regel:** Vor dem Hinzufügen einer neuen Diagnose-Invariante prüfen, ob eine bestehende Kennzahl bereits dieselbe Grundgesamtheit misst (hier: `p_eligible`); ist das der Fall, entweder ersatzlos streichen oder auf eine Grösse umstellen, die die bestehende Kennzahl strukturell NICHT duplizieren kann (#1121).
