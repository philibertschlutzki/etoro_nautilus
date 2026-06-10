# Code-Audit — Zero-Hardcoding & AGENTS.md-Konsistenz (`automation/`)

> **Scope:** Nur das `automation/`-Paket (Standalone-Produkt).
> **Ziel:** (1) Architektur-Review, (2) jeder **ergebnisrelevante** Parameter wird über `automation/config/*.json` gesteuert statt im Python-Code verankert, (3) `AGENTS.md` deckt sich zu 100 % mit dem Code.
> **Methodik:** Statischer Forensik-Durchlauf über alle gelieferten Module + Cross-Check Config ↔ Code ↔ AGENTS.md.
> **Constraint:** Die Code-Funktion darf nicht brechen. Refactors sind verhaltenswahrend; echte Bugs sind als solche markiert.

## Klassifizierung & Fix-Typen

| Prio | Bedeutung |
|------|-----------|
| **P1** | Korrektheits-/Crash-Defekt — verfälscht Ergebnisse oder bricht Recovery-Pfade |
| **P2** | Zero-Hardcoding-Verletzung **oder** Architektur-Schmutz (tote Parameter, falsche Pfade) |
| **P3** | `AGENTS.md` ↔ Code/Config-Diskrepanz (Nachvollziehbarkeit) |

| Typ | Bedeutung | Folge für Ergebnisse |
|-----|-----------|----------------------|
| **R** (Refactor) | Magic Number → Config mit **identischem** Default | Ergebnis ändert sich **nicht** — reine Nachvollziehbarkeit |
| **S** (Semantik-Fix) | Behebt einen echten Bug | Ergebnis **ändert sich** → Validierung/Regression zwingend |
| **D** (Doku) | Reine `AGENTS.md`-Korrektur | Kein Code-Impact |

> **Wichtig für Jules:** Alle **R**-Issues sind so umzusetzen, dass die bestehenden Default-Werte 1:1 in die JSON wandern (gleiche Zahlen) — die Test-Suites (`test_backtest_runner.py`, `test_sizing_precedence.py`, `test_tournament_validation.py`, `test_backtest_trades_generated.py`) müssen unverändert grün bleiben. Die **S**-Issues ändern Zahlen bewusst und benötigen einen frischen Baseline-Lauf + Anpassung der erwarteten Werte.

---

## Architektur-Gesamtbewertung

Die Architektur ist im Kern sauber: Standalone-Prinzip wird (bis auf zwei Fundstellen, siehe ISSUE-18) eingehalten, die Precision-Heuristik ist zentralisiert (`utils._fallback_precisions`), die FSB(16)-Pipeline ist konsistent und durch einen Build-Guard (`_serde.py`) abgesichert, und das Tournament-/OOS-Gating ist gut dokumentiert.

Die **Zero-Hardcoding-Regel ist jedoch an mehreren ergebnisrelevanten Stellen verletzt**. Die gravierendste Klasse betrifft das **Position-Sizing und die Risk-Metrik-Caps**: Werte, die direkt über Turniergewinner und Live-Deployment entscheiden, stehen als Literale im Code. Zusätzlich existieren **vier funktionale Defekte**, von denen zwei (ISSUE-02, ISSUE-03) die Backtest-Ergebnisse still verfälschen und einer (ISSUE-01) den sequentiellen Fallback zum Absturz bringt.

Gefunden: **4× P1**, **15× P2**, **5× P3** (24 Issues).

---

# P1 — Korrektheits- & Crash-Defekte

## ISSUE-01 — `_run_remaining_sequentially` Aufruf ohne `span_tolerance_days` → `TypeError` im BrokenProcessPool-Fallback `[S]`

**Symptom:** Stürzt der `ProcessPoolExecutor` ab (OOM o. ä.), bricht der eigentlich rettende sequentielle Fallback sofort mit `TypeError: _run_remaining_sequentially() missing 1 required positional argument: 'span_tolerance_days'` ab. Der gesamte Backtest-Lauf geht verloren, statt sequenziell weiterzulaufen.

**Root Cause:** `backtest_runner.py` — die Signatur wurde um drei Parameter erweitert:
```python
def _run_remaining_sequentially(..., total_jobs, span_tolerance_days,
                                commission_bps=0.0, spread_bps_by_asset_class=None):
```
`span_tolerance_days` hat **keinen** Default. Die einzige Aufrufstelle (im `_BrokenPool`-Handler) übergibt aber nur 12 Positionsargumente bis `total_jobs` und lässt `span_tolerance_days`, `commission_bps`, `spread_bps_by_asset_class` weg:
```python
_run_remaining_sequentially(
    futures, future, strategies_list, catalog_path,
    start_ns, end_ns, start_capital, args.htmlreport,
    reports_dir, all_results, done_count, total_jobs,
)
```
Verstärkt durch Pitfall #30 (Worker-Signatur-Sakralität), die hier offensichtlich nicht auf den Fallback-Wrapper übertragen wurde.

