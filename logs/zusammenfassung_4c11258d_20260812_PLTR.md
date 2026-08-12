# Sweep-Zusammenfassung 4c11258d_20260812_PLTR


## 1. Ergebnis in einem Satz

14 Studies, 1 Sweep-Promotion(en), **0 deploybar** — kein Kandidat hat sowohl die Holdout-Validierung als auch das Deployment-Gate (``deployment_gate.evaluate_deployment_eligibility``) bestanden. Es gibt kein deploybares Ergebnis aus diesem Lauf. **BLOCKIERENDE Invarianten-FAIL(s):** check_holding_time_cap, check_selection_statistic_availability, check_sizing_identity_coherence — siehe Abschnitt 5.1 für Details.

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
| OpeningRangeBreakoutStrategy | PLTR.ETORO | 2.2 % | REJECTED_ON_HOLDOUT |
| Rsi2ReversionStrategy | PLTR.ETORO | 4.6 % | REJECTED_ON_HOLDOUT |
| SmaCrossoverStrategy | PLTR.ETORO | -1.8 % | HOLD_BOUNDARY_UNRESOLVED |
| SqueezeBreakoutStrategy | PLTR.ETORO | 0.0 % | REJECTED_ON_HOLDOUT |
| TrendPullbackStrategy | PLTR.ETORO | 3.6 % | REJECTED_ON_HOLDOUT |
| VolatilityBreakoutPumpStrategy | PLTR.ETORO | -1.4 % | REJECTED_ON_HOLDOUT |
| VwapExhaustionStrategy | PLTR.ETORO | -0.0 % | REJECTED_SELECTION_OVERFIT |

### 2.3 Vergleich gegen Buy & Hold je Symbol

| Strategie | Symbol | Strategie-Return | Buy&Hold-Return | Excess | Zeit im Markt | Excess/Exposure | Vorzeichen |
|---|---|---:|---:|---:|---:|---:|---|
| Rsi2ReversionStrategy | PLTR.ETORO | 4.6 % | 53.6 % | -49.0 % | 53.6 % | -91.4 % | negativ (unter Buy & Hold) |
| DonchianRegimeBreakoutStrategy | PLTR.ETORO | 4.4 % | 53.6 % | -49.2 % | 22.0 % | -223.9 % | negativ (unter Buy & Hold) |
| TrendPullbackStrategy | PLTR.ETORO | 3.6 % | 53.6 % | -50.0 % | 55.5 % | -90.1 % | negativ (unter Buy & Hold) |
| AdxAtrMomentumStrategy | PLTR.ETORO | 3.0 % | 53.6 % | -50.6 % | 90.7 % | -55.8 % | negativ (unter Buy & Hold) |
| FlashCrashReversalStrategy | PLTR.ETORO | 2.4 % | 53.6 % | -51.2 % | 10.8 % | -472.2 % | negativ (unter Buy & Hold) |
| OpeningRangeBreakoutStrategy | PLTR.ETORO | 2.2 % | 53.6 % | -51.4 % | 29.7 % | -172.6 % | negativ (unter Buy & Hold) |
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
| ComboTrendVwapStrategy | 0.43 h (1538 s) | 0.43 h (1538 s) | 1 |
| Rsi2ReversionStrategy | 0.30 h (1093 s) | 0.30 h (1093 s) | 1 |
| FlashCrashReversalStrategy | 0.30 h (1090 s) | 0.30 h (1090 s) | 1 |
| AdxAtrMomentumStrategy | 0.30 h (1063 s) | 0.30 h (1063 s) | 1 |
| SqueezeBreakoutStrategy | 0.26 h (941 s) | 0.26 h (941 s) | 1 |
| TrendPullbackStrategy | 0.26 h (919 s) | 0.26 h (919 s) | 1 |
| MeanReversionStrategy | 0.23 h (813 s) | 0.23 h (813 s) | 1 |
| HourlyMeanReversionStrategy | 0.22 h (798 s) | 0.22 h (798 s) | 1 |
| VwapExhaustionStrategy | 0.20 h (729 s) | 0.20 h (729 s) | 1 |
| DynamicBreakoutStrategy | 0.20 h (708 s) | 0.20 h (708 s) | 1 |
| OpeningRangeBreakoutStrategy | 0.20 h (703 s) | 0.20 h (703 s) | 1 |
| VolatilityBreakoutPumpStrategy | 0.18 h (645 s) | 0.18 h (645 s) | 1 |
| SmaCrossoverStrategy | 0.18 h (635 s) | 0.18 h (635 s) | 1 |
| DonchianRegimeBreakoutStrategy | 0.01 h (42 s) | 0.01 h (42 s) | 1 |

