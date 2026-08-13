# Sweep-Zusammenfassung 658b8a2f_20260812_TSLA
<!-- report_sha256: 8e8bb4db339e21118fc43b77e9446d73b4d1ea4843ccab862ef16f0765670a1a -->


## 1. Ergebnis in einem Satz

14 Studies, 2 Sweep-Promotion(en), **0 deploybar** — kein Kandidat hat sowohl die Holdout-Validierung als auch das Deployment-Gate (``deployment_gate.evaluate_deployment_eligibility``) bestanden. Es gibt kein deploybares Ergebnis aus diesem Lauf. **BLOCKIERENDE Invarianten-FAIL(s):** check_censored_statistic_in_decision (2 Study/Studies), check_guard_reference_stability, check_holding_time_cap, check_selection_statistic_availability, check_sizing_identity_coherence — siehe Abschnitt 5.1 für Details.

## 2. Monetäres Ergebnis

### 2.1 Promotionskandidaten (Status READY_FOR_PR / PROMOTE_GLOBAL_DEFAULT) — noch NICHT deploybar

**Kein deploybares Ergebnis aus diesem Lauf.** Kein Kandidat hat die Holdout-Validierung bestanden — alle folgenden Zahlen in Abschnitt 2.2 sind ABGELEHNTE, NICHT handelbare Kandidaten.

### 2.1b Quarantäne — Datenintegrität

