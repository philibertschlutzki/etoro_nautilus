"""EBC-H7 Stufe A: Signal-Breite (R, X, W) mit Platzquoten. Ausgabe results/H7/."""
import json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np
import h48, h7_arch, portfolio as pf, sim, universe, common

OUT = "results/H7/stage_a"; os.makedirs(OUT, exist_ok=True)
rows, ct, series, cal = h48.setup(with_neu=False)
g0 = h48.g_start(cal)
dates = [str(dt.date(1970, 1, 1) + dt.timedelta(days=d)) for d in cal.days]
raw = h48.gen_trades(rows, series)
rr = h48.gen_trades([r for r in rows if r["klasse"] in ("K1", "K4")], series, archs=("R",))
raw.update(rr)
W_ETF = ["SPY", "IVV", "VOO", "QQQ", "IWM", "DIA", "VTI", "EFA", "EEM", "VEA", "VWO"]
w_ids = [r["id"] for r in rows if r["klasse"] == "K4" or r["symbol"] in W_ETF]
for i in w_ids: raw[(i, "W")] = h7_arch.w_trades(series[i])
q0 = {r["id"]: ct.c_rt(r["id"]) / h48.atr_med(series[r["id"]]) for r in rows}
qok = {i for i, v in q0.items() if v <= 0.10}
spx = next(r for r in rows if r["symbol"] == "SPX500")["id"]
xt = h7_arch.x_trades(rows, series, cal, qok, spx)
for i, trs in xt.items(): raw[(i, "X")] = trs
# Trades mit Netto/Vektor
def build_arch(arch):
    sub = {k: v for k, v in raw.items() if k[1] == arch}
    return h48.build(rows, ct, series, cal, sub, g0min=g0)[0]
T = {a: build_arch(a) for a in "TMVRXW"}
for t in T["X"]: t["rank"] = t["mom_rank"]
for a, v in T.items():
    for t in v: t["sleeve"] = a
    print(a, "Trades", len(v), flush=True)
