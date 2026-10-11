"""EBC-H2 Stufe A. Ausgabe: results/H2/stage_a/*"""
import csv, datetime as dt, json, math, os, pickle, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import common, portfolio as pf, sim, h2_lib, universe

t0 = time.time()
def P(*a): print(round(time.time() - t0), "s", *a, flush=True)
OUT = "results/H2/stage_a"; os.makedirs(OUT, exist_ok=True)
rows, ct, series, cal = common.setup()
by_id = {r["id"]: r for r in rows}
q = {r["id"]: common.q_of(r, ct, series[r["id"]]) for r in rows}
bp = [r for r in rows if q[r["id"]] <= 0.10]            # B+-Universum
G0 = 250
G_SPLIT = G0 + int(round((cal.n - G0) * 2 / 3))
HS = [1, 3, 5, 10, 20, 40, 60]
CLASSES = ["K1", "K2", "K3", "K4"]
dates = [str(dt.date(1970, 1, 1) + dt.timedelta(days=d)) for d in cal.days]
P("B+ Instrumente", len(bp), "G_SPLIT", dates[G_SPLIT], "cal.n", cal.n)

def arch_trades(h, rows_sel, opts_extra=None):
    out = []
    for r in rows_sel:
        i = r["id"]; S = series[i]
        for arch in common.ARCHS:
            opts = {"H": h}; opts.update(opts_extra or {})
            for t in sim.sequential(S, arch, allow_short=(r["klasse"] in universe.SHORT_OK), opts=opts):
                g0, v = pf.trade_vector(S, t, cal, ct, i)
                out.append(dict(t, id=i, arch=arch, klasse=r["klasse"], g0=g0, vec=v, rank=q[i], tie=int(i), key=(i, arch),
                                net_bps=ct.net_bps(i, t["gross_bps"], t["side"], t["hold_days"]), q=q[i]))
    return out

# ---------------- P2.1 Horizont (Archetypen)
res_h = {}
for h in HS:
    tr = arch_trades(h, bp)
    for k in CLASSES:
        sleeves = []
        taken_all = []
        for a in common.ARCHS:
            sel = [t for t in tr if t["klasse"] == k and t["arch"] == a and t["g0"] >= G0]
            r, eq, tk = pf.simulate(sel, cal, slots=30, g_from=G0)
            sleeves.append(r); taken_all += tk
        r = np.mean(sleeves, axis=0)
        res_h[(h, k)] = dict(r=r, n=len(taken_all), exp=float(np.mean([t["net_bps"] for t in taken_all])) if taken_all else float("nan"),
                             gross=float(np.mean([t["gross_bps"] for t in taken_all])) if taken_all else float("nan"))
    P("h", h, "fertig")
tab = []
for k in CLASSES:
    for h in HS:
        r = res_h[(h, k)]["r"]
        tab.append(dict(klasse=k, h=h, sr_discovery=pf.sharpe(r[G0:G_SPLIT]), sr_confirmation=pf.sharpe(r[G_SPLIT:]), sr_full=pf.sharpe(r[G0:]),
                        n_trades=res_h[(h, k)]["n"], exp_net_bps=res_h[(h, k)]["exp"], exp_gross_bps=res_h[(h, k)]["gross"]))
