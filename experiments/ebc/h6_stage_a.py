"""EBC-H6 Stufe A: Verlustbudget (Katastrophen-Stop DS1, Risikobudget RB, Volatilitaetssteuerung VT). Ausgabe results/H6/."""
import json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np
import h48, portfolio as pf, sim, universe, common
from multiprocessing import Pool

OUT = "results/H6/stage_a"; os.makedirs(OUT, exist_ok=True)
rows, ct, series, cal = h48.setup(with_neu=False)
g0 = h48.g_start(cal)
dates = [str(dt.date(1970, 1, 1) + dt.timedelta(days=d)) for d in cal.days]
raw = {"DS0": h48.gen_trades(rows, series), "DS1": h48.gen_trades(rows, series, opts_by_arch={a: dict(ds_max=0.45) for a in "TMV"}),
       "DSinf": h48.gen_trades(rows, series, opts_by_arch={a: dict(disaster=False) for a in "TMV"})}
B = {}
for k, v in raw.items():
    B[k], q = h48.build(rows, ct, series, cal, v, g0min=g0)
    print(k, len(B[k]), flush=True)
# --- ATR-Median je globalem Tag (kostengefiltertes Universum) und SPX500-EWMA-Vola
qok = {r["id"] for r in rows if q[r["id"]] <= 0.10}
atrp = np.full((len(qok), cal.n), np.nan)
for n_, i in enumerate(sorted(qok)):
    S = series[i]
    for k in range(S.n):
        if S.atr_ok[k]: atrp[n_, cal.g(S.ts[k])] = S.atr[k] / S.c[k]
atr_med_t = np.nanmedian(atrp, axis=0)
reg, ret_ix = common.regime_array(rows, series, cal)
ret_f = np.nan_to_num(ret_ix); lam = 0.94; var = np.zeros(cal.n); v_ = np.nanmedian(ret_f[ret_f != 0] ** 2)
for k in range(cal.n):
    v_ = lam * v_ + (1 - lam) * ret_f[k] ** 2; var[k] = v_
sig = np.sqrt(var * 252)
sig_ref = np.full(cal.n, np.nan)
for k in range(252, cal.n): sig_ref[k] = np.median(sig[k - 252:k])
s_t = np.clip(sig_ref / sig, 0.25, 1.5); s_t = np.where(np.isnan(s_t), 1.0, s_t)

def sizing(trades, f0, rb, vt):
    out = []
    for t in trades:
        gs = max(t["g0"] - 1, 0)    # globaler Tag des Signal-Schlusskurses (naeherungsweise Vortag des Einstiegs)
        f = f0
        if rb:
            f = float(np.clip(f0 * atr_med_t[gs] / (t["atr_bps"] / 1e4), 0.5 * f0, 3 * f0)) if not np.isnan(atr_med_t[gs]) else f0
        if vt: f = f * s_t[gs]
        out.append(dict(t, frac=f))
    return out

def run(trades, slots, f0, rb=False, vt=False):
    tt = sizing(trades, f0, rb, vt)
    r, eq, tk, expo = pf.simulate_sized(tt, cal, slots=slots, g_from=g0, cap_total=0.60, cap_pos=3 * f0)
    return r, tk, expo

ARMS = {"C30": ("DS0", 30, .02, 0, 0), "L1": ("DS1", 30, .02, 0, 0), "L2": ("DS0", 30, .02, 1, 0), "L3": ("DS0", 30, .02, 0, 1),
        "L123": ("DS1", 30, .02, 1, 1), "DSinf": ("DSinf", 30, .02, 0, 0), "L12": ("DS1", 30, .02, 1, 0), "L13": ("DS1", 30, .02, 0, 1),
        "C120": ("DS0", 120, .005, 0, 0), "L1@C120": ("DS1", 120, .005, 0, 0), "L123@C120": ("DS1", 120, .005, 1, 1)}
