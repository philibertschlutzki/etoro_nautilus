# Sweep-Zusammenfassung 02fb2f4d_20260812_BTC


## 1. Ergebnis in einem Satz

14 Studies, 0 Sweep-Promotion(en), **0 deploybar** — kein Kandidat hat sowohl die Holdout-Validierung als auch das Deployment-Gate (``deployment_gate.evaluate_deployment_eligibility``) bestanden. Es gibt kein deploybares Ergebnis aus diesem Lauf. **BLOCKIERENDE Invarianten-FAIL(s):** check_selection_statistic_availability — siehe Abschnitt 5.1 für Details.

## 2. Monetäres Ergebnis

### 2.1 Promotionskandidaten (Status READY_FOR_PR / PROMOTE_GLOBAL_DEFAULT) — noch NICHT deploybar

**Kein deploybares Ergebnis aus diesem Lauf.** Kein Kandidat hat die Holdout-Validierung bestanden — alle folgenden Zahlen in Abschnitt 2.2 sind ABGELEHNTE, NICHT handelbare Kandidaten.

### 2.2 Bester abgelehnter Kandidat je Strategie (NICHT deploybar)

**Diese Kandidaten sind ausdrücklich NICHT deploybar** — der Backtest-Ertrag ist eine Simulationszahl, kein handelbares Ergebnis.

| Strategie | Symbol | Holdout-Return (simuliert) | Ablehnungsgrund |
|---|---|---:|---|
| AdxAtrMomentumStrategy | BTC.ETORO | -5.9 % | REJECTED_ON_HOLDOUT |
| ComboTrendVwapStrategy | BTC.ETORO | -1.1 % | REJECTED_ON_HOLDOUT |
| DonchianRegimeBreakoutStrategy | BTC.ETORO | -1.1 % | REJECTED_ON_HOLDOUT |
| DynamicBreakoutStrategy | BTC.ETORO | -1.1 % | REJECTED_SELECTION_OVERFIT |
| FlashCrashReversalStrategy | BTC.ETORO | -0.8 % | REJECTED_ON_HOLDOUT |
| HourlyMeanReversionStrategy | BTC.ETORO | -1.2 % | REJECTED_ON_HOLDOUT |
| MeanReversionStrategy | BTC.ETORO | -1.2 % | REJECTED_ON_HOLDOUT |
| OpeningRangeBreakoutStrategy | BTC.ETORO | -2.2 % | REJECTED_ON_HOLDOUT |
| Rsi2ReversionStrategy | BTC.ETORO | -3.3 % | REJECTED_ON_HOLDOUT |
| SmaCrossoverStrategy | BTC.ETORO | -1.8 % | REJECTED_ON_HOLDOUT |
| SqueezeBreakoutStrategy | BTC.ETORO | 0.0 % | REJECTED_ON_HOLDOUT |
| TrendPullbackStrategy | BTC.ETORO | -1.6 % | REJECTED_ON_HOLDOUT |
| VolatilityBreakoutPumpStrategy | BTC.ETORO | -1.5 % | REJECTED_ON_HOLDOUT |
| VwapExhaustionStrategy | BTC.ETORO | -2.2 % | REJECTED_ON_HOLDOUT |

### 2.3 Vergleich gegen Buy & Hold je Symbol

