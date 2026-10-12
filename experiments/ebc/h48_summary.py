"""Querschnitt H4-H7: alle Kandidaten-Portfolios vola-gleich zu C30 (Tagesrenditen aus den Stufe-A-Ausgaben)."""
import csv, json, sys, os
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np, portfolio as pf, h48
def load(p):
    rd = list(csv.reader(open(p))); h = rd[0]
    return {n: np.array([float(r[i]) for r in rd[1:]]) for i, n in enumerate(h) if i}
H4, H5, H6, H7 = (load(f"results/H{k}/stage_a/{n}") for k, n in ((4, "portfolio_daily_returns.csv"), (5, "portfolio_daily_returns.csv"), (6, "portfolio_daily_returns.csv"), (7, "sleeves_daily_returns.csv")))
ref = H4["C30"]
cand = {"C30 (B+)": H4["C30"], "C60": H4["C60"], "C120": H4["C120"], "C120+ (U_breit+U_neu)": H4.get("C120plus"), "C30+ (U_breit+U_neu)": H4.get("C30plus"),
        "H5 Hstar@C30": H5["Hstar@C30"], "H5 H0+NP@C120": H5["H0+NP@C120"], "H5 HL@C120": H5["HL@C120"], "H5 HM@C120": H5["HM@C120"],
        "H6 L1 (DS 45 %)": H6["L1"], "H6 L123": H6["L123"], "H6 C120": H6["C120"],
        "H7 TMV-Q": H7["TMV-Q"], "H7 Kombi6-Q": H7["Kombi6-Q"], "H7 TMV120": H7["TMV120"], "H7 Kombi6-P": H7["Kombi6-P"], "H7 TMV120+RXW": H7["TMV120+RXW"],
        "H7 TMV+R-Q": H7["TMV+R-Q"], "H7 R-allein": H7["R-allein"]}
out = {}
for n, r in cand.items():
    if r is None: continue
    rv = h48.volmatch(r, ref)
    out[n] = dict(sr=pf.sharpe(r), maxdd=pf.max_dd(r), total_ret=float(np.prod(1 + r) - 1), ann_ret_volmatched=float(rv.mean() * 252), maxdd_volmatched=pf.max_dd(rv),
                  cvar5_volmatched=pf.cvar5(rv), calmar_volmatched=float(rv.mean() * 252 / abs(pf.max_dd(rv))))
extra = {"Kombi6-P-TMV120": pf.delta_sharpe_ci(H7["Kombi6-P"], H7["TMV120"]), "Kombi6-P-C30": pf.delta_sharpe_ci(H7["Kombi6-P"], H4["C30"]),
         "TMV120+RXW-C30": pf.delta_sharpe_ci(H7["TMV120+RXW"], H4["C30"])}
json.dump(dict(portfolios=out, extra=extra), open("results/H4/summary_h4_h7.json", "w"), indent=1, default=float)
print(f"{'Portfolio':28s} {'SR':>5s} {'MaxDD':>7s} {'Ret':>6s} | vola-gleich zu C30: {'Ret/J':>6s} {'MaxDD':>7s} {'Calmar':>6s}")
for n, d in out.items():
    print(f"{n:28s} {d['sr']:5.2f} {d['maxdd']*100:6.1f}% {d['total_ret']*100:5.0f}% | {d['ann_ret_volmatched']*100:12.1f}% {d['maxdd_volmatched']*100:6.1f}% {d['calmar_volmatched']:6.2f}")
for k, v in extra.items(): print(k, {a: round(b, 3) for a, b in v.items() if a in ("point", "lo", "hi")})
