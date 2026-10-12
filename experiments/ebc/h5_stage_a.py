"""EBC-H5 Stufe A: Haltedauer x Kapazitaet. Ausgabe results/H5/."""
import itertools, json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np
import h48, portfolio as pf, sim, universe
from multiprocessing import Pool

OUT = "results/H5/stage_a"; os.makedirs(OUT, exist_ok=True)
rows, ct, series, cal = h48.setup(with_neu=False)
g0 = h48.g_start(cal)
INF = 10**6
NP = (10, 1.0)
variants = {"T": {"T60": dict(H=60), "T20": dict(H=20), "Tinf": dict(H=INF), "T60np": dict(H=60, np=NP), "Tinfnp": dict(H=INF, np=NP)},
            "M": {"M5": dict(H=5), "M1": dict(H=1), "M2": dict(H=2)},
            "V": {"V20": dict(H=20), "V40": dict(H=40), "Vinf": dict(H=INF), "V20np": dict(H=20, np=NP), "Vinfnp": dict(H=INF, np=NP)}}
built = {}
for a, vs in variants.items():
    for vn, o in vs.items():
        raw = h48.gen_trades(rows, series, archs=(a,), opts_by_arch={a: o})
        built[vn], q = h48.build(rows, ct, series, cal, raw, g0min=g0)
        print(vn, len(built[vn]), flush=True)
CAP = {"C30": (30, .02), "C60": (60, .01), "C120": (120, .005)}
CONF = {  # (T, M, V)
    "H0": ("T60", "M5", "V20"), "HM": ("T60", "M1", "V20"), "T20": ("T20", "M5", "V20"), "Tinf": ("Tinf", "M5", "V20"),
    "Hstar": ("T20", "M1", "V40"), "HL": ("Tinf", "M1", "Vinf"), "H0+NP": ("T60np", "M5", "V20np"), "HL+NP": ("Tinfnp", "M1", "Vinfnp")}
R = {}; TK = {}; ST = {}; M = {}
def run(conf, cap, vs):
    tr = [t for v in vs for t in built[v]]
    s, f = CAP[cap]; st = {}
    r, eq, tk = pf.simulate(tr, cal, slots=s, notional_frac=f, g_from=g0, stats=st)
    return r, tk, st
for cn, vs in CONF.items():
    for cap in CAP:
        r, tk, st = run(cn, cap, vs)
        key = f"{cn}@{cap}"; R[key] = r; TK[key] = tk; f = CAP[cap][1]
        m = h48.summarize(r, tk, g0, key)
        nc = st["n_cand"]; m["dropped_share"] = len(st["dropped"]) / nc if nc else 0
        m["mean_open"] = float(np.mean(st["open"]))
        for a in "TMV":
            x = [t for t in tk if t["arch"] == a]
            m[f"n_{a}"] = len(x); m[f"contrib_pp_{a}"] = float(sum(t["net_bps"] for t in x) / 1e4 * f * 100)
            m[f"slotdays_{a}"] = int(sum(t["bars"] for t in x))
        for rs in ("DISASTER_STOP", "TRAILING_STOP", "TIME_BOX", "SIGNAL", "NO_PROGRESS"):
            m[f"pp_{rs}"] = float(sum(t["net_bps"] for t in tk if t["reason"] == rs) / 1e4 * f * 100)
            m[f"n_{rs}"] = sum(1 for t in tk if t["reason"] == rs)
        M[key] = m
        print(key, round(m["sr"], 2), round(m["maxdd"], 3), m["n_trades"], round(m["expectancy_bps"]), flush=True)
cmp_ = {}
for a, b in [("HM@C30", "H0@C30"), ("Hstar@C30", "H0@C30"), ("HL@C120", "H0@C120"), ("H0+NP@C30", "H0@C30"), ("H0+NP@C120", "H0@C120"),
             ("HL@C30", "H0@C30"), ("HM@C120", "H0@C120"), ("Tinf@C30", "T20@C30"), ("Tinf@C120", "T20@C120"), ("HL+NP@C120", "HL@C120")]:
    cmp_[f"{a}-{b}"] = pf.delta_sharpe_ci(R[a][g0:], R[b][g0:])
inter = pf.contrast_sharpe_ci([R["Tinf@C120"][g0:], R["T20@C120"][g0:], R["Tinf@C30"][g0:], R["T20@C30"][g0:]], [1, -1, -1, 1])
# P5.1 Details
h0, hm = M["H0@C30"], M["HM@C30"]
p51 = dict(dsr=cmp_["HM@C30-H0@C30"], trade_ratio_M=hm["n_M"] / h0["n_M"], ds_loss_M_ratio=None)
msL = lambda tk: sum(t["net_bps"] for t in tk if t["arch"] == "M" and t["reason"] == "DISASTER_STOP")
p51["ds_loss_M_pp"] = {"H0": msL(TK["H0@C30"]) / 1e4 * .02 * 100, "HM": msL(TK["HM@C30"]) / 1e4 * .02 * 100}
p51["ds_loss_M_ratio"] = p51["ds_loss_M_pp"]["HM"] / p51["ds_loss_M_pp"]["H0"] if p51["ds_loss_M_pp"]["H0"] else None
c = p51["dsr"]
p51["pass"] = bool(c["point"] > 0 and c["lo"] > -0.20 and p51["trade_ratio_M"] >= 1.5 and p51["ds_loss_M_ratio"] is not None and p51["ds_loss_M_ratio"] <= 0.5)
p52 = dict(I=inter, pass_=bool(inter["point"] > 0 and inter["lo"] > 0))
refuted = bool(cmp_["HM@C30-H0@C30"]["hi"] < 0 or inter["hi"] < 0)
verdict = "WIDERLEGT" if refuted else ("BESTAETIGT" if (p51["pass"] and p52["pass_"]) else "UNENTSCHIEDEN")