| Strategie | Symbol | Strategie-Return | Buy&Hold-Return | Excess | Zeit im Markt | Excess/Exposure | Vorzeichen |
|---|---|---:|---:|---:|---:|---:|---|
| FlashCrashReversalStrategy | BTC.ETORO | -0.8 % | 6.7 % | -7.5 % | 6.4 % | -117.3 % | negativ (unter Buy & Hold) |
| DynamicBreakoutStrategy | BTC.ETORO | -1.1 % | 6.7 % | -7.8 % | 9.6 % | -80.6 % | negativ (unter Buy & Hold) |
| ComboTrendVwapStrategy | BTC.ETORO | -1.1 % | 6.7 % | -7.8 % | 73.2 % | -10.6 % | negativ (unter Buy & Hold) |
| DonchianRegimeBreakoutStrategy | BTC.ETORO | -1.1 % | 6.7 % | -7.8 % | 32.1 % | -24.3 % | negativ (unter Buy & Hold) |
| MeanReversionStrategy | BTC.ETORO | -1.2 % | 6.7 % | -7.9 % | 16.3 % | -48.2 % | negativ (unter Buy & Hold) |
| HourlyMeanReversionStrategy | BTC.ETORO | -1.2 % | 6.7 % | -7.9 % | 29.0 % | -27.1 % | negativ (unter Buy & Hold) |
| VolatilityBreakoutPumpStrategy | BTC.ETORO | -1.5 % | 6.7 % | -8.1 % | 24.1 % | -33.7 % | negativ (unter Buy & Hold) |
| TrendPullbackStrategy | BTC.ETORO | -1.6 % | 6.7 % | -8.3 % | 25.1 % | -33.0 % | negativ (unter Buy & Hold) |
| SmaCrossoverStrategy | BTC.ETORO | -1.8 % | 6.7 % | -8.4 % | 41.9 % | -20.1 % | negativ (unter Buy & Hold) |
| OpeningRangeBreakoutStrategy | BTC.ETORO | -2.2 % | 6.7 % | -8.8 % | 37.3 % | -23.7 % | negativ (unter Buy & Hold) |
| VwapExhaustionStrategy | BTC.ETORO | -2.2 % | 6.7 % | -8.9 % | 51.1 % | -17.4 % | negativ (unter Buy & Hold) |
| Rsi2ReversionStrategy | BTC.ETORO | -3.3 % | 6.7 % | -10.0 % | 55.5 % | -18.0 % | negativ (unter Buy & Hold) |
| AdxAtrMomentumStrategy | BTC.ETORO | -5.9 % | 6.7 % | -12.5 % | 82.9 % | -15.1 % | negativ (unter Buy & Hold) |


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
| ComboTrendVwapStrategy | 0.40 h (1446 s) | 0.40 h (1446 s) | 1 |
| FlashCrashReversalStrategy | 0.28 h (1007 s) | 0.28 h (1007 s) | 1 |
| TrendPullbackStrategy | 0.26 h (954 s) | 0.26 h (954 s) | 1 |
| MeanReversionStrategy | 0.25 h (915 s) | 0.25 h (915 s) | 1 |
| SqueezeBreakoutStrategy | 0.24 h (879 s) | 0.24 h (879 s) | 1 |
| HourlyMeanReversionStrategy | 0.24 h (859 s) | 0.24 h (859 s) | 1 |
| VwapExhaustionStrategy | 0.22 h (781 s) | 0.22 h (781 s) | 1 |
| DynamicBreakoutStrategy | 0.21 h (740 s) | 0.21 h (740 s) | 1 |
| VolatilityBreakoutPumpStrategy | 0.20 h (706 s) | 0.20 h (706 s) | 1 |
| SmaCrossoverStrategy | 0.19 h (694 s) | 0.19 h (694 s) | 1 |
| OpeningRangeBreakoutStrategy | 0.17 h (617 s) | 0.17 h (617 s) | 1 |
| AdxAtrMomentumStrategy | 0.07 h (257 s) | 0.07 h (257 s) | 1 |
| DonchianRegimeBreakoutStrategy | 0.03 h (124 s) | 0.03 h (124 s) | 1 |
| Rsi2ReversionStrategy | 0.01 h (25 s) | 0.01 h (25 s) | 1 |

### 3.3 Gelaufene vs. budgetierte Trials

- Median Budgetausführung: 0.0 % (p10: 0.0 %, n=14 Studies)
- Trials gesamt: 0 von 1940 budgetiert

### 3.4 Verlorene Zeit: abgebrochene Studies

- STRUCTURAL_ZERO_ELIGIBLE: 7 Studies
- UNKNOWN_INCOMPLETE: 5 Studies
- STRUCTURAL_ALL_UNEVALUABLE: 2 Studies

Barriere-Wartezeit (Symbol-Wallclock minus schnellste Study) — die 1 Symbole mit der längsten Wartezeit:
- BTC.ETORO: 0.39 h (1421 s)

## 4. Trades mit der längsten Haltedauer

