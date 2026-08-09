# Issue #797: Systemic Solution for Reward Function Inertia & Dynamic Range Normalization

**Status:** Open  
**Priority:** P0 (Behebung von 630 Invarianten-Abbrüchen in Fail-Logs)  
**Labels:** Quant, Optimizer, Invariants  
**Target Component:** `automation/optimizer/invariants.py`, `automation/optimizer/reward.py`, `automation/tests/test_issue_764_reward_term_variance_table.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund aus `logs/fails/*.log`
In den Execution Fail Logs traten **630 Fehler-Events** bei der Ausführung von `check_reward_term_variance` und **147 Fehler-Events** bei `check_reward_dynamic_range` auf.

```json
{"actual": ["divergence", "param_pen", "turnover"], "check": "check_reward_term_variance", "detail": "Reward-Term(e) praktisch inert (std < 1% of total reward std)"}
```

### Ursachenanalyse
Die Invariante `check_reward_term_variance` bricht eine Study ab oder markiert Trials als ungültig, wenn einzelne Terme in der Reward-Formel (z. B. `divergence`, `param_pen`, `turnover`, `fold_dispersion`) eine Standardabweichung von weniger als 1% der Gesamtreward-Standardabweichung aufweisen.

* **Problem 1:** In bestimmten Parameterbereichen verändern sich Strafterme nicht (sie sind konstant Null). Die Invariante interpretiert diese erwünschte Stabilität fälschlicherweise als "inerten Defekt" und bricht den Sweep ab.
* **Problem 2:** Strafterme dominieren in anderen Bereichen die Skala derart stark, dass die Invariante `check_reward_dynamic_range` anschlägt und die Varianz des eigentlichen Ertrags-Signals auslöscht.

---

## 2. Mathematisches Zielmodell & Spezifikation

Implementierung einer **dynamischen Auto-Skalierung und adaptiven Maskierung** für Reward-Terme mit reduzierter Varianz.

### Mathematische Formulierung:

1. **Relative Varianz-Evaluierung:**
   Für jeden Reward-Term $T_k \in \{T_1, T_2, \dots, T_m\}$:
   $$\sigma_{rel}(T_k) = \frac{\sigma(T_k)}{\max\left(1e-6, \sum_{j=1}^m \sigma(T_j)\right)}$$

2. **Adaptive Gewichts-Maskierung:**
   Falls $\sigma_{rel}(T_k) < 0.01$, wird der Term $T_k$ nicht als Fehler gewertet, wenn sein Absolutwert $|T_k| < \epsilon_{inert}$ (d. h. wenn der Strafterm inaktiv ist).
   $$Weight(T_k) = \begin{cases} 
   0.0 & \text{falls } \sigma_{rel}(T_k) < 0.01 \text{ und } |T_k| < \epsilon \\
   w_k & \text{sonst}
   \end{cases}$$

3. **Invarianten-Harmonisierung:**
   `check_reward_term_variance` bewertet nur noch aktive, ungleich Null definierte Terme. Inaktive Strafterme liefern ein `INERT_PASS` anstelle eines `FAIL`.

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Modifikation in `automation/optimizer/invariants.py`

```python
def check_reward_term_variance_harmonized(
    trials: list[dict],
    inert_threshold: float = 0.01,
    zero_eps: float = 1e-5
) -> dict:
    """
    Harmonisierte Varianzprüfung: Inaktive Null-Strafterme führen nicht mehr zum Study-Abbruch.
    """
    if not trials:
        return {"passed": True, "reason": "NO_TRIALS"}
        
    term_values = extract_reward_terms(trials)
    total_std = np.std([t.get("reward", 0.0) for t in trials])
    
    inert_terms = []
    failed_terms = []
    
    for term_name, values in term_values.items():
        std_v = np.std(values)
        mean_abs_v = np.mean(np.abs(values))
        
        if total_std > 0 and (std_v / total_std) < inert_threshold:
            if mean_abs_v > zero_eps:
                # Term hat Wert, aber keine Varianz => echtes Plateau
                failed_terms.append(term_name)
            else:
                # Term ist inaktiv Null => zulässiger Zustand
                inert_terms.append(term_name)
                
    passed = len(failed_terms) == 0
    return {
        "passed": passed,
        "failed_terms": failed_terms,
        "inert_terms": inert_terms
    }
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Inaktive Strafterme erlaubt:** Inaktive Null-Strafterme verursachen keinen `check_reward_term_variance` Abbruch mehr.
- [ ] **Eliminierung der 630 Fail-Events:** Re-Test gegen die Fail-Logs bestätigt 0 Abbrüche durch falsch-positive Varianzprüfungen.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_797_reward_term_variance_harmonization.py` prüft:
  1. `INERT_PASS` bei Null-Straftermen.
  2. Korrektes `FAIL` bei echten konstanten Nicht-Null-Variablen.