with open(f"{OUT}/horizon_archetypes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(tab[0].keys())); w.writeheader(); w.writerows(tab)
p21 = {}
for k in CLASSES:
    disc = [(pf.sharpe(res_h[(h, k)]["r"][G0:G_SPLIT]), h) for h in HS]
    best_h = max(disc)[1]
    d = pf.delta_sharpe_ci(res_h[(best_h, k)]["r"][G_SPLIT:], res_h[(1, k)]["r"][G_SPLIT:])
    srs = [x[0] for x in disc]
    monotone_fall = all(srs[i] >= srs[i + 1] for i in range(len(srs) - 1))
    p21[k] = dict(argmax_h=best_h, delta_vs_h1=d, sr_by_h_disc=dict(zip(HS, srs)), monotone_decreasing=bool(monotone_fall),
                  cond=bool(best_h >= 5 and d["lo"] > 0))
n_ok = sum(1 for k in CLASSES if p21[k]["cond"])
n_ge5 = sum(1 for k in CLASSES if p21[k]["argmax_h"] >= 5)
P21 = dict(classes=p21, n_classes_cond=n_ok, n_argmax_ge5=n_ge5, passed=bool(n_ge5 >= 3 and n_ok >= 3),
           n_monotone_decreasing=sum(1 for k in CLASSES if p21[k]["monotone_decreasing"]))
json.dump(P21, open(f"{OUT}/p21.json", "w"), indent=1, default=float)
P("P2.1", {k: (p21[k]["argmax_h"], round(p21[k]["delta_vs_h1"]["point"], 2), round(p21[k]["delta_vs_h1"]["lo"], 2)) for k in CLASSES}, "ok", n_ok)

# ---------------- Diagnose + Krypto: S_MOM / S_REV
HD = [1, 2, 3, 5, 10, 20, 40]
sig_rows = []
crypto_rows = []
class_ids = {k: [r["id"] for r in (bp if k != "K5" else rows) if r["klasse"] == k] for k in ("K1", "K2", "K3", "K4", "K5")}
months_cache = {}
for k in ("K1", "K2", "K3", "K4", "K5"):
    ids = class_ids[k]
    lo = k not in ("K2", "K3", "K4")
    for kind in ("MOM", "REV"):
        for h in HD:
            daily = np.zeros(cal.n); nets = []; grs = []; icx = []; icy = []; mons = []; sd1s = []
            for i in ids:
                S = series[i]; cp = ct.p[i]
                R = h2_lib.sig_windows(S, kind, h, lo, ct.c_rt(i), cp["fin_long"], cp["fin_short"])
                gidx = np.array([cal.g(t_) for t_ in S.ts])
                np.add.at(daily, gidx, R["daily"])
                m = R["p"] != 0
                nets.append(R["net"][m]); grs.append(R["gross"][m]); icx.append(R["ic_x"]); icy.append(R["ic_y"]); sd1s.append(R["sd1"])
                mons.append(np.array([int(S.ts[tt] // pf.DAY_NS) // 30 for tt in R["t"][m]]))
            daily = daily / max(len(ids), 1)
            nets = np.concatenate(nets) if nets else np.array([]); grs = np.concatenate(grs) if grs else np.array([])
            icx = np.concatenate(icx); icy = np.concatenate(icy)
            ic = float(np.corrcoef(icx, icy)[0, 1]) if len(icx) > 10 and icx.std() > 0 else float("nan")
            row = dict(klasse=k, signal=f"S_{kind}", h=h, ic=ic, brutto_bps=float(grs.mean()) if len(grs) else float("nan"),
                       netto_bps=float(nets.mean()) if len(nets) else float("nan"), sr_netto=pf.sharpe(daily[G0:]), n_trades=int(len(nets)),
                       sigma1=float(np.mean(sd1s)), c_rt_bps=float(np.mean([ct.c_rt(i) for i in ids])))
            sig_rows.append(row)
            if k == "K5" and h in (1, 3, 5, 10, 20):
                ci = h2_lib.month_cluster_ci(nets, np.concatenate(mons) if mons else [], B=2000)
                crypto_rows.append(dict(signal=f"S_{kind}", h=h, exp_net_bps=ci["mean"], ci90_lo=ci["lo"], ci90_hi=ci["hi"], n=ci["n"],
                                        sr_netto=row["sr_netto"], exp_gross_bps=row["brutto_bps"]))
    P("Signale", k)
# h*_theo
for k in ("K1", "K2", "K3", "K4", "K5"):
    for kind in ("MOM", "REV"):
        rr = [r for r in sig_rows if r["klasse"] == k and r["signal"] == f"S_{kind}"]
        icm = np.nanmean([r["ic"] for r in rr])
        c = rr[0]["c_rt_bps"] / 1e4; s1 = rr[0]["sigma1"]
        hstar = 4 * c * c / (icm ** 2 * s1 * s1) if icm and abs(icm) > 1e-6 else float("inf")
        best = max(rr, key=lambda r: (r["sr_netto"] if r["sr_netto"] == r["sr_netto"] else -9))
        for r in rr:
            r["h_star_theo"] = hstar; r["ic_mean_over_h"] = float(icm); r["argmax_sr_h"] = best["h"]
with open(f"{OUT}/horizon_signals.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(sig_rows[0].keys())); w.writeheader(); w.writerows(sig_rows)
# Krypto Archetypen
cr_idx = [r for r in rows if r["klasse"] == "K5"]
for h in (1, 3, 5, 10, 20):
    tr = arch_trades(h, cr_idx)
    for a in common.ARCHS:
        sel = [t for t in tr if t["arch"] == a]
        nets = np.array([t["net_bps"] for t in sel]); mons = [int(series[t["id"]].ts[t["entry_i"]] // pf.DAY_NS) // 30 for t in sel]
        ci = h2_lib.month_cluster_ci(nets, mons, B=2000)
        gr = np.array([t["gross_bps"] for t in sel])
        crypto_rows.append(dict(signal=a, h=h, exp_net_bps=ci["mean"], ci90_lo=ci["lo"], ci90_hi=ci["hi"], n=ci["n"], sr_netto=None, exp_gross_bps=float(gr.mean()) if len(gr) else None))
with open(f"{OUT}/crypto.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["signal", "h", "exp_net_bps", "ci90_lo", "ci90_hi", "n", "sr_netto", "exp_gross_bps"]); w.writeheader(); w.writerows(crypto_rows)
sigs = sorted({r["signal"] for r in crypto_rows})
short_ok = all(all((r["ci90_hi"] < 0) for r in crypto_rows if r["signal"] == s_ and r["h"] == h) for s_ in sigs for h in (1, 3, 5))
mid_ok = all(sum(1 for s_ in sigs if next(r for r in crypto_rows if r["signal"] == s_ and r["h"] == h)["exp_net_bps"] < 0) > len(sigs) / 2 for h in (10, 20))
P22 = dict(signals=sigs, h_le5_all_upper_ci_negative=bool(short_ok), h10_20_majority_negative=bool(mid_ok), passed=bool(short_ok and mid_ok))
json.dump(P22, open(f"{OUT}/p22.json", "w"), indent=1)
P("P2.2", P22)
pickle.dump(dict(res_h={k: v for k, v in res_h.items()}, G0=G0, G_SPLIT=G_SPLIT), open("data/ebc/h2_part1.pkl", "wb"))
json.dump(dict(P21=P21, P22=P22), open(f"{OUT}/h2_part1_metrics.json", "w"), indent=1, default=float)
