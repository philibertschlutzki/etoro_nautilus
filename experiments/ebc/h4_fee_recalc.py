"""EBC-H4 §9 Kosten-Rekalibrierung: gemessene Pauschalgebuehr von 1,00 USD je Order (Stufe C 2026-10-12: 23 von 24 h4-Orders, TSLA-Feeprobe
200/500/1000 USD je 1,00 USD) als Kommission fuer Aktien/ETF. Gebuehr in bps je Seite = 1 / (f * Equity) * 1e4 mit Equity 17 388 USD (Demo-Konto)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np
import h48, portfolio as pf, costs

EQ = 17388.0
rows, ct0, series, cal = h48.setup(with_neu=True)
g0 = h48.g_start(cal)
raw = h48.gen_trades(rows, series)
ARMS = {"C10": ("breit", 10, .06), "C15": ("breit", 15, .04), "C20": ("breit", 20, .03), "C30": ("breit", 30, .02), "C60": ("breit", 60, .01), "C120": ("breit", 120, .005), "C120plus": ("all", 120, .005), "N30": ("neu", 30, .02), "C30plus": ("all", 30, .02)}
out = {}; R = {}
for name, (uni, s, f) in ARMS.items():
    rt = 2.0 / (f * EQ) * 1e4
    ct = costs.CostTable(rows, overrides={"EQUITY": {"comm": rt}})
    tr, q = h48.build(rows, ct, series, cal, raw, g0min=g0, qmax=1e9)        # Kosten mit Gebuehr, ohne Filter
    q0 = {r["id"]: ct0.c_rt(r["id"]) / h48.atr_med(series[r["id"]]) for r in rows}   # Filter und Rang weiter mit den Rueckfallkosten (wie Stufe A)
    tr = [dict(t, rank=q0[t["id"]]) for t in tr if q0[t["id"]] <= 0.10]
    tr = [t for t in tr if uni == "all" or t["src"] == uni]
    r, eq, tk = pf.simulate(tr, cal, slots=s, notional_frac=f, g_from=g0)
    m = h48.summarize(r, tk, g0, name); m.update(fee_rt_bps=rt, n_instruments=len({t["id"] for t in tr}))
    out[name] = m; R[name] = r
    print(name, f"fee_RT={rt:.0f} bps", {k: round(v, 3) for k, v in m.items() if k in ("sr", "maxdd", "n_trades", "expectancy_bps", "total_ret", "n_instruments")}, flush=True)
cmp_ = {f"{a}-C30": pf.delta_sharpe_ci(R[a][g0:], R["C30"][g0:]) for a in ("C10", "C15", "C20", "C60", "C120", "C120plus", "C30plus")}
for k, v in cmp_.items(): print(k, {a: round(b, 3) for a, b in v.items() if a in ("point", "lo", "hi")})
json.dump(dict(equity_usd=EQ, fee_usd_per_order=1.0, arms=out, comparisons=cmp_), open("results/H4/cost_recalibration_fee.json", "w"), indent=1, default=float)
