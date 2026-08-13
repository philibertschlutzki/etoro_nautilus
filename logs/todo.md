# Abweichungs-Katalog — Lauf `815455db_20260813T055513723295`

**Prüfgegenstand:** `philibertschlutzki/etoro_nautilus` @ `815455db` · `logs/run_815455db_20260813T055513723295.json` · `logs/zusammenfassung_815455db_20260813T055513723295.md` · `logs/optimizer_815455db_20260813T055513723295.log` (10,8 MB, erstmals vollständig im Repo)

**Stand:** `reward_semantics_version` 23 · `simulation_semantics_version` 4 · `AGENTS.md` 5921 Zeilen, höchster dokumentierter Pitfall **#349**, höchste Issue-Referenz **#1022** ⇒ **#1023–#1062 sind im Code umgesetzt, aber nicht in AGENTS.md nachgetragen** (40 Issues Rückstand, zweite Sitzung in Folge).

**Neuer Katalog:** Issues **#1063–#1085**, Pitfalls **#369–#381**.

---

## 0. Ergebnis in einem Satz

Der Lauf ist **artefakt-sauber und kohortenrein** — die Zusammenfassung regeneriert bit-identisch aus dem Report-JSON, der `report_sha256` stimmt, alle 14 Studies liegen im Laufsfenster, die Sizing-Identität hält. Die Fehler liegen diesmal ausschliesslich in **(a) der Risikoschicht** — der ATR-Trailing-Stop ist nachweislich keine Risikogrösse, sondern eine Konstante, und die dafür gebaute Invariante ist einseitig und deshalb blind —, **(b) dem Suchraum-Rückschrieb**, der bereits fünfmal kompoundiert hat und negative Parametergrenzen in den nächsten Lauf schreibt, und **(c) der Fail-Fast-Mechanik**, die den Lauf über einen Parse-Fehlschlag abbricht und dabei „0 von 14 Studies" meldet, während acht Studies verletzen.

Ökonomisch: **0 von 14 promotet ist korrekt.** Nach Winsorisierung und ehrlichem Kostenstress bleiben genau **drei** Kandidaten mit positiver Erwartung — Donchian, Rsi2, OpeningRange — alle drei mit deflationierter DSR ≤ 0,36 gegen die Schwelle 0,95. Der im Bericht **erstgelistete** Kandidat (AdxAtr, +3,6 %) hat eine **negative winsorisierte Expectancy**.

---

## 1. Beweisteil

Alle Zahlen sind aus `run_815455db_20260813T055513723295.json` nachrechenbar. Reproduktionsbefehle stehen bei jedem Beweis.

### B-1 — Artefaktkette verifiziert (POSITIV, #1024 tritt nicht auf)

```bash
python3 - <<'EOF'
import sys, json, hashlib, difflib
sys.path.insert(0, '.')
from automation.optimizer import summary_de
raw = open('logs/run_815455db_20260813T055513723295.json','rb').read()
sha = hashlib.sha256(raw).hexdigest()
regen = summary_de.generate_german_summary(json.loads(raw), report_sha256=sha)
orig = open('logs/zusammenfassung_815455db_20260813T055513723295.md', encoding='utf-8').read()
print('sha256(run.json) =', sha)
print('Diff-Zeilen      =', len(list(difflib.unified_diff(orig.splitlines(), regen.splitlines()))))
EOF
```

Ergebnis: `sha256(run.json) = b39e5eb7…1229f`, identisch mit dem im Markdown eingebetteten `<!-- report_sha256: … -->`; **Diff-Zeilen = 0**. Die Zusammenfassung beschreibt exakt dieses JSON. Der #1024-Befund (219 Diff-Zeilen, TSLA-B&H-Vorzeichenwechsel) ist nicht reproduzierbar — `summary_de.py` ist endgültig entlastet.

### B-2 — Kohortenreinheit verifiziert (POSITIV, #1043 tritt nicht auf)

`wallclock_s = 5195` ist gesetzt, `studies_excluded_foreign_run = []`, alle 14 `study_started_at_utc` liegen zwischen 05:55:18 und 06:43:03 im Laufsfenster 05:55:13 → 07:21:48. `check_report_cohort_coherence`: Spannweite 2865 s < 5495 s ⇒ PASS mit echter Messung (nicht fail-open). Der #1043-Fail-Open-Pfad wird in diesem Lauf nicht betreten.

### B-3 — Der Trailing-Stop ist keine Risikogrösse (**Kern des Katalogs**)

