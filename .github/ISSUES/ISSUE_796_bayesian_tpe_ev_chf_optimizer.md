# Issue #796: Feature - Implement Bayesian Optimization Pipeline (Expected Value Maximization in CHF)

**Status:** Open  
**Priority:** P0 (Architektonisches Hauptupgrade zur Gewinn-Maximierung)  
**Labels:** Feature, Quant, Optimizer, Profit-Optimization  
**Target Component:** `automation/optimizer/sweep.py`, `automation/optimizer/spaces.py`, `automation/tests/test_issue_400_sweep_parallel.py`  

---

## 1. Symptomatik & Empirische Problemanalyse

### Baseline-Befund aus `combined_proposals.json`
Die bisherige Implementation in `automation/optimizer/sweep.py` stützt sich auf eine statische Enumeration (Grid Search).

### Quant-Analyse des Ertragsverlusts:
Bei 14 Strategien und 76 Symbolen übersteigt der kombinierte kontinuierliche Parameterraum $10^{12}$ Punkte.

1. **Blindheit für Parameter-Interaktionen:** Eine statische Raster-Suche evaluiert nur diskrete isolierte Stützstellen. Parameter-Kopplungen (z. B. SL/TP-Ratio in Abhängigkeit von der ATR-Periodenlänge) bleiben unentdeckt.
2. **Sub-Optimale Zielgröße:** Statische Sweeps optimieren oft auf sekundäre Ersatzgrössen. Für den realen Trading-Erfolg zählt jedoch einzig die **Maximierung des Expected Value in CHF** auf den Holdout-Daten unter strikter Einhaltung der System-RAM-Grenzen.

---

## 2. Mathematisches Zielmodell (Bayesian TPE EV_CHF Maximizer)

Ersetzung/Erweiterung der Grid Search durch eine probabilistische bayesianische Suche (**Tree-structured Parzen Estimator / TPE**), welche direkt das **Expected Value in CHF** auf dem Holdout-Set maximiert.

### Mathematische Target-Funktion:

$$\max_{x \in \mathcal{X}} f(x) = EV_{CHF}(x)$$

$$EV_{CHF}(x) = \Big( P_{win}(x) \cdot \overline{W}_{CHF}(x) \Big) - \Big( (1 - P_{win}(x)) \cdot \overline{L}_{CHF}(x) \Big) - \text{Turnover\_Cost}_{CHF}(x)$$

wobei:
* $x \in \mathcal{X}$: Hyperparameter-Vektor im kontinuierlichen Suchraum.
* $P_{win}(x)$: Realisierte Out-of-Sample Win Rate.
* $\overline{W}_{CHF}(x)$: Durschnittlicher Gewinn pro Trade in CHF.
* $\overline{L}_{CHF}(x)$: Durchschnittlicher Verlust pro Trade in CHF.
* $\text{Turnover\_Cost}_{CHF}(x)$: Gebühren- und Slippage-Kostenmodell.

### TPE Sampling-Dichte Ratios:
$$p(x|y) = \begin{cases} l(x) & \text{falls } y < y^* \\ g(x) & \text{falls } y \ge y^* \end{cases}$$
Der TPE-Sampler maximiert das Expected Improvement (EI) durch Auswahl von Parameterpunkten mit maximalem Dichten-Quotienten $\frac{l(x)}{g(x)}$.

### Memory & System Constraints:
* **RAM Ceiling:** Der Arbeitsspeicher der parallelen Worker-Instanzen ($n\_jobs = 22$) muss strikt unter **28 GB RAM** (auf dem 32GB Zielsystem) gehalten werden via PyArrow Stream Buffering.

---

## 3. Umsetzungs-Spezifikation (Code-Ebene)

### Modifikation in `automation/optimizer/sweep.py`

```python
import optuna

def create_bayesian_tpe_study(study_name: str, storage_url: str = "sqlite:///optuna.db") -> optuna.Study:
    """
    Erstellt eine multivariate Optuna TPE Study optimiert auf die Maximierung des EV_CHF.
    """
    sampler = optuna.samplers.TPESampler(
        multivariate=True,
        group=True,
        n_startup_trials=25,
        warn_independent_sampling=False
    )
    
    study = optuna.create_study(
        study_name=study_name,
        storage=storage_url,
        sampler=sampler,
        direction="maximize",
        load_if_exists=True
    )
    return study

def objective_ev_chf(trial: optuna.Trial, strategy_runner, symbol_data) -> float:
    """
    Evaluierungsfunktion: Maximiert direkt den Expected Value in CHF auf Holdout-Events.
    """
    params = suggest_strategy_params(trial)
    results = strategy_runner.run_backtest(params, symbol_data)
    
    if not results.get("oos_evaluated", False):
        return -9999.0
        
    win_rate = results["oos_win_rate"]
    avg_win = results["oos_avg_win_chf"]
    avg_loss = results["oos_avg_loss_chf"]
    turnover_cost = results.get("oos_turnover_cost_chf", 0.0)
    
    ev_chf = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss) - turnover_cost
    return float(ev_chf)
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Bayesian TPE Optimizer aktiv:** TPE-Sampler ist in `automation/optimizer/sweep.py` als Standard-Suchverfahren hinterlegt.
- [ ] **Ertrags-Steigerung nachgewiesen:** Konvergenztest zeigt mind. 3x höhere Ausbeute an profitablen Champions ($EV_{CHF} > 0$) gegenüber Grid Search.
- [ ] **Strikte RAM-Kappung:** Speicherverbrauch bleibt bei $n\_jobs = 22$ unter **28 GB RAM**.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_796_bayesian_tpe_ev_chf_optimizer.py` prüft:
  1. Multivariate Parameter-Kopplung im Sampler.
  2. Korrekte Persistenz der Optuna-Trials.
  3. `--fallback-grid` Option für Regressionsprüfungen.