Diese Kandidaten hätten die Holdout-Validierung des Sweeps bestanden, tragen aber einen nachgewiesenen Bruch zwischen dem promoteten Datenstand und dem aktuellen Katalog-Snapshot (``deployment_decision.clause_results['snapshot_drift'] = false``, #993). Ihre Kennzahlen sind NICHT belastbar — sie werden hier ausschliesslich zur Nachvollziehbarkeit gelistet, nie als Promotions- oder Ablehnungskandidat.

| Strategie | Symbol | Promotion-Ausgang | Holdout-Return (nicht belastbar) |
|---|---|---|---:|
| FlashCrashReversalStrategy | TSLA.ETORO | READY_FOR_PR | 115.0 % |
| VwapExhaustionStrategy | TSLA.ETORO | READY_FOR_PR | 125.9 % |

### 2.2 Bester abgelehnter Kandidat je Strategie (NICHT deploybar)

**Diese Kandidaten sind ausdrücklich NICHT deploybar** — der Backtest-Ertrag ist eine Simulationszahl, kein handelbares Ergebnis.

| Strategie | Symbol | Holdout-Return (simuliert) | Ablehnungsgrund |
|---|---|---:|---|
| AdxAtrMomentumStrategy | TSLA.ETORO | 0.0 % | REJECTED_ON_HOLDOUT |
| ComboTrendVwapStrategy | TSLA.ETORO | 87.5 % | REJECTED_ON_HOLDOUT |
| DonchianRegimeBreakoutStrategy | TSLA.ETORO | 0.0 % | REJECTED_ON_HOLDOUT |
| DynamicBreakoutStrategy | TSLA.ETORO | -42.7 % | REJECTED_ON_HOLDOUT |
| HourlyMeanReversionStrategy | TSLA.ETORO | -1.1 % | REJECTED_BOUNDARY_SOLUTION |
| MeanReversionStrategy | TSLA.ETORO | 1.0 % | REJECTED_ON_HOLDOUT |
| OpeningRangeBreakoutStrategy | TSLA.ETORO | 0.0 % | REJECTED_ON_HOLDOUT |
| Rsi2ReversionStrategy | TSLA.ETORO | 0.0 % | REJECTED_ON_HOLDOUT |
| SmaCrossoverStrategy | TSLA.ETORO | 0.0 % | REJECTED_ON_HOLDOUT |
| SqueezeBreakoutStrategy | TSLA.ETORO | 0.2 % | REJECTED_ON_HOLDOUT |
| TrendPullbackStrategy | TSLA.ETORO | 0.0 % | REJECTED_ON_HOLDOUT |
| VolatilityBreakoutPumpStrategy | TSLA.ETORO | 0.0 % | REJECTED_ON_HOLDOUT |

### 2.3 Vergleich gegen Buy & Hold je Symbol

| Strategie | Symbol | Strategie-Return | Buy&Hold-Return | Excess | Zeit im Markt | Excess/Exposure | Vorzeichen |
|---|---|---:|---:|---:|---:|---:|---|
| VwapExhaustionStrategy | TSLA.ETORO | 125.9 % | -42.7 % | 168.6 % | 63.1 % | 267.4 % | B&H negativ — Excess trivial positiv |
| FlashCrashReversalStrategy | TSLA.ETORO | 115.0 % | -42.7 % | 157.8 % | 75.4 % | 209.2 % | B&H negativ — Excess trivial positiv |
| ComboTrendVwapStrategy | TSLA.ETORO | 87.5 % | -42.7 % | 130.2 % | 22.4 % | 580.6 % | B&H negativ — Excess trivial positiv |
| MeanReversionStrategy | TSLA.ETORO | 1.0 % | -42.7 % | 43.8 % | 22.1 % | 198.1 % | B&H negativ — Excess trivial positiv |
| SqueezeBreakoutStrategy | TSLA.ETORO | 0.2 % | -42.7 % | 42.9 % | 1.9 % | 2203.9 % | B&H negativ — Excess trivial positiv |
| OpeningRangeBreakoutStrategy | TSLA.ETORO | 0.0 % | -42.7 % | 42.7 % | k. A. | 4274.3 % | B&H negativ — Excess trivial positiv |
| Rsi2ReversionStrategy | TSLA.ETORO | 0.0 % | -42.7 % | 42.7 % | k. A. | 4274.3 % | B&H negativ — Excess trivial positiv |
| SmaCrossoverStrategy | TSLA.ETORO | 0.0 % | -42.7 % | 42.7 % | k. A. | 4274.3 % | B&H negativ — Excess trivial positiv |
| DonchianRegimeBreakoutStrategy | TSLA.ETORO | 0.0 % | -42.7 % | 42.7 % | k. A. | 4274.3 % | B&H negativ — Excess trivial positiv |
| TrendPullbackStrategy | TSLA.ETORO | 0.0 % | -42.7 % | 42.7 % | k. A. | 4274.3 % | B&H negativ — Excess trivial positiv |
| AdxAtrMomentumStrategy | TSLA.ETORO | 0.0 % | -42.7 % | 42.7 % | k. A. | 4274.3 % | B&H negativ — Excess trivial positiv |
| VolatilityBreakoutPumpStrategy | TSLA.ETORO | 0.0 % | -42.7 % | 42.7 % | k. A. | 4274.3 % | B&H negativ — Excess trivial positiv |
| HourlyMeanReversionStrategy | TSLA.ETORO | -1.1 % | -42.7 % | 41.6 % | 8.0 % | 521.5 % | B&H negativ — Excess trivial positiv |
| DynamicBreakoutStrategy | TSLA.ETORO | -42.7 % | -42.7 % | -0.0 % | 16.4 % | -0.0 % | B&H negativ — Excess trivial positiv |


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
| ComboTrendVwapStrategy | 0.61 h (2213 s) | 0.61 h (2213 s) | 1 |
| FlashCrashReversalStrategy | 0.44 h (1591 s) | 0.44 h (1591 s) | 1 |
| SqueezeBreakoutStrategy | 0.41 h (1476 s) | 0.41 h (1476 s) | 1 |
| HourlyMeanReversionStrategy | 0.37 h (1344 s) | 0.37 h (1344 s) | 1 |
| MeanReversionStrategy | 0.37 h (1338 s) | 0.37 h (1338 s) | 1 |
| Rsi2ReversionStrategy | 0.37 h (1333 s) | 0.37 h (1333 s) | 1 |
| VwapExhaustionStrategy | 0.35 h (1268 s) | 0.35 h (1268 s) | 1 |
| OpeningRangeBreakoutStrategy | 0.35 h (1260 s) | 0.35 h (1260 s) | 1 |
| TrendPullbackStrategy | 0.34 h (1231 s) | 0.34 h (1231 s) | 1 |
| AdxAtrMomentumStrategy | 0.33 h (1181 s) | 0.33 h (1181 s) | 1 |
| DonchianRegimeBreakoutStrategy | 0.28 h (1001 s) | 0.28 h (1001 s) | 1 |
| DynamicBreakoutStrategy | 0.27 h (967 s) | 0.27 h (967 s) | 1 |
| SmaCrossoverStrategy | 0.26 h (927 s) | 0.26 h (927 s) | 1 |
| VolatilityBreakoutPumpStrategy | 0.23 h (834 s) | 0.23 h (834 s) | 1 |

### 3.3 Gelaufene vs. budgetierte Trials

- Median Budgetausführung: 0.0 % (p10: 0.0 %, n=14 Studies)
- Trials gesamt: 0 von 1940 budgetiert

### 3.4 Verlorene Zeit: abgebrochene Studies

- STRUCTURAL_ZERO_ELIGIBLE: 8 Studies
- STRUCTURAL_ALL_UNEVALUABLE: 4 Studies
- UNKNOWN_INCOMPLETE: 2 Studies

Barriere-Wartezeit (Symbol-Wallclock minus schnellste Study) — die 1 Symbole mit der längsten Wartezeit:
- TSLA.ETORO: 0.38 h (1379 s)

## 4. Trades mit der längsten Haltedauer

**Scope-Hinweis:** diese Sektion listet die längste beobachtete Haltedauer JE STUDY (Strategie/Symbol, Maximum über alle OOS-evaluierten Trials), NICHT einzelne Trades mit Entry-/Exit-Zeitstempel — siehe Modul-Docstring für die Begründung dieser Scope-Entscheidung (Katalog #832 Fix Punkt 1).

| Strategie | Symbol | Max. Haltedauer | P95 Haltedauer |
|---|---|---:|---:|
| SqueezeBreakoutStrategy | TSLA.ETORO | 3991.00 h (14367600 s) | 3991.00 h (14367600 s) |
| FlashCrashReversalStrategy | TSLA.ETORO | 412.00 h (1483200 s) | 48.00 h (172800 s) |
| OpeningRangeBreakoutStrategy | TSLA.ETORO | 333.45 h (1200426 s) | 28.45 h (102426 s) |
| HourlyMeanReversionStrategy | TSLA.ETORO | 137.00 h (493200 s) | 4.00 h (14400 s) |
| MeanReversionStrategy | TSLA.ETORO | 132.00 h (475200 s) | 3.00 h (10800 s) |
| VwapExhaustionStrategy | TSLA.ETORO | 88.00 h (316800 s) | 8.00 h (28800 s) |
| ComboTrendVwapStrategy | TSLA.ETORO | 86.00 h (309600 s) | 19.00 h (68400 s) |
| DynamicBreakoutStrategy | TSLA.ETORO | 61.00 h (219600 s) | 18.00 h (64800 s) |
| SmaCrossoverStrategy | TSLA.ETORO | 24.00 h (86400 s) | 13.00 h (46800 s) |
| TrendPullbackStrategy | TSLA.ETORO | 23.00 h (82800 s) | 22.00 h (79200 s) |

## 5. Auffälligkeiten

### 5.1 Übersicht — Invarianten-FAILs (71)

| Check | FAILs | betroffene Studies | Schweregrad |
|---|---:|---:|---|
| check_censored_statistic_in_decision | 2 | 2 | blocking |
| check_guard_reference_stability | 1 | 1 | blocking |
| check_holding_time_cap | 1 | 1 | blocking |
| check_selection_statistic_availability | 1 | 1 | blocking |
| check_sizing_identity_coherence | 1 | 1 | blocking |
| check_annualization_commensurability | 9 | 9 | high |
| check_selection_statistic_economic_bias | 3 | 3 | high |
| check_promotion_deployment_coherence | 2 | 2 | high |
| check_reward_dynamic_range | 2 | 2 | high |
| check_atr_scale_homogeneity | 1 | 1 | high |
| check_counter_partition_consistency | 1 | 1 | high |
| check_n_periods_homogeneity | 1 | 1 | high |
| check_search_made_progress | 1 | 1 | high |
| check_symbol_coverage | 1 | 1 | high |
| check_reward_term_variance | 10 | 10 | medium |
| check_inference_diagnostics_concentration | 9 | 9 | medium |
| check_objective_branch_coverage | 9 | 9 | medium |
| check_inference_diagnostics_absent | 7 | 7 | medium |
| check_adaptive_diagnostic_rate | 6 | 6 | medium |
| check_budget_execution | 1 | 1 | medium |
| check_gate_marginal_contribution | 1 | 1 | medium |
| check_window_unreachable_rate | 1 | 1 | medium |

### 5.2 Details

**check_censored_statistic_in_decision**

- (scope=FlashCrashReversalStrategy/TSLA.ETORO): Promotion (status='READY_FOR_PR') beruht auf zensierter/gecappter Kennzahl: oos_profit_factor_censored — der wahre Wert ist unbekannt (#1004).
- (scope=VwapExhaustionStrategy/TSLA.ETORO): Promotion (status='READY_FOR_PR') beruht auf zensierter/gecappter Kennzahl: oos_profit_factor_censored — der wahre Wert ist unbekannt (#1004).

**check_guard_reference_stability**

- (scope=global): 5 Study/Studies mit einer wandernden Guard-Referenz: {'HourlyMeanReversionStrategy/TSLA.ETORO': {'guard_reference_values': [291.5, 293.0, 298.0, 317.5, 320.0, 332.0, 333.0, 338.5, 342.0, 343.0, 349.0, 351.5, 362.0, 364.0, 370.0, 385.0, 401.0, 434.0, 436.5, 439.0, 442.0, 453.0, 458.0, 471.0, 473.0], 'guard_reference_sources': ['absolute_bootstrap', 'family_median']}, 'MeanReversionStrategy/TSLA.ETORO': {'guard_reference_values': [261.0, 261.5, 262.0, 264.0, 266.0, 275.0, 279.5, 284.0, 285.0, 286.0, 288.0, 291.0, 292.0, 297.0, 302.0, 303.0, 304.0, 313.0, 320.0], 'guard_reference_sources': ['absolute_bootstrap', 'family_median']}, 'FlashCrashReversalStrategy/TSLA.ETORO': {'guard_reference_values': [320.0, 793.0, 901.5, 982.0, 1010.0, 1014.0, 1018.0, 1024.5, 1031.0, 1046.0, 1059.5, 1073.0, 1080.5, 1088.0, 1093.0, 1098.0, 1108.0, 1118.0, 1191.5, 1265.0, 1268.0, 1271.0, 1289.5, 1308.0, 1313.5, 1319.0, 1328.5], 'guard_reference_sources': ['absolute_bootstrap', 'family_median']}, 'ComboTrendVwapStrategy/TSLA.ETORO': {'guard_reference_values': [502.0, 511.0], 'guard_reference_sources': ['family_median']}, 'VwapExhaustionStrategy/TSLA.ETORO': {'guard_reference_values': [320.0, 934.5, 971.0, 1033.0, 1089.0, 1095.0, 1109.5, 1124.0, 1151.0, 1178.0, 1189.0, 1200.0, 1244.5, 1289.0, 1294.0, 1299.0, 1318.0, 1325.5, 1333.0, 1360.5, 1377.0, 1388.0, 1395.0, 1402.0, 1402.5, 1409.5, 1417.0, 1418.0, 1419.0, 1461.5, 1462.0, 1467.0, 1472.0, 1474.0, 1476.0, 1477.0], 'guard_reference_sources': ['absolute_bootstrap', 'family_median']}} — der Guard zensiert nach Ankunftsreihenfolge/Scheduler-Timing statt nach statistischer Implausibilität; der Lauf ist trotz festem Seed nicht bitweise reproduzierbar (Pitfall #307).

**check_holding_time_cap**

- (scope=global): Magnituden-Ast (#1036): 7 Study/Studies mit einer Einzelposition > 3.0x der Zeitbox (291600s): {'OpeningRangeBreakoutStrategy/TSLA.ETORO': 12.35, 'HourlyMeanReversionStrategy/TSLA.ETORO': 5.07, 'MeanReversionStrategy/TSLA.ETORO': 4.89, 'FlashCrashReversalStrategy/TSLA.ETORO': 15.26, 'ComboTrendVwapStrategy/TSLA.ETORO': 3.19, 'VwapExhaustionStrategy/TSLA.ETORO': 3.26, 'SqueezeBreakoutStrategy/TSLA.ETORO': 147.81} (Vielfaches der Zeitbox je Study) — ökonomisch untragbar unabhängig vom gepoolten Anteil (Pitfall #358).

**check_selection_statistic_availability**

- (scope=global): 5 Study/Studies unter der Mindestverfügbarkeit (0.8) einer definierten Selektions-Teststatistik: {'HourlyMeanReversionStrategy/TSLA.ETORO': 0.6667, 'MeanReversionStrategy/TSLA.ETORO': 0.7141, 'FlashCrashReversalStrategy/TSLA.ETORO': 0.5554, 'VwapExhaustionStrategy/TSLA.ETORO': 0.4824, 'SqueezeBreakoutStrategy/TSLA.ETORO': 0.7801} — die Eligibility-Auswertung dieser Studies ist strukturell informationsfrei (Issue #913/#915), keine Aussage über die Strategien.

**check_sizing_identity_coherence**

- (scope=global): 6 Study/Studies: der aus (total_return, expectancy, n) implizierte Sizing-Anteil weicht relativ um mehr als 0.35 vom konfigurierten trade_amount_pct ab: {'HourlyMeanReversionStrategy/TSLA.ETORO': {'f_implied_pct': 3.3992, 'trade_amount_pct': 15.0}, 'MeanReversionStrategy/TSLA.ETORO': {'f_implied_pct': 3.1435, 'trade_amount_pct': 15.0}, 'FlashCrashReversalStrategy/TSLA.ETORO': {'f_implied_pct': 1.129, 'trade_amount_pct': 15.0}, 'ComboTrendVwapStrategy/TSLA.ETORO': {'f_implied_pct': 7.6385, 'trade_amount_pct': 15.0}, 'DynamicBreakoutStrategy/TSLA.ETORO': {'f_implied_pct': 61.7053, 'trade_amount_pct': 15.0}, 'VwapExhaustionStrategy/TSLA.ETORO': {'f_implied_pct': 1.1447, 'trade_amount_pct': 15.0}} — Signatur einer Datenanomalie in der zugrundeliegenden Preisreihe, nicht des Sizing-Pfads (#1028).

**check_annualization_commensurability**

- (scope=OpeningRangeBreakoutStrategy/TSLA.ETORO): Trial-Index 706: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 4.701 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=HourlyMeanReversionStrategy/TSLA.ETORO): Trial-Index 533: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.566 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=MeanReversionStrategy/TSLA.ETORO): Trial-Index 220: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.671 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=FlashCrashReversalStrategy/TSLA.ETORO): Trial-Index 21: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.807 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=ComboTrendVwapStrategy/TSLA.ETORO): Trial-Index 1050: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 4.228 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- … und 4 weitere

**check_selection_statistic_economic_bias**

- (scope=ComboTrendVwapStrategy/TSLA.ETORO): Median-OOS-Return der Kohorte OHNE Selektionsstatistik (0.2881, n=53) ist signifikant GRÖSSER als der der Kohorte MIT Statistik (-0.2645, n=1205) — z=9.2618, p=0 < 0.01. Die fehlende Statistik ist nicht MCAR: die Selektion verwirft bevorzugt die profitable Kohorte aus einem numerischen, nicht ökonomischen Grund (Pitfall #306).
- (scope=SmaCrossoverStrategy/TSLA.ETORO): Median-OOS-Return der Kohorte OHNE Selektionsstatistik (-0.0318, n=6) ist signifikant GRÖSSER als der der Kohorte MIT Statistik (-0.4907, n=148) — z=4.0710, p=2.34e-05 < 0.01. Die fehlende Statistik ist nicht MCAR: die Selektion verwirft bevorzugt die profitable Kohorte aus einem numerischen, nicht ökonomischen Grund (Pitfall #306).
- (scope=DynamicBreakoutStrategy/TSLA.ETORO): Median-OOS-Return der Kohorte OHNE Selektionsstatistik (-0.0436, n=11) ist signifikant GRÖSSER als der der Kohorte MIT Statistik (-0.4548, n=388) — z=5.5464, p=1.46e-08 < 0.01. Die fehlende Statistik ist nicht MCAR: die Selektion verwirft bevorzugt die profitable Kohorte aus einem numerischen, nicht ökonomischen Grund (Pitfall #306).

**check_promotion_deployment_coherence**

- (scope=FlashCrashReversalStrategy/TSLA.ETORO): Promotion (status='READY_FOR_PR') besteht die Deployment-Grenze nicht (blocking_clause='snapshot_drift') — der Kandidat ist ein Sweep-Gewinner, aber laut deployment_gate.evaluate_deployment_eligibility NICHT deploybar (#1006).
- (scope=VwapExhaustionStrategy/TSLA.ETORO): Promotion (status='READY_FOR_PR') besteht die Deployment-Grenze nicht (blocking_clause='snapshot_drift') — der Kandidat ist ein Sweep-Gewinner, aber laut deployment_gate.evaluate_deployment_eligibility NICHT deploybar (#1006).

**check_reward_dynamic_range**

- (scope=HourlyMeanReversionStrategy/TSLA.ETORO): reward_std_feasible=2.8458 < 4.0 * max_term_std=1.3718
- (scope=MeanReversionStrategy/TSLA.ETORO): reward_std_feasible=2.6205 < 4.0 * max_term_std=1.5357

**check_atr_scale_homogeneity**

- (scope=global): 1 Symbol(e) mit einer ATR-Spannweite über 6.0x zwischen Strategien: {'TSLA.ETORO': 19.95} — Signatur einer Sprungstelle in der Preisreihe (#1028).

**check_counter_partition_consistency**

- (scope=global): 8 Study/Studies: der Plateau-Zähler und n_trials zerlegen die Trial-Menge NICHT disjunkt/vollständig: {'OpeningRangeBreakoutStrategy/TSLA.ETORO': {'n_trials': 838, 'n_evaluated': 10, 'breakdown_sum': 94, 'reconstructed_total': 104}, 'MeanReversionStrategy/TSLA.ETORO': {'n_trials': 786, 'n_evaluated': 92, 'breakdown_sum': 12, 'reconstructed_total': 104}, 'FlashCrashReversalStrategy/TSLA.ETORO': {'n_trials': 896, 'n_evaluated': 126, 'breakdown_sum': 12, 'reconstructed_total': 138}, 'ComboTrendVwapStrategy/TSLA.ETORO': {'n_trials': 1938, 'n_evaluated': 105, 'breakdown_sum': 140, 'reconstructed_total': 245}, 'SmaCrossoverStrategy/TSLA.ETORO': {'n_trials': 617, 'n_evaluated': 36, 'breakdown_sum': 64, 'reconstructed_total': 100}, 'DynamicBreakoutStrategy/TSLA.ETORO': {'n_trials': 615, 'n_evaluated': 46, 'breakdown_sum': 52, 'reconstructed_total': 98}, 'TrendPullbackStrategy/TSLA.ETORO': {'n_trials': 974, 'n_evaluated': 3, 'breakdown_sum': 118, 'reconstructed_total': 121}, 'SqueezeBreakoutStrategy/TSLA.ETORO': {'n_trials': 1169, 'n_evaluated': 108, 'breakdown_sum': 48, 'reconstructed_total': 156}} — mindestens einer der beteiligten Zähler zielt nicht auf dieselbe Grundgesamtheit (Pitfall #304).

**check_n_periods_homogeneity**

- (scope=global): 1 Symbol(e) mit n_periods-Spannweite > 6.0: {'TSLA.ETORO': 23.12} — die #865-Heterogenitäts-Suppression (deflation_max_n_periods_ratio) greift vermutlich für praktisch jede Familie dieses Symbols (Issue #923).

**check_search_made_progress**

- (scope=global): 3 Study/Studies mit stagnierender/wachsender Constraint-Verletzung bei 0 eligiblen Trials nach ausreichend modellierten Trials: {'OpeningRangeBreakoutStrategy/TSLA.ETORO': 0.0, 'SmaCrossoverStrategy/TSLA.ETORO': -0.12364, 'DynamicBreakoutStrategy/TSLA.ETORO': -0.164104} — der TPE-Sampler hat nachweislich keinen Gradienten gefunden. 5 weitere Study/Studies waren nach der FAIL-Bedingung auffällig, aber ihre Eingabe ist zu grob quantisiert (< 10 verschiedene Werte) für eine belastbare Aussage — als INCONCLUSIVE statt FAIL gezählt: ['Rsi2ReversionStrategy/TSLA.ETORO', 'DonchianRegimeBreakoutStrategy/TSLA.ETORO', 'TrendPullbackStrategy/TSLA.ETORO', 'AdxAtrMomentumStrategy/TSLA.ETORO', 'VolatilityBreakoutPumpStrategy/TSLA.ETORO'].

**check_symbol_coverage**

- (scope=global): 144 Symbol(e) seit mehr als 3 Läufen nicht ERNEUT abgedeckt (stale, least_recently_covered-Rotation sollte das verhindern): {'XOM.ETORO': 5, 'NKE.ETORO': 5, 'NFLX.ETORO': 5, 'VLO.ETORO': 5, 'LLY.ETORO': 5, 'CHTR.ETORO': 5, 'AMAT.ETORO': 4, 'FTI.ETORO': 5, 'AMD.ETORO': 4, 'FISV.ETORO': 5, 'Z.ETORO': 5, 'LRCX.ETORO': 5, 'INTU.ETORO': 5, 'ATI.ETORO': 4, 'GSAT.ETORO': 5, 'ENLT.ETORO': 5, 'TEVA.ETORO': 5, 'MBLY.ETORO': 5, 'PSN.US.ETORO': 5, 'TTD.ETORO': 5, 'ASX.ETORO': 4, 'PODD.ETORO': 5, 'CPRT.ETORO': 5, 'ZTS.ETORO': 5, 'MSTR.ETORO': 5, 'LULU.ETORO': 5, 'ASML.ETORO': 4, 'RVMD.ETORO': 5, 'COIN.ETORO': 5, 'STX.US.ETORO': 5, 'TME.ETORO': 5, 'LQDA.ETORO': 5, 'TXG.ETORO': 5, 'WBD.ETORO': 5, 'KRYS.ETORO': 5, 'TVTX.ETORO': 5, 'CPNG.ETORO': 5, 'UPST.ETORO': 5, 'JOBY.ETORO': 5, 'FRO.ETORO': 5, 'CRS.ETORO': 5, 'ENVA.ETORO': 5, 'BETA.ETORO': 4, 'RBLX.ETORO': 5, 'TWST.ETORO': 5, 'DKNG.ETORO': 5, 'KD.ETORO': 5, 'DUOL.ETORO': 5, 'MNDY.ETORO': 5, 'CVE.ETORO': 5, 'CELH.ETORO': 5, 'NATGAS.ETORO': 5, 'USDZAR.ETORO': 5, 'USDTRY.ETORO': 5, 'CAT.ETORO': 5, 'JNJ.ETORO': 5, 'PBR.ETORO': 5, 'ADBE.ETORO': 4, 'WDC.ETORO': 5, 'MU.ETORO': 5, 'NVDA.ETORO': 5, 'WIX.ETORO': 5, 'PINS.ETORO': 5, 'ROP.ZU.ETORO': 5, 'ACN.ETORO': 4, 'HAL.ETORO': 5, 'GLW.ETORO': 5, 'CRM.ETORO': 5, 'MTZ.ETORO': 5, 'CMG.ETORO': 5, 'SNAP.ETORO': 5, '01211.HK.ETORO': 5, 'RHM.DE.ETORO': 5, 'PALL.ETORO': 5, 'HUBS.ETORO': 5, 'NVS.ETORO': 5, 'NOW.ETORO': 5, 'WDAY.ETORO': 5, 'TEAM.ETORO': 5, 'DOCU.ETORO': 5, 'ZS.ETORO': 5, 'CIEN.ETORO': 5, 'FSLY.ETORO': 5, 'EEFT.ETORO': 5, 'SE.ETORO': 5, 'COHR.ETORO': 5, 'RIOT.ETORO': 5, 'GOOGL.ETORO': 5, 'TSEM.ETORO': 5, 'AG.ETORO': 5, 'XPEV.ETORO': 5, 'SQM.ETORO': 5, 'PLTR.ETORO': 5, 'GTLB.ETORO': 5, 'E.ETORO': 5, 'HUT.ETORO': 5, 'VRT.ETORO': 5, 'FICO.ETORO': 5, 'INSM.ETORO': 5, 'INSW.ETORO': 5, 'TTMI.ETORO': 5, 'POWL.ETORO': 5, 'ESLT.ETORO': 4, 'ETH.ETORO': 5, 'XRP.ETORO': 5, 'ADA.ETORO': 5, 'DOGE.ETORO': 5, 'SOL.ETORO': 5, 'SHIBxM.ETORO': 5, 'AVAX.ETORO': 5, 'PEPExM.ETORO': 5, 'ONDO.ETORO': 5, 'AERO.ETORO': 5, 'HYPE.ETORO': 4, 'TER.ETORO': 5, 'AXON.ETORO': 5, 'SMTC.ETORO': 5, 'SEI.US.ETORO': 5, 'NOK.ETORO': 5, 'GEV.ETORO': 5, 'FDX.ETORO': 5, 'AMKR.ETORO': 5, 'ELF.ETORO': 5, 'NVO.ETORO': 5, 'MRVL.ETORO': 5, 'PYPL.ETORO': 5, 'CAG.ETORO': 5, 'GIS.ETORO': 5, 'NTSK.ETORO': 4, 'ABT.US.ETORO': 5, 'CCL.ETORO': 5, 'JAZZ.ETORO': 5, 'GOOG.ETORO': 5, 'MOD.ETORO': 5, 'ARWR.ETORO': 5, 'FORM.ETORO': 5, 'DPZ.ETORO': 5, 'LSCC.ETORO': 5, 'ONTO.ETORO': 5, 'AXSM.ETORO': 5, 'VSH.ETORO': 5, 'ATRO.ETORO': 5, 'CHWY.ETORO': 5, 'TD.ETORO': 5}

**check_reward_term_variance**

- (scope=OpeningRangeBreakoutStrategy/TSLA.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=1.155175): ['param_pen', 'turnover'].
- (scope=HourlyMeanReversionStrategy/TSLA.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=2.853134): ['param_pen', 'turnover'].
- (scope=MeanReversionStrategy/TSLA.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=2.638889): ['param_pen', 'turnover'].
- (scope=FlashCrashReversalStrategy/TSLA.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=5.474423): ['param_pen', 'turnover'].
- (scope=ComboTrendVwapStrategy/TSLA.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=3.085999): ['param_pen', 'turnover'].
- … und 5 weitere

**check_inference_diagnostics_concentration**

- (scope=OpeningRangeBreakoutStrategy/TSLA.ETORO): 22/102 informative Trials (21.6%) wurden vom Inferenz-Wächter zensiert (SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE, kein regulärer Ausgang) — die Suche ist faktisch zensiert (analog STUDY_GUARD_DOMINATED, #823).
- (scope=HourlyMeanReversionStrategy/TSLA.ETORO): 635/620 — Zaehler/Nenner nicht kommensurabel (Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).
- (scope=MeanReversionStrategy/TSLA.ETORO): 708/589 — Zaehler/Nenner nicht kommensurabel (Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).
- (scope=FlashCrashReversalStrategy/TSLA.ETORO): 746/702 — Zaehler/Nenner nicht kommensurabel (Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).
- (scope=ComboTrendVwapStrategy/TSLA.ETORO): 549/1253 informative Trials (43.8%) wurden vom Inferenz-Wächter zensiert (SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE, kein regulärer Ausgang) — die Suche ist faktisch zensiert (analog STUDY_GUARD_DOMINATED, #823).
- … und 4 weitere

**check_objective_branch_coverage**

- (scope=OpeningRangeBreakoutStrategy/TSLA.ETORO): Nur 0/824 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=Rsi2ReversionStrategy/TSLA.ETORO): Nur 0/1032 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=ComboTrendVwapStrategy/TSLA.ETORO): Nur 140/1927 Trials (7.27%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=SmaCrossoverStrategy/TSLA.ETORO): Nur 0/601 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=DynamicBreakoutStrategy/TSLA.ETORO): Nur 0/601 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- … und 4 weitere

**check_inference_diagnostics_absent**

- (scope=OpeningRangeBreakoutStrategy/TSLA.ETORO): 5218 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 5218}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- (scope=HourlyMeanReversionStrategy/TSLA.ETORO): 234 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 234}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- (scope=MeanReversionStrategy/TSLA.ETORO): 177 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 177}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- (scope=ComboTrendVwapStrategy/TSLA.ETORO): 316 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 316}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- (scope=DynamicBreakoutStrategy/TSLA.ETORO): 1 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 1}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- … und 2 weitere

**check_adaptive_diagnostic_rate**

- (scope=HourlyMeanReversionStrategy/TSLA.ETORO): 627/620 — Zaehler/Nenner nicht kommensurabel (Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).
- (scope=MeanReversionStrategy/TSLA.ETORO): 708/589 — Zaehler/Nenner nicht kommensurabel (Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).
- (scope=FlashCrashReversalStrategy/TSLA.ETORO): 727/702 — Zaehler/Nenner nicht kommensurabel (Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).
- (scope=ComboTrendVwapStrategy/TSLA.ETORO): 544/1253 informative Trials (43.42%) verlassen sich auf einen ADAPTIVE-Korrekturmechanismus (SORTINO_DOWNSIDE_SHRUNK) — über der Schwelle (30%), strukturell zu wenige Downside-Beobachtungen in dieser Study.
- (scope=VwapExhaustionStrategy/TSLA.ETORO): 503/522 informative Trials (96.36%) verlassen sich auf einen ADAPTIVE-Korrekturmechanismus (SORTINO_DOWNSIDE_SHRUNK) — über der Schwelle (30%), strukturell zu wenige Downside-Beobachtungen in dieser Study.
- … und 1 weitere

**check_budget_execution**

- (scope=global): median(budget_executed_fraction)=0.0000 < 0.5 ueber 14 Studies — ein grosser Teil des konfigurierten Suchbudgets wird nicht ausgefuehrt (#768/#769-Fehlerklasse).

**check_gate_marginal_contribution**

- (scope=global): 2 Gate(s) ohne jeden marginalen Beitrag über eine ausreichend grosse Kohorte: {'min_trades': {'marginal_delta': 0, 'n_evaluated': 5528}, 'max_drawdown': {'marginal_delta': 0, 'n_evaluated': 5528}} — Kandidat(en) für Entfernung aus eligible_requires_all oder Neukalibrierung gegen die realisierte Verteilung.

**check_window_unreachable_rate**

- (scope=global): 9 Study/Studies mit überproportional vielen unerreichbaren OOS-Fenstern: {'Rsi2ReversionStrategy/TSLA.ETORO': 0.9876, 'ComboTrendVwapStrategy/TSLA.ETORO': 0.1331, 'SmaCrossoverStrategy/TSLA.ETORO': 0.7245, 'DynamicBreakoutStrategy/TSLA.ETORO': 0.3203, 'DonchianRegimeBreakoutStrategy/TSLA.ETORO': 0.9837, 'TrendPullbackStrategy/TSLA.ETORO': 0.9795, 'AdxAtrMomentumStrategy/TSLA.ETORO': 0.9847, 'VolatilityBreakoutPumpStrategy/TSLA.ETORO': 0.9794, 'SqueezeBreakoutStrategy/TSLA.ETORO': 0.1027} — zu weite Lookback-Bounds für die Datenlage dieses Symbols (spaces.py gegen data_window_days deckeln, #976).

### 5.3 Zusammenfassung

- Guard-dominierte Studies (SORTINO_GUARD_TRIPPED-Mehrheit, #823): 0
- Wirtschaftlich ruinierte Trials (EQUITY_NONPOSITIVE, #801/#825): 0
- Randlösungen mit Bounds-Vorschlag (#831): 9
- Automatisch denylistete Paare (#829/#830): 5
- Budget-deprioritisierte Paare (#830): 0
