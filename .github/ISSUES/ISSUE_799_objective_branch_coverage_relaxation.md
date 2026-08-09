# Issue #799: Primary Objective Branch Reachability & Early Guard Soft-Penalty Relaxation

**Status:** Open  
**Priority:** P0 (Behebung von 616 Invarianten-Abbrüchen in Fail-Logs)  
**Labels:** Quant, Optimizer, Invariants  
**Target Component:** `automation/optimizer/invariants.py`, `automation/optimizer/gate.py`, `automation/tests/test_issue_979_objective_branch_coverage.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund aus `logs/fails/*.log`
In den Execution Fail Logs schlug die Invariante `check_objective_branch_coverage` **616 Mal** fehl.

```json
{"actual": 0.0, "check": "check_objective_branch_coverage", "detail": "Nur 0/89 Trials (0.00%) tragen die ordentliche Objective"}
```

### Ursachenanalyse
Die Invariante `check_objective_branch_coverage` stellt sicher, dass ein signifikanter Anteil der evaluierte Trials den primären Objective-Berechnungspfad (wo Rendite, Sharpe Ratio und Drawdown aggregiert werden) tatsächlich erreicht.

* **Problem:** Harte Frühstopp-Wächter (z. B. strikte Mindest-Trade-Anzahl `N < 30` oder harte Time-Box Strafen) sortieren 100% der ausprobierten Trial-Kombinationen in frühen Versuchen aus. Die Zielfunktion gibt in diesem Fall sofort `-9999.0` zurück. Dadurch erreicht **kein einziger Trial (0,00%)** den Hauptzweig der Zielfunktion, was `check_objective_branch_coverage` zum Abbruch bringt.

---

## 2. Mathematisches Zielmodell & Spezifikation

Transformation harter Frühstopp-Abbrüche in **stetige Soft-Penalty Gates** in frühen Exploration-Phasen, sodass mindestens 20% aller Trials den primären Evaluierungszweig durchlaufen.

### Mathematische Formulierung:

1. **Soft-Penalty anstelle harter Abbrüche:**
   Wenn $N_{trades} < N_{min}$, wird der Trial nicht verworfen (`REJECT`), sondern erhält eine quadratische Mindest-Strafgebühr:
   $$Penalty_{trades}(N) = -C_{penalty} \cdot \left( \frac{N_{min} - N_{trades}}{N_{min}} \right)^2$$

2. **Garantierter Branch Reachability Score:**
   $$\text{Coverage\_Ratio} = \frac{N_{reached\_primary\_branch}}{N_{total\_trials}} \ge 0.20$$

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Modifikation in `automation/optimizer/gate.py` & `invariants.py`

```python
def evaluate_soft_guard_penalty(n_trades: int, min_trades: int = 30, max_penalty: float = 50.0) -> float:
    """
    Berechnet eine stetige Soft-Penalty für ungenügende Trade-Zahlen anstelle eines harten Abruchs.
    """
    if n_trades >= min_trades:
        return 0.0
        
    deficit_ratio = (min_trades - n_trades) / float(min_trades)
    return float(-max_penalty * (deficit_ratio ** 2))
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Soft-Penalties aktiv:** Frühstopp-Wächter nutzen stetige Soft-Penalties in der Explorations-Phase.
- [ ] **Objective Coverage $\ge 20\%$:** 100% aller Studies erfüllen den `check_objective_branch_coverage` Schwellenwert.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_799_objective_branch_coverage_relaxation.py` prüft:
  1. Abdeckung von $\ge 20\%$ in allen Test-Runs.
  2. Eliminierung der 616 `check_objective_branch_coverage` Fehler aus den Logs.