**Fix:** Aufrufstelle um `span_tolerance_days, commission_bps, spread_bps_by_asset_class` ergänzen (Werte liegen im Scope von `run_backtest()` vor). Zusätzlich Regressionstest: simulierter `BrokenProcessPool` muss den Fallback-Wrapper fehlerfrei aufrufen.

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/tests/test_backtest_runner.py`

---

## ISSUE-02 — Aggregat-OOS-Metriken mit hartkodiertem `starting_capital = 100_000.0` statt `10_000` aus `backtest.json` `[S]`

**Symptom:** Der `total_return` und `max_drawdown` des `aggregate_winner` (Portfolio-Equity-Kurve) sind um **Faktor 10 zu klein** gegenüber den Einzel-Symbol-Metriken. Das Phase-5-OOS-Gate (`oos_min_total_return: 0.005`, `oos_max_drawdown`) bewertet damit einen systematisch unterschätzten Return → Strategien fallen fälschlich durch das Aggregat-Gate (oder bestehen ein DD-Gate, das sie nicht bestehen dürften).

**Root Cause:** `backtest_runner.py::select_winners` rekonstruiert die Portfolio-Metriken über `_calculate_stats`, das Renditen als `pnl / starting_capital` berechnet. Das `starting_capital` wird hier aber aus `strat_params` zu lesen versucht — ein Schlüssel, der dort nie existiert (Start-Kapital ist ein *global_setting*, kein Strategie-Param):
```python
starting_capital = 100_000.0
if is_eligible_population:
    starting_capital = is_eligible_population[0].get("strat_params", {}).get("starting_capital", 100_000.0)
```
→ greift **immer** der hartkodierte Fallback `100_000.0`, während die Einzel-Backtests mit `start_capital = 10_000` (aus `backtest.json`) liefen. (Sortino/PF sind skaleninvariant und bleiben korrekt; nur `total_return` und `max_drawdown` brechen.)

**Fix (Typ S — ändert Aggregat-Zahlen):**
1. Echtes `start_capital` in jedes Result-Dict durchreichen: in `run_single_backtest_worker` `"start_capital": start_capital` ergänzen.
2. In `select_winners` `starting_capital` aus dem Result lesen (Fallback: `load_config(backtest.json)["start_capital"]`, **nicht** Literal `100_000.0`).
3. Baseline-Lauf erneuern; OOS-Gate-Schwellen ggf. nachjustieren.

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/daily_orchestrator.py` (Logging der korrigierten Metriken), `automation/tests/test_oos_aggregation.py`

---

## ISSUE-03 — Krypto-Instrumente mit `asset_class="equity"` (+ `"Unknown"`) → falsche Spread-Zuordnung im Backtest `[S]`

**Symptom:** Alle Krypto-Assets (BTC, ETH, SOL, …) erhalten im Backtest **8 bps** (EQUITY) statt der konfigurierten **15 bps** (CRYPTO). Diverse Equities mit `asset_class="Unknown"` (MSTR, AMD, TER, AMAT, …) fallen auf **DEFAULT 4 bps** statt EQUITY 8 bps. Das verzerrt Risk-Metriken und damit die Turnierauswahl — und widerspricht direkt der Annahme in AGENTS.md Issue #232 („eToros weite Crypto-Spreads (15 bps)").

**Root Cause:** Die Spread-Auswahl in `backtest_runner.py::run_single_backtest_worker` liest die Asset-Klasse aus `instrument_map.json` und schlägt sie in `spread_bps_by_asset_class` nach:
```python
asset_class_key = inst_data.get("asset_class", "DEFAULT").upper()
spread_bps = spread_bps_by_asset_class.get(asset_class_key, spread_bps_by_asset_class.get("DEFAULT", 4.0))
```
In `instrument_map.json` sind aber **alle Krypto-Einträge fälschlich als `"asset_class": "equity"`** getaggt (z. B. `"100000": {"symbol": "BTC.ETORO", "asset_class": "equity", "size_precision": 8}`), und ein ganzer Block trägt `"asset_class": "Unknown"`. `"UNKNOWN"` existiert nicht im Spread-Mapping → DEFAULT.

> Hinweis: Die *size_precision* bleibt korrekt (8 für Krypto), weil `_normalize_size_precision` über das **Symbol** (`_fallback_precisions`) entscheidet, nicht über `asset_class`. Betroffen ist ausschließlich die spread-/asset-class-gesteuerte Logik.

**Fix (Typ S — ändert Krypto/Unknown-Metriken):**
1. `instrument_map.json` korrigieren: Krypto → `"crypto"`, „Unknown"-Equities → reale Klasse (über `universe_fetcher`-Metadaten resolvbar). 
2. Defensive Härtung: unbekannte `asset_class` im Worker explizit loggen (`WARNING`), damit künftige Fehl-Tags nicht still auf DEFAULT rutschen.
3. Optional: `asset_class` in `_fallback_precisions`-Logik gegenprüfen (Krypto-Symbol-Set ist bereits vorhanden in `utils._CRYPTO_SYMBOLS`).

