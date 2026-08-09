# Issue #794: Dynamic Position Sizing via Fractional Kelly Criterion

**Status:** Open  
**Priority:** P1 (Ertrags-Maximierung im Live-Handel)  
**Labels:** Quant, Money-Management, Live-Execution  
**Target Component:** `automation/fractional_trading.py`, `automation/momentum_ls_allocator.py`, `automation/tests/test_allocator.py`  

---

## 1. Symptomatik & Problemanalyse

### Baseline-Befund
Die aktuelle Kapitalallokation verwendet eine statische Positionsgrössenbestimmung (z. B. fester Prozentwert des Gesamtkapitals oder einfache Gleichgewichtung). 

* **Problem 1:** Ausgezeichnete Strategien mit hohem Sharpe Ratio und verifizierten Holdout-Ergebnissen werden kapitalmässig genauso behandelt wie schwache Grenzstrategien.
* **Problem 2:** Ohne Einbezug des Erwartungswerts und des Risiko-Profils verfehlt die Strategie das theoretische Optimum der Kapitalwachstumsrate (Capital Growth Rate) bei weitem, wodurch im realen Handel erhebliche Erträge liegen bleiben.

---

## 2. Mathematisches Zielmodell & Spezifikation

Integration der dynamischen Kapitalallokation basierend auf dem **Fractional Kelly Criterion** unter Nutzung der verifizierten Out-of-Sample (Holdout) Metriken.

### Mathematische Formulierung:

1. **Kelly-Formel für ungleiches Payoff-Verhältnis:**
   $$f^* = \frac{p}{a} - \frac{q}{b}$$
   wobei:
   * $p$: Win Rate (Holdout OOS Win Rate)
   * $q = 1 - p$: Loss Rate
   * $b = \frac{\text{Avg Win in CHF}}{\text{Total Capital CHF}}$: Relativer Gewinn pro Trade
   * $a = \frac{\text{Avg Loss in CHF}}{\text{Total Capital CHF}}$: Relativer Verlust pro Trade

2. **Fractional Multiplier $k_{kelly}$:**
   Zur Vermeidung exzessiver Drawdowns (Overbetting Risk) wird die Allokation skaliert:
   $$f_{alloc} = k_{kelly} \cdot f^*$$
   mit $k_{kelly} \in [0.25, 0.50]$ (Quarter-Kelly oder Half-Kelly).

3. **Hard Exposure Cap & Safety Floor:**
   $$f_{final} = \min\left(f_{max\_exposure}, \max\left(0, f_{alloc}\right)\right)$$
   wobei $f_{max\_exposure}$ durch das Aggregate Exposure Cap (z. B. max 15% des Gesamtkapitals pro Position) begrenzt ist.

---

## 3. Konkreter Umsetzungsplan (Code-Ebene)

### Implementierung in `automation/fractional_trading.py`

```python
def calculate_fractional_kelly_size(
    win_rate: float,
    avg_win_chf: float,
    avg_loss_chf: float,
    total_capital_chf: float,
    fractional_multiplier: float = 0.5,
    max_exposure_fraction: float = 0.15
) -> float:
    """
    Berechnet die optimale Positionsgrösse f* nach dem Fractional Kelly Kriterium.
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
        
    f_alloc = f_star * fractional_multiplier
    f_final = min(max_exposure_fraction, f_alloc)
    
    return float(f_final)
```

---

## 4. Akzeptanzkriterien & Verifikation

- [ ] **Kelly Calculator Modul integriert:** `calculate_fractional_kelly_size` ist in `automation/fractional_trading.py` eingebaut und im Allocator (`automation/momentum_ls_allocator.py`) angebunden.
- [ ] **Sicherheitskappung aktiv:** Hard Exposure Cap (max 15% CHF pro Trade) wird bei allen Berechnungen eingehalten.
- [ ] **Unit-Test Abdeckung:** `automation/tests/test_issue_794_fractional_kelly_position_sizing.py` prüft:
  1. $f^* = 0.0$ wenn $EV \le 0$ oder $p < 0.35$ bei $b/a = 1.0$.
  2. Korrekte Skalierung bei Half-Kelly ($k=0.5$) und Quarter-Kelly ($k=0.25$).
  3. Konforme Allokationsanpassung bei veränderten CHF-Kapitalständen.