**Scope-Hinweis:** diese Sektion listet die längste beobachtete Haltedauer JE STUDY (Strategie/Symbol, Maximum über alle OOS-evaluierten Trials), NICHT einzelne Trades mit Entry-/Exit-Zeitstempel — siehe Modul-Docstring für die Begründung dieser Scope-Entscheidung (Katalog #832 Fix Punkt 1).

| Strategie | Symbol | Max. Haltedauer | P95 Haltedauer |
|---|---|---:|---:|
| SmaCrossoverStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| VolatilityBreakoutPumpStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| Rsi2ReversionStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| VwapExhaustionStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| FlashCrashReversalStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| TrendPullbackStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| DonchianRegimeBreakoutStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| SqueezeBreakoutStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| DynamicBreakoutStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |
| MeanReversionStrategy | BTC.ETORO | 24.00 h (86400 s) | 21.00 h (75600 s) |

## 5. Auffälligkeiten

### 5.1 Übersicht — Invarianten-FAILs (41)

| Check | FAILs | betroffene Studies | Schweregrad |
|---|---:|---:|---|
| check_selection_statistic_availability | 1 | 1 | blocking |
| check_annualization_commensurability | 12 | 12 | high |
| check_counter_partition_consistency | 1 | 1 | high |
| check_n_periods_homogeneity | 1 | 1 | high |
| check_search_made_progress | 1 | 1 | high |
| check_symbol_coverage | 1 | 1 | high |
| check_reward_term_variance | 12 | 12 | medium |
| check_objective_branch_coverage | 9 | 9 | medium |
| check_budget_execution | 1 | 1 | medium |
| check_gate_marginal_contribution | 1 | 1 | medium |
| check_champion_seed_coverage | 1 | 1 | low |

### 5.2 Details

**check_selection_statistic_availability**

- (scope=global): 1 Study/Studies unter der Mindestverfügbarkeit (0.8) einer definierten Selektions-Teststatistik: {'SqueezeBreakoutStrategy/BTC.ETORO': 0.0} — die Eligibility-Auswertung dieser Studies ist strukturell informationsfrei (Issue #913/#915), keine Aussage über die Strategien.

**check_annualization_commensurability**

- (scope=SmaCrossoverStrategy/BTC.ETORO): Trial-Index 25: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.304 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=VolatilityBreakoutPumpStrategy/BTC.ETORO): Trial-Index 34: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.414 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=Rsi2ReversionStrategy/BTC.ETORO): Trial-Index 85: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 2.449 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=VwapExhaustionStrategy/BTC.ETORO): Trial-Index 62: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.532 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- (scope=FlashCrashReversalStrategy/BTC.ETORO): Trial-Index 241: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.265 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).
- … und 7 weitere

**check_counter_partition_consistency**