**Betroffene Dateien:** `automation/config/instrument_map.json`, `automation/backtest_runner.py`, `automation/universe_fetcher.py` (Tag-Auflösung)

---

## ISSUE-04 — Hartkodierte `trade_amount_usd`-Injektion überschreibt konfigurierbares `trade_amount_pct` `[S/R]`

**Symptom:** Der konfigurierte Wert `trade_amount_pct: 15.0` (`strategy_defaults.json`) ist im Backtest **wirkungslos**. Das Sizing wird ausschließlich durch hartkodierte Literale `0.15` und `500.0` im Runner bestimmt. Ändert man `trade_amount_pct` in der Config auf z. B. 20.0, bleibt der Backtest bei 15 % — eine klassische „tote Config" + Zero-Hardcoding-Verletzung.

**Root Cause:** `backtest_runner.py::run_single_backtest_worker` injiziert vor der Strategie-Instanziierung:
```python
test_params["trade_amount_usd"] = 1500.0
config = ConfigCls(**test_params)
params["trade_amount_usd"] = max(500.0, start_capital * 0.15)
```
Die Sizing-Präzedenz in `hourly_strategy_base._compute_quantity` priorisiert `trade_amount_usd` (Zweig B) über `trade_amount_pct` (Zweig C), sobald `trade_amount_usd != 100.0`. Da hier `1500.0` injiziert wird, greift immer Zweig B → `trade_amount_pct` wird übersprungen. Dass `0.15 == 15.0 %` ist, **maskiert** den Bug aktuell numerisch.

**Fix (Typ R — aktuell verhaltensneutral, da 0.15 == Config-pct):**
1. Injektion entfernen. Den Worker stattdessen das in der Config (Manifest/Defaults) gesetzte `trade_amount_pct` nutzen lassen — das ist bereits der dokumentierte Standardpfad (AGENTS.md §6/§7).
2. Falls weiterhin ein absoluter USD-Override im Backtest gewünscht ist, diesen aus `backtest.json` lesen (`backtest_trade_amount_pct` o. ä.), **nicht** als Literal.
3. Zusammen mit ISSUE-19 lösen (fragile `!= 100.0`-Erkennung).

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/strategies/hourly_strategy_base.py`, `automation/tests/test_sizing_precedence.py`

---

# P2 — Zero-Hardcoding (ergebnisrelevante Magic Numbers)

## ISSUE-05 — `RATIO_CAP = 50.0` + Sentinel-Skalierung hartkodiert `[R]`

**Symptom/Root Cause:** Das harte Winsorizing-Cap für Sortino/Profit-Factor/Calmar steht als `RATIO_CAP = 50.0` in `_calculate_stats`. Die Sentinel-Skalierung in `select_winners` nutzt dieselbe Zahl mehrfach literal: `min(50.0, max(2.0, 50.0 * (n_trades / 50.0)))`, plus überall `v != 50.0`-Filter. Diese Werte entscheiden über Turnier-Rankings und die Median-Aggregation (AGENTS.md Issue #305/#288), sind aber nicht konfigurierbar. Es existiert bereits `optimizer.json.sortino_clip_abs` (5.0) für einen *anderen* Cap — die beiden dürfen nicht verwechselt werden.

**Fix:** Nach `tournament.json` auslagern: `ratio_cap` (50.0), `sentinel_floor` (2.0). Den `!= 50.0`-Filter an `ratio_cap` koppeln (nicht an Literal), sonst bricht die Sentinel-Filterung bei künftiger Cap-Änderung still.

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/config/tournament.json`

---

## ISSUE-06 — Low-Sample-Gates `n < 5`, `losses_count < 2`, `n < 50` hartkodiert `[R]`

