"""EBC-H4 Stufe A: Kapazitaet x Breite. Arme C30, C60, C120, C120+, N30, C30-K1, C30r, Cinf. Ausgabe results/H4/stage_a + metrics.json."""
import hashlib, json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import h48, portfolio as pf

OUT = "results/H4/stage_a"; os.makedirs(OUT, exist_ok=True)
rows, ct, series, cal = h48.setup(with_neu=True)
g0 = h48.g_start(cal)
raw = h48.gen_trades(rows, series)
tr, q = h48.build(rows, ct, series, cal, raw, g0min=g0)
neu_ids = {r["id"] for r in rows if r["src"] == "neu"}
print("Instrumente", len(rows), "neu", len(neu_ids), "Trades", len(tr), flush=True)
breit = [t for t in tr if t["src"] == "breit"]; neu = [t for t in tr if t["src"] == "neu"]
rng = np.random.default_rng(h48.SEED)
for t in tr: t["rank_rand"] = float(rng.random())
dates = [str(dt.date(1970, 1, 1) + dt.timedelta(days=d)) for d in cal.days]

def run(name, trades, slots, f, rank=None):
    st = {}
    tt = [dict(t, rank=t[rank]) for t in trades] if rank else trades
    r, eq, tk = pf.simulate(tt, cal, slots=slots, notional_frac=f, g_from=g0, stats=st)
    m = h48.summarize(r, tk, g0, name)
    nc = st.get("n_cand", 0); dr = st.get("dropped", [])
    m.update(slots=slots, notional=f, n_cand=nc, dropped_share=(len(dr) / nc if nc else 0.0),
             dropped_expectancy_bps=float(np.mean([t["net_bps"] for t in dr])) if dr else None,
             mean_open=float(np.mean(st["open"])), median_open=float(np.median(st["open"])),
             util=float(np.mean(st["open"]) / slots) if slots < 10**5 else None,
             K1_expectancy_bps=float(np.mean([t["net_bps"] for t in tk if t["klasse"] == "K1"])) if tk else None)
    nb = np.array(sorted([t["net_bps"] for t in tk], reverse=True)); pos = nb.sum()
    m["top1pct_share"] = float(nb[:max(1, len(nb) // 100)].sum() / pos) if pos > 0 else None
    m["top5pct_share"] = float(nb[:max(1, len(nb) // 20)].sum() / pos) if pos > 0 else None
    for a in ("T", "M", "V"):
        x = [t["net_bps"] for t in tk if t["arch"] == a]
        m[f"n_{a}"] = len(x); m[f"exp_{a}"] = float(np.mean(x)) if x else None
    return m, r, tk

arms = {}
R = {}; TK = {}
spec = [("C30", breit, 30, .02, None), ("C60", breit, 60, .01, None), ("C120", breit, 120, .005, None),
        ("C120plus", tr, 120, .005, None), ("N30", neu, 30, .02, None),
        ("C30_K1", [t for t in breit if t["klasse"] == "K1"], 30, .02, None), ("C30r", breit, 30, .02, "rank_rand"),
        ("Cinf", tr, 10**6, .005, None), ("N120", neu, 120, .005, None), ("C30plus", tr, 30, .02, None)]
for name, trades, s, f, rk in spec:
    m, r, tk = run(name, trades, s, f, rk)
    arms[name] = m; R[name] = r; TK[name] = tk
    print(name, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items() if k in ("n_trades", "sr", "maxdd", "expectancy_bps", "dropped_share", "total_ret")}, flush=True)

ref = R["C30"][g0:]
cmp_ = {}
def dsr(a, b): return pf.delta_sharpe_ci(R[a][g0:], R[b][g0:])
for a, b in [("C120plus", "C30"), ("C120", "C30"), ("C60", "C30"), ("C120plus", "C120"), ("C30_K1", "C30"), ("C30", "C30r"), ("C30plus", "C30"), ("N120", "C120")]:
    cmp_[f"{a}-{b}"] = dsr(a, b)
# vola-gleich
for n in ("C120", "C120plus", "N30", "Cinf"):
    rr = h48.volmatch(R[n][g0:], ref)
    arms[n]["volmatched_ann_ret"] = float(rr.mean() * 252); arms[n]["volmatched_maxdd"] = pf.max_dd(rr)
arms["C30"]["volmatched_ann_ret"] = float(ref.mean() * 252); arms["C30"]["volmatched_maxdd"] = pf.max_dd(ref)
# P4.1
mon = lambda tk: [h48.entry_month(cal, t["g0"]) for t in tk]
n30 = TK["N30"]; k1c30 = [t["net_bps"] for t in TK["C30"] if t["klasse"] == "K1"]
p41 = dict(zip(("mean", "lo", "hi"), h48.cluster_boot_mean([t["net_bps"] for t in n30], mon(n30))), k1_c30_expectancy=float(np.mean(k1c30)))
p41["pass"] = bool(p41["lo"] > 0 and p41["mean"] >= 0.5 * p41["k1_c30_expectancy"])
# Verdikt
p42 = dict(trade_ratio=arms["C120plus"]["n_trades"] / arms["C30"]["n_trades"], exp_ratio=arms["C120plus"]["expectancy_bps"] / arms["C30"]["expectancy_bps"])
p42["pass"] = bool(p42["trade_ratio"] >= 3 and p42["exp_ratio"] >= 0.5)
c = cmp_["C120plus-C30"]
p43 = dict(dsr=c, dd_c120plus=-arms["C120plus"]["maxdd"], dd_c30=-arms["C30"]["maxdd"])
p43["pass"] = bool(c["point"] > 0 and c["lo"] > -0.20 and p43["dd_c120plus"] <= p43["dd_c30"] + 0.01)
refuted = bool(p41["hi"] < 0 or c["hi"] < 0)
verdict = "WIDERLEGT" if refuted else ("BESTAETIGT" if (p41["pass"] and p42["pass"] and p43["pass"]) else "UNENTSCHIEDEN")
# Regime
reg, ret_ix = __import__("common").regime_array(rows, series, cal) if False else (None, None)
res = dict(verdict_stage_a=verdict, P41=p41, P42=p42, P43=p43, comparisons=cmp_, arms=arms,
           P44=dict(C30=arms["C30"]["dropped_share"], C120plus=arms["C120plus"]["dropped_share"]),
           P45=dict(ratio=arms["C120plus"]["n_ge2000"] / max(1, arms["C30"]["n_ge2000"])),
           universe_neu=dict(n=len(neu_ids), n_after_cost_filter=len({t["id"] for t in neu}), sha256=hashlib.sha256(open("results/H4/universe_neu.csv", "rb").read()).hexdigest()),
           period=[dates[g0], dates[-1]], n_days=cal.n - g0)
json.dump(res, open("results/H4/metrics.json", "w"), indent=1, default=float)
with open(f"{OUT}/portfolio_daily_returns.csv", "w") as f:
    f.write("date," + ",".join(R) + "\n")
    for k in range(g0, cal.n): f.write(dates[k] + "," + ",".join(f"{R[n][k]:.8f}" for n in R) + "\n")
print(json.dumps({k: res[k] for k in ("verdict_stage_a", "P41", "P42", "P43", "P44", "P45")}, indent=1, default=float))