- (scope=global): 4 Study/Studies: der Plateau-Zähler und n_trials zerlegen die Trial-Menge NICHT disjunkt/vollständig: {'SmaCrossoverStrategy/BTC.ETORO': {'n_trials': 189, 'n_evaluated': 89, 'breakdown_sum': 0, 'reconstructed_total': 89}, 'VwapExhaustionStrategy/BTC.ETORO': {'n_trials': 189, 'n_evaluated': 89, 'breakdown_sum': 0, 'reconstructed_total': 89}, 'FlashCrashReversalStrategy/BTC.ETORO': {'n_trials': 306, 'n_evaluated': 146, 'breakdown_sum': 0, 'reconstructed_total': 146}, 'TrendPullbackStrategy/BTC.ETORO': {'n_trials': 274, 'n_evaluated': 131, 'breakdown_sum': 3, 'reconstructed_total': 134}} — mindestens einer der beteiligten Zähler zielt nicht auf dieselbe Grundgesamtheit (Pitfall #304).

**check_n_periods_homogeneity**

- (scope=global): 1 Symbol(e) mit n_periods-Spannweite > 6.0: {'BTC.ETORO': 119.67} — die #865-Heterogenitäts-Suppression (deflation_max_n_periods_ratio) greift vermutlich für praktisch jede Familie dieses Symbols (Issue #923).

**check_search_made_progress**

- (scope=global): 5 Study/Studies mit stagnierender/wachsender Constraint-Verletzung bei 0 eligiblen Trials nach ausreichend modellierten Trials: {'Rsi2ReversionStrategy/BTC.ETORO': -0.023563, 'VwapExhaustionStrategy/BTC.ETORO': -0.056442, 'DonchianRegimeBreakoutStrategy/BTC.ETORO': 0.0, 'SqueezeBreakoutStrategy/BTC.ETORO': 0.0, 'AdxAtrMomentumStrategy/BTC.ETORO': -0.09488} — der TPE-Sampler hat nachweislich keinen Gradienten gefunden. 1 weitere Study/Studies waren nach der FAIL-Bedingung auffällig, aber ihre Eingabe ist zu grob quantisiert (< 10 verschiedene Werte) für eine belastbare Aussage — als INCONCLUSIVE statt FAIL gezählt: ['OpeningRangeBreakoutStrategy/BTC.ETORO'].

**check_symbol_coverage**

- (scope=global): 144 Symbol(e) seit mehr als 3 Läufen nicht ERNEUT abgedeckt (stale, least_recently_covered-Rotation sollte das verhindern): {'XOM.ETORO': 6, 'NKE.ETORO': 6, 'NFLX.ETORO': 6, 'VLO.ETORO': 6, 'LLY.ETORO': 6, 'CHTR.ETORO': 6, 'AMAT.ETORO': 5, 'FTI.ETORO': 6, 'AMD.ETORO': 5, 'FISV.ETORO': 6, 'Z.ETORO': 6, 'LRCX.ETORO': 6, 'INTU.ETORO': 6, 'ATI.ETORO': 5, 'GSAT.ETORO': 6, 'ENLT.ETORO': 6, 'TEVA.ETORO': 6, 'MBLY.ETORO': 6, 'PSN.US.ETORO': 6, 'TTD.ETORO': 6, 'ASX.ETORO': 5, 'PODD.ETORO': 6, 'CPRT.ETORO': 6, 'ZTS.ETORO': 6, 'MSTR.ETORO': 6, 'LULU.ETORO': 6, 'ASML.ETORO': 5, 'RVMD.ETORO': 6, 'COIN.ETORO': 6, 'STX.US.ETORO': 6, 'TME.ETORO': 6, 'LQDA.ETORO': 6, 'TXG.ETORO': 6, 'WBD.ETORO': 6, 'KRYS.ETORO': 6, 'TVTX.ETORO': 6, 'CPNG.ETORO': 6, 'UPST.ETORO': 6, 'JOBY.ETORO': 6, 'FRO.ETORO': 6, 'CRS.ETORO': 6, 'ENVA.ETORO': 6, 'BETA.ETORO': 5, 'RBLX.ETORO': 6, 'TWST.ETORO': 6, 'DKNG.ETORO': 6, 'KD.ETORO': 6, 'DUOL.ETORO': 6, 'MNDY.ETORO': 6, 'CVE.ETORO': 6, 'CELH.ETORO': 6, 'NATGAS.ETORO': 6, 'USDZAR.ETORO': 6, 'USDTRY.ETORO': 6, 'CAT.ETORO': 6, 'JNJ.ETORO': 6, 'PBR.ETORO': 6, 'TSLA.ETORO': 4, 'ADBE.ETORO': 5, 'WDC.ETORO': 6, 'MU.ETORO': 6, 'NVDA.ETORO': 6, 'WIX.ETORO': 6, 'PINS.ETORO': 6, 'ROP.ZU.ETORO': 6, 'ACN.ETORO': 5, 'HAL.ETORO': 6, 'GLW.ETORO': 6, 'CRM.ETORO': 6, 'MTZ.ETORO': 6, 'CMG.ETORO': 6, 'SNAP.ETORO': 6, '01211.HK.ETORO': 6, 'RHM.DE.ETORO': 6, 'PALL.ETORO': 6, 'HUBS.ETORO': 6, 'NVS.ETORO': 6, 'NOW.ETORO': 6, 'WDAY.ETORO': 6, 'TEAM.ETORO': 6, 'DOCU.ETORO': 6, 'ZS.ETORO': 6, 'CIEN.ETORO': 6, 'FSLY.ETORO': 6, 'EEFT.ETORO': 6, 'SE.ETORO': 6, 'COHR.ETORO': 6, 'RIOT.ETORO': 6, 'GOOGL.ETORO': 6, 'TSEM.ETORO': 6, 'AG.ETORO': 6, 'XPEV.ETORO': 6, 'SQM.ETORO': 6, 'GTLB.ETORO': 6, 'E.ETORO': 6, 'HUT.ETORO': 6, 'VRT.ETORO': 6, 'FICO.ETORO': 6, 'INSM.ETORO': 6, 'INSW.ETORO': 6, 'TTMI.ETORO': 6, 'POWL.ETORO': 6, 'ESLT.ETORO': 5, 'ETH.ETORO': 6, 'XRP.ETORO': 6, 'ADA.ETORO': 6, 'DOGE.ETORO': 6, 'SOL.ETORO': 6, 'SHIBxM.ETORO': 6, 'AVAX.ETORO': 6, 'PEPExM.ETORO': 6, 'ONDO.ETORO': 6, 'AERO.ETORO': 6, 'HYPE.ETORO': 5, 'TER.ETORO': 6, 'AXON.ETORO': 6, 'SMTC.ETORO': 6, 'SEI.US.ETORO': 6, 'NOK.ETORO': 6, 'GEV.ETORO': 6, 'FDX.ETORO': 6, 'AMKR.ETORO': 6, 'ELF.ETORO': 6, 'NVO.ETORO': 6, 'MRVL.ETORO': 6, 'PYPL.ETORO': 6, 'CAG.ETORO': 6, 'GIS.ETORO': 6, 'NTSK.ETORO': 5, 'ABT.US.ETORO': 6, 'CCL.ETORO': 6, 'JAZZ.ETORO': 6, 'GOOG.ETORO': 6, 'MOD.ETORO': 6, 'ARWR.ETORO': 6, 'FORM.ETORO': 6, 'DPZ.ETORO': 6, 'LSCC.ETORO': 6, 'ONTO.ETORO': 6, 'AXSM.ETORO': 6, 'VSH.ETORO': 6, 'ATRO.ETORO': 6, 'CHWY.ETORO': 6, 'TD.ETORO': 6}

**check_reward_term_variance**

- (scope=SmaCrossoverStrategy/BTC.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=5.055945): ['param_pen', 'turnover', 'fold_dispersion'].
- (scope=VolatilityBreakoutPumpStrategy/BTC.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=72456.066565): ['param_pen', 'turnover', 'fold_dispersion', 'gate_distance_penalty'].
- (scope=Rsi2ReversionStrategy/BTC.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=33484.498632): ['param_pen', 'turnover', 'fold_dispersion', 'gate_distance_penalty'].
- (scope=VwapExhaustionStrategy/BTC.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=27972.239249): ['param_pen', 'turnover', 'fold_dispersion', 'gate_distance_penalty'].
- (scope=FlashCrashReversalStrategy/BTC.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=255939.386141): ['param_pen', 'turnover', 'fold_dispersion', 'gate_distance_penalty'].
- … und 7 weitere

**check_objective_branch_coverage**

- (scope=Rsi2ReversionStrategy/BTC.ETORO): Nur 0/162 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=VwapExhaustionStrategy/BTC.ETORO): Nur 0/189 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=FlashCrashReversalStrategy/BTC.ETORO): Nur 0/306 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=TrendPullbackStrategy/BTC.ETORO): Nur 0/274 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- (scope=DonchianRegimeBreakoutStrategy/BTC.ETORO): Nur 0/138 Trials (0.00%) tragen die ordnende Qualitätsinformation (branch=='per_symbol') — unter der Schwelle (10%). Die Suche hat über den Grossteil der Auswertungen kein Ziel (Pitfall #124 — doppelt kodierte Feasibility).
- … und 4 weitere

**check_budget_execution**

- (scope=global): median(budget_executed_fraction)=0.0000 < 0.5 ueber 14 Studies — ein grosser Teil des konfigurierten Suchbudgets wird nicht ausgefuehrt (#768/#769-Fehlerklasse).

**check_gate_marginal_contribution**

- (scope=global): 2 Gate(s) ohne jeden marginalen Beitrag über eine ausreichend grosse Kohorte: {'min_trades': {'marginal_delta': 0, 'n_evaluated': 2930}, 'max_drawdown': {'marginal_delta': 0, 'n_evaluated': 2930}} — Kandidat(en) für Entfernung aus eligible_requires_all oder Neukalibrierung gegen die realisierte Verteilung.

**check_champion_seed_coverage**

- (scope=global): strategy_defaults-Anteil=100.0% > 90% — der Champion-Store-Closed-Loop (#702) ist fuer diesen Lauf nachweislich unwirksam ({'strategy_defaults': 14}).

### 5.3 Zusammenfassung

- Guard-dominierte Studies (SORTINO_GUARD_TRIPPED-Mehrheit, #823): 0
- Wirtschaftlich ruinierte Trials (EQUITY_NONPOSITIVE, #801/#825): 0
- Randlösungen mit Bounds-Vorschlag (#831): 10
- Automatisch denylistete Paare (#829/#830): 5
- Budget-deprioritisierte Paare (#830): 0
