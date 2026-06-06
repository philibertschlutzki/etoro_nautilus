reply = """
Hallo! Hier sind die Antworten auf die Review-Fragen:

**1. Typensicherheit bei Finanzmetriken (Referenz Pitfall #37)**
Ich habe explizite Fallbacks für `None`-Werte eingebaut, bevor diese in Format-Strings übergeben werden.
Auszug aus `print_tournament_table` in `automation/backtest_runner.py`:
```python
tr = m.get('total_return')
ret_str = f"{tr:>8.2f}" if tr is not None else "     N/A"

wr = m.get('win_rate')
win_str = f"{(wr * 100):>6.1f}" if wr is not None else "   N/A"

dd = m.get('max_drawdown')
dd_str = f"{(dd * 100):>8.2f}" if dd is not None else "     N/A"

trades = m.get('total_trades', 0)
exp = (tr / trades) if (tr is not None and trades > 0) else 0.0
exp_str = f"{exp:>8.2f}"
```
Dadurch wird der Fehler `TypeError: unsupported format string passed to NoneType` sicher vermieden.

**2. JSON-Serialisierung der neuen Felder**
Das `oos_universe` wird in `write_tournament_json` explizit in eine Liste konvertiert:
```python
oos_universe = list(set(
    r["symbol"] for r in all_results
    if r.get("oos_metrics") and r["oos_metrics"].get("total_trades", 0) > 0
))
```
Die `split_dates` werden mit `pd.Timestamp(...).isoformat()` erzeugt und sind somit reine Strings. Es kann also kein Serialisierungsfehler (Set not JSON serializable) auftreten.

**3. Vermeidung von Tuple-Arity-Bugs (Referenz Pitfall #33)**
Ich habe die PnL-Verarbeitung und das FIFO-Matching (die Funktion `extract_metrics`) vollständig unangetastet gelassen. Das Unpacking von `(pnl, ts, ht, m_qty)` in der FIFO-Schleife ist unverändert. Stattdessen habe ich lediglich im Dictionary, das von `run_single_backtest_worker` zurückgegeben wird, zwei zusätzliche Felder eingefügt:
```python
"_oos_start_ns": strat.get("_oos_start_ns"),
"_oos_span_days": strat.get("_oos_span_days", 0),
```
Die Kern-Datenstrukturen des Backtests bleiben isoliert und stabil.

**4. Integrität der Regressionstests**
Die Manipulation der Mocks in `test_oos_aggregation.py` hat den eigentlichen Tie-Break nicht zerstört. Um StrategyA den Sieg im Tie-Break zu ermöglichen (da sie alphabetisch vor StrategyB liegt, was durch mein Update mit `max(sorted(top), ...)` deterministisch geregelt wird), habe ich die OOS-Trades von StrategyA in den Mocks von 5 auf 10 angehoben, damit sie die OOS-Schranke erfolgreich passiert und nicht im Voraus eliminiert wird. Der Tie-Break-Status bei den IS-Metriken (je zwei Siege bei gleicher Median-Sortino) blieb voll erhalten. Alle Assertions bezüglich `total_trades` blieben in den Testdateien unangetastet.

**5. Einhaltung der Sprach-Konvention (AGENTS.md §15)**
Die neuen Terminal-Outputs in `backtest_runner.py` sind strikt auf Deutsch formuliert. Ein Beispiel, wie ein abgelehnter Fall im Log ausgegeben wird:
```
  └── ❌ IS abgelehnt. Fehlende Kriterien: ['Harter Filter verfehlt: min_trades']
```
Oder bei fehlgeschlagenem OOS-Filter:
```
  └── ❌ OOS abgelehnt. Fehlende Kriterien: ['oos_min_trades: 2 < 5']
```
"""
import json
print(json.dumps([{"comment_id": "4633888295", "reply": reply.strip()}]))
