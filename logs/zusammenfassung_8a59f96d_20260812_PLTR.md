# Sweep-Zusammenfassung 8a59f96d_20260812_PLTR
<!-- report_sha256: b1f81e9fde934cbf8d4673152e5f323d19ee435256f52e2917b0e4badb670a36 -->


## 1. Ergebnis in einem Satz

14 Studies, 1 Sweep-Promotion(en), **0 deploybar** — kein Kandidat hat sowohl die Holdout-Validierung als auch das Deployment-Gate (``deployment_gate.evaluate_deployment_eligibility``) bestanden. Es gibt kein deploybares Ergebnis aus diesem Lauf. **BLOCKIERENDE Invarianten-FAIL(s):** check_selection_statistic_availability — siehe Abschnitt 5.1 für Details.

## 2. Monetäres Ergebnis

### 2.1 Promotionskandidaten (Status READY_FOR_PR / PROMOTE_GLOBAL_DEFAULT) — noch NICHT deploybar

Diese Kandidaten haben die Holdout-Validierung des Sweeps bestanden. Das ist NICHT dasselbe wie Deploybarkeit — die letzte Spalte zeigt das tatsächliche Urteil von ``deployment_gate.evaluate_deployment_eligibility`` (dieselbe Funktion, die vor jedem Live-Kapitaleinsatz entscheidet).

| Strategie | Symbol | Holdout-Return | Expectancy | Win-Rate | Profit-Faktor | Trades | Deployment-Urteil |
|---|---|---:|---:|---:|---:|---:|---|
| DonchianRegimeBreakoutStrategy | PLTR.ETORO | 4.4 % | 0.0126 | 47.8 % | 3.16 | 23 | abgelehnt (status_ready_for_pr) |

### 2.2 Bester abgelehnter Kandidat je Strategie (NICHT deploybar)

**Diese Kandidaten sind ausdrücklich NICHT deploybar** — der Backtest-Ertrag ist eine Simulationszahl, kein handelbares Ergebnis.

| Strategie | Symbol | Holdout-Return (simuliert) | Ablehnungsgrund |
|---|---|---:|---|
| AdxAtrMomentumStrategy | PLTR.ETORO | 3.0 % | REJECTED_ON_HOLDOUT |
| ComboTrendVwapStrategy | PLTR.ETORO | 0.6 % | REJECTED_SELECTION_OVERFIT |
| DynamicBreakoutStrategy | PLTR.ETORO | -0.3 % | REJECTED_SELECTION_OVERFIT |
| FlashCrashReversalStrategy | PLTR.ETORO | 2.4 % | REJECTED_ON_HOLDOUT |
| HourlyMeanReversionStrategy | PLTR.ETORO | -0.4 % | REJECTED_ON_HOLDOUT |
| MeanReversionStrategy | PLTR.ETORO | -0.4 % | REJECTED_ON_HOLDOUT |
| OpeningRangeBreakoutStrategy | PLTR.ETORO | k. A. | REJECTED_ON_HOLDOUT |
| Rsi2ReversionStrategy | PLTR.ETORO | k. A. | REJECTED_ON_HOLDOUT |
| SmaCrossoverStrategy | PLTR.ETORO | -1.8 % | HOLD_BOUNDARY_UNRESOLVED |
| SqueezeBreakoutStrategy | PLTR.ETORO | k. A. | REJECTED_ON_HOLDOUT |
| TrendPullbackStrategy | PLTR.ETORO | k. A. | REJECTED_ON_HOLDOUT |
| VolatilityBreakoutPumpStrategy | PLTR.ETORO | -1.4 % | REJECTED_ON_HOLDOUT |
| VwapExhaustionStrategy | PLTR.ETORO | -0.0 % | REJECTED_SELECTION_OVERFIT |

### 2.3 Vergleich gegen Buy & Hold je Symbol

