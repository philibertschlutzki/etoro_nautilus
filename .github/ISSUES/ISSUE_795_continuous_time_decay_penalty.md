# Issue #795: Refactor Time-Box Penalty to Continuous Time-Decay Function

**Status:** Open  
**Priority:** P0 (Kritisch für Trendfolge-Strategien & Reward-Gradienten)  
**Labels:** Quant, Optimizer, Reward-Design, Profit-Optimization  
**Target Component:** `automation/optimizer/reward.py`, `automation/optimizer/spaces.py`, `automation/tests/test_issue_711_time_box_penalty.py`  

---

## 1. Symptomatik & Empirische Problemanalyse

### Baseline-Befund aus `combined_proposals.json`
In `combined_proposals.json` führten **61 Proposals** zu einem direkten Abbruch mit `REJECT_OOS_TIMEBOX_VIOLATION`.

### Quant-Analyse des Ertragsverlusts:
Die bisherige Time-Box Logik verwendet eine harte Stufenfunktion (Step Function): Sobald ein Trade länger als $t_{max} = 24h$ läuft, verfällt eine scharfe Pauschalstrafe.

1. **Zerstörung der Gradienten-Information:** Eine Stufenfunktion besitzt überall die Ableitung $0$ und an der Kante eine undefinierte Sprungstelle. Der Bayesian TPE/GP-Sampler kann im Parameterraum kein geglättetes Oberflächenmodell lernen ("Cliff Effect").
2. **Abwürgen hochprofitabler Trends:** Reale Markt-Trends dauern oft länger als 24 Stunden. Eine harte Klippe zwingt das System, Positionen vorzeitig zu schliessen und entzieht der Strategie den grössten Teil ihres Reingewinns in CHF. Opportunity Costs und Overnight-Funding-Gebühren verlaufen stetig, nicht in Stufen.

---

## 2. Mathematisches Zielmodell (Continuous Exponential Decay)

Ersetzung der diskreten Klippe durch eine stetig differenzierbare, exponentielle **Time-Decay Straffunktion** $Penalty(t) \in (0, 1.0]$.

### Mathematische Formulierung:

1. **Stetiges Exponentielles Decay-Modell:**
   $$Penalty(t) = \exp\left( -\lambda_{decay} \cdot \max(0, t - t_{soft}) \right)$$
   wobei:
   * $t$: Haltedauer des Trades in Stunden.
   * $t_{soft}$: Beginn der Opportunity-Cost Bepreisung (z. B. $t_{soft} = 6.0$ Stunden).
   * $\lambda_{decay}$: Kalibrierte Decay-Konstante.

2. **Exakte Kalibrierung an Ziel-Strafe bei $t_{max}$:**
   $$\lambda_{decay} = \frac{-\ln(Penalty_{target})}{t_{max} - t_{soft}}$$
   Für $Penalty_{target} = 0.20$ bei $t_{max} = 24.0h$ und $t_{soft} = 6.0h$:
   $$\lambda_{decay} = \frac{-\ln(0.20)}{18.0} \approx 0.0894$$

3. **Integrierte Ertrags-Bewertung in CHF:**
   $$EV_{adj}(t) = EV_{raw} \cdot Penalty(t) - \text{Funding\_Costs}_{CHF}(t)$$

---

## 3. Umsetzungs-Spezifikation (Code-Ebene)

### Modifikation in `automation/optimizer/reward.py`

```python
import math

def calculate_continuous_time_decay_penalty(
    holding_time_hours: float,
    t_soft_hours: float = 6.0,
    t_max_hours: float = 24.0,
    penalty_at_max: float = 0.20
) -> float:
    """
    Berechnet die stetige, differenzierbare Time-Decay Penalty zur Erhaltung glatter
    Optimizer-Gradienten und Verhinderung des vorzeitigen Abwürgens von Trends.
    """
    if holding_time_hours <= t_soft_hours:
        return 1.0
        
    delta_t = holding_time_hours - t_soft_hours
    denom = max(0.1, t_max_hours - t_soft_hours)
    decay_lambda = -math.log(max(0.01, penalty_at_max)) / denom
    
    penalty_factor = math.exp(-decay_lambda * delta_t)
    return float(max(0.0, min(1.0, penalty_factor)))
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Stetigkeit & Differenzierbarkeit:** Die Straffunktion ist auf $[0, \infty)$ stetig ohne Sprungstellen.
- [ ] **Eliminierung der 61 Timebox-Abbrüche:** 0 Abbrüche durch harte `REJECT_OOS_TIMEBOX_VIOLATION` Klippen.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_795_continuous_time_decay_penalty.py` verifiziert:
  1. Smooth Gradientenverlauf im TPE-Optimizer.
  2. Exakte Übereinstimmung mit dem Kalibrierungspunkt bei $t = 24h$.
  3. Höhere Ertragsausbeute bei langanhaltenden Trendfolge-Positionen.
