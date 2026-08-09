# Issue #791: Transformation auf Event-basiertes Holdout-Sampling

**Status:** Open  
**Priority:** P0 (Blocker for Holdout Stability)  
**Labels:** Quant, Architecture, Holdout  
**Target Component:** `automation/backtest_runner.py`, `automation/optimizer/cpcv.py`, `automation/tests/test_issue_448_oos_split.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund aus `combined_proposals.json`
In der aktuellen Optimierungsarchitektur basieren Holdout-Splits auf kalendarischen Datumsfenstern (`DateTime`-basiertes Slicing). Dies führt zu stark schwankenden Trade-Zahlen ($N_{trades}$) zwischen verschiedenen Anlageklassen (z. B. Hochfrequenz-Crypto vs. Niederfrequenz-Aktien/Indices). 

* **Konsequenz 1:** Bei n=1064 Evaluierungen in `combined_proposals.json` wiesen 13 Proposals den Status `REJECT_OOS_WINDOW_UNREACHABLE` auf, während 954 Proposals (`HOLDOUT_NO_ELIGIBLE_TRIALS`) an ungenügender statistischer Power der OOS-Stichprobe scheiterten.
* **Konsequenz 2:** In Märkten mit niedriger Volatilität oder langen Halteperioden fallen im festen Zeitfenster (z. B. 6 Monate) nur sehr wenige Trades an ($N_{oos} < 20$). Das Konfidenzintervall um die Performancemetriken (Sharpe Ratio, Win Rate, Expectancy) oszilliert extrem, wodurch valide Strategien fälschlicherweise verworfen werden (Type-II Error).

---

## 2. Mathematisches Zielmodell & Spezifikation

Anstelle eines festen Zeitfensters $[T_{start}, T_{end}]$ wird das Holdout-Set auf ein **transaktionales (Event-basiertes) Slicing** umgestellt, bei dem die Anzahl der realisierten Trade-Events $N_{oos}$ strikt fixiert wird.

### Mathematische Definition:
Sei $E = \{e_1, e_2, \dots, e_N\}$ die chronologisch geordnete Menge aller abgeschlossenen Trade-Events einer In-Sample- und Out-of-Sample-Simulation.

1. **Event-Index Split:**
   $$N_{IS} = N - N_{oos}$$
   wobei $N_{oos} \ge 100$ als statistisches Minimum gefordert ist.

2. **Holdout Event Slice:**
   $$E_{OOS} = \{e_{N_{IS}+1}, e_{N_{IS}+2}, \dots, e_N\}$$

3. **Invarianz-Bedingung:**
   Für jedes Instrument $i$ und jede Strategie $s$ gilt:
   $$\text{Length}(E_{OOS}) = N_{oos} \equiv \text{const} \ge 100$$

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Step 1: Anpassung des Holdout-Generators in `automation/backtest_runner.py`
Entfernung der DateTime-basierten Slicing-Logik und Implementierung des Event-Index-Slicings auf dem `NautilusTrader` Data Catalog.

```python
def generate_event_based_holdout_split(trades: list[dict], min_oos_events: int = 100) -> tuple[list[dict], list[dict]]:
    """
    Spaltet eine Liste von Trade-Events in IS und OOS basierend auf Event-Indizes.
    Guarantees N_oos >= min_oos_events.
    """
    total_trades = len(trades)
    if total_trades < min_oos_events * 2:
        raise ValueError(f"Insuffiziente Trade-Anzahl: {total_trades} Trades vorhanden, mindestens {min_oos_events * 2} benötigt.")
    
    split_idx = total_trades - min_oos_events
    is_trades = trades[:split_idx]
    oos_trades = trades[split_idx:]
    
    return is_trades, oos_trades
```

### Step 2: Entkopplung der Data-Catalog Integrationspunkte
In `automation/optimizer/cpcv.py` wird die Erzeugung der Test-Folds von zeitbasierten Maschen auf Event-Index-Gitter transformiert.

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Event-Slicing verifiziert:** In `automation/backtest_runner.py` sind alle DateTime-Splits für Holdout-Evaluierungen durch Event-Index-Splits ersetzt.
- [ ] **Strikte Stichprobengrösse:** $N_{oos} \ge 100$ wird in allen Folds und Holdout-Tests eingehalten.
- [ ] **Unit-Test Coverage:** Neuer Unit-Test `automation/tests/test_issue_791_event_based_holdout_sampling.md` sichert ab:
  1. Invariante $N_{oos} = 100$ unabhängig von der Zeitspanne des Backtests.
  2. Keine Überlappung von Trade-ID-Mengen zwischen In-Sample und Holdout.
  3. Stabile Varianzschätzung der Out-of-Sample Sharpe Ratio.