| Strategie | Symbol | Strategie-Return | Buy&Hold-Return | Excess | Zeit im Markt | Excess/Exposure | Vorzeichen |
|---|---|---:|---:|---:|---:|---:|---|
| DonchianRegimeBreakoutStrategy | PLTR.ETORO | 4.4 % | 53.6 % | -49.2 % | 22.0 % | -223.9 % | negativ (unter Buy & Hold) |
| AdxAtrMomentumStrategy | PLTR.ETORO | 3.0 % | 53.6 % | -50.6 % | 90.7 % | -55.8 % | negativ (unter Buy & Hold) |
| FlashCrashReversalStrategy | PLTR.ETORO | 2.4 % | 53.6 % | -51.2 % | 10.8 % | -472.2 % | negativ (unter Buy & Hold) |
| ComboTrendVwapStrategy | PLTR.ETORO | 0.6 % | 53.6 % | -53.0 % | 28.5 % | -186.4 % | negativ (unter Buy & Hold) |
| VwapExhaustionStrategy | PLTR.ETORO | -0.0 % | 53.6 % | -53.6 % | 15.2 % | -352.7 % | negativ (unter Buy & Hold) |
| DynamicBreakoutStrategy | PLTR.ETORO | -0.3 % | 53.6 % | -53.9 % | 23.5 % | -229.0 % | negativ (unter Buy & Hold) |
| HourlyMeanReversionStrategy | PLTR.ETORO | -0.4 % | 53.6 % | -54.0 % | 20.3 % | -265.9 % | negativ (unter Buy & Hold) |
| MeanReversionStrategy | PLTR.ETORO | -0.4 % | 53.6 % | -54.0 % | 25.7 % | -210.4 % | negativ (unter Buy & Hold) |
| VolatilityBreakoutPumpStrategy | PLTR.ETORO | -1.4 % | 53.6 % | -55.0 % | 18.8 % | -292.2 % | negativ (unter Buy & Hold) |
| SmaCrossoverStrategy | PLTR.ETORO | -1.8 % | 53.6 % | -55.4 % | 26.6 % | -208.1 % | negativ (unter Buy & Hold) |


### 2.4 Kostenbasis

