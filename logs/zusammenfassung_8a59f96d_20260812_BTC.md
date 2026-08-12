# Sweep-Zusammenfassung 8a59f96d_20260812_BTC


## 1. Ergebnis in einem Satz

1 Studies, 0 Promotionen — kein Parametervektor hat die Holdout-Validierung bestanden. Es gibt kein deploybares Ergebnis aus diesem Lauf.

## 2. Monetäres Ergebnis

### 2.1 Promotionskandidaten (Status READY_FOR_PR / PROMOTE_GLOBAL_DEFAULT) — noch NICHT deploybar

**Kein deploybares Ergebnis aus diesem Lauf.** Kein Kandidat hat die Holdout-Validierung bestanden — alle folgenden Zahlen in Abschnitt 2.2 sind ABGELEHNTE, NICHT handelbare Kandidaten.

### 2.2 Bester abgelehnter Kandidat je Strategie (NICHT deploybar)

**Diese Kandidaten sind ausdrücklich NICHT deploybar** — der Backtest-Ertrag ist eine Simulationszahl, kein handelbares Ergebnis.

| Strategie | Symbol | Holdout-Return (simuliert) | Ablehnungsgrund |
|---|---|---:|---|
| ComboTrendVwapStrategy | BTC.ETORO | -1.1 % | REJECTED_ON_HOLDOUT |

### 2.3 Vergleich gegen Buy & Hold je Symbol

| Strategie | Symbol | Strategie-Return | Buy&Hold-Return | Excess | Zeit im Markt | Excess/Exposure | Vorzeichen |
|---|---|---:|---:|---:|---:|---:|---|
| ComboTrendVwapStrategy | BTC.ETORO | -1.1 % | 6.7 % | -7.8 % | 73.2 % | -10.6 % | negativ (unter Buy & Hold) |


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
| ComboTrendVwapStrategy | 0.42 h (1506 s) | 0.42 h (1506 s) | 1 |

### 3.3 Gelaufene vs. budgetierte Trials

- Median Budgetausführung: 0.0 % (p10: 0.0 %, n=1 Studies)
- Trials gesamt: 0 von 280 budgetiert

### 3.4 Verlorene Zeit: abgebrochene Studies

- EXCEPTION: 1 Studies

Keine Barriere-Wartezeit-Telemetrie in diesem Report (Pre-#851-Lauf, leere Kohorte, oder jedes Symbol hatte nur eine Strategie-Study).

## 4. Trades mit der längsten Haltedauer

**Scope-Hinweis:** diese Sektion listet die längste beobachtete Haltedauer JE STUDY (Strategie/Symbol, Maximum über alle OOS-evaluierten Trials), NICHT einzelne Trades mit Entry-/Exit-Zeitstempel — siehe Modul-Docstring für die Begründung dieser Scope-Entscheidung (Katalog #832 Fix Punkt 1).

| Strategie | Symbol | Max. Haltedauer | P95 Haltedauer |
|---|---|---:|---:|
| ComboTrendVwapStrategy | BTC.ETORO | 24.00 h (86400 s) | 24.00 h (86400 s) |

## 5. Auffälligkeiten

### 5.1 Übersicht — Invarianten-FAILs (4)

| Check | FAILs | betroffene Studies | Schweregrad |
|---|---:|---:|---|
| check_annualization_commensurability | 1 | 1 | high |
| check_budget_execution | 1 | 1 | medium |
| check_reward_term_variance | 1 | 1 | medium |
| check_champion_seed_coverage | 1 | 1 | low |

### 5.2 Details

**check_annualization_commensurability**

- (scope=ComboTrendVwapStrategy/BTC.ETORO): Trial-Index 23: sqrt(F)-Spannweite innerhalb des Trials beträgt Faktor 1.3 (> 1.05) — die Fold-Annualisierung ist über die Folds DESSELBEN Trials nicht kommensurabel; Fold-Mittel/-Median des annualisierten Sortino mitteln inkommensurable Grössen (Pitfall #310).

**check_budget_execution**

- (scope=global): median(budget_executed_fraction)=0.0000 < 0.5 ueber 1 Studies — ein grosser Teil des konfigurierten Suchbudgets wird nicht ausgefuehrt (#768/#769-Fehlerklasse).

**check_reward_term_variance**

- (scope=ComboTrendVwapStrategy/BTC.ETORO): Reward-Term(e) praktisch inert (std < 1.00% von reward_std=5.480633): ['param_pen', 'turnover', 'fold_dispersion'].

**check_champion_seed_coverage**

- (scope=global): strategy_defaults-Anteil=100.0% > 90% — der Champion-Store-Closed-Loop (#702) ist fuer diesen Lauf nachweislich unwirksam ({'strategy_defaults': 1}).

### 5.3 Zusammenfassung

- Guard-dominierte Studies (SORTINO_GUARD_TRIPPED-Mehrheit, #823): 0
- Wirtschaftlich ruinierte Trials (EQUITY_NONPOSITIVE, #801/#825): 0
- Randlösungen mit Bounds-Vorschlag (#831): 9
- Automatisch denylistete Paare (#829/#830): 3
- Budget-deprioritisierte Paare (#830): 0
