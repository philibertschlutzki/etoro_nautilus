# Issue #800: Real-Trading Execution Reconciliation & Dynamic Spread/Slippage Protection in Live Allocator

**Status:** Open  
**Priority:** P0 (Kritisch für den realen Trading-Ertrag & Live-Execution)  
**Labels:** Live-Trading, Execution, Reconciliation  
**Target Component:** `automation/daily_orchestrator.py`, `automation/momentum_ls_allocator.py`, `automation/tests/test_live_allocator_smoke.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund
Wenn Strategien im Optimierungs-Sweep gefördert werden ("Promoted Champions"), entsteht der kritischste Schnittstellenschritt beim Übergang in den **realen Handel (Live Execution)**. 

* **Problem 1:** Slippage und Weitung der Geld-Brief-Spanne (Bid-Ask Spread) während volatiler Marktphasen können den im Backtest ermittelten Edge vollständig auffressen.
* **Problem 2:** Bei Reconnects oder Teilausführungen von Orders muss der Live Allocator den Portfolio-Zustand abgleichen (Reconciliation). Fehlende Ableitung dynamischer Execution-Buffer führt zu Fehlallokationen oder unbeabsichtigtem Over-Hedging.

---

## 2. Mathematisches Zielmodell & Spezifikation

Implementierung einer dynamischen **Execution-Sicherheits-Schicht** mit automatischer Order-Reconciliation und Ausführungs-Dämpfung.

### Mathematische Formulierung:

1. **Maximal Zulässiger Spread Filter:**
   Ein Signal wird live nur ausgeführt, wenn der aktuelle Markt-Spread $S_{live}$ das Backtest-Limit $S_{max}$ nicht überschreitet:
   $$S_{live} \le S_{max} = \alpha \cdot \text{ATR}_{14}$$
   wobei $\alpha = 0.05$ (max 5% der ATR als Spread-Toleranz).

2. **Slippage-Adjustierte Position-Sizing Limitierung:**
   $$Size_{live} = Size_{kelly} \cdot \max\left(0.0, 1.0 - \beta \cdot \frac{S_{live}}{S_{max}}\right)$$

3. **Reconciliation Invariante:**
   $$\sum \text{Position}_{broker} \equiv \sum \text{Position}_{strategy}$$

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Modifikation in `automation/momentum_ls_allocator.py` & `daily_orchestrator.py`

```python
def validate_and_reconcile_live_execution(
    target_size: float,
    current_spread: float,
    atr_14: float,
    broker_position: float,
    strategy_position: float,
    alpha_spread_tolerance: float = 0.05
) -> float:
    """
    Validiert den Spread und führt eine Live-Reconciliation durch.
    """
    # 1. State Reconciliation
    if abs(broker_position - strategy_position) > 1e-5:
        # Reconcile difference first
        position_delta = strategy_position - broker_position
        return float(position_delta)
        
    # 2. Dynamic Spread Filter
    max_spread = alpha_spread_tolerance * atr_14
    if current_spread > max_spread:
        # Spread too wide, suppress market order
        return 0.0
        
    # 3. Slippage Dampened Sizing
    dampening = max(0.0, 1.0 - (current_spread / max_spread))
    adjusted_size = target_size * dampening
    
    return float(adjusted_size)
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Spread Guard aktiv:** Orders werden bei $S_{live} > 0.05 \cdot \text{ATR}_{14}$ automatisch unterdrückt.
- [ ] **Order Reconciliation verifiziert:** Differenzen zwischen Broker-State und Strategy-State werden vor Neuallokation ausgeglichen.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_800_real_trading_execution_reconciliation.py` prüft:
  1. 0.0 Allokation bei extremen Spreads.
  2. Korrekte Ausgleichs-Order bei Reconnect-Diskrepanz.
  3. Vollständigen Schutz des realen Trading-Kapitals im Live-Betrieb.