Alle oben genannten Zahlen sind **simulierte Backtest-Ergebnisse** über das Holdout-Fenster (45 Tage) unter dem im Lauf konfigurierten Kostenmodell (Spread + Kommission je Asset-Klasse, #774/#775) — kein garantiertes zukünftiges Ergebnis.

## 3. Zeitdauer

### 3.1 Gesamtlaufzeit

- Start: k. A.
- Gesamtlaufzeit: k. A.
- n_jobs: k. A. (Quelle: k. A.)
- Lauf-Status: vollständig abgeschlossen

### 3.2 Laufzeit je Symbol/Strategie

| Strategie | Median-Wallclock | p90-Wallclock | n Studies |
|---|---:|---:|---:|
| ComboTrendVwapStrategy | 0.51 h (1839 s) | 0.51 h (1839 s) | 1 |
| AdxAtrMomentumStrategy | 0.37 h (1334 s) | 0.37 h (1334 s) | 1 |
| Rsi2ReversionStrategy | 0.34 h (1241 s) | 0.34 h (1241 s) | 1 |
| FlashCrashReversalStrategy | 0.30 h (1090 s) | 0.30 h (1090 s) | 1 |
| SqueezeBreakoutStrategy | 0.29 h (1037 s) | 0.29 h (1037 s) | 1 |
| TrendPullbackStrategy | 0.26 h (924 s) | 0.26 h (924 s) | 1 |
| MeanReversionStrategy | 0.25 h (910 s) | 0.25 h (910 s) | 1 |
| HourlyMeanReversionStrategy | 0.25 h (905 s) | 0.25 h (905 s) | 1 |
| VwapExhaustionStrategy | 0.22 h (785 s) | 0.22 h (785 s) | 1 |
| DynamicBreakoutStrategy | 0.22 h (783 s) | 0.22 h (783 s) | 1 |
| DonchianRegimeBreakoutStrategy | 0.21 h (773 s) | 0.21 h (773 s) | 1 |
| VolatilityBreakoutPumpStrategy | 0.17 h (604 s) | 0.17 h (604 s) | 1 |
| SmaCrossoverStrategy | 0.17 h (601 s) | 0.17 h (601 s) | 1 |
| OpeningRangeBreakoutStrategy | 0.16 h (576 s) | 0.16 h (576 s) | 1 |

### 3.3 Gelaufene vs. budgetierte Trials

- Median Budgetausführung: 0.0 % (p10: 0.0 %, n=14 Studies)
- Trials gesamt: 0 von 1940 budgetiert

### 3.4 Verlorene Zeit: abgebrochene Studies

- EXCEPTION: 11 Studies
- STRUCTURAL_ZERO_ELIGIBLE: 3 Studies

Barriere-Wartezeit (Symbol-Wallclock minus schnellste Study) — die 1 Symbole mit der längsten Wartezeit:
- PLTR.ETORO: 0.35 h (1263 s)

## 4. Trades mit der längsten Haltedauer

**Scope-Hinweis:** diese Sektion listet die längste beobachtete Haltedauer JE STUDY (Strategie/Symbol, Maximum über alle OOS-evaluierten Trials), NICHT einzelne Trades mit Entry-/Exit-Zeitstempel — siehe Modul-Docstring für die Begründung dieser Scope-Entscheidung (Katalog #832 Fix Punkt 1).

| Strategie | Symbol | Max. Haltedauer | P95 Haltedauer |
|---|---|---:|---:|
| Rsi2ReversionStrategy | PLTR.ETORO | 456.00 h (1641600 s) | 48.00 h (172800 s) |
| TrendPullbackStrategy | PLTR.ETORO | 456.00 h (1641600 s) | 48.00 h (172800 s) |
| VwapExhaustionStrategy | PLTR.ETORO | 168.00 h (604800 s) | 6.00 h (21600 s) |
| ComboTrendVwapStrategy | PLTR.ETORO | 39.00 h (140400 s) | 12.00 h (43200 s) |
| MeanReversionStrategy | PLTR.ETORO | 36.00 h (129600 s) | 24.00 h (86400 s) |
| HourlyMeanReversionStrategy | PLTR.ETORO | 35.00 h (126000 s) | 21.00 h (75600 s) |
| FlashCrashReversalStrategy | PLTR.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| DonchianRegimeBreakoutStrategy | PLTR.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| VolatilityBreakoutPumpStrategy | PLTR.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| SmaCrossoverStrategy | PLTR.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |

## 5. Auffälligkeiten

### 5.1 Übersicht — Invarianten-FAILs (43)

| Check | FAILs | betroffene Studies | Schweregrad |
|---|---:|---:|---|
| check_selection_statistic_availability | 1 | 1 | blocking |
| check_annualization_commensurability | 12 | 12 | high |
| check_counter_partition_consistency | 1 | 1 | high |
| check_effective_stop_distance | 1 | 1 | high |
| check_n_periods_homogeneity | 1 | 1 | high |
| check_promotion_deployment_coherence | 1 | 1 | high |
| check_search_made_progress | 1 | 1 | high |
| check_reward_term_variance | 14 | 14 | medium |
| check_objective_branch_coverage | 6 | 6 | medium |
| check_budget_execution | 1 | 1 | medium |
| check_gate_marginal_contribution | 1 | 1 | medium |
| check_inference_diagnostics_concentration | 1 | 1 | medium |
| check_window_unreachable_rate | 1 | 1 | medium |
| check_champion_seed_coverage | 1 | 1 | low |

### 5.2 Details

**check_selection_statistic_availability**

- (scope=global): 1 Study/Studies unter der Mindestverfügbarkeit (0.8) einer definierten Selektions-Teststatistik: {'SqueezeBreakoutStrategy/PLTR.ETORO': 0.0069} — die Eligibility-Auswertung dieser Studies ist strukturell informationsfrei (Issue #913/#915), keine Aussage über die Strategien.

**check_annualization_commensurability**

- (scope=VwapExhaustionStrategy/PLTR.ETORO): Trial-Index 55: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 2.052 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=FlashCrashReversalStrategy/PLTR.ETORO): Trial-Index 15: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 2.583 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=DonchianRegimeBreakoutStrategy/PLTR.ETORO): Trial-Index 13: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.868 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=HourlyMeanReversionStrategy/PLTR.ETORO): Trial-Index 59: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.655 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=MeanReversionStrategy/PLTR.ETORO): Trial-Index 17: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.573 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- … und 7 weitere

**check_counter_partition_consistency**