**Symptom/Root Cause:** Mehrere ergebnisrelevante Stichproben-Schwellen sind im Code verankert (in `_calculate_stats`, `_is_eligible`, `_evaluate_oos_eligibility`):
- `n < 5` → Sortino wird `None` (Mindest-Round-Trips).
- `losses_count < 2 and n < 50` → PF/Sortino werden `None` (Minimum-Downside-Gate, AGENTS.md #37/#43).
Diese Werte steuern, welche Paare überhaupt eligibel werden, stehen aber in keiner JSON.

**Fix:** Nach `tournament.json`: `min_sample_sortino` (5), `min_losses_for_ratio` (2), `low_sample_trade_threshold` (50). Im Startup-Header mit ausgeben (Observability-Regel / Pitfall #46).

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/config/tournament.json`

---

## ISSUE-07 — Micro-Sizing-Floor `< 10.0` doppelt hartkodiert `[R]`

**Symptom/Root Cause:** Der Median-Notional-Floor (`median_position_notional < 10.0`) steht zweimal literal — in `_is_eligible` **und** `_evaluate_oos_eligibility`. Divergenzgefahr; nicht konfigurierbar.

**Fix:** `tournament.json.min_position_notional_usd` (10.0), an genau einer Stelle gelesen und an beide Gates übergeben.

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/config/tournament.json`

---

## ISSUE-08 — eToro-Mindestbetrag `11.0` doppelt hartkodiert (`MIN_TRADE_USD` + Allocator) `[R]`

**Symptom/Root Cause:** Der $11-Floor existiert zweifach: `MIN_TRADE_USD = 11.0` in `hourly_strategy_base._compute_quantity` und `if allocation_per_signal < 11.0` in `momentum_ls_allocator.py`. AGENTS.md deklariert ihn als „Konstante Floor" (Pitfall #45) — er ist aber ein **ergebnisrelevanter** Schwellwert und doppelt gepflegt (DRY-Verletzung).

**Fix:** Eine Quelle, z. B. `backtest.json.etoro_min_order_usd` (11.0), von Strategy-Layer und Allocator gelesen. Falls bewusst „hart" gewünscht, mindestens als **eine** benannte Modul-Konstante in `utils.py` konsolidieren statt zwei Literale.

**Betroffene Dateien:** `automation/strategies/hourly_strategy_base.py`, `automation/momentum_ls_allocator.py`, `automation/config/backtest.json` (oder `automation/utils.py`)

---

## ISSUE-09 — `max_daily_trades = 5` existiert nur als Code-Default (Whipsaw-Guard) `[R]`

**Symptom/Root Cause:** Der globale Whipsaw-Detektor (`HourlyStrategyConfig.max_daily_trades: int | None = 5`) blockiert Entries ab 5 Trades/Tag — ein ergebnisrelevanter Risk-Parameter, der in **keiner** JSON steht (weder `strategy_defaults.json` noch `strategies.json`). Tuning erfordert aktuell eine Code-Änderung. Das Autotuner-Konzept friert ihn explizit als „eingefroren" ein — dann muss er aber dokumentiert in der Config liegen, nicht als Code-Default.

**Fix:** Nach `strategy_defaults.json` (pro Strategie) bzw. als globaler Eintrag in `backtest.json`. Default 5 beibehalten.

**Betroffene Dateien:** `automation/strategies/hourly_strategy_base.py`, `automation/config/strategy_defaults.json`

---

## ISSUE-10 — Kohorten-/Regime-Bias-Schwelle „1 Tag in ns" hartkodiert `[R]`

**Symptom/Root Cause:** Die Data-Start-Alignment-Warnung (Issue #148) nutzt eine literale Schwelle `86400 * 1_000_000_000` („Threshold: 1 day in nanoseconds") in `select_winners`. Sie steuert, ob ein Regime-Bias geloggt wird.

**Fix:** `backtest.json.cohort_alignment_tolerance_days` (1.0).

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/config/backtest.json`

---

## ISSUE-11 — ComboTrendVwap: Trend-Toleranz `0.98`/`1.02` und `bb_touch_window = 10` hartkodiert `[R]`

**Symptom/Root Cause:** In `tesla_combo_strategy.on_bar` stehen ergebnisrelevante Entry-Schwellen literal:
```python
trend_bullish = close_price > (self.sma.value * 0.98)
trend_bearish = close_price < (self.sma.value * 1.02)
... and self.bars_since_bb_touch <= 10
```
Die 2 %-Trendbänder und das 10-Bar-BB-Fenster sind nicht in der Config (obwohl das Autotuner-Konzept genau diese Strategie optimieren will).

**Fix:** `ComboTrendVwapConfig` + `strategy_defaults.json`: `trend_tolerance_pct` (0.02), `bb_touch_window` (10).

**Betroffene Dateien:** `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json`

---

## ISSUE-12 — Annualisierungsfaktor `math.sqrt(252)` hartkodiert `[R]`

**Symptom/Root Cause:** Die Sortino-Annualisierung nutzt `math.sqrt(252)` in `_calculate_stats`. Für 1h-Bars und 24/7-Krypto ist 252 (Aktienhandelstage) methodisch fragwürdig; in jedem Fall ein nicht-konfigurierbarer, metrik-bestimmender Faktor. (Er ist über alle Paare konstant und verändert das *relative* Ranking nicht — daher P2/R, nicht P1 — sollte aber explizit konfiguriert/dokumentiert sein.)

**Fix:** `backtest.json.annualization_periods` (252) mit Schema-Kommentar zur Wahl bei Stunden-/Krypto-Daten.

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/config/backtest.json`

---

## ISSUE-13 — `_warmup_trend_filter`: Buffer `+48`/`+10` und Dummy-Bar-Precision `4` hartkodiert `[R]`

**Symptom/Root Cause:** In `hourly_strategy_base._warmup_trend_filter`:
- `needed_hours = self.trend_filter_period + 48` und `warmup_bars = bars_1h.tail(self.trend_filter_period + 10)` — literale Puffer.
- `Price(close_price, 4)` / `Quantity(1.0, 4)` — **hartkodierte Precision 4** für die Dummy-Bars, unabhängig vom realen Instrument (potenziell falsch für 2-/8-Dezimal-Symbole; Korrektheits-Nuance).

**Fix:** Puffer als benannte Konstanten/Config (`trend_warmup_buffer_bars`); Precision aus `_fallback_precisions(symbol)` statt Literal `4`. Siehe auch ISSUE-17 (Pfad-Bug derselben Funktion).

**Betroffene Dateien:** `automation/strategies/hourly_strategy_base.py`, `automation/config/backtest.json`

---

## ISSUE-14 — Fallback-Defaults duplizieren die Config still `[R]`

**Symptom/Root Cause:** An vielen Stellen werden Config-Werte über `.get(key, <Literal>)` gelesen, wobei das Literal den Config-Wert **dupliziert**. Driftet die Config, weicht das Literal still ab. Beispiele:
- `spread_bps_by_asset_class.get("DEFAULT", 4.0)` (Worker)
- `tournament_cfg.get("k_shrinkage", 20.0)`
- `compute_tournament_score`-Defaults `0.4 / 0.3 / 0.2 / 0.1`
- `daily_orchestrator`: `wf_cfg.get("warmup_days", 60)` — **`warmup_days` existiert gar nicht** in `backtest.json.walk_forward`, also greift immer `60` (steuert die Historien-Tiefe des `historical_fetcher` → ergebnisrelevant!).
- `_build_backtest_config`: `is_window_days(120)/splits(1)/oos_window_days(30)`-Fallbacks.

**Fix:** Für ergebnisrelevante Keys (insb. `warmup_days`) den Schlüssel **explizit** in `backtest.json` aufnehmen und das Literal entfernen; wo ein Fallback nötig ist, zentral definieren (kein verstreutes Duplikat). `warmup_days: 60` in `backtest.json.walk_forward` ergänzen + im Schema dokumentieren.

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/config/backtest.json`, `automation/config/tournament.json`

---

# P2 — Architektur / Tote Parameter / latente Bugs

## ISSUE-15 — `keltner_atr_period` wird nie an `KeltnerChannel` übergeben (toter Parameter) `[S/D]`

**Symptom/Root Cause:** `mean_reversion.py` und `hourly_mean_reversion.py` führen `keltner_atr_period` in der Config (und in `strategy_defaults.json`: `keltner_atr_period: 20` bzw. `10`), instanziieren den Indikator aber ohne diesen Wert:
```python
self.keltner = KeltnerChannel(period=config.keltner_period, k_multiplier=config.keltner_multiplier)
```
→ `keltner_atr_period` ist **wirkungslos**. **AGENTS.md behauptet das Gegenteil:** der Eintrag „🟢 KeltnerChannel `atr_period` Mismatch … **Fix:** Parameter korrekt übergeben." ist faktisch falsch.

**Fix:** Entweder (a) den Parameter tatsächlich an den Indikator übergeben (falls die Nautilus-`KeltnerChannel`-Signatur ein separates ATR-Period-Argument unterstützt — **vor Umsetzung verifizieren**), oder (b) den toten Parameter aus Config-Klasse, `strategy_defaults.json` und Optimizer-Suchraum entfernen. In **beiden** Fällen AGENTS.md korrigieren (der Pitfall ist aktuell eine Falschaussage).

**Betroffene Dateien:** `automation/strategies/mean_reversion.py`, `automation/strategies/hourly_mean_reversion.py`, `automation/config/strategy_defaults.json`, `automation/AGENTS.md`

---

## ISSUE-16 — `ComboTrendVwapConfig.bb_entry_tolerance` ist toter Parameter `[R/D]`

**Symptom/Root Cause:** `bb_entry_tolerance` ist in `ComboTrendVwapConfig` deklariert und in `strategy_defaults.json` mit `1.0` gesetzt, wird in `on_bar()` aber **nie referenziert** (das BB-Touch-Fenster nutzt `atr_tolerance = atr * atr_multiplier`). Das Autotuner-Konzept benennt dies bereits explizit als „toten Parameter, vor Aufnahme verifizieren" — er ist aber noch in der Config.

**Fix:** Entfernen aus Config-Klasse + `strategy_defaults.json` (oder tatsächlich verdrahten). Nicht in den Optimizer-Suchraum aufnehmen.

**Betroffene Dateien:** `automation/strategies/tesla_combo_strategy.py`, `automation/config/strategy_defaults.json`

---

## ISSUE-17 — `_warmup_trend_filter` nutzt falschen Katalog-Pfad `[S]`

**Symptom/Root Cause:** Der Trend-Filter-Warmup liest aus
```python
parquet_dir = Path("data/nautilus/quote_tick") / str(self.instrument_id)
```
Der reale Katalog liegt unter `data/nautilus/**data**/quote_tick/SYMBOL/` (vgl. `QUOTE_TICK_PATH = CATALOG_PATH / "data" / "quote_tick"`). Das `/data/`-Segment fehlt → Warmup findet **nie** Parquet-Daten → `can_go_long()` liefert dauerhaft `False` (fail-closed), d. h. eine aktivierte Trend-Filter-Strategie generiert keine BUY-Signale. Aktuell latent, weil `trend_filter_period` per Default `0` ist — aber sobald über Config aktiviert, ist die Strategie still tot.

**Fix:** Pfad auf `data/nautilus/data/quote_tick` korrigieren; idealerweise aus der zentralen `CATALOG_PATH`-Konstante ableiten statt String-Literal. Regressionstest mit aktiviertem `trend_filter_period`.

**Betroffene Dateien:** `automation/strategies/hourly_strategy_base.py`

---

## ISSUE-18 — `_build_backtest_config` Fallback verletzt Standalone-Prinzip (Root-Modulpfad) `[S]`

**Symptom/Root Cause:** Der Notfall-Fallback in `daily_orchestrator._build_backtest_config` (wenn `strategies.json` leer/unlesbar) referenziert das **Root**-Modul:
```python
strategies = [{"strategy_module": "strategies.sma_crossover", ...}]
```
Das verletzt das harte Standalone-Constraint (§4) und würde beim Import scheitern (`strategies/` ist nach `archive/` verschoben). Greift selten, ist aber ein latenter Crash + Architektur-Verletzung.

**Fix:** `"automation.strategies.sma_crossover"` (+ korrekte `config_class`). Optional: `tests/test_automation_isolation.py` um eine AST-Prüfung dieses Literals erweitern.

**Betroffene Dateien:** `automation/daily_orchestrator.py`

---

## ISSUE-19 — Fragile Sizing-Präzedenz via `trade_amount_usd_cfg != 100.0` `[R]`

**Symptom/Root Cause:** `_compute_quantity` unterscheidet „explizit gesetzt" von „Default" durch einen Float-Vergleich gegen den hartkodierten Default `100.0`:
```python
elif trade_amount_usd_cfg is not None and trade_amount_usd_cfg > 0 and trade_amount_usd_cfg != 100.0:
```
Das koppelt die Steuerlogik an einen Magic-Default und bricht, sobald (a) der Default in der Config-Klasse geändert wird oder (b) jemand legitim `trade_amount_usd = 100.0` setzen will. In Kombination mit ISSUE-04 ist dies der Mechanismus, der `trade_amount_pct` aushebelt.

**Fix:** Präzedenz über ein explizites Sentinel/Flag (`trade_amount_usd: float | None = None`) statt Float-Gleichheit. `None` = „nicht gesetzt", jeder positive Wert = „explizit". Zusammen mit ISSUE-04 lösen.

**Betroffene Dateien:** `automation/strategies/hourly_strategy_base.py`, `automation/tests/test_sizing_precedence.py`

---

# P3 — AGENTS.md ↔ Code/Config-Konsistenz

## ISSUE-20 — §6/§2 listen MeanReversion & DynamicBreakout als „aktiv" — sind `active:false` `[D]`

**Symptom/Root Cause:** `strategies.json` setzt `MeanReversionStrategy` und `DynamicBreakoutStrategy` auf `active: false` (Note: „Whipsaw-Anfälligkeit / Overtrading"; bestätigt im Changelog 2026-06-09). AGENTS.md führt beide aber weiterhin in der **Tabelle „Aktive Strategien" (§6)** und im Struktur-Baum (§2) mit Kommentar `# aktiv`.

**Fix:** Beide in §6 in den Inaktiv-Block verschieben (Grund: Whipsaw/Overtrading), §2-Kommentare auf `# INAKTIV` ändern.

**Betroffene Dateien:** `automation/AGENTS.md`

---

## ISSUE-21 — §12 Ausführungsbeispiel nutzt entferntes `daily_orchestrator --dry-run` `[D]`

**Symptom/Root Cause:** §12 listet `python3 -m automation.daily_orchestrator --dry-run --skip-api-fetch`. `--dry-run` wurde aus dem Orchestrator **restlos entfernt** (Pitfall #53 / `build_arg_parser` kennt nur `--no-deploy`, `--skip-api-fetch`, `--reset-catalog`). Der Befehl würde mit argparse-Error abbrechen.

> Abgrenzung: `backtest_runner.py` besitzt weiterhin ein **eigenes** `--dry-run` (reine Config-Validierung) — das ist korrekt und bleibt. Nur das Orchestrator-Beispiel ist stale.

**Fix:** §12-Zeile auf `--no-deploy --skip-api-fetch` ändern.

**Betroffene Dateien:** `automation/AGENTS.md`

---

## ISSUE-22 — Pitfall-Nummerierung kollidiert (mehrfach „#50") `[D]`

**Symptom/Root Cause:** §16/§19 enthalten mindestens fünf verschiedene Einträge mit der Nummer **„Pitfall #50"** (Alternation-Lock, Zero-Trade-Cascades, Gate-Scope-Mismatch, Restriktive-Frequenzen, Cooldown-Bypass) bei gleichzeitig vorhandenen #51/#52/#53. Die im Jules-Contract geforderte „nächste freie fortlaufende Nummer" wurde verletzt → Referenzierbarkeit (forensischer Workflow) leidet.

**Fix:** Pitfalls neu durchnummerieren (eindeutig, fortlaufend), Querverweise aktualisieren. Einmalige Aufräum-Aktion; rein redaktionell.

**Betroffene Dateien:** `automation/AGENTS.md`

---

## ISSUE-23 — Code-Config-Defaults divergieren von `strategy_defaults.json`; „Code-only"-Params fehlen in JSON `[R/D]`

**Symptom/Root Cause:** Zwei verwandte Probleme:

**(A) Divergierende Defaults** (JSON gewinnt im Orchestrator-Pfad, daher heute meist harmlos, aber irreführend und in Manifest-/Test-/Direkt-Instanziierungspfaden wirksam):
| Strategie | Code-Default | `strategy_defaults.json` |
|-----------|--------------|--------------------------|
| `SmaCrossoverConfig.sma_period` | 20 | **5** |
| `ComboTrendVwapConfig` | sma 50 / macd 12,26 | **sma 20 / macd 5,13** |
| `FlashCrashReversalConfig` | bb 20 / std 2.5 / rsi 14 / oversold 25 | **bb 10 / std 2.0 / rsi 7 / oversold 30** (+ `atr_trailing_multiplier 0.75`, `max_bars_in_trade 16` nur in JSON) |

**(B) „Code-only"-Parameter** ohne jeglichen JSON-Eintrag — diese sind die **echten** Zero-Hardcoding-Lücken (immer Code-Default, Tuning nur per Code-Edit): `atr_period` (14), `max_open_positions` (1), `profit_target_pct` (None), `atr_trailing_multiplier`/`max_bars_in_trade`/`cooldown_bars` für Strategien, die sie nicht in JSON listen, sowie `max_daily_trades` (siehe ISSUE-09).

**Fix:** 
- (A) Code-Defaults an die JSON-Werte angleichen **oder** in AGENTS.md §6/§7 explizit verankern: „`strategy_defaults.json` ist autoritativ; Config-Klassen-Defaults dienen nur als Schema-Fallback." (empfohlen: angleichen, um Manifest-/Test-Pfade konsistent zu halten).
- (B) Alle ergebnisrelevanten Basis-Parameter in `strategy_defaults.json` aufnehmen (Werte = aktuelle Code-Defaults, also verhaltensneutral).

**Betroffene Dateien:** `automation/strategies/*.py`, `automation/config/strategy_defaults.json`, `automation/AGENTS.md`

---

## ISSUE-24 — §3/§6 Beispiel-Walk-Forward-Fenster weichen vom aktiven Config-Stand ab `[D]`

**Symptom/Root Cause:** AGENTS.md nennt an mehreren Stellen Beispiel-Fenster wie `is_window_days=60, oos_window_days=7` bzw. `90d+30d`. Der aktive Stand in `backtest.json` ist `is_window_days: 120, oos_window_days: 30, splits: 1, holdout_days: 45`. Die als „z. B." markierten Stellen sind tolerierbar, die nicht-markierten („Backtests rechneten mit 30 Tagen … ein 90d+30d Walk-Forward-Fenster") wirken jedoch wie aktueller Stand.

**Fix:** Beispiele auf den aktiven Config-Stand (120/30/1, holdout 45) vereinheitlichen oder klar als historische Illustration kennzeichnen.

**Betroffene Dateien:** `automation/AGENTS.md`

---

# Anhang A — Vorgeschlagene Config-Schema-Erweiterungen (verhaltensneutral)

> Alle Defaults = aktuelle Code-Literale → keine Ergebnisänderung für R-Issues.

**`automation/config/tournament.json`** (ISSUE-05/06/07):
```jsonc
{
  "ratio_cap": 50.0,                    // ISSUE-05  (vorher RATIO_CAP)
  "sentinel_floor": 2.0,               // ISSUE-05
  "min_sample_sortino": 5,             // ISSUE-06  (vorher n < 5)
  "min_losses_for_ratio": 2,           // ISSUE-06  (vorher losses_count < 2)
  "low_sample_trade_threshold": 50,    // ISSUE-06  (vorher n < 50)
  "min_position_notional_usd": 10.0    // ISSUE-07  (vorher < 10.0, doppelt)
}
```

**`automation/config/backtest.json`** (ISSUE-08/10/12/14):
```jsonc
{
  "etoro_min_order_usd": 11.0,             // ISSUE-08  (vorher 11.0, doppelt)
  "cohort_alignment_tolerance_days": 1.0,  // ISSUE-10  (vorher 1 day in ns)
  "annualization_periods": 252,            // ISSUE-12  (vorher sqrt(252))
  "walk_forward": {
    "is_window_days": 120,
    "oos_window_days": 30,
    "splits": 1,
    "holdout_days": 45,
    "warmup_days": 60                      // ISSUE-14  (vorher impliziter .get-Fallback)
  }
}
```

**`automation/config/strategy_defaults.json`** (ISSUE-09/11/23B) — Basis-Parameter pro Strategie ergänzen:
```jsonc
{
  "_common_hourly_base": {                 // ggf. pro Strategie ausschreiben
    "atr_period": 14,
    "atr_trailing_multiplier": 1.5,
    "max_bars_in_trade": 48,
    "max_open_positions": 1,
    "max_daily_trades": 5                  // ISSUE-09
  },
  "ComboTrendVwapStrategy": {
    "trend_tolerance_pct": 0.02,           // ISSUE-11  (vorher 0.98/1.02)
    "bb_touch_window": 10                  // ISSUE-11
  }
}
```

---

# Anhang B — AGENTS.md Korrektur-Checkliste

- [ ] §2/§6: MeanReversion + DynamicBreakout → Inaktiv (ISSUE-20)
- [ ] §12: `daily_orchestrator --dry-run` → `--no-deploy` (ISSUE-21)
- [ ] §16: Falschaussage „KeltnerChannel atr_period Fix: Parameter korrekt übergeben" korrigieren (ISSUE-15)
- [ ] §16/§19: Pitfall-Nummern eindeutig durchnummerieren (ISSUE-22)
- [ ] §3/§6: Beispiel-WF-Fenster auf 120/30/1 + holdout 45 angleichen (ISSUE-24)
- [ ] §7: Neue Config-Keys aus Anhang A dokumentieren (ratio_cap, sentinel_floor, min_*, etoro_min_order_usd, annualization_periods, warmup_days, cohort_alignment_tolerance_days, trend_tolerance_pct, bb_touch_window)
- [ ] §6/§7: Autorität klären — „strategy_defaults.json ist autoritativ, Config-Klassen-Defaults nur Schema-Fallback" (ISSUE-23A)
- [ ] §10/§16: Observability-Header (Pitfall #46) um neue Gates erweitern

---

# Anhang C — Test-/Regression-Gates

| Issue-Klasse | Pflicht-Gates vor Merge |
|--------------|-------------------------|
| Alle **R** (Extract-to-Config) | `pytest automation/tests/test_backtest_runner.py -v` + `test_tournament_validation.py` + `test_sizing_precedence.py` müssen **unverändert** grün bleiben (identische Zahlen) |
| **ISSUE-01** | Neuer Test: simulierter `BrokenProcessPool` → Fallback-Wrapper läuft ohne `TypeError` |
| **ISSUE-02** (S) | `test_oos_aggregation.py`: erwartete Aggregat-`total_return`/`max_drawdown` neu kalibrieren; Phase-5-Gate-Outcome dokumentieren |
| **ISSUE-03** (S) | Baseline-Lauf Krypto: Spread 15 bps verifizieren; `test_backtest_trades_generated.py` für ein Krypto-Symbol |
| **ISSUE-04/19** | `test_sizing_precedence.py`: `trade_amount_pct` ist im Backtest wirksam (nicht durch Injektion überschrieben) |
| **ISSUE-15/16** (tote Params) | Sicherstellen, dass Entfernen keine `__struct_fields__`-Validierung in `run_single_backtest_worker` bricht |
| **ISSUE-17** (S) | Test mit `trend_filter_period > 0`: Warmup findet Parquet, `can_go_long` korrekt |
| **ISSUE-18** | `test_automation_isolation.py` um Literal-Check `strategies.` (ohne `automation.`-Präfix) erweitern |

---

## Umsetzungs-Reihenfolge (Empfehlung)

1. **Sofort (P1):** ISSUE-01 (Crash-Fix, isoliert) → ISSUE-03 (Daten-Fix instrument_map) → ISSUE-02 (starting_capital) → ISSUE-04+19 (Sizing).
2. **Refactor-Welle (P2/R):** ISSUE-05…14 als chirurgische Einzel-Commits (1 Parameter-Gruppe pro Commit, Tests unverändert grün).
3. **Architektur (P2):** ISSUE-15…18 (tote Params + Pfade), je mit Verifikation.
4. **Doku (P3):** ISSUE-20…24 + Anhang B in einem AGENTS.md-Sync-Commit.

> Jeder Commit chirurgisch (1 Anliegen), deutsche Commit-Message, Changelog-Eintrag in §19, CI-Gate grün — gemäß bestehender `automation/AGENTS.md`-Konventionen.
