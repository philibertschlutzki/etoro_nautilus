# Issue #792: Adaptive Blocklängen-Kalibrierung (Politis & White 2004) für Stationary Bootstrap

**Status:** Open  
**Priority:** P0 (Kritisch für Bootstrap-Stabilität & Tail-Risk Bewertung)  
**Labels:** Quant, Core-Math, Robustness, Profit-Optimization  
**Target Component:** `automation/optimizer/bootstrap.py`, `automation/optimizer/deflation.py`, `automation/tests/test_issue_599_bootstrap_ci.py`  

---

## 1. Symptomatik & Empirische Problemanalyse

### Baseline-Befund aus `combined_proposals.json`
Bei **165 Proposals (15,5% aller Evaluierungen)** wurde die Strategie-Promotion durch den Fehler-Status `REJECT_OOS_STATISTIC_UNAVAILABLE` abgebrochen.

### Mathematische Ursache für Ertragsverluste:
Der Stationary Bootstrap (Politis & Romano, 1994) zieht Blöcke mit geometrisch verteilter Länge mit Parameter $p = 1/\tau$. Bisher war $\tau = 10$ fest verdrahtet.

1. **Matrix-Degeneration:** Bei mittelfrequenten und Niederfrequenz-Strategien ($N_{trades} < 50$) führt eine starr gewählte Blocklänge $\tau=10$ dazu, dass resampling-basierte Varianz-Kovarianzmatrizen $\widehat{\mathbf{\Sigma}}$ singulär (nicht positiv-definit) werden.
2. **Absturz der Risikoschätzung:** Die Berechnung von $PSR$ und $DSR$ scheitert an Divisionen durch Null oder `NaN`-Werten. Profitabel arbeitende Strategien werden als "nicht bewertbar" verworfen.

---

## 2. Mathematisches Zielmodell (Politis & White 2004)

Implementierung des automatischen, datengetriebenen Spektraldichte-Schätzers nach **Politis & White (2004)** zur Bestimmung der optimalen mittleren Blocklänge $\hat{b}_{opt}$.

### Mathematische Formulierung:

1. **Autokovarianz-Funktion $\hat{R}(k)$:**
   $$\hat{R}(k) = \frac{1}{N} \sum_{t=1}^{N-|k|} (r_t - \bar{r})(r_{t+|k|} - \bar{r})$$

2. **Spektraldichte-Komponenten am Frequenz-Nullpunkt:**
   $$\hat{g} = \sum_{k=-M}^M w\left(\frac{k}{M}\right) |k| \hat{R}(k)$$
   $$\hat{G} = \sum_{k=-M}^M w\left(\frac{k}{M}\right) \hat{R}(k)$$
   wobei $w(x)$ der trapezförmige Politis-Romano Flat-Top Kernel und $M = \min(\lfloor 2\sqrt{N} \rfloor, 20)$ das Truncation Lag ist.

3. **Optimale Blocklänge $\hat{b}_{opt}$:**
   $$\hat{b}_{opt} = \left( \frac{2 \hat{g}^2}{\hat{G}^2} \right)^{1/3} N^{1/3}$$

4. **Entropie-Kappung & Fallback-Regel:**
   $$\tau_{eff} = \max\left(1, \min\left(\lfloor \hat{b}_{opt} \rfloor, \left\lfloor \frac{N}{3} \right\rfloor\right)\right)$$

---

## 3. Umsetzungs-Spezifikation (Code-Ebene)

### Implementierung in `automation/optimizer/bootstrap.py`

```python
import numpy as np

def estimate_politis_white_block_length(returns: np.ndarray) -> int:
    """
    Berechnet die optimale Stationary Bootstrap Blocklänge nach Politis & White (2004)
    mit dynamischer Entropie-Kappung zur Erhaltung der Positiv-Definitheit.
    """
    n = len(returns)
    if n < 6:
        return 1
        
    mean_ret = np.mean(returns)
    centered = returns - mean_ret
    var_0 = np.sum(centered ** 2) / n
    
    if var_0 < 1e-12:
        return 1
        
    max_lag = min(int(2.0 * np.sqrt(n)), 20)
    autocov = np.array([np.sum(centered[:n-k] * centered[k:]) / n for k in range(max_lag + 1)])
    
    g_hat = 0.0
    G_hat = autocov[0]
    
    for k in range(1, max_lag + 1):
        # Flat-top Kernel
        x = k / max_lag
        w_k = 1.0 if x <= 0.5 else 2.0 * (1.0 - x)
        g_hat += 2.0 * w_k * k * autocov[k]
        G_hat += 2.0 * w_k * autocov[k]
        
    if abs(G_hat) < 1e-12:
        return max(1, n // 3)
        
    b_opt = ((2.0 * (g_hat ** 2)) / (G_hat ** 2)) ** (1.0 / 3.0) * (n ** (1.0 / 3.0))
    tau = int(np.floor(b_opt))
    
    max_entropy_tau = max(1, n // 3)
    return max(1, min(tau, max_entropy_tau))
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Politis-White Schätzer integriert:** `estimate_politis_white_block_length` ist in `automation/optimizer/bootstrap.py` produktiv geschaltet.
- [ ] **Zero Matrix Degeneration:** 0% Abbrüche durch `REJECT_OOS_STATISTIC_UNAVAILABLE`.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_792_politis_white_stationary_bootstrap.py` verifiziert:
  1. Mathematisch exakte Konvergenz auf AR(1)-Prozessen.
  2. Robuste Entropie-Kappung bei kleinen Stichproben ($N < 30$).
