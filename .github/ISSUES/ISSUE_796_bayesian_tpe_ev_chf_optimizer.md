# Issue #796: Feature - Implement Bayesian Optimization Pipeline (Expected Value Maximization)

**Status:** Open  
**Priority:** P0 (Architektonisches Upgrade des Sweeps)  
**Labels:** Feature, Quant, Optimizer  
**Target Component:** `automation/optimizer/sweep.py`, `automation/optimizer/spaces.py`, `automation/tests/test_issue_400_sweep_parallel.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund aus `combined_proposals.json`
Die gegenwärtige Implementation in `automation/optimizer/sweep.py` stützt sich primär auf eine statische Enumeration (Grid Search) bzw. vereinfachte Zufallsstichproben. 

* **Problem 1:** Bei 14 Strategien und 76 Symbolen übersteigt der kombinierte Parameterraum $10^{12}$ Kombinationen. Eine statische Enumeration deckt weniger als $0,0001\%$ des Raumes ab.
* **Problem 2:** Relevante Parameter-Interaktionen (z. B. SL/TP-Ratio gekoppelt an Indikator-Perioden) werden nicht gelernt. Das System findet keine ertragsstarken Optima im realen CHF-Gewinnraum.

---

## 2. Mathematisches Zielmodell & Spezifikation

Ablösung/Erweiterung der statischen Enumeration durch eine probabilistische bayesianische Suche (**Tree-structured Parzen Estimator / TPE**), welche direkt den **Expected Value in CHF** auf dem Holdout-Set maximiert.

### Mathematische Target-Funktion:

Maximierung der Zielfunktion $f(x)$ im Parameterraum $\mathcal{X}$:
$$EV_{CHF}(x) = \Big( P_{win}(x) \cdot \overline{W}_{CHF}(x) \Big) - \Big( (1 - P_{win}(x)) \cdot \overline{L}_{CHF}(x) \Big) - \text{Penalty}_{cost}(x)$$

wobei:
* $P_{win}(x)$: Reelle Win-Rate des Parametervektors $x$.
* $\overline{W}_{CHF}(x)$: Durchschnittlicher Gewinn in CHF.
* $\overline{L}_{CHF}(x)$: Durchschnittlicher Verlust in CHF.
* $\text{Penalty}_{cost}(x)$: Berücksichtigung von Turnover, Spread und Holding Costs.

### System-Constraints & RAM-Management:
* **Streaming-Dataframe Processing:** PyArrow/Pandas Datensätze müssen gepuffert und im Speicher gedrosselt werden.
* **Hard RAM Ceiling:** Die maximale Arbeitsspeichernutzung über alle parallelen Worker-Instanzen hinweg darf **28 GB RAM** auf dem 32GB Ubuntu-Zielsystem nicht überschreiten.

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Integration in `automation/optimizer/sweep.py`

```python
import optuna

def create_bayesian_tpe_study(study_name: str, storage_url: str = "sqlite:///optuna.db"):
    """
    Erstellt eine Optuna TPE Study optimiert auf EV_CHF Maximierung.
    """
    sampler = optuna.samplers.TPESampler(
        multivariate=True,
        group=True,
        n_startup_trials=20,
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
    Ziel-Evaluierungsfunktion für den Bayesian Optimizer.
    """
    params = suggest_strategy_params(trial)
    results = strategy_runner.run_backtest(params, symbol_data)
    
    if not results.get("oos_evaluated", False):
        return -9999.0
        
    win_rate = results["oos_win_rate"]
    avg_win = results["oos_avg_win_chf"]
    avg_loss = results["oos_avg_loss_chf"]
    
    ev_chf = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)
    return float(ev_chf)
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Bayesian Optimizer integriert:** TPE-Sampler ist in `automation/optimizer/sweep.py` produktiv geschaltet.
- [ ] **Strikte RAM-Grenze eingehalten:** Arbeitsspeicher bleibt bei $n\_jobs = 22$ unter **28 GB RAM**.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_796_bayesian_tpe_ev_chf_optimizer.py` verifiziert:
  1. Schnellere Konvergenz auf $EV_{CHF} > 0$ gegenüber Grid Search (mind. 3x höhere Effizienz).
  2. Korrekte Persistenz der Trial-Zustände in der Optuna-DB.
  3. Fallback auf Grid Search bei explizitem CLI-Flag `--fallback-grid`.
