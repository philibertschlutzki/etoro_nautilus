"""EBC-H3 Stufe A (Tagesachse): E1 Stop, E2 TTP, Short-Layer, Zeitbox. Ausgabe results/H3/stage_a/*"""
import csv, datetime as dt, json, math, os, pickle, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np, pandas as pd
import common, portfolio as pf, sim, universe

t0 = time.time()
def P(*a): print(round(time.time() - t0), "s", *a, flush=True)
OUT = "results/H3/stage_a"; os.makedirs(OUT, exist_ok=True)
rows, ct, series, cal = common.setup()
by_id = {r["id"]: r for r in rows}
q = {r["id"]: common.q_of(r, ct, series[r["id"]]) for r in rows}
bp = [r for r in rows if q[r["id"]] <= 0.10]
OOS_I = 250
B = 5000; rng = np.random.default_rng(20261011)

def month_of(S, i):
    d = dt.date(1970, 1, 1) + dt.timedelta(days=int(S.ts[i] // pf.DAY_NS))
    return d.year * 12 + d.month - 1

def signals_variant(opts, allow_short_classes=universe.SHORT_OK):
    """Eintrags-gepaart: {(id, arch, signal_i, side): trade} fuer eine Exit-Variante."""
    out = {}
    for r in bp:
        i = r["id"]; S = series[i]
        o = dict(opts); o["min_lock_bps"] = 2.0 * ct.p[i]["spread"] + ct.p[i]["comm"]
        for a in common.ARCHS:
            for t in sim.independent(S, a, allow_short=(r["klasse"] in allow_short_classes), opts=o):
                if t["signal_i"] < OOS_I: continue
                t["net_bps"] = ct.net_bps(i, t["gross_bps"], t["side"], t["hold_days"])
                t["month"] = month_of(S, t["entry_i"])
                out[(i, a, t["signal_i"], t["side"])] = t
    return out

# ---------------- E1 / E2 : gepaarte Varianten
variants = {"S0P0": dict(stop="S0", ttp="P0"), "S1P0": dict(stop="S1", ttp="P0"), "S2P0": dict(stop="S2", ttp="P0"),
            "S0P1": dict(stop="S0", ttp="P1"), "S0P2": dict(stop="S0", ttp="P2"), "S0P3": dict(stop="S0", ttp="P3")}
V = {}
for name, o in variants.items():
    V[name] = signals_variant(o); P("Variante", name, len(V[name]))
keys = sorted(V["S0P0"].keys())
assert all(set(V[n].keys()) == set(keys) for n in V), "Signalmengen muessen uebereinstimmen"
arch_of = np.array([k[1] for k in keys]); cls_of = np.array([by_id[k[0]]["klasse"] for k in keys])
months = np.array([V["S0P0"][k]["month"] for k in keys])
R = {n: np.array([V[n][k]["net_bps"] for k in keys]) for n in V}
um, inv = np.unique(months, return_inverse=True)
nm = len(um)
cnt = np.bincount(inv, minlength=nm).astype(float)
def month_sums(x): return np.bincount(inv, weights=x, minlength=nm)
midx = rng.integers(0, nm, size=(B, nm))
def boot_mean_diff(x1, x0):
    d = x1 - x0; s = month_sums(d)
    reps = s[midx].sum(1) / cnt[midx].sum(1)
    return float(d.mean()), float(np.percentile(reps, 5)), float(np.percentile(reps, 95)), reps
def cvar5(x):
    x = np.sort(x); k = max(1, int(math.ceil(0.05 * len(x)))); return x[:k].mean()
month_members = [np.nonzero(inv == m)[0] for m in range(nm)]
midx_cv = rng.integers(0, nm, size=(1000, nm))
def boot_cvar_improve(x0, x1):
    """(CVaR5_S1 - CVaR5_S0)/|CVaR5_S0| (positiv = S1 besser); Cluster-Bootstrap nach Monat."""
    pt = (cvar5(x1) - cvar5(x0)) / abs(cvar5(x0))
    reps = []
    for rr in midx_cv:
        ix = np.concatenate([month_members[m] for m in rr])
        c0, c1 = cvar5(x0[ix]), cvar5(x1[ix])
        reps.append((c1 - c0) / abs(c0))
    return float(pt), float(np.percentile(reps, 5)), float(np.percentile(reps, 95)), np.array(reps)

# Stop-Zusammenfassung je Archetyp/Klasse/Variante
sum_rows = []
for vn in ("S0P0", "S1P0", "S2P0"):
    for a in common.ARCHS + ("alle",):
        for k in ("K1", "K2", "K3", "K4", "alle"):
            m = np.ones(len(keys), bool)
            if a != "alle": m &= arch_of == a
            if k != "alle": m &= cls_of == k
            if m.sum() == 0: continue
            tr = [V[vn][keys[j]] for j in np.nonzero(m)[0]]
            x = R[vn][m]
            stops = [t for t in tr if t["stop_gap_bps"] is not None]
            sum_rows.append(dict(variante=vn, archetyp=a, klasse=k, n=int(m.sum()), expectancy_bps=float(x.mean()), cvar5_bps=float(cvar5(x)),
                                 loss_per_stop_exit_bps=float(np.mean([t["net_bps"] for t in stops])) if stops else None, n_stop_exits=len(stops),
                                 stop_gap_bps=float(np.mean([t["stop_gap_bps"] for t in stops])) if stops else None,
                                 whipsaw_share=float(np.mean([t["whipsaw"] for t in stops])) if stops else None,
                                 hold_days=float(np.mean([t["hold_days"] for t in tr]))))
with open(f"{OUT}/stop_summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(sum_rows[0].keys())); w.writeheader(); w.writerows(sum_rows)
# P3.1
d_exp, lo, hi, reps_e = boot_mean_diff(R["S1P0"], R["S0P0"])
imp, ilo, ihi, reps_c = boot_cvar_improve(R["S0P0"], R["S1P0"])
P31 = dict(expectancy_S0=float(R["S0P0"].mean()), expectancy_S1=float(R["S1P0"].mean()), delta_expectancy=dict(point=d_exp, lo=lo, hi=hi),
           cvar5_S0=float(cvar5(R["S0P0"])), cvar5_S1=float(cvar5(R["S1P0"])), cvar5_improvement=dict(point=imp, lo=ilo, hi=ihi),
           passed=bool(imp >= 0.20 and lo > -2.0),
           refuted=bool(boot_mean_diff(R["S0P0"], R["S1P0"])[1] > 0 and np.percentile(-reps_c, 5) > 0),
           n_signals=len(keys))
d2, l2, h2_, _ = boot_mean_diff(R["S2P0"], R["S0P0"])
P31["S2_vs_S0_expectancy"] = dict(point=d2, lo=l2, hi=h2_)
P31["by_archetype"] = {}
for a in common.ARCHS:
    m = arch_of == a
    P31["by_archetype"][a] = dict(exp_S0=float(R["S0P0"][m].mean()), exp_S1=float(R["S1P0"][m].mean()), cvar5_S0=float(cvar5(R["S0P0"][m])), cvar5_S1=float(cvar5(R["S1P0"][m])),
                                  n=int(m.sum()))
json.dump(P31, open(f"{OUT}/p31.json", "w"), indent=1, default=float)
P("P3.1", {k: P31[k] for k in ("delta_expectancy", "cvar5_improvement", "passed", "refuted")})

# P3.2 TTP
ttp = {}
reps_by = {}
for a in common.ARCHS:
    m = arch_of == a
    for pv in ("P1", "P2", "P3"):
        x1, x0 = R[f"S0{pv}"][m], R["S0P0"][m]
        d = x1 - x0
        mon = inv[m]
        s_ = np.bincount(mon, weights=d, minlength=nm); c_ = np.bincount(mon, minlength=nm).astype(float)
        reps = s_[midx].sum(1) / np.maximum(c_[midx].sum(1), 1)
        ttp.setdefault(pv, {})[a] = dict(delta=float(d.mean()), lo=float(np.percentile(reps, 5)), hi=float(np.percentile(reps, 95)), n=int(m.sum()),
                                         exp_P0=float(x0.mean()), exp_Pv=float(x1.mean()))
        reps_by[(pv, a)] = reps
inter = {}
for pv in ("P1", "P2", "P3"):
    d = {a: ttp[pv][a]["delta"] for a in common.ARCHS}
    pt = d["M"] - 0.5 * (d["T"] + d["V"])
    reps = reps_by[(pv, "M")] - 0.5 * (reps_by[(pv, "T")] + reps_by[(pv, "V")])
    inter[pv] = dict(point=float(pt), lo=float(np.percentile(reps, 5)), hi=float(np.percentile(reps, 95)))
P32 = dict(delta_by_archetype=ttp, interaction=inter,
           passed=bool(ttp["P1"]["M"]["delta"] > 0 and ttp["P1"]["T"]["delta"] < 0 and ttp["P1"]["V"]["delta"] < 0 and inter["P1"]["lo"] > 0),
           refuted=bool(inter["P1"]["hi"] < 0))
json.dump(P32, open(f"{OUT}/ttp_interaction.json", "w"), indent=1, default=float)
P("P3.2", {"dM": ttp["P1"]["M"]["delta"], "dT": ttp["P1"]["T"]["delta"], "dV": ttp["P1"]["V"]["delta"], "inter": inter["P1"]})
# exits_paired.parquet
recs = []
for vn, d in V.items():
    for k in keys:
        t = d[k]
        recs.append(dict(signal_id=f"{k[0]}_{k[1]}_{k[2]}_{k[3]}", archetyp=k[1], klasse=by_id[k[0]]["klasse"], variante=vn, rendite_bps=t["net_bps"],
                         exit_grund=t["reason"], haltedauer=t["hold_days"]))
pd.DataFrame(recs).to_parquet(f"{OUT}/exits_paired.parquet")

# ---------------- Short-Layer (Z0..Z3) als Portfolio
def z_trades(zname):
    out = []
    for r in bp:
        i = r["id"]; S = series[i]; k = r["klasse"]
        if zname == "Z0": sh, gate = False, False
        elif zname == "Z1": sh, gate = True, False
        elif zname == "Z2": sh, gate = True, True
        else: sh, gate = (k in ("K2", "K3", "K4")), True
        for a in common.ARCHS:
            for t in sim.sequential(S, a, allow_short=sh, short_gate=gate):
                if t["signal_i"] < OOS_I: continue
                g0, v = pf.trade_vector(S, t, cal, ct, i)
                out.append(dict(t, id=i, arch=a, klasse=k, g0=g0, vec=v, rank=q[i], tie=int(i), key=(i, a),
                                net_bps=ct.net_bps(i, t["gross_bps"], t["side"], t["hold_days"])))
    return out
Z = {}
G0 = int(min(t["g0"] for t in z_trades("Z0")))
for zn in ("Z0", "Z1", "Z2", "Z3"):
    tr = z_trades(zn)
    r, eq, tk = pf.simulate(tr, cal, slots=30, g_from=G0)
    Z[zn] = dict(all=tr, r=r, taken=tk, eq=eq)
    P("Z", zn, len(tr), "SR", round(pf.sharpe(r[G0:]), 2))
reg, ret_ix = common.regime_array(rows, series, cal)
zrows = []
def side_exp(tr, side, cls=None):
    x = [t["net_bps"] for t in tr if t["side"] == side and (cls is None or t["klasse"] == cls)]
    return (float(np.mean(x)), len(x)) if x else (None, 0)
for zn in Z:
    r = Z[zn]["r"][G0:]
    s_eq, n_eq = side_exp(Z[zn]["all"], -1, "K1"); s_fx, n_fx = side_exp(Z[zn]["all"], -1, "K2")
    s_all, n_all = side_exp(Z[zn]["all"], -1)
    # Korrelation Short-/Long-Seite in den 10 % schlechtesten Portfoliotagen
    eqs = Z[zn]["eq"]
    eprev = np.concatenate([[10_000.0], eqs[:-1]])
    long_p = np.zeros(cal.n); short_p = np.zeros(cal.n)
    for t in Z[zn]["taken"]:
        for kx, vv in enumerate(t["vec"]):
            g = t["g0"] + kx
            if g < G0: continue
            (long_p if t["side"] > 0 else short_p)[g] += t["N"] * vv / eprev[g]
    worst = np.argsort(r)[: max(5, int(0.1 * len(r)))] + G0
    corr = float(np.corrcoef(long_p[worst], short_p[worst])[0, 1]) if short_p[worst].std() > 0 and long_p[worst].std() > 0 else None
    fin = 0.0; gross_short = 0.0
    for t in Z[zn]["all"]:
        if t["side"] < 0:
            fin += ct.p[t["id"]]["fin_short"] * t["hold_days"]; gross_short += abs(t["gross_bps"])
    zrows.append(dict(variante=zn, sr=pf.sharpe(r), maxdd=pf.max_dd(r), ann_ret=float(r.mean() * 252), n_short_trades=n_all, short_exp_bps_all=s_all,
                      short_exp_bps_equity=s_eq, n_short_equity=n_eq, short_exp_bps_fx=s_fx, n_short_fx=n_fx,
                      short_financing_bps_total=fin, corr_long_short_worst10=corr))
with open(f"{OUT}/short_layer.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(zrows[0].keys())); w.writeheader(); w.writerows(zrows)
zr = {z["variante"]: z for z in zrows}
dsr_z2 = pf.delta_sharpe_ci(Z["Z2"]["r"][G0:], Z["Z0"]["r"][G0:])
dd0, dd2 = zr["Z0"]["maxdd"], zr["Z2"]["maxdd"]
dd_red = (abs(dd0) - abs(dd2)) / abs(dd0) if dd0 else 0.0
# Z1 vs Z2 Aktien-Shorts (Cluster-Bootstrap nach Monat der Einstiege)
def eq_short_months(zn):
    xs = []; ms = []
    for t in Z[zn]["all"]:
        if t["side"] < 0 and t["klasse"] == "K1":
            xs.append(t["net_bps"]); ms.append(month_of(series[t["id"]], t["entry_i"]))
    return np.array(xs), np.array(ms)
x1, m1 = eq_short_months("Z1"); x2, m2 = eq_short_months("Z2")
allm = np.unique(np.concatenate([m1, m2])); mi = {m: j for j, m in enumerate(allm)}
def msc(x, m):
    s_ = np.zeros(len(allm)); c_ = np.zeros(len(allm))
    for xv, mv in zip(x, m): s_[mi[mv]] += xv; c_[mi[mv]] += 1
    return s_, c_
s1, c1 = msc(x1, m1); s2, c2 = msc(x2, m2)
ix = rng.integers(0, len(allm), size=(B, len(allm)))
rep1 = s1[ix].sum(1) / np.maximum(c1[ix].sum(1), 1); rep2 = s2[ix].sum(1) / np.maximum(c2[ix].sum(1), 1)
diff = rep1 - rep2
P33 = dict(exp_Z1_equity_short=float(x1.mean()) if len(x1) else None, n_Z1_equity_short=int(len(x1)),
           exp_Z2_equity_short=float(x2.mean()) if len(x2) else None, n_Z2_equity_short=int(len(x2)),
           exp_Z1_equity_ci90=[float(np.percentile(rep1, 5)), float(np.percentile(rep1, 95))],
           exp_Z2_equity_ci90=[float(np.percentile(rep2, 5)), float(np.percentile(rep2, 95))],
           Z1_minus_Z2_equity=dict(point=float(x1.mean() - x2.mean()) if len(x1) and len(x2) else None, lo=float(np.percentile(diff, 5)), hi=float(np.percentile(diff, 95))),
           dSR_Z2_minus_Z0=dsr_z2, maxdd_Z0=dd0, maxdd_Z2=dd2, maxdd_reduction=dd_red,
           dSR_Z3_minus_Z0=pf.delta_sharpe_ci(Z["Z3"]["r"][G0:], Z["Z0"]["r"][G0:]), dSR_Z1_minus_Z0=pf.delta_sharpe_ci(Z["Z1"]["r"][G0:], Z["Z0"]["r"][G0:]))
cond1 = (P33["exp_Z1_equity_short"] is not None and P33["exp_Z1_equity_short"] < 0)
cond2 = (P33["exp_Z2_equity_short"] is not None and P33["exp_Z2_equity_short"] > 0) or (dd_red >= 0.15 and dsr_z2["point"] >= 0)
P33["passed"] = bool(cond1 and cond2); P33["refuted"] = bool(P33["Z1_minus_Z2_equity"]["lo"] > 0)
json.dump(P33, open(f"{OUT}/p33.json", "w"), indent=1, default=float)
rt = common.regime_table({z: Z[z]["r"] for z in Z}, G0, reg, ret_ix)
with open(f"{OUT}/regimes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rt[0].keys())); w.writeheader(); w.writerows(rt)
P("P3.3", {k: P33[k] for k in ("exp_Z1_equity_short", "exp_Z2_equity_short", "maxdd_reduction", "passed", "refuted")})

# ---------------- Zeitbox P3.5
tb = []
for a in common.ARCHS:
    for h in (1, 3, 5, 10, 20, 40):
        tr = []
        for r in bp:
            i = r["id"]; S = series[i]
            for t in sim.sequential(S, a, allow_short=False, opts={"H": h}):
                if t["signal_i"] < OOS_I: continue
                g0, v = pf.trade_vector(S, t, cal, ct, i)
                tr.append(dict(t, id=i, g0=g0, vec=v, rank=q[i], tie=int(i), key=(i, a), net_bps=ct.net_bps(i, t["gross_bps"], t["side"], t["hold_days"])))
        rr, _, tk = pf.simulate(tr, cal, slots=30, g_from=G0)
        tb.append(dict(archetyp=a, h=h, sr_netto=pf.sharpe(rr[G0:]), maxdd=pf.max_dd(rr[G0:]), n_trades=len(tk), expectancy_bps=float(np.mean([t["net_bps"] for t in tk])) if tk else None))
with open(f"{OUT}/timebox.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(tb[0].keys())); w.writeheader(); w.writerows(tb)
opt = {a: max((r for r in tb if r["archetyp"] == a), key=lambda r: r["sr_netto"])["h"] for a in common.ARCHS}
P35 = dict(optimal_timebox_days=opt, passed=bool(opt["T"] >= 10 and opt["M"] <= 5), table=tb)
json.dump(P35, open(f"{OUT}/p35.json", "w"), indent=1, default=float)
P("P3.5", opt)
json.dump(dict(P31=P31, P32=P32, P33=P33, P35={k: v for k, v in P35.items() if k != "table"}, short_layer=zrows),
          open(f"{OUT}/h3_metrics_partial.json", "w"), indent=1, default=float)