- (scope=global): 1 Study/Studies: der Plateau-Zähler und n_trials zerlegen die Trial-Menge NICHT disjunkt/vollständig: {'Rsi2ReversionStrategy/PLTR.ETORO': {'n_trials': 142, 'n_evaluated': 116, 'breakdown_sum': 25, 'reconstructed_total': 141}} — mindestens einer der beteiligten Zähler zielt nicht auf dieselbe Grundgesamtheit (Pitfall #304).

**check_effective_stop_distance**

- (scope=global): 1 Study/Studies unterschreiten das Verhältnis Ø-Bruttoverlust / konfigurierter Stop-Abstand (0.4): {'FlashCrashReversalStrategy/PLTR.ETORO': 0.3171} — der Stop reagiert nicht auf seinen eigenen Multiplikator (Pitfall #286) und rastet vermutlich auf der ATR-Schätzung statt auf dem Preis-Extremum (Pitfall #285, Issue #897).

**check_n_periods_homogeneity**

- (scope=global): 1 Symbol(e) mit n_periods-Spannweite > 6.0: {'PLTR.ETORO': 135.58} — die #865-Heterogenitäts-Suppression (deflation_max_n_periods_ratio) greift vermutlich für praktisch jede Familie dieses Symbols (Issue #923).

**check_promotion_deployment_coherence**

- (scope=DonchianRegimeBreakoutStrategy/PLTR.ETORO): Promotion (status='PROMOTE_GLOBAL_DEFAULT') besteht die Deployment-Grenze nicht (blocking_clause='status_ready_for_pr') — der Kandidat ist ein Sweep-Gewinner, aber laut deployment_gate.evaluate_deployment_eligibility NICHT deploybar (#1006).

**check_search_made_progress**

- (scope=global): 3 Study/Studies mit stagnierender/wachsender Constraint-Verletzung bei 0 eligiblen Trials nach ausreichend modellierten Trials: {'OpeningRangeBreakoutStrategy/PLTR.ETORO': -0.190018, 'Rsi2ReversionStrategy/PLTR.ETORO': -0.038351, 'TrendPullbackStrategy/PLTR.ETORO': -0.180602} — der TPE-Sampler hat nachweislich keinen Gradienten gefunden.

**check_reward_term_variance**

- (scope=VwapExhaustionStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=2.106058): ['param_pen', 'turnover'].
- (scope=FlashCrashReversalStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=0.671460): ['param_pen', 'turnover', 'fold_dispersion'].
- (scope=DonchianRegimeBreakoutStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=1.978643): ['param_pen', 'turnover', 'fold_dispersion'].
- (scope=OpeningRangeBreakoutStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=1.025417): ['param_pen', 'turnover', 'fold_dispersion'].
- (scope=HourlyMeanReversionStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=2.194736): ['param_pen', 'turnover'].
- … und 9 weitere

**check_objective_branch_coverage**

- (scope=FlashCrashReversalStrategy/PLTR.ETORO): Nur 13/160 Trials (8.12%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=DonchianRegimeBreakoutStrategy/PLTR.ETORO): Nur 0/120 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=OpeningRangeBreakoutStrategy/PLTR.ETORO): Nur 0/106 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=Rsi2ReversionStrategy/PLTR.ETORO): Nur 0/141 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=TrendPullbackStrategy/PLTR.ETORO): Nur 0/123 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- … und 1 weitere

**check_budget_execution**

- (scope=global): median(budget_executed_fraction)=0.0000 < 0.5 ueber 14 Studies — ein grosser Teil des konfigurierten Suchbudgets wird nicht ausgefuehrt (#768/#769-Fehlerklasse).

**check_gate_marginal_contribution**

- (scope=global): 2 Gate(s) ohne jeden marginalen Beitrag über eine ausreichend grosse Kohorte: {'min_trades': {'marginal_delta': 0, 'n_evaluated': 1783}, 'max_drawdown': {'marginal_delta': 0, 'n_evaluated': 1783}} — Kandidat(en) für Entfernung aus eligible_requires_all oder Neukalibrierung gegen die realisierte Verteilung.

**check_inference_diagnostics_concentration**

- (scope=SqueezeBreakoutStrategy/PLTR.ETORO): 144/1 informative Trials (14400.0%) wurden vom Inferenz-Wächter zensiert (SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE, kein regulärer Ausgang) — die Suche ist faktisch zensiert (analog STUDY_GUARD_DOMINATED, #823).

**check_window_unreachable_rate**

- (scope=global): 1 Study/Studies mit überproportional vielen unerreichbaren OOS-Fenstern: {'TrendPullbackStrategy/PLTR.ETORO': 0.065} — zu weite Lookback-Bounds für die Datenlage dieses Symbols (spaces.py gegen data_window_days deckeln, #976).

**check_champion_seed_coverage**

- (scope=global): strategy_defaults-Anteil=100.0% > 90% — der Champion-Store-Closed-Loop (#702) ist fuer diesen Lauf nachweislich unwirksam ({'strategy_defaults': 14}).

### 5.3 Zusammenfassung

- Guard-dominierte Studies (SORTINO_GUARD_TRIPPED-Mehrheit, #823): 0
- Wirtschaftlich ruinierte Trials (EQUITY_NONPOSITIVE, #801/#825): 0
- Randlösungen mit Bounds-Vorschlag (#831): 9
- Automatisch denylistete Paare (#829/#830): 3
- Budget-deprioritisierte Paare (#830): 0
