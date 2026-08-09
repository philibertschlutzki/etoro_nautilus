# Issue #795: Refactor Time-Box Penalty to Continuous Time-Decay Function

**Status:** Open  
**Priority:** P0 (Kritisch für Trendfolge-Strategien & Reward-Gradieten)  
**Labels:** Quant, Optimizer, Reward-Design  
**Target Component:** `automation/optimizer/reward.py`, `automation/optimizer/spaces.py`, `automation/tests/test_issue_711_time_box_penalty.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund aus `combined_proposals.json` & `logs/fails/*.log`
In `combined_proposals.json` führten 61 Proposals zu einem direkten Abbruch mit `REJECT_OOS_TIMEBOX_VIOLATION`.

### Ursachenanalyse
Die aktuelle Time-Box Penalty Logik (`automation/tests/test_issue_711_time_box_penalty.py`) verwendet eine harte, stufenförmige Diskontinuität (Step Function): Sobald ein Trade länger als eine fixe Kante (z. B. $t > 24h$) offen bleibt, fällt eine scharfe Strafgebühr an.

1. **Gradienten-Klippe für den Optimizer:** Eine Stufenfunktion zerstört den lokalen Gradienten im Parameterraum. Für den TPE/GP-Sampler erscheint der Übergang von 23h59m zu 24h01m wie ein abstürzender Abgrund ("Cliff Effect").
2. **Abwürgen von Trendfolge-Gewinnen:** Trend-Strategien, die ihre Gewinne laufen lassen, werden künstlich abgeschnitten. Opportunity Costs in CHF und Overnight-Funding-Risiken skalieren stetig mit der Zeit, nicht in einer binären Stufe.

---

## 2. Mathematisches Zielmodell & Spezifikation

Ersetzung der diskreten Klippe durch eine stetig differenzierbare, exponentielle **Time-Decay Straffunktion** $Penalty\_Factor \in (0, 1.0]$.

### Mathematische Formulierung:

1. **Kontinuierliche Time-Decay Funktion:**
   $$Penalty\_Factor(t) = \exp\left( -\lambda \cdot \max(0, t - t_{soft}) \right)$$
   wobei:
   * $t$: Haltedauer des Trades in Stunden.
   * $t_{soft}$: Soft-Penalty Start-Grenzschwelle (z. B. $t_{soft} = 6.0$ Stunden).
   * $\lambda$: Exponentielle Decay-Konstante.

2. **Decay-Rate Kalibrierung ($\lambda$):**
   $\lambda$ wird so gewählt, dass bei $t = t_{max} = 24.0$ Stunden die Penalty einen Zielwert (z. B. 0.20) erreicht:
   $$\lambda = \frac{-\ln(0.20)}{t_{max} - t_{soft}} = \frac{1.6094}{18.0} \approx 0.0894$$

3. **Modifizierter Trade-Score:**
   $$Score_{adj} = Score_{raw} \cdot Penalty\_Factor(t)$$

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Anpassung in `automation/optimizer/reward.py`

```python
import math

def calculate_continuous_time_decay_penalty(
    holding_time_hours: float,
    t_soft_hours: float = 6.0,
    t_max_hours: float = 24.0,
    penalty_at_max: float = 0.20
) -> float:
    """
    Berechnet die stetige exponentielle Time-Decay Penalty.
    """
    if holding_time_hours <= t_soft_hours:
        return 1.0
        
    delta_t = holding_time_hours - t_soft_hours
    denom = max(0.1, t_max_hours - t_soft_hours)
    decay_lambda = -math.log(max(0.01, penalty_at_max)) / denom
    
    penalty_factor = math.exp(-decay_lambda * delta_t)
    return float(max(0.0, min(1.0, penalty_factor)))
```

### Parameter-Deklaration in `automation/optimizer/spaces.py`
Aufnahme des Parameters `decay_lambda` als kontinuierliche Suchraum-Variable in `spaces.py`.

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Stetigkeit verifiziert:** Die Penalty-Funktion ist in $[6h, 24h]$ strikt stetig und monoton fallend ohne Sprungstellen.
- [ ] **Gradienten-Erhaltung:** Das TPE-Optimizer-Signal bleibt im gesamten Parameterraum glatt.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_795_continuous_time_decay_penalty.py` testet:
  1. $Penalty\_Factor(t) = 1.0$ für $t \le 6h$.
  2. Exact Matching des Zielwerts bei $t = 24h$.
  3. Vollständiges Abfedern von `REJECT_OOS_TIMEBOX_VIOLATION` Klippen.
