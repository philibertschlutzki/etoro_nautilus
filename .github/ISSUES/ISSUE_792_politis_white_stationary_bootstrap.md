# Issue #792: Adaptive Blocklängen-Kalibrierung (Politis & White) für Stationary Bootstrap

**Status:** Open  
**Priority:** P0 (Blocker for Bootstrapping & PSR Calculation)  
**Labels:** Quant, Core-Math, Robustness  
**Target Component:** `automation/optimizer/bootstrap.py`, `automation/optimizer/deflation.py`, `automation/tests/test_issue_599_bootstrap_ci.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund aus `combined_proposals.json`
In 165 von 1.064 Strategie-Proposals (15,5%) wurde die Evaluierung durch den Fehler status `REJECT_OOS_STATISTIC_UNAVAILABLE` abgebrochen.

### Ursachenanalyse
Der Stationary Bootstrap (Politis & Romano, 1994) resampelt Renditezeitreihen mit geometrisch verteilten Blocklängen mit Parameter $p = 1 / \tau$.
Bislang wurde die durchschnittliche Blocklänge $\tau$ auf einen statischen Wert (z. B. $\tau = 10$) fest verdrahtet.

1. **Low-Frequency Degeneration:** Bei Niederfrequenz-Strategien mit kurzen Renditeserien ($N_{trades} < 50$) führt eine statische Blocklänge $\tau = 10$ dazu, dass einzelne Resamples stark von wenigen Blöcken dominiert werden.
2. **Singuläre Kovarianzmatrix:** Die resultierende Resampling-Varianz-Kovarianzmatrix degeneriert (wird nicht positiv-definit), wodurch der Bootstrap-Schätzer für die Sharpe-Ratio-Varianz fehlschlägt und ein `NaN` bzw. `REJECT_OOS_STATISTIC_UNAVAILABLE` auslöst.

---

## 2. Mathematisches Zielmodell & Spezifikation

Implementierung der datengetriebenen, adaptiven Blocklängen-Auswahl nach **Politis & White (2004)** zur Ermittlung der optimalen mittleren Blocklänge $\hat{b}_{opt}$.

### Mathematische Formulierung:

1. **Autokovarianz-Schätzung:**
   $$\hat{R}(k) = \frac{1}{N} \sum_{t=1}^{N-|k|} (r_t - \bar{r})(r_{t+|k|} - \bar{r})$$

2. **Spektrale Dichte-Komponenten at Frequency Zero:**
   $$\hat{g} = \sum_{k=-M}^M w\left(\frac{k}{M}\right) |k| \hat{R}(k)$$
   $$\hat{G} = \sum_{k=-M}^M w\left(\frac{k}{M}\right) \hat{R}(k)$$
   wobei $w(x)$ ein Flat-Top Kernel (z. B. Politis-Romano Kernel) und $M$ das Truncation Lag ist.

3. **Optimale Blocklänge $\hat{b}_{opt}$:**
   $$\hat{b}_{opt} = \left( \frac{2 \hat{g}^2}{\hat{G}^2} \right)^{1/3} N^{1/3}$$

4. **Entropie-Kappung & Fallback:**
   Falls $N_{trades} < \hat{b}_{opt}$ oder $\hat{G}^2 \to 0$, wird auf die Entropie-Obergrenze zurückgegriffen:
   $$\tau = \max\left(1, \min\left(\lfloor \hat{b}_{opt} \rfloor, \left\lfloor \frac{N}{3} \right\rfloor\right)\right)$$

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Modifikation in `automation/optimizer/bootstrap.py`

```python
import numpy as np

def estimate_politis_white_block_length(returns: np.ndarray) -> int:
    """
    Berechnet die optimale Stationary Bootstrap Blocklänge nach Politis & White (2004).
    Inklusive Entropie-Kappung tau = min(b_opt, floor(N / 3)).
    """
    n = len(returns)
    if n < 5:
        return 1
    
    # Autokovarianz bis max_lag = min(n // 4, 20)
    max_lag = min(n // 4, 20)
    mean_ret = np.mean(returns)
    centered = returns - mean_ret
    
    autocov = np.array([np.sum(centered[:n-k] * centered[k:]) / n for k in range(max_lag + 1)])
    
    # Flat-top Kernel Gewichte
    g_hat = 0.0
    G_hat = autocov[0]
    
    for k in range(1, max_lag + 1):
        w_k = 1.0 if k <= max_lag / 2 else 2.0 * (1.0 - k / max_lag)
        g_hat += 2.0 * w_k * k * autocov[k]
        G_hat += 2.0 * w_k * autocov[k]
        
    if abs(G_hat) < 1e-12:
        return max(1, n // 3)
        
    b_opt = ((2.0 * (g_hat ** 2)) / (G_hat ** 2)) ** (1.0 / 3.0) * (n ** (1.0 / 3.0))
    tau = int(np.floor(b_opt))
    
    # Hard Limit: minimal 1, maximal floor(n / 3)
    max_tau = max(1, n // 3)
    return max(1, min(tau, max_tau))
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Politis & White Algorithmus integriert:** `estimate_politis_white_block_length` ist in `automation/optimizer/bootstrap.py` vollständig eingebaut und verdrahtet.
- [ ] **Zero Failures bei Low-Frequency:** Kein `REJECT_OOS_STATISTIC_UNAVAILABLE` mehr bei Stichproben mit $N \ge 15$.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_792_politis_white_stationary_bootstrap.py` testet:
  1. Korrekte Konvergenz von $\hat{b}_{opt}$ bei synthetischem AR(1)-Prozess.
  2. Fallback-Verhalten bei identischen Renditen ($\sigma^2 = 0$).
  3. Vollständige Vermeidung von `NaN`-Werten in der Varianz-Kovarianzmatrix.
