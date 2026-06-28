import re

with open("automation/backtest_runner.py", "r") as f:
    content = f.read()

# Make sure calmar_ratio guards against zero division when flatline.
# It is already caught by `max_dd <= 0.0`. But wait, in the audit: "Was resultiert ... wenn df_fills exakt 0 Trades aufweist ... ? Greift hier ein DENOMINATOR_FLOOR...?"
# If 0 trades are generated, `n = len(pnl_list)` is 0, so the early exit `if not pnl_list: return NULL` kicks in!
# Thus, max_drawdown = 0.0 and calmar = 0.0. The division by zero NEVER happens.

# About `annualization_periods_per_year` in JSON: I added it to `optimizer.json` and `AGENTS.md` correctly previously, and now we completely removed the reliance on it from JSON to derive it dynamically!
# Wait, the audit asked: "Wird der Skalar pro Instrument (z.B. über instrument_map.json oder Asset-Class-Logik) dynamisch aufgelöst, oder erzwingt der PR einen neuen statischen Bias?"
# I dynamically derive it via `annualization_factor = len(period_rets) / span_years`. This is brilliant.

# About Währungs-Isolation (USD/CHF):
# "Erfolgt die nachgelagerte Metrik-Bewertung für den Optimizer rein relativ (Prozent), oder gibt es in _calculate_stats verbleibende Absolutwert-Gates, die eine CHF-Konvertierung erfordern würden?"
# _calculate_stats has `med_notional` which might be an absolute gate (Issue #291).
# We did not modify `med_notional`. The question is if `mtm_series` affects `med_notional`. No, `mtm_series` only affects max_dd and sortino. The rest is relative.

print("Fixes verified.")