R = {}; TK = {}; EX = {}; M = {}
for n, (ds, s, f, rb, vt) in ARMS.items():
    r, tk, ex = run(B[ds], s, f, rb, vt); R[n] = r; TK[n] = tk; EX[n] = ex
ref = R["C30"][g0:]
# Plain-B+ Gegenprobe
r_plain, eq_p, tk_p = pf.simulate(B["DS0"], cal, slots=30, g_from=g0)
par = dict(simulate=dict(n=len(tk_p), sr=pf.sharpe(r_plain[g0:]), maxdd=pf.max_dd(r_plain[g0:])))
for n in ARMS:
    tk = TK[n]; r = R[n][g0:]
    m = h48.summarize(R[n], tk, g0, n)
    rv = h48.volmatch(r, ref)
    m["maxdd_volmatched"] = pf.max_dd(rv); m["cvar5_volmatched"] = pf.cvar5(rv); m["ann_ret_volmatched"] = float(rv.mean() * 252)
    m["mean_exposure"] = float(EX[n][g0:].mean())
    loss = sum(t["net_bps"] * (t["f"]) for t in tk if t["net_bps"] < 0)
    dsl = sum(t["net_bps"] * (t["f"]) for t in tk if t["reason"] == "DISASTER_STOP")
    m["ds_share_gross_losses"] = dsl / loss if loss else 0.0
    m["ds_share_trades"] = sum(1 for t in tk if t["reason"] == "DISASTER_STOP") / len(tk)
    m["contrib_pp"] = float(sum(t["net_bps"] * t["f"] for t in tk) / 1e4 * 100)
    M[n] = m
    print(n, {k: round(v, 3) for k, v in m.items() if k in ("sr", "maxdd", "maxdd_volmatched", "cvar5_volmatched", "n_trades", "expectancy_bps", "ds_share_gross_losses", "ann_ret_volmatched", "total_ret")}, flush=True)
par["C30_sized"] = dict(n=M["C30"]["n_trades"], sr=M["C30"]["sr"], maxdd=M["C30"]["maxdd"])
cmp_ = {k: pf.delta_sharpe_ci(R[a][g0:], R[b][g0:]) for k, (a, b) in
        {"L1-C30": ("L1", "C30"), "L2-C30": ("L2", "C30"), "L3-C30": ("L3", "C30"), "L123-C30": ("L123", "C30"), "L12-C30": ("L12", "C30"),
         "L13-C30": ("L13", "C30"), "DSinf-C30": ("DSinf", "C30"), "L123@C120-C120": ("L123@C120", "C120"), "L1@C120-C120": ("L1@C120", "C120")}.items()}
# ---- eintragsgepaart DS0 / DS1 / DSinf
def paired(args):
    iid, arch, sh = args
    S = series[iid]
    a = {t["signal_i"]: t for t in sim.independent(S, arch, allow_short=sh)}
    b = {t["signal_i"]: t for t in sim.independent(S, arch, allow_short=sh, opts=dict(ds_max=0.45))}
    c = {t["signal_i"]: t for t in sim.independent(S, arch, allow_short=sh, opts=dict(disaster=False))}
    out = []
    for s_, t in a.items():
        out.append((iid, arch, S.ts[t["entry_i"]], t["side"], t["gross_bps"], b[s_]["gross_bps"], c[s_]["gross_bps"], t["reason"], b[s_]["reason"], t["hold_days"], b[s_]["hold_days"], c[s_]["hold_days"], t["whipsaw"], t["stop_gap_bps"]))
    return out