### 3.3 Gelaufene vs. budgetierte Trials

- Median Budgetausführung: 0.0 % (p10: 0.0 %, n=14 Studies)
- Trials gesamt: 0 von 1940 budgetiert

### 3.4 Verlorene Zeit: abgebrochene Studies

- UNKNOWN_INCOMPLETE: 10 Studies
- STRUCTURAL_ZERO_ELIGIBLE: 4 Studies

Barriere-Wartezeit (Symbol-Wallclock minus schnellste Study) — die 1 Symbole mit der längsten Wartezeit:
- PLTR.ETORO: 0.42 h (1496 s)

## 4. Trades mit der längsten Haltedauer

**Scope-Hinweis:** diese Sektion listet die längste beobachtete Haltedauer JE STUDY (Strategie/Symbol, Maximum über alle OOS-evaluierten Trials), NICHT einzelne Trades mit Entry-/Exit-Zeitstempel — siehe Modul-Docstring für die Begründung dieser Scope-Entscheidung (Katalog #832 Fix Punkt 1).

| Strategie | Symbol | Max. Haltedauer | P95 Haltedauer |
|---|---|---:|---:|
| Rsi2ReversionStrategy | PLTR.ETORO | 456.00 h (1641600 s) | 48.00 h (172800 s) |
| TrendPullbackStrategy | PLTR.ETORO | 456.00 h (1641600 s) | 48.00 h (172800 s) |
| VwapExhaustionStrategy | PLTR.ETORO | 168.00 h (604800 s) | 6.00 h (21600 s) |
| ComboTrendVwapStrategy | PLTR.ETORO | 40.00 h (144000 s) | 23.00 h (82800 s) |
| HourlyMeanReversionStrategy | PLTR.ETORO | 36.00 h (129600 s) | 14.00 h (50400 s) |
| MeanReversionStrategy | PLTR.ETORO | 36.00 h (129600 s) | 24.00 h (86400 s) |
| FlashCrashReversalStrategy | PLTR.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| DonchianRegimeBreakoutStrategy | PLTR.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| VolatilityBreakoutPumpStrategy | PLTR.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| SmaCrossoverStrategy | PLTR.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |

## 5. Auffälligkeiten

### 5.1 Übersicht — Invarianten-FAILs (54)

| Check | FAILs | betroffene Studies | Schweregrad |
|---|---:|---:|---|
| check_holding_time_cap | 1 | 1 | blocking |
| check_selection_statistic_availability | 1 | 1 | blocking |
| check_sizing_identity_coherence | 1 | 1 | blocking |
| check_annualization_commensurability | 12 | 12 | high |
| check_atr_scale_homogeneity | 1 | 1 | high |
| check_counter_partition_consistency | 1 | 1 | high |
| check_n_periods_homogeneity | 1 | 1 | high |
| check_promotion_deployment_coherence | 1 | 1 | high |
| check_search_made_progress | 1 | 1 | high |
| check_selection_statistic_economic_bias | 1 | 1 | high |
| check_symbol_coverage | 1 | 1 | high |
| check_reward_term_variance | 14 | 14 | medium |
| check_inference_diagnostics_absent | 8 | 8 | medium |
| check_objective_branch_coverage | 6 | 6 | medium |
| check_budget_execution | 1 | 1 | medium |
| check_gate_marginal_contribution | 1 | 1 | medium |
| check_inference_diagnostics_concentration | 1 | 1 | medium |
| check_champion_seed_coverage | 1 | 1 | low |

### 5.2 Details

**check_holding_time_cap**

- (scope=global): Magnituden-Ast (#1036): 3 Study/Studies mit einer Einzelposition > 3.0x der Zeitbox (291600s): {'VwapExhaustionStrategy/PLTR.ETORO': 6.22, 'Rsi2ReversionStrategy/PLTR.ETORO': 16.89, 'TrendPullbackStrategy/PLTR.ETORO': 16.89} (Vielfaches der Zeitbox je Study) — ökonomisch untragbar unabhängig vom gepoolten Anteil (Pitfall #358).

**check_selection_statistic_availability**

- (scope=global): 1 Study/Studies unter der Mindestverfügbarkeit (0.8) einer definierten Selektions-Teststatistik: {'SqueezeBreakoutStrategy/PLTR.ETORO': 0.0098} — die Eligibility-Auswertung dieser Studies ist strukturell informationsfrei (Issue #913/#915), keine Aussage über die Strategien.

**check_sizing_identity_coherence**

- (scope=global): 1 Study/Studies: der aus (total_return, expectancy, n) implizierte Sizing-Anteil weicht relativ um mehr als 0.35 vom konfigurierten trade_amount_pct ab: {'VwapExhaustionStrategy/PLTR.ETORO': {'f_implied_pct': 5.6031, 'trade_amount_pct': 15.0}} — Signatur einer Datenanomalie in der zugrundeliegenden Preisreihe, nicht des Sizing-Pfads (#1028).

**check_annualization_commensurability**

- (scope=VwapExhaustionStrategy/PLTR.ETORO): Trial-Index 55: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 2.052 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=FlashCrashReversalStrategy/PLTR.ETORO): Trial-Index 15: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 2.583 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=DonchianRegimeBreakoutStrategy/PLTR.ETORO): Trial-Index 13: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.868 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=HourlyMeanReversionStrategy/PLTR.ETORO): Trial-Index 59: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.655 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=MeanReversionStrategy/PLTR.ETORO): Trial-Index 221: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.587 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- … und 7 weitere

