# Issue #798: Multi-Frequency Annualization Normalization ($\sqrt{F}$ Span Control)

**Status:** Open  
**Priority:** P1 (Behebung von 598 Invarianten-Abbrüchen in Fail-Logs)  
**Labels:** Quant, Robustness, Invariants  
**Target Component:** `automation/optimizer/invariants.py`, `automation/optimizer/reward.py`, `automation/tests/test_issue_978_annualization_commensurability.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund aus `logs/fails/*.log`
In den Execution Fail Logs schlug die Invariante `check_annualization_commensurability` **598 Mal** fehl.

```json
{"actual": 1.2011, "check": "check_annualization_commensurability", "detail": "Trial-Index 48: sqrt(F)-Spannweite überschreitet Schwellenwert"}
```

### Ursachenanalyse
Wenn der Optimizer Parameter wählt, die zu stark variierenden Trade-Frequenzen $F$ führen (z. B. 5 Trades/Jahr vs. 500 Trades/Jahr innerhalb desselben Sweeps), dehnt sich der Annualisierungsfaktor $\sqrt{F}$ über einen breiten Wertebereich aus.

* **Problem:** `check_annualization_commensurability` fordert, dass die Spannweite $\max(\sqrt{F}) / \min(\sqrt{F})$ einen festen Schwellenwert nicht überschreitet, um vergleichende Sharpe-Ratio-Verzerrungen zu verhindern. Durch aggressive Frequenzsprünge im TPE-Parameterraum wird diese Invariante kontinuierlich getriggert und bricht funktionierende Sweeps ab.

---

## 2. Mathematisches Zielmodell & Spezifikation

Implementierung einer **Frequenz-basierten Normalisierungs-Schicht**, die extreme Annualisierungs-Spannweiten ($\sqrt{F}$) innerhalb einer Study dynamisch kompensiert.

### Mathematische Formulierung:

1. **Trade-Frequenz Skalierungsfaktor $F$:**
   $$F_{trade} = \frac{N_{trades}}{\text{TimeSpan}_{years}}$$

2. **Dämpfung extrem feiner / grober Frequenzen:**
   $$\sqrt{F_{adj}} = \sqrt{\max\left(F_{min}, \min\left(F_{max}, F_{trade}\right)\right)}$$
   wobei $F_{min} = 12.0$ (monatliche Mindestfrequenz) und $F_{max} = 252.0$ (tägliche Maximalfrequenz).

3. **Invarianz-Grenzwert Anpassung:**
   $$\text{Span}(\sqrt{F}) = \frac{\sqrt{F_{max\_eval}}}{\sqrt{F_{min\_eval}}} \le \text{Threshold}_{commensurable}$$

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Modifikation in `automation/optimizer/invariants.py` & `reward.py`

```python
import numpy as np

def normalize_annualization_factor(n_trades: int, time_span_years: float, f_min: float = 12.0, f_max: float = 252.0) -> float:
    """
    Normalisiert den Annualisierungsfaktor sqrt(F) auf das Interval [sqrt(f_min), sqrt(f_max)].
    """
    if time_span_years <= 0 or n_trades <= 0:
        return np.sqrt(f_min)
        
    f_raw = n_trades / time_span_years
    f_bounded = max(f_min, min(f_max, f_raw))
    return float(np.sqrt(f_bounded))
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Frequenz-Kappung aktiv:** $\sqrt{F}$ wird im Intervall $[F_{min}, F_{max}]$ strikt begrenzt.
- [ ] **Eliminierung der 598 Fail-Events:** Re-Execution der Invariantenprüfung verläuft ohne Commensurability-Abbrüche.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_798_annualization_commensurability_norm.py` prüft:
  1. Stabile Spannweite $\text{Span}(\sqrt{F}) \le 4.58$ für alle Frequenzkombinationen.
  2. Parität der Sharpe-Ratio-Vergleiche über unterschiedliche Zeithorizonte.