# ---- Handpruefung (20 zufaellige Trades je neuem Archetyp, aus den Kerzen unabhaengig nachgerechnet)
rng = np.random.default_rng(h48.SEED); hand = []
for a in "RXW":
    pick = rng.choice(len(T[a]), size=20, replace=False)
    for n in pick:
        t = T[a][n]; S = series[t["id"]]; e = t["entry_i"]; s = t["signal_i"]
        ok = abs(S.o[e] - t["entry_px"]) < 1e-12 and e == s + 1
        if a == "R":
            ibs = (S.c[s] - S.l[s]) / (S.h[s] - S.l[s]); ok &= (ibs < .15) and (S.c[s] > S.sma200[s])
        if a == "W":
            ok &= S.ts[e] // 86400_000_000_000 >= 0 and h7_arch._ym(S.ts[e]) == h7_arch._ym(S.ts[s]) and (e == S.n - 1 or h7_arch._ym(S.ts[e + 1]) != h7_arch._ym(S.ts[e]))
        px = S.o[t["exit_i"]] if t["reason"] in ("SIGNAL", "TIME_BOX", "TRAILING_STOP", "NO_PROGRESS") else t["exit_px"]
        g = (px / S.o[e] - 1) * 1e4
        ok &= abs(g - t["gross_bps"]) < 1e-6
        hand.append(dict(archetyp=a, instrument=t["id"], signal=str(dt.date(1970, 1, 1) + dt.timedelta(days=int(S.ts[s] // 86400_000_000_000))), reason=t["reason"], gross_bps=round(t["gross_bps"], 3), nachgerechnet_bps=round(g, 3), ok=bool(ok)))
import csv
with open(f"{OUT}/handcheck.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(hand[0])); w.writeheader(); w.writerows(hand)
print("Handprüfung ok:", sum(h["ok"] for h in hand), "/", len(hand), flush=True)
# ---- Sleeve-Arme
def run(names, quotas, f=0.005, cap=0.60, pool=None, tradelist=None):
    tl = tradelist if tradelist is not None else [t for a in names for t in T[a]]
    if pool: tl = [dict(t, sleeve=pool) for t in tl]
    st = {}
    r, eq, tk = pf.simulate_sleeves(tl, cal, quotas, notional_frac=f, cap_total=cap, g_from=g0, stats=st)
    return r, tk, st
ARM = {}
ARM["TMV-Q"] = run("TMV", {"T": 40, "M": 40, "V": 40})
ARM["Kombi6-Q"] = run("TMVRXW", {a: 20 for a in "TMVRXW"})
for a in "RXWM": ARM[f"{a}-allein"] = run([a], {a: 20})
ARM["Kombi6-P"] = run("TMVRXW", {"P": 120}, pool="P")
ARM["TMV120"] = run("TMV", {"P": 120}, pool="P")
tl = [dict(t, sleeve="P") for a in "TMV" for t in T[a]] + [t for a in "RXW" for t in T[a]]
ARM["TMV120+RXW"] = run(None, {"P": 120, "R": 20, "X": 20, "W": 20}, cap=0.90, tradelist=tl)
ARM["Kombi5-Q"] = run("TMVRW", {a: 24 for a in "TMVRW"})
ARM["TMV+R-Q"] = run("TMVR", {a: 30 for a in "TMVR"})
ARM["TMV+W-Q"] = run("TMVW", {a: 30 for a in "TMVW"})
ARM["TMV+X-Q"] = run("TMVX", {a: 30 for a in "TMVX"})
# Test: ein Sleeve = simulate
r_a, _, tk_a = pf.simulate([dict(t) for t in T["M"]], cal, slots=20, notional_frac=.005, g_from=g0)
test_equal = bool(np.allclose(r_a, ARM["M-allein"][0]) and len(tk_a) == len(ARM["M-allein"][1]))
R = {n: v[0] for n, v in ARM.items()}
M = {}
for n, (r, tk, st) in ARM.items():
    m = h48.summarize(r, tk, g0, n)
    m["per_sleeve"] = {sl: dict(n=sum(1 for t in tk if t["sleeve"] == sl), contrib_pp=float(sum(t["net_bps"] for t in tk if t["sleeve"] == sl) / 1e4 * .5),
                                dropped=len(st[sl]["dropped"]), n_cand=st[sl]["n_cand"],
                                mean_open=float(np.mean(st[sl]["open"][g0:])) if "open" in st[sl] else None) for sl in st}
    M[n] = m
    print(n, {k: round(v, 3) for k, v in m.items() if k in ("sr", "maxdd", "n_trades", "expectancy_bps", "total_ret")}, flush=True)
# ---- Archetyp-Steckbrief
ix = common.regime_array(rows, series, cal)[1]
ix = np.nan_to_num(ix)
def alpha(r):
    y = np.asarray(r)[g0:]; x = ix[g0:]
    X = np.vstack([np.ones_like(x), x]).T
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    from automation.optimizer.bootstrap import optimal_block_length
    idx = pf.boot_indices(len(y), optimal_block_length(y), 2000, np.random.default_rng(h48.SEED))
    al = []
    for ii in idx[:2000]:
        yy, xx = y[ii], x[ii]; XX = np.vstack([np.ones_like(xx), xx]).T
        al.append(np.linalg.lstsq(XX, yy, rcond=None)[0][0] * 252)
    return dict(alpha_ann=float(b[0] * 252), ci90=[float(np.percentile(al, 5)), float(np.percentile(al, 95))], beta=float(b[1]))
def corr(a, b, mask=None):
    a = np.asarray(a)[g0:]; b = np.asarray(b)[g0:]
    if mask is not None: a, b = a[mask], b[mask]
    return float(np.corrcoef(a, b)[0, 1])
tmv = R["TMV-Q"]; worst = tmv[g0:] <= np.percentile(tmv[g0:], 10)
arch_tab = {}
for a in "RXWM":
    tl = T[a]; net = [t["net_bps"] for t in tl]
    mon = [h48.entry_month(cal, t["g0"]) for t in tl]
    mean, lo, hi = h48.cluster_boot_mean(net, mon)
    sl = ARM[f"{a}-allein"]
    tk = sl[1]; net_t = [t["net_bps"] for t in tk]
    mean_t, lo_t, hi_t = h48.cluster_boot_mean(net_t, [h48.entry_month(cal, t["g0"]) for t in tk])
    arch_tab[a] = dict(n_signals_trades=len(tl), expectancy_all_bps=mean, ci90_all=[lo, hi], n_taken=len(tk), expectancy_taken_bps=mean_t, ci90_taken=[lo_t, hi_t],
                       hit=float(np.mean(np.array(net) > 0)), median_hold_days=float(np.median([t["hold_days"] for t in tl])),
                       gross_exp_bps=float(np.mean([t["gross_bps"] for t in tl])), contrib_pp_allein=float(sum(net_t) / 1e4 * .5),
                       alpha=alpha(R[f"{a}-allein"]), corr_tmv=corr(R[f"{a}-allein"], tmv), corr_tmv_worst10=corr(R[f"{a}-allein"], tmv, worst),
                       corr_M=corr(R[f"{a}-allein"], R["M-allein"]), sr_allein=M[f"{a}-allein"]["sr"])
# Stress: +4 bps RT-Kosten fuer R/W (Unsicherheit der Spread-Messung)
for a in "RW":
    net = [t["net_bps"] - 4.0 for t in T[a]]
    mean, lo, hi = h48.cluster_boot_mean(net, [h48.entry_month(cal, t["g0"]) for t in T[a]])
    arch_tab[a]["stress_plus4bps_expectancy"] = mean; arch_tab[a]["stress_plus4bps_ci90"] = [lo, hi]
# Ueberlappung R/M
msig = {}
for t in T["M"]: msig.setdefault(t["id"], []).append(t["signal_i"])
ov = [any(abs(s - t["signal_i"]) <= 1 for s in msig.get(t["id"], [])) for t in T["R"]]
nr = np.array([t["net_bps"] for t in T["R"]]); ovm = np.array(ov)
overlap = dict(share=float(ovm.mean()), exp_with=float(nr[ovm].mean()) if ovm.any() else None, exp_without=float(nr[~ovm].mean()))
cmp_ = {f"{a}-TMV-Q": pf.delta_sharpe_ci(R[a][g0:], R["TMV-Q"][g0:]) for a in ("Kombi6-Q", "Kombi5-Q", "TMV+R-Q", "TMV+W-Q", "TMV+X-Q", "Kombi6-P")}
cmp_["TMV120+RXW-TMV120"] = pf.delta_sharpe_ci(R["TMV120+RXW"][g0:], R["TMV120"][g0:])
ref = R["TMV120"][g0:]; rv = h48.volmatch(R["TMV120+RXW"][g0:], ref)
div = dict(TMV120_maxdd=pf.max_dd(ref), RXW_volmatched_maxdd=pf.max_dd(rv))
# Verdikt
p71 = {}
for a in "RXW":
    d = arch_tab[a]
    ok = (d["ci90_taken"][0] > 0) if a == "R" else (d["expectancy_taken_bps"] > 0 and d["alpha"]["alpha_ann"] > 0)
    p71[a] = bool(ok)
p72 = {a: bool(arch_tab[a]["corr_tmv"] <= 0.5 and (a != "R" or arch_tab["R"]["corr_M"] <= 0.6)) for a in "RXW"}
c = cmp_["Kombi6-Q-TMV-Q"]
p73 = dict(trade_ratio=M["Kombi6-Q"]["n_trades"] / M["TMV-Q"]["n_trades"], dsr=c, dd_k=-M["Kombi6-Q"]["maxdd"], dd_t=-M["TMV-Q"]["maxdd"])
p73["pass"] = bool(p73["trade_ratio"] >= 1.5 and c["point"] > 0 and c["lo"] > -0.10 and p73["dd_k"] <= p73["dd_t"] + 0.01)
refuted = bool(arch_tab["R"]["ci90_taken"][1] < 0 or c["hi"] < 0)
allp = all(p71.values()) and all(p72.values()) and p73["pass"]
verdict = "WIDERLEGT" if refuted else ("BESTAETIGT" if allp else "UNENTSCHIEDEN")
out = dict(verdict_stage_a=verdict, P71=p71, P72=p72, P73=p73, archetypes=arch_tab, overlap_RM=overlap, comparisons=cmp_, arms=M,
           diag=div, test_single_sleeve_equals_simulate=test_equal, handcheck_ok=int(sum(h["ok"] for h in hand)),
           bplusplus_candidates=[a for a in "RXW" if p71[a] and p72[a]], period=[dates[g0], dates[-1]])
json.dump(out, open("results/H7/metrics.json", "w"), indent=1, default=float)
with open(f"{OUT}/sleeves_daily_returns.csv", "w") as f:
    f.write("date," + ",".join(R) + "\n")
    for k in range(g0, cal.n): f.write(dates[k] + "," + ",".join(f"{R[n][k]:.8f}" for n in R) + "\n")
print(json.dumps({k: out[k] for k in ("verdict_stage_a", "P71", "P72", "P73", "overlap_RM", "diag", "test_single_sleeve_equals_simulate")}, indent=1, default=float))
for a, d in arch_tab.items(): print(a, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items() if k not in ("alpha",)}, d["alpha"])
for k, v in cmp_.items(): print(k, {a: round(b, 3) for a, b in v.items() if a in ("point", "lo", "hi")})
