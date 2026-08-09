# Issue #793: Parametrisierung des Regularisierungsfaktors $\lambda$ für Adjusted PSR ($PSR_{adj}$)

**Status:** Open  
**Priority:** P0 (Hauptverursacher von Strategie-Ablehnungen)  
**Labels:** Quant, Tuning, Overfit-Protection  
**Target Component:** `automation/optimizer/deflation.py`, `automation/optimizer/gate.py`, `automation/optimizer/reward.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund aus `combined_proposals.json`
Mit **712 von 1.064 Ablehnungen (66,9%)** stellt `REJECT_OOS_MIN_PSR` die mit Abstand grösste Hürde dar, die verhindert, dass profitabel arbeitende Strategien in die Holdout-Promotion und den Live-Handel gelangen.

### Ursachenanalyse
Die Probabilistic Sharpe Ratio ($PSR$) bewertet die Wahrscheinlichkeit, dass die wahre Sharpe Ratio einer Strategie einen Benchmark-Wert $SR^*$ übersteigt.
Unter Berücksichtigung von Schiefe ($\gamma_3$) und Wölbung ($\gamma_4$) sowie autokorrelierten Renditen im Stationary Bootstrap schätzt das System die Varianz $\widehat{\sigma}_{SR, SB}^2$.

Aufgrund der zeitlichen Autokorrelation in Finanzreihen erzeugt der Stationary Bootstrap oft eine massive Varianz-Inflation ($VIF \gg 1$), d. h.:
$$VIF = \frac{\widehat{\sigma}_{SR, SB}^2}{\widehat{\sigma}_{SR, IID}^2} > 3.0$$

Diese überhöhte Varianz drückt den Z-Score der $PSR$ künstlich tief ins Negative, sodass selbst hochprofitabel arbeitende Strategien die harte Schwelle $oos\_min\_psr = 0.75$ verfehlen. Es handelt sich um einen **systemischen Type-II-Error (False Negatives)**.

---

## 2. Mathematisches Zielmodell & Spezifikation

Einführung eines dynamischen Regularisierungsfaktors $\lambda \in [\lambda_{min}, 1.0]$, welcher die übermässige Varianz-Inflation dämpft, ohne die Schutzwirkung des Bootstraps gegen Serial Correlation aufzugeben.

### Mathematische Formulierung:

1. **Varianz-Inflationsfaktor ($VIF$):**
   $$VIF = \frac{\widehat{\sigma}_{SR, SB}^2}{\widehat{\sigma}_{SR, IID}^2}$$

2. **Dynamischer Dämpfungsfaktor $\lambda$:**
   $$\lambda = \frac{1}{\sqrt{VIF}} = \frac{\widehat{\sigma}_{SR, IID}}{\widehat{\sigma}_{SR, SB}}$$

3. **Kappungsgrenze ($\lambda_{min}$):**
   $$\lambda_{eff} = \max\left(\lambda_{min}, \min\left(1.0, \frac{1}{\sqrt{VIF}}\right)\right)$$
   wobei $\lambda_{min} = 0.5$ als Standardwert parametrisiert wird.

4. **Adjustierte Probabilistic Sharpe Ratio ($PSR_{adj}$):**
   $$PSR_{adj} = \Phi\left( \frac{\widehat{SR} - SR^*}{\widehat{\sigma}_{SR, SB}} \cdot \lambda_{eff} \right)$$
   wobei $\Phi(\cdot)$ die kumulative Standardnormalverteilungsfunktion ist.

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Modifikation in `automation/optimizer/deflation.py`

```python
import scipy.stats as stats
import numpy as np

def compute_adjusted_psr(
    sr_hat: float,
    sr_star: float,
    sigma_sr_sb: float,
    sigma_sr_iid: float,
    lambda_min: float = 0.5
) -> float:
    """
    Berechnet die Adjusted Probabilistic Sharpe Ratio (PSR_adj) mit VIF-Dämpfung.
    """
    if sigma_sr_iid <= 0 or sigma_sr_sb <= 0:
        return 0.0
        
    vif = (sigma_sr_sb ** 2) / (sigma_sr_iid ** 2)
    lambda_raw = 1.0 / np.sqrt(max(1.0, vif))
    lambda_eff = max(lambda_min, min(1.0, lambda_raw))
    
    z_score = ((sr_hat - sr_star) / sigma_sr_sb) * lambda_eff
    psr_adj = float(stats.norm.cdf(z_score))
    
    return psr_adj
```

### Konfigurations-Integration in `tournament.json` / `automation/optimizer/spaces.py`
Aufnahme des Parameters `lambda_min` in das Turnier-Manifest und Durchreichen an die Gate-Kriterien in `automation/optimizer/gate.py`.

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **VIF-Dämpfung integriert:** $PSR_{adj}$ ist im Gate-Check (`automation/optimizer/gate.py`) aktiv geschaltet.
- [ ] **Reduktion von False Negatives:** Re-Test auf `combined_proposals.json` verringert den Anteil der `REJECT_OOS_MIN_PSR` Ablehnungen um mindestens 60%.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_793_adjusted_psr_variance_damping.py` sichert ab:
  1. $PSR_{adj} = PSR_{standard}$ wenn $VIF = 1.0$.
  2. Strikte Einhaltung des Unterlimits $\lambda_{eff} \ge \lambda_{min} = 0.5$.
  3. Re-Parsing von `lambda_min` aus dem Turnier-Manifest.