Nominale Stopdistanz `d = atr_median_bps · atr_trailing_multiplier_median`, realisierter Verlust `oos_gross_loss_mean_bps_trailing_stop` (seit #1034 **nur** Stop-Exits, nicht mehr alle Verlust-Trades):

| Strategie | ATR (bps) | k | d (bps) | realisierter Stop-Verlust (bps) | Verhältnis |
|---|---:|---:|---:|---:|---:|
| OpeningRangeBreakout | 27,665 | 2,247 | 62,17 | 73,65 | **1,18** |
| FlashCrashReversal | 20,354 | 2,007 | 40,86 | 61,18 | **1,50** |
| DonchianRegimeBreakout | 21,329 | 3,353 | 71,51 | 107,31 | **1,50** |
| VolatilityBreakoutPump | 21,015 | 3,464 | 72,79 | 113,40 | **1,56** |
| SqueezeBreakout | 22,528 | 1,725 | 38,86 | 61,14 | **1,57** |
| ComboTrendVwap | 15,399 | 2,780 | 42,80 | 103,83 | **2,43** |
| Rsi2Reversion | 17,930 | 1,265 | 22,68 | 89,86 | **3,96** |
| AdxAtrMomentum | 13,502 | 1,163 | 15,70 | 89,11 | **5,68** |
| MeanReversion | 6,115 | 1,067 | 6,52 | 94,02 | **14,41** |
| TrendPullback | 4,189 | 1,122 | 4,70 | 92,73 | **19,74** |
| VwapExhaustion | 2,598 | 2,312 | 6,01 | 152,37 | **25,37** |
| HourlyMeanReversion | 2,361 | 1,470 | 3,47 | 99,70 | **28,74** |
| DynamicBreakout | **2,000** | 1,694 | 3,39 | 124,22 | **36,66** |

Drei unabhängige Aussagen, alle aus derselben Tabelle:

1. **Perfekte Trennung am ATR-Floor.** Alle fünf Studies mit `atr_median_bps < 7` haben ein Verhältnis > 14; alle acht mit `atr_median_bps ≥ 13,5` haben < 6. Keine Überlappung. Der Mechanismus ist der Floor, nicht allgemeiner Schlupf.
2. **Der realisierte Verlust ist praktisch konstant.** 61,1–152,4 bps, Median 94,0 — bei einer nominalen Stopdistanz, die um Faktor 21 variiert (3,39–72,79).
3. **Spearman(d, realisierter Verlust) = −0,18** über n=13. Der eigene Multiplikator des Stops hat **keinen** messbaren Einfluss darauf, was der Stop kostet, wenn er auslöst.

Konsequenz: `oos_max_drawdown`, `holdout_cvar_95`, `holdout_es_99` und der gesamte Risikoteil der Selektion sind gegen einen Stop kalibriert, der nicht hält. `DynamicBreakout` liegt mit `atr_median_bps = 2.0` **exakt** auf `atr_floor_bps_by_asset_class["EQUITY"]` — der Stop ist dort eine Konstante, kein Volatilitätsmass.

### B-4 — Die Invariante, die B-3 finden müsste, ist einseitig

`invariants.check_effective_stop_distance` (Ausschnitt):

```python
ratio = float(loss_bps) / configured_distance_bps
if ratio < min_ratio:               # min_ratio = 0.4
    offenders[key] = round(ratio, 4)
```

Es gibt **keine Obergrenze**. Bei Verhältnis 36,66 meldet der Check `passed=True`, `actual=None`, `detail="OK"` — der Wert erscheint in keinem Artefakt. Der Docstring antizipiert ausschliesslich die Gegenrichtung („Breakeven-Klemme"). Dieselbe Fehlerklasse wie #1055 (`check_reward_dynamic_range` nur „std zu klein"), hier in dem Check, der die Risikoschicht bewacht — **und der Check steht in `optimizer.json['fail_fast_invariants']`**.

### B-5 — Der Suchraum-Rückschrieb hat fünfmal kompoundiert und schreibt negative Grenzen

`cross_study.boundary_solutions` enthält:

```
TrendPullbackStrategy:  ema_period [-325.0, 300] · rsi_oversold [-30.0, 45.0] · max_bars_in_trade [-6.0, 24]
DonchianRegimeBreakout: donchian_period [-70.0, 60] · atr_period [7, 42.0]
```

Aktuelle Default-Bounds (`bounds.extract_numeric_bounds`): `ema_period (50,300)`, `rsi_oversold (15,45)`, `max_bars_in_trade (12,24)`, `donchian_period (8,60)`, `atr_period (7,21)`. Mit `widen_fraction = 0.3` und **k Anwendungen auf die URSPRÜNGLICHE Spannweite** gilt `lo_k = lo₀ − k · 0,3 · (hi₀ − lo₀)`:

| Parameter | lo₀ | 0,3·span | k=5 ⇒ lo₅ | im Report |
|---|---:|---:|---:|---:|
| `ema_period` | 50 | 75,0 | −325,0 | **−325,0** ✓ |
| `rsi_oversold` | 15 | 9,0 | −30,0 | **−30,0** ✓ |
| `max_bars_in_trade` | 12 | 3,6 | −6,0 | **−6,0** ✓ |
| `donchian_period` | 8 | 15,6 | −70,0 | **−70,0** ✓ |
| `atr_period` (high) | 21 | 4,2 | +42,0 | **42,0** ✓ |

Fünf Parameter, zwei Strategien, **exakt k = 5** für alle. Die Weitung ist kein Einmalereignis, sie akkumuliert bei jedem Lauf.

`sweep_diagnostics._widen_bounds_toward` klammert **nur nach oben** (`max_bars_in_trade` gegen `_MAX_BARS_IN_TRADE_CAP`), nach unten gar nicht. `spaces._bounds_for` reicht den Cache-Wert ungeprüft weiter: `return auto_bound[0], auto_bound[1]` → `trial.suggest_int("ema_period", -325, 300)`.

**Die Overrides sind bereits produktiv.** Der TrendPullback-Gewinner dieses Laufs trägt `ema_period = 18` (Default-Untergrenze 50) und `max_bars_in_trade = 5` (Default-Untergrenze 12, GR-01-Zeitbox-Band 12–24). Der Lauf hat also bereits ausserhalb des entworfenen Suchraums getunt — und beim nächsten Lauf liegt ~52 % des `ema_period`-Bereichs im Negativen.

Zusätzlich: `diagnosed_pairs[*].n_runs_confirmed = 1` bei fünf nachgewiesenen Weitungen, `budget_executed_fraction = null` bei Study-seitigem `1.0` (#1027-Wiederkehr an der Rückschrieb-Call-Site), `expires_after_runs = 10`.

### B-6 — Fail-Fast bricht über einen Parse-Fehlschlag ab

Log, 07:21:48:

```
[#839/#1016] FAIL_FAST_INVARIANT: check_holding_time_cap FAILt auf 0 von 14 Studies
des einzigen geplanten Symbols — Sweep bricht sofort ab (run_status=aborted_invariant)
```

`sweep._offending_pairs_for_fail_fast_check` liest `chk["actual"]` und verlangt ein `dict` der Form `{"strategy/symbol": wert}`. `check_holding_time_cap` liefert aber `actual: null` — seine acht Offender stehen korrekt strukturiert eine Ebene tiefer in `provenance.magnitude_offenders`. Der Parser fällt auf `return {}, set()`, der Aufrufer nimmt den konservativen Zweig („Struktur unbekannt ⇒ global abbrechen") und die #1016-Breitenschwelle `fail_fast_min_offending_studies = 3` wird **nie ausgewertet** — genau in der Ein-Symbol-Konfiguration, für die sie gebaut wurde (Pitfall #349).

Drei Zahlen für denselben Sachverhalt im selben Lauf: Log **0** Studies, Report §5.1 **1** betroffene Study, Report §5.2 **8** namentlich genannte Studies.

Zeitlich: letzte Study endet 07:04:12, Report gebaut 07:21:29, Abbruch protokolliert 07:21:48, `SWEEP_ABORTED` mit `failed_symbols: []`, `symbols_completed: 1`, `symbols_planned: 1`. Es wurde nichts abgebrochen.

### B-7 — Coverage-Ledger: Log sagt PASS, Artefakt sagt FAIL

| Quelle | Zeit | `observed` | Verdikt | `detail` |
|---|---|---:|---|---|
| `optimizer_*.log` Zeile 11825 | 07:21:29.295 | 1 | **PASS** | „OK" |
| `run.json` `invariant_checks` | — | 1 | **FAIL (blocking)** | „…Ledger zurückgesetzt/verloren (Pitfall #237, achte Wiederkehr)" |

Identischer Messwert, gegensätzliches Urteil. `report.py:1038`:

```python
has_prior_reports = REPORTS_DIR.exists() and any(REPORTS_DIR.glob("run_*.json"))
```

Zwischen den beiden Auswertungen landet der Report **dieses** Laufs in `REPORTS_DIR` — der Lauf zählt seinen eigenen Report als „früheren Report". Zusätzlich ignoriert der Check `symbol_coverage.coverage_bootstrap_phase = true`, obwohl `check_symbol_coverage` im selben Report ausdrücklich schreibt: „145 Symbol(e) noch nie abgedeckt, aber INNERHALB der Bootstrap-Phase — Telemetrie, kein FAIL."

### B-8 — Der Spitzenkandidat hat eine negative winsorisierte Expectancy

| Strategie | Expectancy (bps) | winsorisiert (bps) | Ausreisser / Trades | |
|---|---:|---:|---:|---|
| AdxAtrMomentum | +17,23 | **−1,44** | 6 / 132 | **Vorzeichenwechsel** |
| ComboTrendVwap | +3,24 | **−5,80** | 1 / 55 | **Vorzeichenwechsel** |
| MeanReversion | −54,76 | **+0,26** | 1 / 37 | **Vorzeichenwechsel** |
| DonchianRegimeBreakout | +11,35 | +16,58 | 0 / 22 | robust |
| Rsi2Reversion | +15,77 | +15,92 | 2 / 56 | robust |
| OpeningRangeBreakout | +8,82 | +10,12 | 2 / 42 | robust |

Der im Bericht §2.2/§2.3 **erstgelistete** Kandidat (AdxAtr, +3,6 % Holdout) verdankt sein gesamtes positives Ergebnis **6 von 132 Trades**. `holdout_expectancy_winsorized` ist telemetriert, wird aber weder gerankt, noch gegatet, noch im Bericht gezeigt — sortiert wird nach `holdout_total_return`, der ausreisser-empfindlichsten verfügbaren Grösse.

### B-9 — Der Kostenstress stresst nur die Kommission

`(exp − exp_1.5x)/0,5 = (exp_1.5x − exp_2x)/0,5 = 1,000 bps` in **allen 13** Studies mit Trades. `backtest_runner._expectancy_cost_stress`:

```python
extra_rate = (multiplier - 1.0) * (commission_bps / 10000.0)   # commission_bps = 1.0
```

Der Spread (`spread_bps_by_asset_class["EQUITY"] = 3.0`) bleibt unangetastet. `_read_default_round_trip_cost_bps` beziffert c_rt für TSLA korrekt mit **4,0 bps** (1,0 Kommission + 3,0 Spread). Der „2×-Kostenstress" erhöht die realen Round-Trip-Kosten also um **25 %**, nicht um 100 % — und `deployment_gate._clause_cost_stress` konsumiert genau diese Zahl als Kosten-Robustheitsklausel.

Ehrlicher 2×-Stress (−4,0 bps auf die winsorisierte Expectancy):

| Strategie | wins. (bps) | −4,0 bps | Holdout | Trades | DSR |
|---|---:|---:|---:|---:|---:|
| DonchianRegimeBreakout | +16,58 | **+12,58** | +0,40 % | 22 | 0,157 |
| Rsi2Reversion | +15,92 | **+11,92** | +1,37 % | 56 | 0,361 |
| OpeningRangeBreakout | +10,12 | **+6,12** | +0,60 % | 42 | 0,073 |
| MeanReversion | +0,26 | −3,74 | −2,82 % | 37 | 0,000 |
| AdxAtrMomentum | −1,44 | −5,44 | +3,60 % | 132 | 0,646 |
| ComboTrendVwap | −5,80 | −9,80 | +0,32 % | 55 | 0,032 |

Drei Überlebende. Alle drei mit DSR ≪ 0,95 ⇒ die Ablehnung ist **richtig**.

### B-10 — `gate_inventory.n_rejections` zählt die Bestandenen

| Study | `gate_inventory[oos_min_psr].n_rejections` | `n_eligible` | `is_rejection_detail_counts["REJECT_OOS_MIN_PSR"]` |
|---|---:|---:|---:|
| OpeningRangeBreakout | 98 | **98** | **1** |
| FlashCrashReversal | 136 | **136** | 11 |
| ComboTrendVwap | 196 | **196** | 60 |
| Rsi2Reversion | 15 | **15** | 117 |
| TrendPullback | 8 | **8** | 103 |
| AdxAtrMomentum | 4 | **4** | 132 |

In **13 von 14** Studies gilt `n_rejections == n_eligible == is_rejection_detail_counts["NONE"]` exakt; der tatsächliche PSR-Ablehnungszähler weicht um bis Faktor **98** ab (OpeningRange). `n_solo_rejections` und `marginal_delta` tragen denselben invertierten Wert.

`check_gate_marginal_contribution` konsumiert genau dieses Feld und empfiehlt die Entfernung von `min_trades` und `max_drawdown` aus `eligible_requires_all` — **den beiden Gates, die `tournament.json['gate_consolidation_protected']` ausdrücklich schützt**. Nach fünf bereits vollzogenen Gate-Entfernungen auf derselben Evidenzbasis (#677/#697/#776/#960/#848) ist `eligible_requires_all` auf `["min_trades", "max_drawdown", "oos_min_psr"]` geschrumpft; die Empfehlung würde `oos_min_psr` als **einziges** Eligibility-Gate zurücklassen.

### B-11 — `binding_gate` ist ein Einheiten-Artefakt

`binding_gate == argmin(near_miss_deltas["binding"])` über die **rohen** Deltas in **14 von 14** Studies. Beispiel AdxAtr: `{oos_min_trades: 442,0 · oos_max_drawdown: 0,2626 · oos_min_psr: 0,0653}` — eine Trade-Anzahl, ein Drawdown-Bruchteil und eine Wahrscheinlichkeit auf einer Achse. `oos_min_trades` gewinnt nie (Skala 10²), und `oos_max_drawdown` liegt in allen 14 Studies im engen Band 0,2626–0,2984 (weil der beobachtete Drawdown ~1–4 % gegen ein 30-%-Gate steht, also **nirgends bindet**) — und wird trotzdem in **8 von 14** Studies als „das bindende Gate" ausgewiesen.

`holdout_binding_gate` folgt derselben Regel und nennt zusätzlich `oos_min_expectancy` (in 4 Studies), das seit v13/#697 **kein Gate mehr ist** — und meldet es auch dann, wenn `decision_chain` für die Holdout-Stufe `passed: true` trägt.

### B-12 — UNKNOWN-Exit ≡ EXPECTANCY_NOTIONAL_DEGENERATE

In **allen 14** Studies gilt `exit_reason_histogram["UNKNOWN"] == inference_diagnostics_by_code["EXPECTANCY_NOTIONAL_DEGENERATE"]` exakt (1378/1378, 3301/3301, 1156/1156, …, beide `None` bei Sma und VolPump). Zwei getrennt benannte Telemetriekanäle zählen dasselbe Ereignis.

`check_inference_diagnostics_concentration` feuert bei Squeeze mit **1,4464 = 81/56**: Zähler 81 = `SORTINO_INSUFFICIENT_TRADES` 67 + `SORTINO_DOWNSIDE_SHRUNK` 3 + `SORTINO_INSUFFICIENT_DOWNSIDE` 11, gezählt über alle 180 Trials; Nenner 56 = `n_trials_informative`. Inkommensurable Grundgesamtheiten (#1033-Wiederkehr mit exakter Wurzel).

### B-13 — Die blockierende Selektions-Invariante rechnet gegen geprunte Trials

Squeeze: `n_trials 180 = informative 56 + pruned 78 + unevaluable 46` ✓ (`check_denominator_coherence` PASS). Aber `n_evaluable = 130 = 56 informative + 74 unmeasurable`, und `130 + 78 pruned = 208 > 180` ⇒ **74 geprunte Trials sind in `n_evaluable` mitgezählt**. `check_selection_statistic_availability` rechnet `56/130 = 0,4308` gegen die Schwelle 0,8 und feuert **blocking**. Auf der ehrlichen Grundgesamtheit (`n_selection_statistic_available / n_trials_informative = 56/56`) ist die Verfügbarkeit **1,0** und der Check passt. Squeeze ist die einzige Study mit `n_trials_pruned > 0` und zugleich die einzige, bei der die Zähl-Identität bricht (#914 unvollständig verdrahtet).

### B-14 — Familien-Multiplizität

`cross_study.n_family["TSLA.ETORO"] = 467` gegen Σ `n_selection_statistic_available` = **1619** (die seit #822 vorgeschriebene Grundgesamtheit), Σ `n_evaluable` = 1693, Σ `n_family_stage1` = **1508**. `TrendPullbackStrategy` fehlt vollständig im `n_family_stage1`-Block (Study-Feld `null`), obwohl es 111 Trials mit verfügbarer Selektionsstatistik trägt. Effekt auf die Deflationsschwelle: Φ⁻¹(1−1/467) = 2,859 gegen Φ⁻¹(1−1/1619) = 3,229 — **SR\* um ~13 % zu niedrig**, in jeder Promotionsentscheidung.

### B-15 — Excess/Exposure mit fabriziertem Nenner

`summary_de._EXPOSURE_EPSILON = 0.01`. `TrendPullbackStrategy` hat `holdout_total_trades = 0`, `holdout_exposure_fraction = null` ⇒ die Zeile zeigt „Zeit im Markt: k. A." und zugleich **„Excess/Exposure: 1395,2 %"** = 0,13951897 / 0,01. Die grösste Zahl der ökonomischen Tabelle gehört der einzigen Strategie, die nicht gehandelt hat. SqueezeBreakout (1,7 % Exposure, 6 Trades) erhält 816,6 %. Die Spalte belohnt Nicht-Handeln monoton.

### B-16 — ATR-Homogenität ist ein Floor-Artefakt, nicht eine Preis-Sprungstelle

`check_atr_scale_homogeneity` meldet für TSLA **13,83** und nennt es „Signatur einer Sprungstelle in der Preisreihe (#1028)". Nachgerechnet: max `atr_median_bps` = 27,66525 (OpeningRange) ÷ min = **2,0** (DynamicBreakout) = 13,8326. Der Nenner liegt **exakt** auf `atr_floor_bps_by_asset_class["EQUITY"] = 2.0`. Die Spannweite entsteht am Floor, nicht in den Daten. Wörtliche Wiederkehr von #1052.

Analog `check_n_periods_homogeneity` = **68,98** = 1655,5 (AdxAtr) ÷ 24,0 (Squeeze) — getrieben von der degenerierten Squeeze-Study aus B-13, nicht von einer Datenanomalie.

---

## 2. Positiv verifiziert

| Befund | Beleg |
|---|---|
| Zusammenfassung bit-identisch regenerierbar, `report_sha256` korrekt | B-1, 0 Diff-Zeilen |
| Kohortenreinheit, `check_report_cohort_coherence` mit echter Messung | B-2, 2865 s < 5495 s |
| Sizing-Identität hält | `f_implied` 12,85–18,02 % gegen `trade_amount_pct` 15,0 % über 11 Studies mit n ≥ 10; `check_sizing_identity_coherence` PASS mit Messung |
| Exit-Reason-Telemetrie vollständig | Σ `exit_reason_histogram` == `oos_total_trades_with_exit_telemetry` in 14/14; UNKNOWN ≤ 11,8 % (vorher 68,5 %) |
| `stop_reason` ehrlich | 14/14 `BUDGET_EXHAUSTED`, 1940/1940 Trials, `budget_executed_fraction` 1,0 — #1026-Zählartefakt behoben |
| Buy&Hold einheitlich | −13,951897 % in allen 14 Studies, kein #1024-Vorzeichenwechsel |
| Profit-Factor unzensiert | `holdout_profit_factor_censored = False` in 14/14, `_raw` == `_censored` |
| Promotionskette | `promotion_correction_mode = 'conjunction'`, `deployment_decision = None` überall, `check_promotion_deployment_coherence` PASS 14/14 |
| Reward-Landschaft | `base` `var_contrib` 0,9987 (AdxAtr), `dd_penalty` inaktiv (#977 wirksam) |
| Worker-Auslastung nachgerechnet | Σ 20618 s / (6 × 5195 s) = 66,15 % ✓; CPU 17719 s / 31170 s = 56,85 % ✓ |
| Guard-Referenz | `check_guard_reference_coherence` PASS, kein `absolute`-Fallback unter `family_median` (#901/#913 wirksam) |

---

## 3. Issues

### Kohorte A — Lauf-Governance und Fail-Fast (P0, zuerst)

---

#### #1063 — `check_holding_time_cap` verletzt die `actual`-Konvention; die Fail-Fast-Breitenschwelle ist dadurch inoperativ

**Priorität:** P0

**Symptom:** Der Lauf bricht mit `run_status=aborted_invariant` ab. Die Logmeldung lautet „`check_holding_time_cap` FAILt auf **0 von 14 Studies**", während der Check acht Studies namentlich als Verletzer führt. Report §5.1 nennt eine dritte Zahl („betroffene Studies: 1").

**Root-Cause:** `sweep._offending_pairs_for_fail_fast_check` liest `chk["actual"]` und erwartet `{"<strategy>/<symbol>": wert}`. `check_holding_time_cap` setzt `actual = None` und legt seine Offender in `provenance.magnitude_offenders` ab — dort korrekt strukturiert. Der Parser trifft `if not isinstance(actual, dict): return {}, set()`, der Aufrufer nimmt den dokumentierten konservativen Zweig („Struktur nicht ermittelbar ⇒ global abbrechen, UNABHÄNGIG von `fail_fast_policy`"). Damit wird `_fail_fast_systemic_verdict` und mit ihm `fail_fast_min_offending_studies = 3` (#1016, Pitfall #349) nie ausgewertet — in genau der Ein-Symbol-Konfiguration, für die diese Schwelle gebaut wurde.

**Fix:**
1. `check_holding_time_cap` stempelt seine Offender in `actual` (Magnituden-Ast: `{pair_key: vielfaches}`), `provenance.magnitude_offenders` bleibt als Duplikat erhalten oder entfällt.
2. `_offending_pairs_for_fail_fast_check` liest zusätzlich `provenance.magnitude_offenders` als Fallback, bevor es auf den Konservativ-Zweig fällt.
3. Meta-Invariante: jeder Name in `optimizer.json['fail_fast_invariants']`, der FAILt und dessen `actual` nicht der Pair-Konvention folgt, erzeugt einen eigenen, benannten FAIL (`check_fail_fast_actual_convention`) statt eines stillen Konservativ-Abbruchs.

**Akzeptanzkriterien:**
- Auf diesem Report liefert `_offending_pairs_for_fail_fast_check(checks, "check_holding_time_cap")` genau 8 Paare.
- Die Logmeldung nennt 8 von 14 Studies; `_fail_fast_systemic_verdict` wird mit `offending_pairs=8 ≥ 3` ausgewertet und entscheidet über `fail_fast_policy`, statt den Konservativ-Zweig zu nehmen.
- Report §5.1 „betroffene Studies" für diesen Check = 8.
- Regressionstest mit einem Check, dessen `actual=None` ist: `check_fail_fast_actual_convention` FAILt.

**Betroffene Dateien:** `automation/optimizer/invariants.py`, `automation/optimizer/sweep.py`, `automation/optimizer/report.py`

---

#### #1064 — `check_coverage_ledger_continuity`: Log PASS, Report FAIL; der Lauf zählt seinen eigenen Report als Vorlauf

**Priorität:** P0

**Symptom:** Identischer Messwert `total_runs_started = 1`, zwei gegensätzliche Urteile Sekunden auseinander: `optimizer_*.log` Zeile 11825 `status=PASS, detail="OK"`; `run.json` `passed=false`, blocking, „Ledger zurückgesetzt/verloren (Pitfall #237, achte Wiederkehr)".

**Root-Cause:** `report.py:1038` ermittelt `has_prior_reports = REPORTS_DIR.exists() and any(REPORTS_DIR.glob("run_*.json"))`. Der Report **dieses** Laufs wird zwischen den beiden Auswertungen in `REPORTS_DIR` geschrieben ⇒ der Lauf sieht sich selbst als „früheren Report". Zusätzlich konsultiert der Check `symbol_coverage.coverage_bootstrap_phase` nicht, obwohl `check_symbol_coverage` im selben Report `coverage_bootstrap_phase=true` korrekt als „Telemetrie, kein FAIL" behandelt — `total_runs_started = 1` ist in der Bootstrap-Phase der **erwartete** Wert.

**Fix:**
1. `has_prior_reports` schliesst den eigenen `run_id` aus (`glob("run_*.json")` filtern gegen den aktuellen Dateinamen).
2. Der Check nimmt `coverage_bootstrap_phase` als Parameter und liefert PASS mit `detail="Bootstrap-Phase — nicht anwendbar"`, solange sie aktiv ist.
3. Die Invariante wird genau **einmal** je Lauf ausgewertet; das `INVARIANT_RESULT`-JSON-Event und der Report-Record stammen aus demselben `InvariantResult`-Objekt.

**Akzeptanzkriterien:**
- Für jeden Check-Namen gilt: `status` im Log == `passed` im Report-JSON. Regressionstest über alle 50 Checks dieses Laufs.
- Auf diesem Report: `check_coverage_ledger_continuity` PASS (Bootstrap-Phase).
- Ein künstlicher Report-Ordner mit einem FREMDEN `run_*.json` und `coverage_bootstrap_phase=false` erzeugt weiterhin FAIL.

**Betroffene Dateien:** `automation/optimizer/report.py`, `automation/optimizer/invariants.py`

---

#### #1065 — `aborted_invariant` wird als „Lauf unvollständig" berichtet, obwohl 1940/1940 Trials abgeschlossen sind

**Priorität:** P1

**Symptom:** §1 der Zusammenfassung: „**Hinweis:** dieser Lauf ist NICHT vollständig (aborted_invariant; 1/1 Symbole abgeschlossen) — die folgenden Zahlen beziehen sich NUR auf die bereits abgeschlossene Kohorte." Tatsächlich: 14/14 Studies, 1940/1940 Trials, `budget_executed_fraction` 1,0 in allen 14, `stop_reason` durchgehend `BUDGET_EXHAUSTED`, letzte Study beendet 07:04:12, Abbruch protokolliert 07:21:48, `SWEEP_ABORTED` mit `failed_symbols: []`.

**Root-Cause:** Die Vollständigkeits-Klausel wird aus `run_status` abgeleitet statt aus der Abdeckung. `aborted_invariant` bedeutet „eine blockierende Invariante hat FAILt", nicht „Arbeit wurde abgebrochen". Bei einem Abbruch, der erst nach dem Report-Bau greift, ist die Aussage schlicht falsch — und sie entwertet einen Bericht, dessen Zahlen vollständig sind.

**Fix:** Die Klausel trennt zwei Aussagen: (a) **Abdeckung** — aus `symbols_completed/symbols_planned` und `budget_executed_fraction`, (b) **Gültigkeit** — aus den blockierenden Invarianten. Text für diesen Lauf: „Vollständig gerechnet (1/1 Symbole, 1940/1940 Trials), aber wegen blockierender Invarianten nicht entscheidungsfähig: …". Zusätzlich `run_status`-Wert `completed_invalid` für den Fall „alles gerechnet, Invariante blockiert", getrennt von `aborted_invariant` (echter Abbruch mit unvollständiger Arbeit).

**Akzeptanzkriterien:**
- Auf diesem Report erscheint keine Behauptung über Unvollständigkeit; die blockierenden Invarianten werden unverändert genannt.
- Ein synthetischer Report mit `symbols_completed < symbols_planned` erzeugt weiterhin den Unvollständigkeits-Hinweis.

**Betroffene Dateien:** `automation/optimizer/summary_de.py`, `automation/optimizer/sweep.py`

---

### Kohorte B — Suchraum-Rückschrieb (P0, wirkt auf den NÄCHSTEN Lauf)

---

#### #1066 — `_widen_bounds_toward` klammert nicht nach unten; die Weitung kompoundiert und erzeugt negative Parametergrenzen

**Priorität:** P0 — **SPERRVERMERK: kein weiterer Sweep, bevor dieser Fix gemergt und der Cache bereinigt ist.**

**Symptom:** `cross_study.boundary_solutions[*].proposed_bounds` enthält `ema_period: [-325.0, 300]`, `rsi_oversold: [-30.0, 45.0]`, `max_bars_in_trade: [-6.0, 24]`, `donchian_period: [-70.0, 60]`. Negative Perioden, negative Bar-Anzahlen und ein RSI-Schwellwert unterhalb des Wertebereichs von RSI.

**Root-Cause:** `sweep_diagnostics._widen_bounds_toward` senkt bei `direction == "low"` die Untergrenze um `widen_fraction · span` ab, **ohne jede Untergrenze**. Nach oben ist geklammert (`min(new_hi, _MAX_BARS_IN_TRADE_CAP)` für `max_bars_in_trade`), nach unten nicht. Der Vorschlag landet über `confirm.py:1671` im #761-Diagnose-Cache und wird von `spaces._bounds_for` beim nächsten Lauf ungeprüft an `trial.suggest_int/suggest_float` weitergereicht.

Die Weitung **akkumuliert**: `lo_k = lo₀ − k · 0,3 · (hi₀ − lo₀)` mit nachgewiesenem **k = 5** über fünf Parameter und zwei Strategien (Beweis B-5). Es gibt keinen Konvergenzpunkt und keine Rücknahme.

**Fix:**
1. Ein Parameter-Typ-/Domänenregister (`spaces`): pro Parameter `{min_admissible, max_admissible, dtype}`. `_widen_bounds_toward` klammert **symmetrisch** gegen dieses Register (`ema_period ≥ 2`, `donchian_period ≥ 2`, `atr_period ≥ 2`, `rsi_oversold ≥ 1`, `max_bars_in_trade ∈ [1, MAX_BARS_IN_TRADE_HARD_CAP]`, alle Perioden `int ≥ 1`).
2. `spaces._bounds_for` validiert jeden Cache-Wert gegen dasselbe Register und **verwirft** unzulässige Overrides fail-loud (`SEARCH_SPACE_OVERRIDE_INADMISSIBLE`), statt sie zu benutzen.
3. Weitungs-Deckel: maximal `max_widen_applications` (Vorschlag 2) je (Strategie, Symbol, Parameter); der Zähler steht im Cache-Eintrag und wird bei jeder Anwendung inkrementiert.
4. Neue Invariante `check_search_space_override_admissible` (severity blocking): jeder Eintrag im #761-Cache liegt innerhalb des Registers.
5. Einmalige Migration: alle bestehenden `proposed_bounds` gegen das Register klammern; Einträge mit negativer Untergrenze auf den Default zurücksetzen.

**Akzeptanzkriterien:**
- `_widen_bounds_toward({"ema_period": "low"}, {"ema_period": (50, 300)}, widen_fraction=0.3)` liefert eine Untergrenze ≥ `min_admissible`, nie < 0 — für jeden Parameter des Registers.
- Fünfmalige Anwendung konvergiert gegen `min_admissible` statt zu divergieren.
- `check_search_space_override_admissible` FAILt auf dem aktuellen Cache-Stand und PASST nach der Migration.
- Ein Sweep-Start mit einem manuell auf `[-325, 300]` gesetzten Override bricht fail-loud ab, statt negativ zu sampeln.

**Betroffene Dateien:** `automation/optimizer/sweep_diagnostics.py`, `automation/optimizer/spaces.py`, `automation/optimizer/invariants.py`, `automation/optimizer/confirm.py`

---

#### #1067 — Die GR-01-Zeitbox ist nur nach oben verdrahtet; `max_bars_in_trade` wurde bereits unter das Band gedrückt

**Priorität:** P0

**Symptom:** Der TrendPullback-Gewinner dieses Laufs trägt `max_bars_in_trade = 5` (Default-Suchband 12–24) und `ema_period = 18` (Default-Untergrenze 50). Der Vorschlag für den nächsten Lauf lautet `max_bars_in_trade: [-6.0, 24]`.

**Root-Cause:** `_MAX_BARS_IN_TRADE_CAP` (#714/#777/#858) ist ausdrücklich als „harte Obergrenze für JEDE `max_bars_in_trade`-Suchraum-Bound über alle 15 Strategien" dokumentiert — „Untergrenzen bleiben unverändert". Diese Asymmetrie war korrekt, solange Untergrenzen nur kuratiert gesetzt wurden. Seit dem automatischen Rückschrieb (#761/#763) senkt der Optimizer sie selbst ab, und die GR-01-Zeitbox-Semantik (Trade schliesst nach ~1 Handelstag) hat keinen Wächter mehr nach unten.

Nebenbefund: die #714-Semantik hat auch eine ökonomische Untergrenze — bei `max_bars_in_trade` ≤ wenigen Bars wird die Strategie zu einem Kosten-Generator (siehe #1072).

**Fix:** `MIN_BARS_IN_TRADE_FLOOR` (Vorschlag 6, gemeinsam mit `MAX_BARS_IN_TRADE_HARD_CAP` in `_contracts.py`) als Single Source of Truth; `_widen_bounds_toward`, `spaces._bounds_for` und `spaces.sample_params` klammern gegen beide. Zusätzlich Telemetrie: jede Study meldet, ob ihr Gewinner ausserhalb des **Default**-Suchbands liegt (`winner_outside_default_bounds`).

**Akzeptanzkriterien:**
- Kein Trial samplet `max_bars_in_trade < MIN_BARS_IN_TRADE_FLOOR`.
- `winner_outside_default_bounds` ist für TrendPullback dieses Laufs `{"ema_period": 18, "max_bars_in_trade": 5}` und erscheint im Report.
- Test analog `test_issue_777` für die Untergrenze.

**Betroffene Dateien:** `automation/optimizer/_contracts.py`, `automation/optimizer/spaces.py`, `automation/optimizer/sweep_diagnostics.py`, `automation/optimizer/report.py`

---

#### #1068 — Der Diagnose-Rückschrieb erhält keinen Laufkontext und widerspricht dem Coverage-Ledger

**Priorität:** P1

**Symptom:** `cross_study.diagnosed_pairs[*].budget_executed_fraction = null`, obwohl jede Study `budget_executed_fraction = 1.0` meldet. `n_runs_confirmed = 1` bei fünf nachgewiesenen Weitungs-Anwendungen (B-5). `expires_after_runs = 10` gegen ein Coverage-Ledger, das `total_runs_started = 1` führt ⇒ der Eintrag kann nie ablaufen.

**Root-Cause:** Wörtliche Wiederkehr von #1027: der #1015-Laufkontext-Fix ist an der Rückschrieb-Call-Site (`run_optimization.py` Denylist-/Override-Pfad) weiterhin nicht angewandt. Zusätzlich sind zwei persistente Stores (Diagnose-Cache, Coverage-Ledger) nicht gekoppelt — der eine hat fünf Läufe gesehen, der andere einen.

**Fix:**
1. `record_diagnosed_pair` erhält denselben `run_id`/`budget_executed_fraction`-Kontext wie `report.py:478` und `run_optimization.py:2356`.
2. `n_runs_confirmed` wird beim Rückschrieb inkrementiert, nicht auf 1 gesetzt; `widen_applications` als eigener Zähler (siehe #1066).
3. Neue Invariante `check_diagnosis_ledger_coherence`: `max(diagnosed_pairs[*].n_runs_confirmed) ≤ symbol_coverage.total_runs_started`. Ist sie verletzt, ist einer der beiden Stores verloren gegangen.

**Akzeptanzkriterien:**
- `budget_executed_fraction` in `diagnosed_pairs` stimmt mit dem Study-Wert überein.
- `check_diagnosis_ledger_coherence` FAILt auf diesem Report (5 > 1) und PASST nach dem Ledger-Fix aus #1064.

**Betroffene Dateien:** `automation/optimizer/run_optimization.py`, `automation/optimizer/sweep_diagnostics.py`, `automation/optimizer/invariants.py`

---

### Kohorte C — Risikoschicht (P0, ökonomischer Kern)

---

#### #1069 — Der ATR-Trailing-Stop begrenzt den Verlust nicht; der realisierte Verlust ist von der Stopdistanz unabhängig

**Priorität:** P0 — **grösster Risiko-Hebel des Katalogs**

**Symptom:** Über 13 Studies: nominale Stopdistanz 3,39–72,79 bps (Faktor 21), realisierter mittlerer Verlust bei Stop-Exits 61,1–152,4 bps (Median 94,0). **Spearman(Stopdistanz, realisierter Verlust) = −0,18.** Verhältnis bis **36,66** (DynamicBreakout). Perfekte Trennung: alle fünf Studies mit `atr_median_bps < 7` liegen über 14, alle acht mit `atr_median_bps ≥ 13,5` unter 6 (Beweis B-3).

**Root-Cause (zu entscheiden, drei prüfbare Kandidaten):**
1. **ATR-Floor + Bar-Granularität.** Bei `atr_median_bps` am Floor (2,0 bps für EQUITY) liegt die Stopdistanz **unterhalb der typischen Bar-Spanne**. `_check_exits_and_update` prüft auf Bar-Basis; der Exit füllt frühestens zum Bar-Schluss ⇒ der realisierte Verlust ist die **volle adverse Bar-Bewegung**, nicht die Stopdistanz. Das erklärt sowohl die Konstanz (~94 bps ≈ Median-Bar-Range) als auch die Floor-Trennung.
2. **`atr_median_bps` ist ein Study-Median über Trials**, der realisierte Verlust ein über alle Trials gepoolter Mittelwert — Mischungsartefakt.
3. Ratsche/Preis-Extremum (#897) greift bei degeneriertem ATR nicht wie spezifiziert.

Entscheidender Einzelbeleg: der Anteil Bars mit `high == low` und die Median-Bar-Range in bps für TSLA.ETORO im Holdout-Fenster. Liegt die Median-Bar-Range in der Grössenordnung 60–150 bps, ist (1) bewiesen und (2)/(3) erledigt.

**Fix:**
1. **Messen und ausweisen:** `realized_stop_loss_ratio = oos_gross_loss_mean_bps_trailing_stop / (atr_median_bps · atr_trailing_multiplier_median)` je Study als erstklassiges Report-Feld.
2. **Kosten- und spannenbewusster Stop-Floor:** `atr_floor_bps` wird nicht mehr als freie Konstante gepflegt, sondern als `max(atr_floor_bps_by_asset_class, min_stop_to_cost_ratio · c_rt, median_bar_range_bps)`. Vorschlag `min_stop_to_cost_ratio = 3.0` (siehe #1072).
3. **Preflight in `spaces.py`:** Parametervektoren, deren erwartete Stopdistanz `k · max(ATR, floor)` unter `min_stop_to_cost_ratio · c_rt` liegt, werden gar nicht erst gesampelt (Constraint über `#612-constraints_func`, nicht als Reward-Strafe — Pitfall #124).
4. **Simulationsehrlichkeit:** entweder Intrabar-Stop-Fill über eine echte Stop-Order in der Engine, oder — wenn das Bar-Raster das nicht hergibt — der realisierte Verlust wird explizit als „Bar-Schluss-Slippage" telemetriert und **in die Kostenrechnung übernommen**, statt als Stop-Verlust ausgewiesen zu werden.

Punkte 2–4 ändern die simulierten Fills ⇒ `simulation_semantics_version` 4 → 5, Pflicht-Purge.

**Akzeptanzkriterien:**
- `realized_stop_loss_ratio` ist für alle 14 Studies dieses Laufs im Report; die Werte reproduzieren B-3.
- Nach dem Floor-Fix: kein Study mit `atr_median_bps` exakt auf dem Floor **und** Verhältnis > 3.
- Der OHLC-Auszug (Anteil `high == low`, Median-Bar-Range in bps) ist beigefügt und entscheidet die Root-Cause-Alternative aktenkundig.

**Betroffene Dateien:** `automation/strategies/hourly_strategy_base.py`, `automation/backtest_runner.py`, `automation/config/backtest.json`, `automation/optimizer/spaces.py`, `automation/optimizer/report.py`

---

#### #1070 — `check_effective_stop_distance` ist einseitig und deshalb für die reale Fehlerrichtung blind

**Priorität:** P0

**Symptom:** Bei einem Verhältnis von 36,66 meldet der Check `passed=True`, `actual=None`, `detail="OK"`. Der Wert erscheint in keinem Artefakt. Der Check steht in `optimizer.json['fail_fast_invariants']` — die Fail-Fast-Mechanik kann diese Fehlerklasse also strukturell nicht erkennen.

**Root-Cause:** `if ratio < min_ratio: offenders[key] = ratio` — es gibt kein `max_ratio`. Der Docstring antizipiert nur die Gegenrichtung („Breakeven-Klemme, die auf der Volatilitätsschätzung statt auf dem Preis-Extremum rastet"). Dieselbe Fehlerklasse wie #1055 (`check_reward_dynamic_range` prüfte nur „std zu klein" und war deshalb auf der BTC-Explosion blind). Zweite Wiederkehr — die Regel „ein Verhältnis-Check braucht **beide** Schranken, sonst wird die nicht geprüfte Richtung zur blinden Flanke" gehört in AGENTS.md.

**Fix:**
1. `max_ratio` (Vorschlag 2.5) als zweite Schranke, konfigurierbar über `optimizer.json['stop_distance_max_ratio']`.
2. `actual` trägt **immer** die gemessenen Verhältnisse je Study (nicht nur die Offender), damit der Wert im Report sichtbar ist — und damit `_offending_pairs_for_fail_fast_check` ihn parsen kann (#1063).
3. `passed=True, actual=None` wird für „nichts gemessen" und „nichts auffällig" unterscheidbar: `detail` nennt explizit `n_studies_measured`.
4. Audit aller Verhältnis-/Schwellen-Checks in `invariants.py` auf einseitige Schranken; jeder Fund bekommt entweder eine zweite Schranke oder einen Kommentar mit der Begründung, warum die andere Richtung unmöglich ist.

**Akzeptanzkriterien:**
- Auf diesem Report FAILt `check_effective_stop_distance` mit fünf Offendern (14,41 / 19,74 / 25,37 / 28,74 / 36,66).
- `actual` enthält alle 13 messbaren Verhältnisse.
- Das Audit aus Punkt 4 ist als Tabelle im PR dokumentiert.

**Betroffene Dateien:** `automation/optimizer/invariants.py`, `automation/config/optimizer.json`, `automation/AGENTS.md`

---

#### #1071 — `check_atr_scale_homogeneity` meldet eine Ursache, die es nicht gemessen hat (Wiederkehr #1052)

**Priorität:** P1

**Symptom:** „1 Symbol(e) mit einer ATR-Spannweite über 6.0x zwischen Strategien: {'TSLA.ETORO': 13.83} — **Signatur einer Sprungstelle in der Preisreihe (#1028)**." Nachgerechnet: 27,66525 (OpeningRange) ÷ **2,0** (DynamicBreakout) = 13,8326. Der Nenner liegt exakt auf `atr_floor_bps_by_asset_class["EQUITY"] = 2.0`.

**Root-Cause:** Der Check misst eine Spannweite und behauptet eine Ursache. Es gibt mindestens drei Mechanismen, die dieselbe Spannweite erzeugen: (a) Preis-Sprungstelle, (b) **Floor-Bindung im Nenner**, (c) Fremdkohorte. Der Meldungstext nennt nur (a). Wörtliche Wiederkehr von #1052 — dort mit drei Symbolen, zwei Mechanismen und einem dritten in der Meldung; hier mit einem Symbol und dem Floor-Mechanismus.

Analog `check_n_periods_homogeneity` = 68,98 = 1655,5 ÷ 24,0, getrieben von der degenerierten Squeeze-Study (#1079), nicht von einer Datenanomalie.

**Fix:** Beide Checks melden den **Mechanismus**, nicht eine geratene Ursache: `atr_floor_binding_studies` (Studies mit `atr_median_bps == resolve_atr_floor_bps(symbol)` bis auf Rundung) wird ausgewiesen und die Meldung differenziert — „Nenner an der Floor-Grenze (n Studies) ⇒ Spannweite ist ein Konfigurationsartefakt" vs. „Nenner frei ⇒ Datenanomalie-Verdacht". Kein Check nennt eine Ursache, die er nicht gemessen hat.

**Akzeptanzkriterien:**
- Auf diesem Report nennt `check_atr_scale_homogeneity` die Floor-Bindung von DynamicBreakout und **nicht** eine Sprungstelle.
- `atr_floor_binding_studies` ist ein Report-Feld; für diesen Lauf `["DynamicBreakoutStrategy/TSLA.ETORO"]`.
- `check_n_periods_homogeneity` nennt Squeeze als degenerierten Nenner (24 Perioden) und verweist auf #1079.

**Betroffene Dateien:** `automation/optimizer/invariants.py`, `automation/optimizer/report.py`

---

#### #1072 — Stopdistanz unter den Round-Trip-Kosten (Wiederkehr #1050/#1051)

**Priorität:** P1

**Symptom:** `c_rt(TSLA) = commission_bps 1,0 + spread EQUITY 3,0 = 4,0 bps`. Nominale Stopdistanz: DynamicBreakout **3,39 bps** (0,85 × c_rt), HourlyMeanReversion **3,47 bps** (0,87 × c_rt), TrendPullback 4,70 bps (1,17 ×), VwapExhaustion 6,01 bps (1,50 ×), MeanReversion 6,52 bps (1,63 ×).

**Root-Cause:** Die notwendige Bedingung `E[MFE] > d + c_rt` ist für die ersten beiden strukturell verletzt — die Position kann den Stop nicht überleben, bevor die Kosten sie auffressen, unabhängig vom Signal. Es gibt keinen Preflight, der einen Parametervektor mit `d < min_stop_to_cost_ratio · c_rt` gar nicht erst zulässt. Die betroffenen Studies sind exakt die mit negativer Expectancy: DynamicBreakout −8,20 bps, HourlyMeanReversion −52,70 bps.

**Fix:** `min_stop_to_cost_ratio` (Vorschlag 3.0) in `tournament.json`; Preflight über den `#612-constraints_func`-Pfad in `spaces.py` (Sampler-Constraint, keine Reward-Strafe); `atr_floor_bps_by_asset_class` an `c_rt` gekoppelt statt frei gepflegt (gemeinsam mit #1069 Punkt 2). Neue Invariante `check_stop_cost_ratio` (severity high): kein Study mit `atr_median_bps · atr_trailing_multiplier_median < min_stop_to_cost_ratio · c_rt`.

**Akzeptanzkriterien:**
- `check_stop_cost_ratio` FAILt auf diesem Report mit fünf Offendern (0,85 / 0,87 / 1,17 / 1,50 / 1,63).
- Nach dem Preflight sampelt kein Trial einen Vektor unterhalb der Schwelle; die Constraint-Verletzung erscheint in `constraint_violations_observed`, nicht im Reward.

**Betroffene Dateien:** `automation/config/tournament.json`, `automation/config/backtest.json`, `automation/optimizer/spaces.py`, `automation/optimizer/invariants.py`

---

### Kohorte D — Bericht- und Entscheidungs-Semantik (P1)

---

#### #1073 — Die ausreisser-robuste Expectancy wird gemessen, aber weder gerankt noch gegatet

**Priorität:** P1

**Symptom:** Drei Vorzeichenwechsel zwischen `holdout_expectancy` und `holdout_expectancy_winsorized` (Beweis B-8), darunter der **erstgelistete** Kandidat des Berichts: AdxAtrMomentum +17,23 bps → **−1,44 bps** bei 6 Ausreissern unter 132 Trades. ComboTrendVwap +3,24 → −5,80 bei **1** Ausreisser unter 55. Der Bericht sortiert §2.2 und §2.3 nach `holdout_total_return`.

**Root-Cause:** `holdout_expectancy_winsorized` und `holdout_expectancy_outlier_count` sind seit #1031/#1042 telemetriert, werden aber von keiner Sortierung, keinem Gate und keiner `deployment_gate`-Klausel konsumiert. Die Rangfolge des Berichts entsteht damit auf der ausreisser-empfindlichsten verfügbaren Grösse.

**Fix:**
1. §2.2/§2.3 erhalten die Spalten „Expectancy (winsorisiert)" und „Ausreisser/Trades" und werden nach der **winsorisierten** Expectancy sortiert; `holdout_total_return` bleibt sichtbar.
2. `deployment_gate` erhält die Klausel `expectancy_outlier_robust`: `sign(holdout_expectancy_winsorized) == sign(holdout_expectancy)` **und** `holdout_expectancy_winsorized > 0`.
3. Neue Invariante `check_expectancy_outlier_dependence` (severity high): FAIL bei Vorzeichenwechsel zwischen roher und winsorisierter Expectancy.

**Akzeptanzkriterien:**
- Auf diesem Report FAILt `check_expectancy_outlier_dependence` mit drei Offendern (AdxAtr, Combo, MeanRev).
- §2.2 listet Donchian/Rsi2/OpeningRange vor AdxAtr/Combo.
- Ein Kandidat mit Vorzeichenwechsel erhält `deployment_decision.admitted = False` mit benannter Klausel.

**Betroffene Dateien:** `automation/optimizer/summary_de.py`, `automation/optimizer/deployment_gate.py`, `automation/optimizer/invariants.py`

---

#### #1074 — `binding_gate` ist argmin über rohe, dimensionsbehaftete Deltas

**Priorität:** P1

**Symptom:** `binding_gate == argmin(near_miss_deltas["binding"])` über die **rohen** Werte in 14/14 Studies. AdxAtr: `{oos_min_trades: 442,0 · oos_max_drawdown: 0,2626 · oos_min_psr: 0,0653}`. `oos_min_trades` (Skala 10²) gewinnt nie; `oos_max_drawdown` liegt in allen 14 Studies im Band 0,2626–0,2984 — der beobachtete Drawdown steht bei ~1–4 % gegen ein 30-%-Gate, das Gate bindet also **nirgends** — und wird trotzdem in **8 von 14** Studies als „das bindende Gate" ausgewiesen.

**Root-Cause:** Wiederkehr der #631-Fehlerklasse (Constraint-Deltas müssen dimensionslos sein), hier in der Near-Miss-/Binding-Attribution statt im Reward. Eine Trade-Anzahl, ein Drawdown-Bruchteil und eine Wahrscheinlichkeit werden auf einer Achse verglichen.

**Fix:** Normierung wie in `reward._normalized_gate_distances`: `delta_norm = delta / scale(gate)` mit gate-spezifischer Skala (Schwellwert selbst oder realisierte Streuung der Kohorte). `binding_gate = argmin(delta_norm)`. Beide Werte (roh und normiert) im Report, damit der Wechsel nachvollziehbar bleibt.

**Akzeptanzkriterien:**
- `near_miss_deltas` trägt `binding_normalized`; `binding_gate` ist daraus abgeleitet.
- Auf diesem Report ist `oos_max_drawdown` in keiner Study mehr das bindende Gate (normiert 0,875–0,995 gegen `oos_min_psr` 0,109–0,663).
- Regressionstest mit drei Gates unterschiedlicher Skala.

**Betroffene Dateien:** `automation/optimizer/confirm.py`, `automation/optimizer/reward.py`, `automation/optimizer/report.py`

---

#### #1075 — `holdout_binding_gate` nennt Gates, die keine sind, und meldet sie auch bei bestandenem Holdout

**Priorität:** P2

**Symptom:** `holdout_binding_gate == argmin(holdout_gate_deltas)` (roh) in 13/13 Studies mit Holdout. In vier Studies lautet die Antwort `oos_min_expectancy` — seit v13/#697 **kein `eligible_requires_all`-Gate mehr**. Bei AdxAtr trägt `decision_chain` für die Holdout-Stufe `passed: true`, das Delta ist mit +0,001648 **positiv** (bestanden), und der Bericht nennt trotzdem ein „bindendes" Gate.

**Root-Cause:** Dieselbe Skalen-Ursache wie #1074 (`oos_min_expectancy` hat die kleinsten natürlichen Einheiten und gewinnt deshalb bei bestandenem Holdout immer; bei gescheitertem gewinnt `oos_min_sortino` als negativster Rohwert). Zusätzlich filtert die Attribution nicht auf die aktuell **aktive** Gate-Menge und nicht auf „Stufe tatsächlich gescheitert".

**Fix:** (1) Normierung wie #1074. (2) `holdout_binding_gate` wird nur gesetzt, wenn die Holdout-Stufe FAILt; sonst `null` und stattdessen `holdout_tightest_margin` (semantisch ehrlich). (3) Die Attribution filtert gegen `tournament.json['eligible_requires_all'] ∪ ['eligible_requires_any']`; Deltas zu inaktiven Gates bleiben als Telemetrie erhalten, sind aber nicht attributionsfähig.

**Akzeptanzkriterien:**
- Für AdxAtr/Donchian/OpeningRange/Rsi2 ist `holdout_binding_gate = null` und `holdout_tightest_margin` gesetzt.
- Kein deaktiviertes Gate erscheint je als `binding_gate` oder `holdout_binding_gate`.

**Betroffene Dateien:** `automation/optimizer/confirm.py`, `automation/optimizer/report.py`, `automation/optimizer/summary_de.py`

---

#### #1076 — `gate_inventory.n_rejections` zählt die Bestandenen; die Gate-Konsolidierungs-Governance läuft auf diesem Zähler

**Priorität:** P0

**Symptom:** In 13 von 14 Studies gilt exakt `gate_inventory[oos_min_psr].n_rejections == n_eligible == is_rejection_detail_counts["NONE"]`. Der tatsächliche PSR-Ablehnungszähler derselben Study weicht um bis Faktor **98** ab (OpeningRange: Inventar 98, Detail-Zähler 1). `n_solo_rejections` und `marginal_delta` tragen denselben Wert.

**Root-Cause:** Der Zähler greift auf den `NONE`-Eimer des Rejection-Detail-Histogramms zu statt auf den gate-spezifischen. (Squeeze weicht ab: Inventar 31, `NONE` 5 — dort existieren sieben Rejection-Codes statt drei, der Fehlgriff trifft einen anderen Eimer.)

**Tragweite:** `check_gate_marginal_contribution` konsumiert genau dieses Feld und empfiehlt in diesem Lauf die Entfernung von `min_trades` und `max_drawdown` aus `eligible_requires_all` — **beide stehen in `tournament.json['gate_consolidation_protected']`**. Fünf Gates wurden bereits auf derselben Evidenzbasis entfernt (#677/#697/#776/#960/#848); `eligible_requires_all` ist auf drei Klauseln geschrumpft. Die Empfehlung würde `oos_min_psr` als einziges Eligibility-Gate zurücklassen.

**Fix:**
1. `gate_inventory` wird aus dem gate-spezifischen Rejection-Zähler gebildet; Kreuzprüfung `Σ n_rejections über alle Gates + n_eligible == n_evaluable` als Assertion im Aufbau.
2. `check_gate_marginal_contribution` liest `gate_consolidation_protected` und formuliert für geschützte Gates keine Entfernungsempfehlung, sondern eine Neukalibrierungs-Empfehlung.
3. Neue Invariante `check_gate_inventory_coherence` (severity high): `gate_inventory[g].n_rejections` stimmt mit `is_rejection_detail_counts` überein.

**Akzeptanzkriterien:**
- `check_gate_inventory_coherence` FAILt auf diesem Report für 13 Studies und PASST nach dem Fix.
- Für OpeningRange gilt danach `gate_inventory[oos_min_psr].n_rejections == 1`.
- `check_gate_marginal_contribution` empfiehlt für `min_trades`/`max_drawdown` keine Entfernung mehr.
- **SPERRVERMERK:** kein weiteres Gate wird aus `eligible_requires_all` entfernt, bevor dieser Fix gemergt und die #677/#697/#776/#960/#848-Entscheidungen gegen den korrigierten Zähler nachgerechnet sind.

**Betroffene Dateien:** `automation/optimizer/confirm.py`, `automation/optimizer/invariants.py`, `automation/optimizer/report.py`

---

#### #1077 — „Excess/Exposure" mit fabriziertem Nenner belohnt Nicht-Handeln

**Priorität:** P1

**Symptom:** `TrendPullbackStrategy` hat 0 Holdout-Trades und `holdout_exposure_fraction = null`. Die Zeile zeigt „Zeit im Markt: k. A." und zugleich „**Excess/Exposure: 1395,2 %**" — die grösste Zahl der ökonomischen Tabelle. SqueezeBreakout (1,7 % Exposure, 6 Trades) erhält 816,6 %, FlashCrash (2,0 %) 661,5 %.

**Root-Cause:** `summary_de._EXPOSURE_EPSILON = 0.01` wird als Nenner-Floor eingesetzt: `excess / max(exposure or 0.0, 0.01)`. Der Kommentar nennt das ausdrücklich „reine Anzeige-Sicherung, kein kalibrierter Schwellenwert" — die Zahl erscheint aber ungekennzeichnet in einer Spalte, die als Qualitätsnormierung gelesen wird, und ordnet monoton nach *weniger* Handel.

**Fix:** Kein Ersatznenner. `excess_per_exposure = null` (Anzeige „k. A."), wenn `exposure` fehlt oder unter einer Mindestexposition (Vorschlag 5 %) liegt; die Zeile trägt stattdessen einen Hinweis „zu geringe Marktzeit für eine Normierung (n Trades)". Zusätzlich: Studies mit `holdout_total_trades < oos_min_trades` erscheinen in §2.3 in einem eigenen Block „nicht bewertbar", nicht in derselben Rangfolge.

**Akzeptanzkriterien:**
- TrendPullback zeigt „k. A." statt 1395,2 %.
- Squeeze/FlashCrash/Vwap (Exposure < 5 %) stehen im Block „nicht bewertbar".
- Keine Zeile der Zusammenfassung enthält einen Wert, der aus einem Ersatznenner entstanden ist.

**Betroffene Dateien:** `automation/optimizer/summary_de.py`

---

### Kohorte E — Inferenz, Zähler, Suchhaushalt (P1/P2)

---

#### #1078 — UNKNOWN-Exit und `EXPECTANCY_NOTIONAL_DEGENERATE` sind dasselbe Ereignis; die Konzentrationsrate mischt Grundgesamtheiten

**Priorität:** P2

**Symptom:** In **allen 14** Studies gilt `exit_reason_histogram["UNKNOWN"] == inference_diagnostics_by_code["EXPECTANCY_NOTIONAL_DEGENERATE"]` exakt (1378/1378, 3301/3301, 1156/1156, 1309/1309, 1146/1146, …, beide `None` bei Sma und VolPump). `check_inference_diagnostics_concentration` feuert bei Squeeze mit **1,4464 = 81/56**: Zähler über alle 180 Trials, Nenner `n_trials_informative` = 56.

**Root-Cause:** (a) Ein Round-Trip mit degeneriertem Notional erhält keine Exit-Reason — zwei Telemetriekanäle beschreiben dasselbe, und `check_inference_diagnostics_absent` sowie die Exit-Reason-Abdeckung doppelzählen es. (b) Die Konzentrationsrate teilt einen über die volle Trial-Menge gezählten Zähler durch einen über die informative Teilmenge gezählten Nenner (#1033-Wiederkehr mit exakter Wurzel).

**Fix:** (a) Die Identität wird explizit gemacht: `exit_reason_histogram["UNKNOWN"]` erhält den Kommentar/Alias `NOTIONAL_DEGENERATE`, oder der Diagnosecode wird aus dem Exit-Histogramm herausgerechnet. Genau eine Quelle bleibt normativ. (b) Zähler und Nenner der Konzentrationsrate stammen aus derselben Grundgesamtheit (`inference_diagnostics_trials_by_code` gegen `n_trials`, nicht gegen `n_trials_informative`).

**Akzeptanzkriterien:**
- Keine Rate > 1,0 in `check_inference_diagnostics_concentration` über diesen Report.
- Squeeze: 81/180 = 0,45.
- Die UNKNOWN/NOTIONAL_DEGENERATE-Identität ist als Test fixiert (falls beabsichtigt) oder aufgelöst (falls nicht).

**Betroffene Dateien:** `automation/optimizer/invariants.py`, `automation/backtest_runner.py`, `automation/optimizer/report.py`

---

#### #1079 — Der blockierende Selektions-Check rechnet gegen geprunte Trials

**Priorität:** P1

**Symptom:** `check_selection_statistic_availability` FAILt **blocking** mit `{'SqueezeBreakoutStrategy/TSLA.ETORO': 0.4308}` = 56/130. Squeeze ist die einzige Study mit `n_trials_pruned = 78` und die einzige, bei der `n_trials = n_evaluable + n_trials_unevaluable + n_trials_failed` **nicht** gilt (180 ≠ 130 + 46 + 0). `n_evaluable = 130 = 56 informative + 74 unmeasurable`; `130 + 78 = 208 > 180` ⇒ 74 geprunte Trials stecken in `n_evaluable`.

**Root-Cause:** #914 („geprunte Trials verlassen den Reward-Pfad vollständig und fallen aus `n_family`/`n_modelled`/`budget_executed_fraction` heraus") ist für `n_evaluable` nicht durchgezogen. Ein geprunter Trial kann per Konstruktion keine Selektionsstatistik tragen; ihn in den Nenner der Verfügbarkeitsquote zu nehmen, erzeugt einen garantierten Fehlalarm. Auf der ehrlichen Grundgesamtheit ist die Verfügbarkeit **56/56 = 1,0**.

Der Check steht in `fail_fast_invariants` ⇒ dieser Fehlalarm kann Läufe abbrechen.

**Fix:** `n_evaluable` schliesst `TrialState.PRUNED` aus; die Partition `n_trials = informative + pruned + unevaluable + failed` ist die einzige normative. `check_selection_statistic_availability` rechnet gegen `n_trials_informative`. `check_denominator_coherence` wird um die zweite Identität `n_evaluable + n_trials_pruned + n_trials_unevaluable + n_trials_failed == n_trials` erweitert.

**Akzeptanzkriterien:**
- Squeeze: `n_evaluable = 56`, Verfügbarkeit 1,0, Check PASST.
- Die erweiterte `check_denominator_coherence` FAILt auf dem aktuellen Report für Squeeze und PASST nach dem Fix.
- Der Lauf bricht nicht mehr über diesen Check ab.

**Betroffene Dateien:** `automation/optimizer/report.py`, `automation/optimizer/invariants.py`, `automation/optimizer/sweep.py`

---

#### #1080 — Familien-Multiplizität 467 gegen 1619; eine Study fehlt vollständig im Stage-1-Block

**Priorität:** P1

**Symptom:** `cross_study.n_family["TSLA.ETORO"] = 467`. Σ `n_selection_statistic_available` = **1619** (die seit #822 vorgeschriebene Grundgesamtheit), Σ `n_evaluable` = 1693 (identisch mit `selection_rule_families`), Σ `n_family_stage1` = **1508**. `TrendPullbackStrategy` fehlt vollständig im `n_family_stage1`-Block, obwohl seine Study 111 Trials mit verfügbarer Selektionsstatistik trägt (Feld `n_family_stage1 = null`, vermutlich weil `holdout_total_trades = 0`).

**Tragweite:** Φ⁻¹(1−1/467) = 2,859 gegen Φ⁻¹(1−1/1619) = 3,229 ⇒ die Deflations-Referenz SR\* liegt um ~13 % zu niedrig, in **jeder** Promotionsentscheidung mit familienweiter Korrektur.

**Fix:** Eine Ableitung, eine Quelle: `n_family` und `n_family_stage1` werden aus derselben Funktion über dieselbe Grundgesamtheit gebildet (`oos_selection_statistic_available`, #822). `n_family_stage1` wird auch für Studies ohne Holdout-Trades gestempelt. Neue Invariante `check_n_family_partition`: `n_family(symbol) == Σ n_family_stage1(symbol, *)`.

**Akzeptanzkriterien:**
- `n_family["TSLA.ETORO"] == Σ n_family_stage1 == 1619`.
- `n_family_stage1["TrendPullbackStrategy"] == 111`.
- `check_n_family_partition` FAILt auf diesem Report und PASST nach dem Fix.

**Betroffene Dateien:** `automation/optimizer/sweep.py`, `automation/optimizer/report.py`, `automation/optimizer/invariants.py`

---

#### #1081 — Der Kostenstress stresst nur die Kommission, nicht die Round-Trip-Kosten

**Priorität:** P1

**Symptom:** `(exp − exp_1.5x)/0,5 = (exp_1.5x − exp_2x)/0,5 = 1,000 bps` in allen 13 Studies mit Trades — exakt `commission_bps`. Der Spread (3,0 bps EQUITY) bleibt unangetastet. Der „2×-Stress" erhöht die realen Round-Trip-Kosten von 4,0 auf 5,0 bps (**+25 %**), nicht auf 8,0 bps.

**Root-Cause:** `backtest_runner._expectancy_cost_stress` verwendet `extra_rate = (multiplier - 1.0) * (commission_bps / 10000.0)`. Der Docstring ist ehrlich („`multiplier`-fache Round-Trip-**Kommission**"), aber `deployment_gate._clause_cost_stress` konsumiert den Wert als Kosten-Robustheitsklausel, und die Zusammenfassung nennt §2.4 „Kostenbasis (Spread + Kommission je Asset-Klasse, #774/#775)". Der Spread ist der grössere Kostenblock (75 % von c_rt) und wird nicht gestresst.

**Fix:** `_expectancy_cost_stress` konsumiert `_read_default_round_trip_cost_bps(inst_id_str)` statt `commission_bps` — dieselbe Auflösungskette, die #775 bereits etabliert hat. Die Felder werden zur Klarheit umbenannt (`expectancy_round_trip_cost_stress_*`); der alte Name bleibt eine Sitzung lang als Alias.

**Akzeptanzkriterien:**
- Auf diesem Report ergibt der 2×-Stress −4,0 bps je Trade; ComboTrendVwap kippt von +3,24 auf −0,75 bps.
- `deployment_gate._clause_cost_stress` konsumiert den neuen Wert.
- Regressionstest: für CRYPTO ergibt der Stress −16,0 bps (1,0 + 15,0), nicht −1,0.

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/optimizer/parsing.py`, `automation/optimizer/deployment_gate.py`, `automation/optimizer/summary_de.py`

---

#### #1082 — Die Suche hat über 90 % ihres Budgets kein ordnendes Ziel; `reward_std_feasible` ist keine eigene Messung

**Priorität:** P2

**Symptom:** `check_objective_branch_coverage` FAILt für fünf Studies: AdxAtr 4/140 (2,86 %), Squeeze 5/180 (2,78 %), TrendPullback 8/140 (5,71 %), DynamicBreakout 9/100 (9,00 %), Rsi2 15/160 (9,38 %). Zugleich `reward_std_total == reward_std_feasible` **bit-identisch** in 13 von 14 Studies — auch dort, wo nur 4 von 136 Trials eligible sind (AdxAtr) oder 196 von 256 (Combo).

**Root-Cause:** (a) Die `branch == 'per_symbol'`-Quote misst, welcher Anteil der Auswertungen überhaupt die ordnende Qualitätsinformation trägt; bei 2,8 % verbrennt die Study 97 % ihres Budgets ohne Gradienten (Pitfall #124, doppelt kodierte Feasibility). (b) `reward_std_feasible` sollte die Streuung über die *feasible* Teilmenge sein; dass sie in 13/14 Studies exakt der Gesamtstreuung entspricht, zeigt, dass sie über dieselbe Menge gebildet wird — der #949-Diagnosewert trägt keine eigene Information. Nur Squeeze weicht ab (0,3638 vs 0,5314) — die einzige Study mit geprunten Trials, was den Verdacht bestätigt, dass die Trennung ausschliesslich Pruning abbildet.

**Fix:** (a) Die Branch-Quote wird zum Suchbudget-Kriterium: eine Study unter der Schwelle erhält im nächsten Lauf entweder gelockerte Bounds (über den geklammerten Pfad aus #1066) oder wird deprioritisiert — nicht dasselbe Budget noch einmal. (b) `reward_std_feasible` wird über die Trials mit `constraint ≤ 0` gebildet und gegen `reward_std_total` in `check_reward_dynamic_range` geprüft; sind sie identisch, ist das selbst ein Befund (`REWARD_FEASIBLE_PARTITION_DEGENERATE`).

**Akzeptanzkriterien:**
- `reward_std_feasible` unterscheidet sich für AdxAtr (4 eligible von 136) messbar von `reward_std_total`, oder der neue Diagnosecode feuert.
- Die fünf Studies unter der Branch-Schwelle erscheinen im Suchbudget-Vorschlag des nächsten Laufs.

**Betroffene Dateien:** `automation/optimizer/report.py`, `automation/optimizer/invariants.py`, `automation/optimizer/sweep.py`

---

## 3b. Nachtrag aus dem vollständigen Optimizer-Log

Der Lauf hat erstmals sein vollständiges Log im Repo (`optimizer_815455db_*.log`, 10,8 MB, 11 793 parsebare `JSON_EVENT`s). Drei Befunde, die aus dem Report-JSON allein nicht sichtbar sind.

### B-17 — Die Invariantensuite läuft viermal pro Lauf; das Artefakt hält Welle 4, das Log Welle 1

`INVARIANT_CHECK_FAILED` erscheint in **vier** zeitlich getrennten Wellen, `INVARIANT_RESULT` nur in der ersten:

| Welle | Zeit | `INVARIANT_CHECK_FAILED` | `INVARIANT_RESULT` |
|---|---|---:|---|
| 1 | 07:21:29.145–.156 | **55** | 310 (255 PASS / **55 FAIL**) |
| 2 | 07:21:40.539–.550 | **55** | — |
| 3 | 07:21:48.641–.650 | **56** | — |
| 4 | 07:21:54.964–.976 | **56** | — |

Jeder FAIL steht damit exakt viermal im Log (Faktor 4,0 für 14 von 15 Check-Namen). Die einzige Ausnahme ist `check_coverage_ledger_continuity` mit **2** — er FAILt nur in den Wellen 3 und 4. Der Report behält Welle 4 (56 FAILs), das `INVARIANT_RESULT`-Log Welle 1 (55 FAILs, `check_coverage_ledger_continuity` = PASS).

Damit ist **#1064 mechanistisch bewiesen**: zwischen Welle 2 (07:21:40) und Welle 3 (07:21:48) landet der Report *dieses* Laufs in `REPORTS_DIR`, `has_prior_reports` kippt von `False` auf `True`, und der Check schlägt um. Der Fail-Fast-Abbruch wird um **07:21:48** protokolliert — exakt in Welle 3, der ersten mit dem gekippten Verdikt.

Nebenrechnung: 4 × 310 = **1240 Invarianten-Auswertungen** für 310 berichtete, über 26 s. Auf diesem Ein-Symbol-Lauf ist das billig; auf dem 143-Symbol-Lauf mit 2263 Checks nicht.

### B-18 — Der Champion-Closed-Loop ist nicht unerreichbar, sondern auf dem Coverage-Ledger deadlocked

`check_champion_writeback_reachability` meldet: „2 Champion-Store-Einträge, aber 0 Writebacks über den gesamten Store-Stand — Ebene 2 (#706) ist vermutlich unerreichbar (**#818-Regression: `maybe_write_back` ohne Produktions-Call-Site**)."

Das Log widerlegt das: **14 `CHAMPION_WRITEBACK`-Events**, eines je Study, jeweils bei Study-Abschluss, alle `applied: false`:

```
07:05:34  SmaCrossover              STORE_EMPTY
07:06:46  MeanReversion             STORE_EMPTY
07:08:02  DynamicBreakout           STORE_EMPTY
07:09:12  FlashCrashReversal        STORE_EMPTY
07:10:23  VolatilityBreakoutPump    STORE_EMPTY
07:11:39  ComboTrendVwap            STORE_EMPTY
07:12:52  VwapExhaustion            STORE_EMPTY
07:14:04  TrendPullback             STORE_EMPTY
07:15:21  AdxAtrMomentum            NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED   corroboration_count=1
07:16:37  HourlyMeanReversion       STORE_EMPTY
07:17:44  SqueezeBreakout           STORE_EMPTY
07:18:55  OpeningRangeBreakout      NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED   corroboration_count=1
07:20:06  DonchianRegimeBreakout    STORE_EMPTY
07:21:20  Rsi2Reversion             STORE_EMPTY
```

Zwei Aussagen:

1. **Die Call-Site existiert und feuert 14×.** Die gemeldete Ursache ist falsch. Der Reason-Wert `STORE_EMPTY` ist zudem **nicht-monoton** — leer, dann um 07:15:21 nicht-leer, dann um 07:16:37 wieder leer, um 07:18:55 nicht-leer, um 07:21:20 wieder leer. Entweder ist der Reason pair-skopiert und schlicht falsch benannt (dann verdeckt er, dass 12 von 14 Paaren **überhaupt keinen** Champion haben), oder der Store verliert zwischen Lesevorgängen seinen Inhalt.

2. **Die zwei Paare mit Eintrag scheitern an `corroboration_count = 1`.** Korroboration verlangt einen zweiten Lauf, der das Paar bestätigt. Das Coverage-Ledger führt aber `total_runs_started = 1` (#1064) — der Zähler kann nie über 1 hinaus. Kausalkette:

   > Ledger-Reset (#1064) → `corroboration_count` bleibt 1 → `maybe_write_back` nie `applied` → `check_champion_seed_coverage`: `strategy_defaults` = **100 %** → jeder Lauf startet kalt.

Der Report verliert beide Informationen: `champions.skipped_by_reason` meldet `{"NOT_WRITTEN_BACK": 2}` — ein Label, das im Log nicht vorkommt, das nur die 2 Store-Einträge statt der 14 Versuche zählt, und das tautologisch ist („übersprungen, weil nicht zurückgeschrieben").

### B-19 — `EXPECTANCY_NOTIONAL_DEGENERATE` ist numerischer Staub, kein Mikro-Trade

9205 Ereignisse mit dem Notional im Payload:

```
notional = 9.269029987990507e-14  <  5 % des Median-Notionals (74.88657500000001)
```

Verteilung über alle 9205: **min 4,26e-14 · Median 1,86e-13 · max 9,90e-13**. **100 %** liegen unter 1e-12. Das Referenz-Median-Notional liegt konstant bei 74,83–74,94. Es handelt sich also nicht um kleine Trades, sondern um Round-Trips mit einer Positionsgrösse von rund **1e-15 der normalen** — Fliesskomma-Residuen eines Netto-Exposure-Nulldurchgangs.

Diese Dust-Legs werden trotzdem als volle Round-Trips gezählt:

| Strategie | Dust-Round-Trips | Round-Trips gesamt | Anteil |
|---|---:|---:|---:|
| DonchianRegimeBreakout | 1156 | 9 794 | **11,80 %** |
| Rsi2Reversion | 3301 | 34 413 | **9,59 %** |
| VwapExhaustion | 1146 | 20 477 | 5,60 % |
| TrendPullback | 1309 | 31 395 | 4,17 % |
| SqueezeBreakout | 18 | 637 | 2,83 % |
| AdxAtrMomentum | 1378 | 67 882 | 2,03 % |
| … | | | |
| **Summe** | **9205** | **330 083** | 2,79 % |

Zum Vergleich: 330 083 gepoolte Round-Trips über alle Studies gegen **572** Holdout-Trades.

Damit ist zweierlei erklärt: (a) die Identität `UNKNOWN` ≡ `EXPECTANCY_NOTIONAL_DEGENERATE` aus B-12 — ein Dust-Leg hat keinen Exit-Grund, weil es keinen Exit gab; (b) der Verdünnungsmechanismus, gegen den #1036 den Magnituden-Ast bauen musste — `timebox_violating_trades_frac = 10/67882` hat einen Nenner, der zu 2–12 % aus Simulationsartefakten besteht.

---

#### #1083 — Die Invariantensuite läuft viermal; Log und Artefakt tragen verschiedene Wellen

**Priorität:** P1

**Symptom:** Vier Wellen `INVARIANT_CHECK_FAILED` (55/55/56/56), eine Welle `INVARIANT_RESULT` (Welle 1). Der Artefakt-Stand ist Welle 4. Jeder FAIL steht viermal im Log. 1240 Auswertungen für 310 berichtete Ergebnisse.

**Root-Cause:** `_build_report` wird mehrfach aufgerufen (Report-Bau, Fail-Fast-Probe, Abbruchpfad, finaler Artefakt-Schreibvorgang) und wertet die vollständige Suite jedes Mal neu aus, statt das Ergebnis durchzureichen. Solange alle Eingaben stabil sind, ist das nur Redundanz; sobald eine Eingabe vom eigenen Lauf abhängt (#1064), entscheidet die Reihenfolge über das Verdikt.

**Fix:** Die Suite wird genau **einmal** je Lauf ausgewertet; das Ergebnis wird an Fail-Fast-Probe, Abbruchpfad und Artefakt-Schreiber durchgereicht. Ist eine Neuauswertung fachlich nötig (z. B. nach Symbol-Quarantäne), trägt jedes `INVARIANT_*`-Event ein `evaluation_seq`-Feld, und der Report weist aus, welche Auswertung er hält.

**Akzeptanzkriterien:** `count(INVARIANT_CHECK_FAILED) == count(FAIL in run.json)` je Check-Name. Für jeden Check-Namen gilt `INVARIANT_RESULT.status == run.json.passed` (gemeinsames Kriterium mit #1064).

**Betroffene Dateien:** `automation/optimizer/report.py`, `automation/optimizer/sweep.py`

---

#### #1084 — `check_champion_writeback_reachability` nennt eine widerlegte Ursache; der Closed Loop hängt am Coverage-Ledger

**Priorität:** P1

**Symptom:** Der Check behauptet „`maybe_write_back` ohne Produktions-Call-Site (#818-Regression)". Das Log zeigt 14 `CHAMPION_WRITEBACK`-Events, eines je Study. 12 mit `STORE_EMPTY`, 2 mit `NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED` bei `corroboration_count = 1`. `STORE_EMPTY` wechselt nicht-monoton (leer → nicht-leer → leer → nicht-leer → leer).

**Root-Cause:** (a) Der Check schliesst aus `written_back == 0` auf eine fehlende Call-Site, statt die `CHAMPION_WRITEBACK`-Telemetrie zu konsumieren, die die Antwort enthält. Dieselbe Fehlerklasse wie #1071/#1052 — eine Meldung behauptet eine Ursache, die der Check nicht gemessen hat. (b) Der tatsächliche Blocker ist `corroboration_count = 1` gegen eine Schwelle ≥ 2; Korroboration erfordert einen zweiten Lauf, den ein auf `total_runs_started = 1` zurückgesetztes Ledger (#1064) nie ausweist. (c) `STORE_EMPTY` ist entweder pair-skopiert und falsch benannt — dann verdeckt der Name, dass 12 von 14 Paaren keinen Champion besitzen — oder der Store ist nicht persistenzstabil.

**Fix:**
1. Der Check konsumiert die `CHAMPION_WRITEBACK`-Events und meldet die **beobachtete** Verteilung der `skipped_reason`-Werte, nicht eine geratene Ursache. Erst wenn **null** Events vorliegen, lautet die Diagnose „keine Call-Site".
2. `STORE_EMPTY` wird in `STORE_EMPTY` (Store insgesamt leer) und `NO_ENTRY_FOR_PAIR` (Store nicht leer, kein Eintrag für dieses Paar) aufgetrennt.
3. `champions.skipped_by_reason` zählt **Versuche** (14), nicht Store-Einträge (2), und übernimmt die Reason-Werte aus dem Log; das tautologische `NOT_WRITTEN_BACK` entfällt.
4. Neue Invariante `check_champion_corroboration_reachable`: FAIL, wenn `max(corroboration_count) < corroboration_threshold` **und** `symbol_coverage.total_runs_started == 1` — der Deadlock wird benannt, statt sich als „unerreichbar" zu tarnen.

**Akzeptanzkriterien:**
- Auf diesem Report nennt der Check „14 Versuche, 12× `NO_ENTRY_FOR_PAIR`, 2× `NOT_CORROBORATED` bei `corroboration_count=1`" und **nicht** die fehlende Call-Site.
- `check_champion_corroboration_reachable` FAILt auf diesem Report und PASST nach dem #1064-Ledger-Fix, sobald ein zweiter Lauf gezählt wird.
- `champions.skipped_by_reason` summiert auf 14.

**Betroffene Dateien:** `automation/optimizer/invariants.py`, `automation/optimizer/champions.py`, `automation/optimizer/report.py`

---

#### #1085 — Dust-Round-Trips (Notional ~1e-13) füllen jeden gepoolten Nenner

**Priorität:** P1

**Symptom:** 9205 Round-Trips mit einem Notional zwischen 4,26e-14 und 9,90e-13 gegen ein Median-Notional von 74,89 — **100 % unter 1e-12**. Sie werden als vollwertige Round-Trips gezählt: bis 11,80 % einer Study (Donchian), 9,59 % (Rsi2), 2,79 % über alle 330 083 gepoolten Round-Trips.

**Root-Cause:** Die Round-Trip-Extraktion (Position-Open → Flat / Netto-Exposure-Nulldurchgang) erzeugt bei Scale-in/Scale-out Fliesskomma-Residuen und wertet den Rest-Nulldurchgang als eigenen Round-Trip. `_expectancy_winsorized`/`_expectancy_capital_weighted` filtern sie korrekt über den 5-%-Median-Notional-Boden (#1031) — **alle anderen gepoolten Zähler nicht**. Betroffen sind unter anderem `oos_total_trades_with_exit_telemetry`, `exit_reason_histogram` (Dust-Legs erscheinen als `UNKNOWN`, siehe B-12) und `timebox_violating_trades_denominator`.

**Tragweite:** `timebox_violating_trades_frac` wird gegen einen Nenner gerechnet, der zu 2–12 % aus Artefakten besteht — genau der Verdünnungsmechanismus, gegen den #1036 den Magnituden-Ast einführen musste. Die Anteilsschwelle `timebox_violation_study_tolerance = 0.25` ist dadurch zusätzlich unerreichbar.

**Fix:**
1. Der 5-%-Median-Notional-Boden aus #1031 wird an der **Quelle** angewandt: ein Round-Trip unterhalb des Bodens ist kein Round-Trip und geht in keinen Zähler ein. Die verworfene Anzahl wird als `dust_round_trips_filtered` telemetriert.
2. Ursachenanalyse in der Round-Trip-Extraktion: ein Netto-Exposure-Nulldurchgang mit einer Restmenge unter der `size_precision`-Auflösung des Instruments ist ein Rundungsartefakt und muss beim Flat-Erkennen auf null geklemmt werden.
3. Neue Invariante `check_dust_round_trip_share` (severity high): Anteil der Dust-Round-Trips je Study ≤ 1 %.

**Akzeptanzkriterien:**
- `check_dust_round_trip_share` FAILt auf diesem Report für Donchian (11,80 %), Rsi2 (9,59 %) und Vwap (5,60 %).
- Nach dem Quellfix ist `EXPECTANCY_NOTIONAL_DEGENERATE` = 0 und `exit_reason_histogram["UNKNOWN"]` = 0 (die Identität aus B-12 löst sich auf).
- `oos_total_trades_with_exit_telemetry` sinkt um die gefilterte Anzahl; `timebox_violating_trades_frac` wird gegen den bereinigten Nenner gerechnet.

**Betroffene Dateien:** `automation/backtest_runner.py`, `automation/optimizer/invariants.py`, `automation/optimizer/report.py`

---

## 4. Pitfalls für AGENTS.md

| # | Pitfall |
|---|---|
| **#369** | Ein Verhältnis-Check braucht **beide** Schranken. Eine einseitige Schwelle macht die nicht geprüfte Richtung zur blinden Flanke — zweite Wiederkehr (#1055 `check_reward_dynamic_range`, #1070 `check_effective_stop_distance`). Wer nur eine Richtung prüft, dokumentiert im Code, warum die andere unmöglich ist. |
| **#370** | Ein Check, der in `fail_fast_invariants` steht, muss die `actual`-Pair-Konvention erfüllen. Offender in `detail` oder `provenance` sind für die Fail-Fast-Mechanik unsichtbar; der Konservativ-Zweig setzt dann die Breitenschwelle ausser Kraft, die genau diesen Fall verhindern sollte (#1063). |
| **#371** | Automatische Suchraum-Weitung ohne Domänenklammer divergiert. Ein Rückschrieb, der bei jedem Lauf um denselben Betrag weitet, erreicht nach k Läufen `lo₀ − k·Δ` — negative Perioden, negative Bar-Anzahlen. Jede Weitung braucht eine Untergrenze, einen Anwendungszähler und eine Zulässigkeitsprüfung an der Konsumstelle (#1066). |
| **#372** | Ein Hard-Cap ist keine Bandgrenze. `MAX_BARS_IN_TRADE_HARD_CAP` schützt nur nach oben; sobald ein Automatismus auch Untergrenzen bewegt, braucht dieselbe Invariante ein `MIN_..._FLOOR` (#1067). |
| **#373** | Eine Invariante, die ihren eigenen Lauf-Output liest, ist selbstreferenziell. `has_prior_reports` über ein Verzeichnis, in das der aktuelle Lauf gerade schreibt, liefert je nach Auswertungszeitpunkt PASS oder FAIL für denselben Messwert (#1064). |
| **#374** | Ein Zähler, der numerisch mit `n_eligible` übereinstimmt, zählt vermutlich die Bestandenen. Bevor eine Gate-Entfernung auf `marginal_delta == 0` gestützt wird, muss der Zähler gegen `is_rejection_detail_counts` kreuzgeprüft sein (#1076). |
| **#375** | Argmin über rohe, dimensionsbehaftete Deltas ist ein Einheiten-Artefakt, keine Attribution. Wer „das bindende Gate" ausweist, normiert vorher — sonst gewinnt immer das Gate mit der kleinsten natürlichen Skala (#1074/#1075). Wiederkehr von #631 in neuer Umgebung. |
| **#376** | Ein Ersatznenner (Epsilon-Floor) darf nie ungekennzeichnet in eine Ergebnistabelle. `excess/max(exposure, 0.01)` erzeugt für die Strategie mit null Trades die grösste Zahl des Berichts (#1077). Fehlender Nenner ⇒ „k. A.", nicht Epsilon. |
| **#377** | Geprunte Trials gehören in keinen Verfügbarkeits-Nenner. Ein Trial, der per Konstruktion keine Statistik tragen kann, erzeugt dort einen garantierten Fehlalarm — hier in einem blockierenden, fail-fast-verdrahteten Check (#1079). |
| **#379** | Wird eine Invariantensuite mehrfach je Lauf ausgewertet, entscheidet die Reihenfolge über das Verdikt, sobald eine Eingabe vom eigenen Lauf abhängt. Vier Auswertungen, zwei verschiedene Ergebnisse, ein Artefakt — genau einmal auswerten und durchreichen (#1083). |
| **#380** | Aus `written_back == 0` folgt nicht „keine Call-Site". Wenn eine Telemetrie existiert, die die Antwort enthält (`CHAMPION_WRITEBACK` mit `skipped_reason`), muss der Check sie konsumieren, statt eine Ursache zu raten — dritte Wiederkehr dieser Klasse (#1052, #1071, #1084). |
| **#381** | Ein Filter, der nur an einer Konsumstelle sitzt, schützt nur diese. Der 5-%-Median-Notional-Boden (#1031) hält die Expectancy sauber, während dieselben Dust-Round-Trips jeden anderen gepoolten Nenner füllen (#1085). Degenerierte Einheiten werden an der Quelle verworfen, nicht je Verbraucher. |
| **#378** | Ein Kostenstress muss die vollen Round-Trip-Kosten stressen, nicht den kleineren Bestandteil. Wird nur die Kommission (1,0 bps) skaliert, während der Spread (3,0 bps) fest bleibt, ist „2×" faktisch „+25 %" — und die darauf gestützte Deployment-Klausel misst nicht, was ihr Name behauptet (#1081). |

---

## 5. Merge-Reihenfolge

```
Stufe 0 — SOFORT, vor jedem weiteren Sweep (Datenschutz für den nächsten Lauf)
  #1066 (Bounds-Klammer + Cache-Migration)  ──►  #1067 (MIN_BARS_FLOOR)  ──►  #1068 (Rückschrieb-Kontext)
  SPERRVERMERK: kein Sweep, solange der #761-Cache negative Untergrenzen trägt.

Stufe 1 — Lauf-Governance (macht Abbruch und Bericht ehrlich)
  #1063 (actual-Konvention)  ──►  #1064 (Ledger-Selbstreferenz)  ──►  #1065 (Vollständigkeit ≠ Gültigkeit)
  #1079 (geprunte Trials aus dem Nenner) parallel — entfernt einen der drei blockierenden FAILs als Fehlalarm.

Stufe 2 — Risikoschicht (der ökonomische Kern; simulation_semantics_version 4 → 5)
  #1070 (zweiseitiger Check, confirm-only, sofort auf diesem Report re-runnable)
     ──►  #1069 (Stop-Realismus: messen, Floor koppeln, Preflight)
     ──►  #1072 (min_stop_to_cost_ratio)
     ──►  #1071 (Mechanismus statt geratener Ursache)
  Erst NACH #1070 mergen: der zweiseitige Check ist die Abnahmemessung für #1069.

Stufe 3 — Entscheidungs-Semantik (keine Simulation, kein Purge)
  #1076 (Gate-Inventar; SPERRVERMERK: keine weitere Gate-Entfernung vorher)
     ──►  #1074 (binding_gate normieren)  ──►  #1075 (holdout_binding_gate)
  #1073 (winsorisierte Expectancy ranken + gaten) parallel
  #1077 (Excess/Exposure) parallel

Stufe 1 (Ergänzung) — Log/Artefakt-Konsistenz
  #1083 (Suite genau einmal auswerten) ZUSAMMEN mit #1064 — derselbe Fix, dieselbe Abnahme.

Stufe 4 — Inferenz und Haushalt
  #1085 (Dust-Round-Trips an der Quelle filtern)  ──►  #1078 (Diagnose-Zähler; die B-12-Identität löst sich damit auf)
  #1080 (n_family-Partition)  ──►  #1081 (Kostenstress)  ──►  #1082 (Branch-Coverage)
  #1084 (Champion-Diagnose) NACH #1064 — der Deadlock-Nachweis braucht das reparierte Ledger.

Stufe 5 — AGENTS.md
  #1023–#1062 nachtragen (40 Issues Rückstand) + Pitfalls #350–#378.
```

**Semantik-Bumps:** `simulation_semantics_version` 4 → 5 **nur** in Stufe 2 (#1069 Punkte 2–4 und #1072 ändern simulierte Fills). Pflicht-Purge (`python -m automation.optimizer.purge_stale_studies`) als **letzte** Aktion vor dem Re-Run. `reward_semantics_version` bleibt bei 23 — kein Issue dieses Katalogs ändert einen gestempelten Reward-Wert oder die Eligibility-Definition. **Kein Doppel-Bump.**

**Weitere Sperrvermerke:**
- Kein Kapitaleinsatz auf einem Kandidaten dieses Laufs. Die drei robusten (Donchian/Rsi2/OpeningRange) tragen DSR 0,157 / 0,361 / 0,073 gegen 0,95 bei 22 / 56 / 42 Holdout-Trades.
- Keine Gate-Entfernung aus `eligible_requires_all`, bevor #1076 gemergt ist.
- Kein SQLite-Purge vor Ende Stufe 2 — die Studies dieses Laufs sind die Referenzkohorte für die Abnahme von #1069/#1070.

---

## 6. Abnahmeprotokoll

1. **Cache-Prüfung (Stufe 0):** `check_search_space_override_admissible` PASST auf dem migrierten #761-Cache; kein Eintrag mit Untergrenze < `min_admissible`. Ein Testlauf mit `--strategies TrendPullback --symbols TSLA.ETORO --n-trials 10` sampelt keine negativen Parameter.
2. **Governance (Stufe 1):** Für alle 50 Check-Namen dieses Laufs gilt `log.status == report.passed`. Die Fail-Fast-Meldung nennt 8 von 14 Studies. Die Zusammenfassung behauptet keine Unvollständigkeit.
3. **Risiko (Stufe 2), confirm-only auf den bestehenden Studies:** `realized_stop_loss_ratio` ist für alle 14 Studies im Report; `check_effective_stop_distance` FAILt mit fünf Offendern; `check_stop_cost_ratio` FAILt mit fünf Offendern.
4. **Risiko (Stufe 2), nach Re-Run:** kein Study mit `atr_median_bps` exakt auf dem Floor; `realized_stop_loss_ratio` ≤ 2,5 in allen Studies; die Verteilung des realisierten Stop-Verlusts korreliert mit der Stopdistanz (Spearman > 0,5 statt −0,18).
5. **Entscheidung (Stufe 3):** `check_gate_inventory_coherence` PASST; kein deaktiviertes Gate erscheint als `binding_gate`; §2.2 ist nach winsorisierter Expectancy sortiert; keine Zeile trägt einen Epsilon-Nenner.
6. **Log/Artefakt (Stufe 1):** `count(INVARIANT_CHECK_FAILED) == count(FAIL in run.json)` je Check-Name; genau eine Auswertungswelle je Lauf.
7. **Haushalt (Stufe 4):** `n_family == Σ n_family_stage1 == 1619`; keine Diagnose-Rate > 1,0; der 2×-Kostenstress bewegt die Expectancy um 4,0 bps; `EXPECTANCY_NOTIONAL_DEGENERATE` = 0 und `exit_reason_histogram["UNKNOWN"]` = 0.

---

## 7. Empfehlungen — Ertrag und Risiko

### E-1 (Risiko, grösster Hebel) — den Stop reparieren, bevor irgendetwas anderes optimiert wird
Der realisierte Verlust bei Stop-Exits liegt bei 61–152 bps und ist von der Stopdistanz unabhängig. Solange das gilt, ist jede Drawdown-, CVaR- und Sortino-Zahl gegen einen Mechanismus kalibriert, der nicht existiert. Reihenfolge: messen (#1070) → Floor an Kosten und Bar-Spanne koppeln (#1069) → Preflight (#1072). Erwarteter Effekt: die fünf Studies mit ATR am Floor verschwinden entweder aus dem Suchraum oder bekommen einen Stop, der hält.

### E-2 (Risiko) — Sizing-Parität herstellen
`check_sizing_parity_backtest_vs_allocator` meldet für **alle 15** Strategien `backtest_trade_amount_pct = 15,0` gegen `allocator_max_symbol_pct = 10,0`. Backtest und Live validieren zwei verschiedene Risikoprozesse; jede Live-Umsetzung realisiert 2/3 des Backtest-Ertrags **und** 2/3 des Risikos. Eine Zahl, in einer Quelle, mit einer Invariante. (Wiederkehr #1042 E-2, dritte Sitzung.)

### E-3 (Ertrag) — nach der robusten Grösse ranken
Der erstgelistete Kandidat des Berichts (+3,6 %) hat eine negative winsorisierte Expectancy. Wer nach `holdout_total_return` sortiert, sortiert nach Ausreissern. Die Umstellung (#1073) kostet nichts und ändert die Rangfolge dieses Laufs sofort: Donchian, Rsi2, OpeningRange vor AdxAtr und Combo.

### E-4 (Ertrag) — die drei robusten Kandidaten gezielt vertiefen, statt breit weiterzusuchen
Donchian (22 Trades, winsorisiert +16,58 bps, DSR 0,157), Rsi2 (56 Trades, +15,92 bps, DSR 0,361), OpeningRange (42 Trades, +10,12 bps, DSR 0,073) sind die einzigen, die Winsorisierung **und** ehrlichen 2×-Kostenstress überstehen. Ihr Problem ist nicht die Erwartung, sondern die **Evidenzmenge**. Ein längeres Holdout-Fenster oder mehr Symbole für diese drei ist ertragreicher als 1940 weitere Trials über 14 Strategien auf einem Symbol.

### E-5 (Risiko) — die Multiplizität ehrlich rechnen
1940 Trials auf **einem** Symbol, `n_family` mit 467 statt 1619 angesetzt. Die Deflationsschwelle ist um ~13 % zu niedrig — zugunsten der Promotion. Bei einem Lauf, der genau eine Zahl als Ergebnis produziert, ist das der falsche Fehler. #1080.

### E-6 (Ertrag) — Suchbudget umschichten
Fünf Studies tragen über 90 % ihres Budgets keine ordnende Qualitätsinformation (`branch == 'per_symbol'` in 2,8–9,4 % der Trials). Squeeze verbrennt 180 Trials für 6 Holdout-Trades und eine degenerierte Selektionsstatistik. Das Budget dieser fünf gehört zu den drei Kandidaten aus E-4. #1082.

### E-7 (Risiko) — Zeitbox durchsetzen, nicht berichten
Acht von 14 Studies halten Einzelpositionen 3,4–22,9× über der Zeitbox (Squeeze 618 h gegen 27 h Deckel). `timebox_violating_trades_frac` verdünnt das auf 0,0001–0,0328 gegen eine Toleranz von 0,25 — die Anteilsschwelle kann nie greifen. Der Magnituden-Ast fängt es ab, aber erst im Bericht. Der Exit-Pfad selbst muss die Box durchsetzen; `penalty_time_box_weight = 0.0` ist zudem der dokumentierte Reaktivierungspunkt, für den jetzt erstmals eine belastbare Exit-Telemetrie vorliegt.

### E-8 (Ertrag) — den Kostenstress ehrlich machen und dann als Filter nutzen
Nach der Korrektur (#1081) kippt ComboTrendVwap bereits beim 2×-Stress ins Negative. Ein Stress, der die vollen Round-Trip-Kosten skaliert, ist der billigste verfügbare Robustheitsfilter — er kostet keinen einzigen Backtest.

### E-9 (Risiko) — den Champion-Closed-Loop über das Ledger reparieren, nicht über die Call-Site
Alle 14 Studies starten von `strategy_defaults` (100 %). Das Log zeigt: die Call-Site existiert und feuert 14×; der Blocker ist `corroboration_count = 1` gegen eine Schwelle ≥ 2, und der Korroborationszähler kann nicht steigen, solange das Coverage-Ledger `total_runs_started = 1` führt. Der Fix liegt damit in #1064, nicht in #706/#818 — ein neuer Writeback-Pfad würde das Problem nicht berühren. Ein Store, der nie zurückschreibt, ist teurer als keiner: er suggeriert Kontinuität, die nicht existiert.

### E-10 (Governance) — AGENTS.md nachziehen
40 Issues (#1023–#1062) und 29 Pitfalls (#350–#378) sind im Code oder im Katalog, aber nicht in AGENTS.md. Zwei der Befunde dieses Katalogs (#1071 Floor-vs-Sprungstelle, #1068 Rückschrieb-Kontext) sind wörtliche Wiederkehren von Befunden aus dem Vorkatalog. Der Rückstand ist selbst eine Fehlerquelle.

---

## 8. Angeforderte Artefakte für die nächste Sitzung

1. **OHLC-Auszug TSLA.ETORO für das Holdout-Fenster:** Anteil Bars mit `high == low`, Median- und p95-Bar-Range in bps. Entscheidet #1069 in einer Zeile.
2. **20 Round-Trips mit Exit-Reason `TRAILING_STOP`** aus einem DynamicBreakout-Trial (ATR am Floor) und einem OpeningRange-Trial (ATR frei), je mit `entry_px`, `exit_px`, `stop_px_at_exit`, `bar_high`, `bar_low`, `holding_ns`. Trennt Root-Cause (1) von (2)/(3).
3. **`data/optimizer/diagnostics/diagnosed_pairs_cache.json`** vor und nach diesem Lauf. Belegt die fünf Weitungs-Anwendungen und die Cache-Historie.
3b. **`data/optimizer/champions/`-Inhalt** mit Zeitstempeln und `corroboration_count` je Eintrag. Entscheidet, ob `STORE_EMPTY` pair-skopiert (falsch benannt) oder ein Persistenzproblem ist (#1084).
4. **`data/optimizer/reports/`-Listing** mit Zeitstempeln. Belegt die Selbstreferenz aus #1064.
5. **Die 6 Ausreisser-Trades von AdxAtrMomentum** (PnL, Notional, Haltedauer, Exit-Reason). Entscheidet, ob der Vorzeichenwechsel aus #1073 ein Datenartefakt oder echte Fat Tails ist.
6. **`symbol_coverage.json`** — steht `total_runs_started = 1` wirklich im Ledger, oder wird der Wert im Report neu abgeleitet?
7. **Antwort auf:** Wurde der #761-Diagnose-Cache seit dem 12.08. manuell angefasst, und wie viele Sweeps sind seit dem letzten `purge_stale_studies` gelaufen?