# ---- NP10 eintragsgepaart
def paired_np(args):
    iid, arch, sh = args
    S = series[iid]
    a = sim.independent(S, arch, allow_short=sh, opts=dict(H=sim.ARCH[arch]["H"]))
    b = sim.independent(S, arch, allow_short=sh, opts=dict(H=sim.ARCH[arch]["H"], np=NP))
    mb = {t["signal_i"]: t for t in b}
    out = []
    for t in a:
        u = mb[t["signal_i"]]
        if u["reason"] == "NO_PROGRESS" and t["entry_i"] >= 0:
            out.append((iid, arch, S.ts[t["entry_i"]], t["gross_bps"], u["gross_bps"], t["reason"], u["hold_days"], t["hold_days"], t["side"]))
    return out
import h48 as _h
_h._G["series"] = series
qq = {r["id"]: ct.c_rt(r["id"]) / h48.atr_med(series[r["id"]]) for r in rows}
jobs = [(r["id"], a, r["klasse"] in universe.SHORT_OK) for r in rows if qq[r["id"]] <= 0.10 for a in ("T", "V")]
globals()["series"] = series
with Pool(20) as p: res = [x for y in p.map(paired_np, jobs, chunksize=8) for x in y]
start_ns = cal.days[g0] * pf.DAY_NS
res = [x for x in res if x[2] >= start_ns]
net = lambda iid, g, side, hold: ct.net_bps(iid, g, side, hold)
d = np.array([net(x[0], x[4], x[8], x[6]) - net(x[0], x[3], x[8], x[7]) for x in res])
months = [ (dt.datetime.utcfromtimestamp(x[2] / 1e9).year * 12 + dt.datetime.utcfromtimestamp(x[2] / 1e9).month) for x in res]
m_, lo_, hi_ = h48.cluster_boot_mean(d, months)
n_sig = sum(len(sim.independent(series[r["id"]], a, allow_short=r["klasse"] in universe.SHORT_OK)) for r in rows if qq[r["id"]] <= 0.10 for a in ("T", "V"))
lost = int(sum(1 for x in res if net(x[0], x[3], x[8], x[7]) >= 2000))
np10 = dict(n_triggered=len(res), share_triggered_of_signals=len(res) / n_sig, delta_expectancy_bps=m_, ci90=[lo_, hi_], lost_outliers=lost)

# ---- Gitter und PBO
from automation.optimizer.cpcv import cpcv_group_boundaries, cpcv_paths, probability_of_backtest_overfitting
grid = list(itertools.product(("T20", "T60", "Tinf"), ("M1", "M2", "M5"), ("V20", "V40", "Vinf")))
G, K = 12, 6
paths = cpcv_paths(G, K); n_days = cal.n - g0
bounds = cpcv_group_boundaries(n_days, G)
Pm = np.zeros((len(paths), G)); Qm = np.zeros((len(paths), G))
for pi, (trn, te) in enumerate(paths):
    for g_ in trn: Pm[pi, g_] = 1
    for g_ in te: Qm[pi, g_] = 1
nn = np.array([b - a for (a, b) in bounds], float)
gridsr = {}; pbo = {}
for cap in ("C30", "C120"):
    X = []
    for cfg in grid:
        tr = [t for v in cfg for t in built[v]]
        s, f = CAP[cap]
        r, eq, tk = pf.simulate(tr, cal, slots=s, notional_frac=f, g_from=g0)
        X.append(r[g0:]); gridsr[f"{'/'.join(cfg)}@{cap}"] = pf.sharpe(r[g0:])
    X = np.array(X)
    s1 = np.array([[X[c, a:b].sum() for (a, b) in bounds] for c in range(len(grid))])
    s2 = np.array([[(X[c, a:b] ** 2).sum() for (a, b) in bounds] for c in range(len(grid))])
    def perf(Mm):
        n_ = Mm @ nn; m1 = Mm @ s1.T; m2 = Mm @ s2.T
        mean = m1 / n_[:, None]; var = m2 / n_[:, None] - mean ** 2
        return np.where(var > 1e-24, mean / np.sqrt(np.maximum(var, 1e-24)), 0.0)
    pbo[cap] = probability_of_backtest_overfitting(perf(Pm), perf(Qm))
    print("PBO", cap, pbo[cap], flush=True)
reg, ret_ix = __import__("common").regime_array(rows, series, cal) if False else (None, None)
dates = [str(dt.date(1970, 1, 1) + dt.timedelta(days=d)) for d in cal.days]
res_ = dict(verdict_stage_a=verdict, P51=p51, P52=p52, comparisons=cmp_, np10=np10, pbo=pbo, configs=M,
            grid_best={cap: sorted(((v, k) for k, v in gridsr.items() if k.endswith(cap)), reverse=True)[:5] for cap in ("C30", "C120")},
            period=[dates[g0], dates[-1]])
json.dump(res_, open("results/H5/metrics.json", "w"), indent=1, default=float)
json.dump(gridsr, open(f"{OUT}/grid_sr.json", "w"), indent=1)
with open(f"{OUT}/portfolio_daily_returns.csv", "w") as f:
    f.write("date," + ",".join(R) + "\n")
    for k in range(g0, cal.n): f.write(dates[k] + "," + ",".join(f"{R[n][k]:.8f}" for n in R) + "\n")
print(json.dumps({k: res_[k] for k in ("verdict_stage_a", "P51", "P52", "np10", "pbo", "grid_best")}, indent=1, default=float))
for k, v in cmp_.items(): print(k, {a: round(b, 3) for a, b in v.items() if a in ("point", "lo", "hi")})
