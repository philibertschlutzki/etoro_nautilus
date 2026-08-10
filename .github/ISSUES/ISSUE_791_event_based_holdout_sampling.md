# Issue #791: Transformation auf Event-basiertes Holdout-Sampling zur Ertrags-Maximierung

**Status:** Open  
**Priority:** P0 (Kritisch für Holdout-Stabilität & Strategie-Promotion)  
**Labels:** Quant, Architecture, Holdout, Profit-Optimization  
**Target Component:** `automation/backtest_runner.py`, `automation/optimizer/cpcv.py`, `automation/tests/test_issue_448_oos_split.py`  

---

## 1. Symptomatik & Empirische Problemanalyse

### Baseline-Befund aus `combined_proposals.json`
Die kalendarische (DateTime-basierte) Split-Logik führt zu einer massiven Verzerrung bei der Trennung von In-Sample (IS) und Out-of-Sample (OOS) Holdout-Daten. In der Evaluierung von 1.064 Proposals führte dieses Verfahren bei **954 Proposals (`HOLDOUT_NO_ELIGIBLE_TRIALS`)** und **13 Proposals (`REJECT_OOS_WINDOW_UNREACHABLE`)** zum Scheitern.

### Systemische Ursache für Ertragsverlust im Real-Trading:
1. **Asymmetrische Trade-Frequenzen:** Ein festes Zeitfenster (z. B. 6 Monate) erzeugt bei Hochfrequenz-Strategien hunderte Trades ($N_{oos} > 500$), bei Niederfrequenz-Aktien-/Index-Strategien jedoch nur vereinzelt 5–15 Trades ($N_{oos} < 15$).
2. **Statistische Invarianz-Verletzung:** Das Konfidenzintervall der Out-of-Sample Sharpe Ratio $\widehat{SR}_{OOS}$ skaliert mit $\frac{1}{\sqrt{N_{oos}}}$. Bei kleinen $N_{oos}$ oszilliert die Varianz extrem. Hochprofitables Alpha wird durch stochastisches Rauschen als "nicht signifikant" verworfen (Type-II Error), während ertragsschwache High-Frequency-Strategien zufällig promoted werden.

---

## 2. Mathematisches Zielmodell (Ertrags-Maximiert)

Restrukturierung der Holdout-Generierung von chronologischem Zeit-Slicing auf ein **transaktionales, Event-basiertes Slicing**, das eine mathematisch konstante Stichproben-Power $N_{oos} \ge 100$ garantiert.

### Formale Definition:

Sei $\mathcal{E} = \{e_1, e_2, \dots, e_N\}$ die chronologisch geordnete Menge aller realisierten Trade-Events eines Backtests.

1. **Event-Index Split Rule:**
   $$N_{oos} = \max\left(100, \left\lceil \gamma \cdot N \right\rceil\right) \quad \text{mit } \gamma = 0.30$$
   $$N_{IS} = N - N_{oos}$$

2. **Holdout Event Slicing:**
   $$\mathcal{E}_{IS} = \{e_1, e_2, \dots, e_{N_{IS}}\}$$
   $$\mathcal{E}_{OOS} = \{e_{N_{IS}+1}, e_{N_{IS}+2}, \dots, e_N\}$$

3. **Strikte Disjunktheit & Parität:**
   $$\mathcal{E}_{IS} \cap \mathcal{E}_{OOS} = \emptyset$$
   $$\text{Var}\left(\widehat{SR}_{OOS}\right) = \frac{1 + \frac{1}{2}\widehat{SR}_{OOS}^2 - \gamma_3 \widehat{SR}_{OOS} + \frac{\gamma_4 - 1}{4}\widehat{SR}_{OOS}^2}{N_{oos}} \equiv \mathcal{O}\left(\frac{1}{100}\right)$$

---

## 3. Umsetzungs-Spezifikation (Code-Ebene)

### Modifikation in `automation/backtest_runner.py`

```python
import numpy as np

def generate_event_based_holdout_split(
    trade_events: list[dict],
    min_oos_trades: int = 100,
    oos_fraction: float = 0.30
) -> tuple[list[dict], list[dict]]:
    """
    Erzeugt einen strikt event-basierten Holdout-Split mit konstanter statistischer Power.
    """
    total_trades = len(trade_events)
    required_min = int(min_oos_trades / oos_fraction)
    
    if total_trades < required_min:
        raise ValueError(
            f"Insuffiziente Trade-Anzahl für valide OOS-Evaluierung: "
            f"{total_trades} Trades vorhanden, mindestens {required_min} benötigt."
        )
        
    n_oos = max(min_oos_trades, int(np.ceil(total_trades * oos_fraction)))
    split_idx = total_trades - n_oos
    
    is_trades = trade_events[:split_idx]
    oos_trades = trade_events[split_idx:]
    
    return is_trades, oos_trades
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Event-Indexing aktiv:** In `automation/backtest_runner.py` und `automation/optimizer/cpcv.py` wird OOS strikt über Trade-Indizes ($N_{oos} \ge 100$) abgebildet.
- [ ] **Garantierte Stichproben-Power:** Keines der 76 Symbole verfehlt die OOS-Mindesttradeanzahl aufgrund variabler Zeitfenster.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_791_event_based_holdout_sampling.py` prüft:
  1. $N_{oos} \ge 100$ für alle Assetklassen.
  2. Mathematische Fehlerfreiheit der Konfidenzintervall-Stabilisierung.
  3. Vollständige Vermeidung von `REJECT_OOS_WINDOW_UNREACHABLE`.
