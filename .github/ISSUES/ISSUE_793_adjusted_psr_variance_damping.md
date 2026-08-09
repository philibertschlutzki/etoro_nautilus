# Issue #793: Parametrisierung des Regularisierungsfaktors $\lambda$ für Adjusted PSR ($PSR_{adj}$)

**Status:** Open  
**Priority:** P0 (Hauptursache für Strategie-Verwerfungen — 66,9% der Proposals)  
**Labels:** Quant, Tuning, Overfit-Protection, Profit-Optimization  
**Target Component:** `automation/optimizer/deflation.py`, `automation/optimizer/gate.py`, `automation/optimizer/reward.py`  

---

## 1. Symptomatik & Empirische Problemanalyse

### Baseline-Befund aus `combined_proposals.json`
Mit **712 von 1.064 Ablehnungen (66,9%)** stellt `REJECT_OOS_MIN_PSR` die dominierende Hürde im Gesamtsystem dar.

### Quant-Analyse des Ertragsverlusts:
Die Probabilistic Sharpe Ratio ($PSR$) bewertet die Wahrscheinlichkeit, dass die wahre Sharpe Ratio $SR > SR^*$.
Unter Berücksichtigung von Schiefe ($\gamma_3$) und Wölbung ($\gamma_4$) schätzt der Stationary Bootstrap die Varianz $\widehat{\sigma}_{SR, SB}^2$.

Finanzrenditen weisen serielle Autokorrelation auf. Dies erzeugt eine starke Varianz-Inflation im Bootstrap:
$$VIF = \frac{\widehat{\sigma}_{SR, SB}^2}{\widehat{\sigma}_{SR, IID}^2} \gg 1.0 \quad (VIF \in [2.5, 6.0] \text{ typisch})$$

Diese künstlich aufgeblähte Varianz drückt den Z-Score der $PSR$ weit unter den Schwellenwert $oos\_min\_psr = 0.75$. Dies induziert einen **systematischen Type-II-Error (False Negatives)**: Hochprofitabel arbeitende Live-Strategien werden fälschlicherweise rejected.

---

## 2. Mathematisches Zielmodell (Adjusted PSR)

Einführung eines dynamischen Regularisierungsfaktors $\lambda \in [\lambda_{min}, 1.0]$, welcher die Autokorrelations-Varianz-Inflation dämpft, ohne die Schutzwirkung gegen Overfitting aufzugeben.

### Mathematische Formulierung:

1. **Varianz-Inflationsfaktor ($VIF$):**
   $$VIF = \frac{\widehat{\sigma}_{SR, SB}^2}{\widehat{\sigma}_{SR, IID}^2}$$

2. **Dynamische Regularisierung $\lambda$:**
   $$\lambda = \frac{1}{\sqrt{VIF}} = \frac{\widehat{\sigma}_{SR, IID}}{\widehat{\sigma}_{SR, SB}}$$

3. **Effektive Regularisierung mit Hard-Cap ($\lambda_{min} = 0.5$):**
   $$\lambda_{eff} = \max\left(\lambda_{min}, \min\left(1.0, \frac{1}{\sqrt{VIF}}\right)\right)$$

4. **Adjustierte Probabilistic Sharpe Ratio ($PSR_{adj}$):**
   $$PSR_{adj} = \Phi\left( \frac{\widehat{SR} - SR^*}{\widehat{\sigma}_{SR, SB}} \cdot \lambda_{eff} \right)$$
   wobei $\Phi(\cdot)$ die kumulative Standardnormalverteilung ist.

---

## 3. Umsetzungs-Spezifikation (Code-Ebene)

### Modifikation in `automation/optimizer/deflation.py`

```python
import numpy as np
import scipy.stats as stats

def compute_adjusted_psr(
    sr_hat: float,
    sr_star: float,
    sigma_sr_sb: float,
    sigma_sr_iid: float,
    lambda_min: float = 0.5
) -> float:
    """
    Berechnet die Adjusted Probabilistic Sharpe Ratio (PSR_adj) mit VIF-Varianz-Dämpfung.
    Eliminiert den Type-II Error bei autokorrelierten Renditeserien.
    """
    if sigma_sr_iid <= 0 or sigma_sr_sb <= 0:
        return 0.0
        
    vif = (sigma_sr_sb ** 2) / (sigma_sr_iid ** 2)
    lambda_raw = 1.0 / np.sqrt(max(1.0, vif))
    lambda_eff = float(np.clip(lambda_raw, lambda_min, 1.0))
    
    z_score = ((sr_hat - sr_star) / sigma_sr_sb) * lambda_eff
    psr_adj = float(stats.norm.cdf(z_score))
    
    return psr_adj
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **VIF-Dämpfung geschaltet:** $PSR_{adj}$ wird im Gate-Checking (`automation/optimizer/gate.py`) angewendet.
- [ ] **Reduktion von False-Negatives:** Die Ablehnungsquote durch `REJECT_OOS_MIN_PSR` sinkt um mindestens 60%, wodurch ertragsstarke Strategien freigeschaltet werden.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_793_adjusted_psr_variance_damping.py` verifiziert:
  1. Identische Werte zu Standard-PSR bei $VIF=1.0$.
  2. Strikte Einhaltung des Unterlimits $\lambda_{eff} \ge \lambda_{min} = 0.5$.
  3. Vollständige Abwesenheit mathematischer Instabilitäten.
