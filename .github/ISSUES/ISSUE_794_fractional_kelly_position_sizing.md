# Issue #794: Dynamic Position Sizing via Fractional Kelly Criterion zur Ertrags-Maximierung

**Status:** Open  
**Priority:** P1 (Haupttreiber für maximale Erträge im Live-Handel)  
**Labels:** Quant, Money-Management, Live-Execution, Profit-Optimization  
**Target Component:** `automation/fractional_trading.py`, `automation/momentum_ls_allocator.py`, `automation/tests/test_allocator.py`  

---

## 1. Symptomatik & Ertrags-Potenzial

### Baseline-Befund
Die aktuelle Allokation nutzt statische Positionsgrössen (z. B. 1% bis 2% festes Kapital pro Trade). 

### Quant-Analyse des Ertragsverlusts:
In der Theorie des optimalen Kapitalwachstums (Kelly, 1956; Breiman, 1961) ist die logarithmische Vermögenswachstumsrate $g(f)$ bei fester Positionsgrösse stark sub-optimal. 

1. **Unterallokation bei Top-Performance:** Strategien mit hoher Win Rate ($p > 0,60$) und verifiziertem OOS-Edge werden mit demselben geringen Kapital gewichtet wie schwache Grenzstrategien.
2. **Ruined-Risk bei statischem Overbetting:** Ohne stetige Anpassung an das Payoff-Verhältnis $b/a$ verfehlt die Strategie die maximale Kapital-Zinseszins-Kurve in CHF.

---

## 2. Mathematisches Zielmodell (Ertrags-Maximiertes Kelly)

Integration einer dynamischen Kapitalallokation basierend auf dem **Fractional Kelly Kriterium** unter Nutzung der verifizierten Out-of-Sample (Holdout) Metriken.

### Mathematische Formulierung:

1. **Kelly-Formel für ungleiche Payoffs:**
   $$f^* = \frac{p}{a} - \frac{1-p}{b}$$
   wobei:
   * $p$: OOS Win Rate ($p = \text{oos\_win\_rate}$)
   * $b = \frac{\overline{W}_{CHF}}{\text{Capital}_{CHF}}$: Relativer Durchschnittsgewinn pro Trade
   * $a = \frac{\overline{L}_{CHF}}{\text{Capital}_{CHF}}$: Relativer Durchschnittsverlust pro Trade

2. **Fractional Multiplier $k_{kelly}$ & Volatilitäts-Skalierung:**
   $$f_{alloc} = k_{kelly} \cdot f^* \cdot \left(\frac{\sigma_{target}}{\sigma_{asset}}\right)$$
   wobei $k_{kelly} \in [0.25, 0.50]$ (Half-Kelly / Quarter-Kelly) das Overbetting-Risiko eliminiert.

3. **Hard CHF Exposure Cap & Drawdown Feedback Control:**
   $$f_{final} = \min\left(f_{max\_exposure}, \max\left(0, f_{alloc} \cdot \psi(DD)\right)\right)$$
   wobei $\psi(DD) = \max\left(0.2, 1.0 - \frac{DD_{current}}{DD_{max}}\right)$ ein automatischer Feedback-Dämpfer bei aktuellen Portfolio-Drawdowns ist.

---

## 3. Umsetzungs-Spezifikation (Code-Ebene)

### Modifikation in `automation/fractional_trading.py`

```python
import numpy as np

def calculate_fractional_kelly_size(
    win_rate: float,
    avg_win_chf: float,
    avg_loss_chf: float,
    total_capital_chf: float,
    fractional_multiplier: float = 0.5,
    max_exposure_fraction: float = 0.15,
    current_drawdown_frac: float = 0.0,
    max_drawdown_limit: float = 0.20
) -> float:
    """
    Berechnet die ertragsoptimierte Positionsgrösse f* nach dem Fractional Kelly Kriterium
    mit Drawdown-Feedback-Schleife und Hard-Exposure Cap.
    """
    if total_capital_chf <= 0 or avg_loss_chf <= 0 or avg_win_chf <= 0:
        return 0.0
        
    p = win_rate
    q = 1.0 - p
    
    a = avg_loss_chf / total_capital_chf
    b = avg_win_chf / total_capital_chf
    
    if a <= 0 or b <= 0:
        return 0.0
        
    f_star = (p / a) - (q / b)
    if f_star <= 0:
        return 0.0
        
    # Drawdown Feedback Control
    dd_ratio = min(1.0, current_drawdown_frac / max(0.01, max_drawdown_limit))
    psi_dd = max(0.2, 1.0 - dd_ratio)
    
    f_alloc = f_star * fractional_multiplier * psi_dd
    f_final = min(max_exposure_fraction, f_alloc)
    
    return float(f_final)
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Kelly-Calculator produktiv:** `calculate_fractional_kelly_size` ist im Allocator (`automation/momentum_ls_allocator.py`) verdrahtet.
- [ ] **Ertrags-Maximierung nachgewiesen:** Backtest-Vergleich zeigt mindestens 25% höhere Zinseszins-Rendite in CHF gegenüber statischem Sizing bei gleichem Max Drawdown.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_794_fractional_kelly_position_sizing.py` prüft:
  1. $f^* = 0.0$ bei negativer Expectancy.
  2. Greifen des Drawdown-Dämpfers $\psi(DD)$.
  3. Strikte Einhaltung des Hard-Exposure Caps (max 15%).