**check_atr_scale_homogeneity**

- (scope=global): 1 Symbol(e) mit einer ATR-Spannweite über 6.0x zwischen Strategien: {'PLTR.ETORO': 19.43} — Signatur einer Sprungstelle in der Preisreihe (#1028).

**check_counter_partition_consistency**

- (scope=global): 3 Study/Studies: der Plateau-Zähler und n_trials zerlegen die Trial-Menge NICHT disjunkt/vollständig: {'OpeningRangeBreakoutStrategy/PLTR.ETORO': {'n_trials': 226, 'n_evaluated': 106, 'breakdown_sum': 0, 'reconstructed_total': 106}, 'Rsi2ReversionStrategy/PLTR.ETORO': {'n_trials': 302, 'n_evaluated': 116, 'breakdown_sum': 25, 'reconstructed_total': 141}, 'TrendPullbackStrategy/PLTR.ETORO': {'n_trials': 263, 'n_evaluated': 104, 'breakdown_sum': 19, 'reconstructed_total': 123}} — mindestens einer der beteiligten Zähler zielt nicht auf dieselbe Grundgesamtheit (Pitfall #304).

**check_n_periods_homogeneity**

- (scope=global): 1 Symbol(e) mit n_periods-Spannweite > 6.0: {'PLTR.ETORO': 116.77} — die #865-Heterogenitäts-Suppression (deflation_max_n_periods_ratio) greift vermutlich für praktisch jede Familie dieses Symbols (Issue #923).

**check_promotion_deployment_coherence**

- (scope=DonchianRegimeBreakoutStrategy/PLTR.ETORO): Promotion (status='PROMOTE_GLOBAL_DEFAULT') besteht die Deployment-Grenze nicht (blocking_clause='status_ready_for_pr') — der Kandidat ist ein Sweep-Gewinner, aber laut deployment_gate.evaluate_deployment_eligibility NICHT deploybar (#1006).

**check_search_made_progress**

- (scope=global): 3 Study/Studies mit stagnierender/wachsender Constraint-Verletzung bei 0 eligiblen Trials nach ausreichend modellierten Trials: {'DonchianRegimeBreakoutStrategy/PLTR.ETORO': -0.076063, 'TrendPullbackStrategy/PLTR.ETORO': -0.008325, 'SqueezeBreakoutStrategy/PLTR.ETORO': -0.362525} — der TPE-Sampler hat nachweislich keinen Gradienten gefunden.

**check_selection_statistic_economic_bias**

- (scope=SqueezeBreakoutStrategy/PLTR.ETORO): Median-OOS-Return der Kohorte OHNE Selektionsstatistik (-0.0000, n=303) ist signifikant GRÖSSER als der der Kohorte MIT Statistik (-0.0118, n=3) — z=2.8953, p=0.00189 < 0.01. Die fehlende Statistik ist nicht MCAR: die Selektion verwirft bevorzugt die profitable Kohorte aus einem numerischen, nicht ökonomischen Grund (Pitfall #306).

**check_symbol_coverage**

- (scope=global): 144 Symbol(e) seit mehr als 3 Läufen nicht ERNEUT abgedeckt (stale, least_recently_covered-Rotation sollte das verhindern): {'XOM.ETORO': 5, 'NKE.ETORO': 5, 'NFLX.ETORO': 5, 'VLO.ETORO': 5, 'LLY.ETORO': 5, 'CHTR.ETORO': 5, 'AMAT.ETORO': 4, 'FTI.ETORO': 5, 'AMD.ETORO': 4, 'FISV.ETORO': 5, 'Z.ETORO': 5, 'LRCX.ETORO': 5, 'INTU.ETORO': 5, 'ATI.ETORO': 4, 'GSAT.ETORO': 5, 'ENLT.ETORO': 5, 'TEVA.ETORO': 5, 'MBLY.ETORO': 5, 'PSN.US.ETORO': 5, 'TTD.ETORO': 5, 'ASX.ETORO': 4, 'PODD.ETORO': 5, 'CPRT.ETORO': 5, 'ZTS.ETORO': 5, 'MSTR.ETORO': 5, 'LULU.ETORO': 5, 'ASML.ETORO': 4, 'RVMD.ETORO': 5, 'COIN.ETORO': 5, 'STX.US.ETORO': 5, 'TME.ETORO': 5, 'LQDA.ETORO': 5, 'TXG.ETORO': 5, 'WBD.ETORO': 5, 'KRYS.ETORO': 5, 'TVTX.ETORO': 5, 'CPNG.ETORO': 5, 'UPST.ETORO': 5, 'JOBY.ETORO': 5, 'FRO.ETORO': 5, 'CRS.ETORO': 5, 'ENVA.ETORO': 5, 'BETA.ETORO': 4, 'RBLX.ETORO': 5, 'TWST.ETORO': 5, 'DKNG.ETORO': 5, 'KD.ETORO': 5, 'DUOL.ETORO': 5, 'MNDY.ETORO': 5, 'CVE.ETORO': 5, 'CELH.ETORO': 5, 'NATGAS.ETORO': 5, 'USDZAR.ETORO': 5, 'USDTRY.ETORO': 5, 'CAT.ETORO': 5, 'JNJ.ETORO': 5, 'PBR.ETORO': 5, 'ADBE.ETORO': 4, 'WDC.ETORO': 5, 'MU.ETORO': 5, 'NVDA.ETORO': 5, 'WIX.ETORO': 5, 'PINS.ETORO': 5, 'ROP.ZU.ETORO': 5, 'ACN.ETORO': 4, 'HAL.ETORO': 5, 'GLW.ETORO': 5, 'CRM.ETORO': 5, 'MTZ.ETORO': 5, 'CMG.ETORO': 5, 'SNAP.ETORO': 5, '01211.HK.ETORO': 5, 'RHM.DE.ETORO': 5, 'PALL.ETORO': 5, 'HUBS.ETORO': 5, 'NVS.ETORO': 5, 'NOW.ETORO': 5, 'WDAY.ETORO': 5, 'TEAM.ETORO': 5, 'DOCU.ETORO': 5, 'ZS.ETORO': 5, 'CIEN.ETORO': 5, 'FSLY.ETORO': 5, 'EEFT.ETORO': 5, 'SE.ETORO': 5, 'COHR.ETORO': 5, 'RIOT.ETORO': 5, 'GOOGL.ETORO': 5, 'TSEM.ETORO': 5, 'AG.ETORO': 5, 'XPEV.ETORO': 5, 'SQM.ETORO': 5, 'PLTR.ETORO': 5, 'GTLB.ETORO': 5, 'E.ETORO': 5, 'HUT.ETORO': 5, 'VRT.ETORO': 5, 'FICO.ETORO': 5, 'INSM.ETORO': 5, 'INSW.ETORO': 5, 'TTMI.ETORO': 5, 'POWL.ETORO': 5, 'ESLT.ETORO': 4, 'ETH.ETORO': 5, 'XRP.ETORO': 5, 'ADA.ETORO': 5, 'DOGE.ETORO': 5, 'SOL.ETORO': 5, 'SHIBxM.ETORO': 5, 'AVAX.ETORO': 5, 'PEPExM.ETORO': 5, 'ONDO.ETORO': 5, 'AERO.ETORO': 5, 'HYPE.ETORO': 4, 'TER.ETORO': 5, 'AXON.ETORO': 5, 'SMTC.ETORO': 5, 'SEI.US.ETORO': 5, 'NOK.ETORO': 5, 'GEV.ETORO': 5, 'FDX.ETORO': 5, 'AMKR.ETORO': 5, 'ELF.ETORO': 5, 'NVO.ETORO': 5, 'MRVL.ETORO': 5, 'PYPL.ETORO': 5, 'CAG.ETORO': 5, 'GIS.ETORO': 5, 'NTSK.ETORO': 4, 'ABT.US.ETORO': 5, 'CCL.ETORO': 5, 'JAZZ.ETORO': 5, 'GOOG.ETORO': 5, 'MOD.ETORO': 5, 'ARWR.ETORO': 5, 'FORM.ETORO': 5, 'DPZ.ETORO': 5, 'LSCC.ETORO': 5, 'ONTO.ETORO': 5, 'AXSM.ETORO': 5, 'VSH.ETORO': 5, 'ATRO.ETORO': 5, 'CHWY.ETORO': 5, 'TD.ETORO': 5}

**check_reward_term_variance**

- (scope=VwapExhaustionStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=2.064678): ['param_pen', 'turnover'].
- (scope=FlashCrashReversalStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=0.671460): ['param_pen', 'turnover', 'fold_dispersion'].
- (scope=DonchianRegimeBreakoutStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=2.062549): ['param_pen', 'turnover', 'fold_dispersion'].
- (scope=OpeningRangeBreakoutStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=1.007806): ['param_pen', 'turnover', 'fold_dispersion'].
- (scope=HourlyMeanReversionStrategy/PLTR.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=2.040818): ['param_pen', 'turnover'].
- … und 9 weitere

**check_inference_diagnostics_absent**

- (scope=VwapExhaustionStrategy/PLTR.ETORO): 8 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 8}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- (scope=HourlyMeanReversionStrategy/PLTR.ETORO): 40 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 40}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- (scope=MeanReversionStrategy/PLTR.ETORO): 31 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 31}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- (scope=Rsi2ReversionStrategy/PLTR.ETORO): 822 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 822}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- (scope=ComboTrendVwapStrategy/PLTR.ETORO): 29 Inferenzpfad-Diagnose(n) über die Study ({'EXPECTANCY_NOTIONAL_DEGENERATE': 29}) — siehe INFERENCE_DIAGNOSTIC-Ereignisse im Optimizer-Log für Details je Trial.
- … und 3 weitere