common_ = h48._G; common_["series"] = series
jobs = [(r["id"], a, r["klasse"] in universe.SHORT_OK) for r in rows if q[r["id"]] <= 0.10 for a in "TMV"]
with Pool(20) as p: pr = [x for y in p.map(paired, jobs, chunksize=8) for x in y]
st_ns = cal.days[g0] * pf.DAY_NS
pr = [x for x in pr if x[2] >= st_ns]
d01 = np.array([ct.net_bps(x[0], x[5], x[3], x[10]) - ct.net_bps(x[0], x[4], x[3], x[9]) for x in pr])
mon = [ (dt.datetime.fromtimestamp(x[2] / 1e9, dt.timezone.utc).year * 12 + dt.datetime.fromtimestamp(x[2] / 1e9, dt.timezone.utc).month) for x in pr]
dm, dlo, dhi = h48.cluster_boot_mean(d01, mon)
net0 = np.array([ct.net_bps(x[0], x[4], x[3], x[9]) for x in pr]); net1 = np.array([ct.net_bps(x[0], x[5], x[3], x[10]) for x in pr])
dsx = [x for x in pr if x[7] == "DISASTER_STOP"]
newr = {}
for x in dsx: newr[x[8]] = newr.get(x[8], 0) + 1
paired_res = dict(n_signals=len(pr), delta_expectancy_bps=dm, ci90=[dlo, dhi], expectancy_ds0=float(net0.mean()), expectancy_ds1=float(net1.mean()),
                  share_outcome_changed=float(np.mean(np.abs(net1 - net0) > 1e-9)), n_ds0_exits=len(dsx), former_ds_new_reason=newr,
                  whipsaw_share_ds0=float(np.mean([x[12] for x in dsx])) if dsx else None,
                  mean_net_former_ds_exit_ds0=float(np.mean([ct.net_bps(x[0], x[4], x[3], x[9]) for x in dsx])),
                  mean_net_former_ds_exit_ds1=float(np.mean([ct.net_bps(x[0], x[5], x[3], x[10]) for x in dsx])),
                  cvar5_trade_ds0=float(np.sort(net0)[:max(1, int(.05 * len(net0)))].mean()), cvar5_trade_ds1=float(np.sort(net1)[:max(1, int(.05 * len(net1)))].mean()))
# ---- Regime
regd = common.regime_table({n: R[n] for n in ("C30", "L1", "L2", "L3", "L123")}, g0, reg, ret_ix)
# ---- Verdikt
p61 = dict(ds_share=M["L1"]["ds_share_gross_losses"], d_exp=paired_res["delta_expectancy_bps"], lo=dlo)
p61["pass"] = bool(p61["ds_share"] <= 0.25 and dm > 0 and dlo > -5)
c = cmp_["L123-C30"]
p62 = dict(dd_ratio=M["L123"]["maxdd_volmatched"] / M["C30"]["maxdd_volmatched"], cvar_ratio=M["L123"]["cvar5_volmatched"] / M["C30"]["cvar5_volmatched"],
           dsr=c, trades_ratio=M["L123"]["n_trades"] / M["C30"]["n_trades"])
p62["pass"] = bool(p62["dd_ratio"] <= 0.75 and p62["cvar_ratio"] <= 0.80 and c["lo"] > -0.15 and p62["trades_ratio"] >= 0.9)
refuted = bool(dhi < 0 or (c["hi"] < 0 and M["L123"]["maxdd_volmatched"] >= M["C30"]["maxdd_volmatched"]))
verdict = "WIDERLEGT" if refuted else ("BESTAETIGT" if p61["pass"] and p62["pass"] else "UNENTSCHIEDEN")
out = dict(verdict_stage_a=verdict, P61=p61, P62=p62, paired=paired_res, comparisons=cmp_, arms=M, parity=par, regimes=regd, period=[dates[g0], dates[-1]])
json.dump(out, open("results/H6/metrics.json", "w"), indent=1, default=float)
with open(f"{OUT}/portfolio_daily_returns.csv", "w") as f:
    f.write("date," + ",".join(R) + "\n")
    for k in range(g0, cal.n): f.write(dates[k] + "," + ",".join(f"{R[n][k]:.8f}" for n in R) + "\n")
print(json.dumps({k: out[k] for k in ("verdict_stage_a", "P61", "P62", "paired", "parity")}, indent=1, default=float))
for k, v in cmp_.items(): print(k, {a: round(b, 3) for a, b in v.items() if a in ("point", "lo", "hi")})