**check_objective_branch_coverage**

- (scope=FlashCrashReversalStrategy/PLTR.ETORO): Nur 13/160 Trials (8.12%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=DonchianRegimeBreakoutStrategy/PLTR.ETORO): Nur 0/126 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=OpeningRangeBreakoutStrategy/PLTR.ETORO): Nur 0/226 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=Rsi2ReversionStrategy/PLTR.ETORO): Nur 0/301 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=TrendPullbackStrategy/PLTR.ETORO): Nur 0/263 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- … und 1 weitere

**check_budget_execution**

- (scope=global): median(budget_executed_fraction)=0.0000 < 0.5 ueber 14 Studies — ein grosser Teil des konfigurierten Suchbudgets wird nicht ausgefuehrt (#768/#769-Fehlerklasse).

**check_gate_marginal_contribution**

- (scope=global): 2 Gate(s) ohne jeden marginalen Beitrag über eine ausreichend grosse Kohorte: {'min_trades': {'marginal_delta': 0, 'n_evaluated': 3373}, 'max_drawdown': {'marginal_delta': 0, 'n_evaluated': 3373}} — Kandidat(en) für Entfernung aus eligible_requires_all oder Neukalibrierung gegen die realisierte Verteilung.

**check_inference_diagnostics_concentration**

- (scope=SqueezeBreakoutStrategy/PLTR.ETORO): 303/3 — Zaehler/Nenner nicht kommensurabel (Rate > 1.0 ist kein gueltiger Beobachtungswert, #1033).

**check_champion_seed_coverage**

- (scope=global): strategy_defaults-Anteil=92.9% > 90% — der Champion-Store-Closed-Loop (#702) ist fuer diesen Lauf nachweislich unwirksam ({'strategy_defaults': 13, 'champion': 1}).

### 5.3 Zusammenfassung

- Guard-dominierte Studies (SORTINO_GUARD_TRIPPED-Mehrheit, #823): 0
- Wirtschaftlich ruinierte Trials (EQUITY_NONPOSITIVE, #801/#825): 0
- Randlösungen mit Bounds-Vorschlag (#831): 10
- Automatisch denylistete Paare (#829/#830): 5
- Budget-deprioritisierte Paare (#830): 0
