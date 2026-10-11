"""EBC-H2 Stufe A, Teil 2: Breite (P2.4), Sizing (P2.5), Testmacht (P2.6), H2-Portfolio, Regime."""
import csv, datetime as dt, json, math, os, pickle, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import common, portfolio as pf, sim, universe

t0 = time.time()
def P(*a): print(round(time.time() - t0), "s", *a, flush=True)
OUT = "results/H2/stage_a"
rows, ct, series, cal = common.setup()
by_id = {r["id"]: r for r in rows}
q = {r["id"]: common.q_of(r, ct, series[r["id"]]) for r in rows}
bp_ids = {r["id"] for r in rows if q[r["id"]] <= 0.10}
st1 = pickle.load(open("data/ebc/h2_part1.pkl", "rb"))
G0, G_SPLIT = st1["G0"], st1["G_SPLIT"]
p1 = json.load(open(f"{OUT}/h2_part1_metrics.json"))
arch_all, _ = common.build_arch(rows, ct, series, cal)
arch = [t for t in arch_all if t["id"] in bp_ids and t["g0"] >= G0]
P("B+ Archetyp-Trades", len(arch))

# ---------------- P2.4 Breite
keys = {}
for t in arch:
    keys.setdefault(t["key"], []).append(t)
X = []; kl = []; kk = []
for key, tl in keys.items():
    d = np.zeros(cal.n)
    for t in tl: d[t["g0"]:t["g0"] + len(t["vec"])] += t["vec"]
    d = d[G0:G_SPLIT]
    if d.std() > 0:
        X.append(d); kl.append(by_id[key[0]]["klasse"]); kk.append(key)
X = np.array(X); kl = np.array(kl)
C = np.corrcoef(X)
sd = X.std(axis=1, ddof=1)
within = {k: float(np.nanmean(C[np.ix_(kl == k, kl == k)][~np.eye((kl == k).sum(), dtype=bool)])) for k in ("K1", "K2", "K3", "K4") if (kl == k).sum() > 2}
between = float(np.nanmean(C[kl[:, None] != kl[None, :]]))
rng = np.random.default_rng(20261011)
NS = [5, 10, 20, 50, 100, 200, None]
draws = []
def one_draw(pool, N):
    idx = rng.choice(pool, size=N, replace=False)
    w = 1.0 / sd[idx]; w /= w.sum()
    r = w @ X[idx]
    sr = pf.sharpe(r)
    sub = C[np.ix_(idx, idx)]
    rho = (sub.sum() - len(idx)) / (len(idx) * (len(idx) - 1)) if len(idx) > 1 else 0.0
    return sr, float(rho)
for scheme in ("aktien", "alle"):
    pool = np.where(kl == "K1")[0] if scheme == "aktien" else np.arange(len(kl))
    for N in NS:
        n = len(pool) if N is None else N
        if n > len(pool): continue
        reps = 1 if N is None else 200
        for z in range(reps):
            if scheme == "alle" and N is not None:
                # proportional zu den Klassen-Anteilen am Pool
                idx = []
                for k in ("K1", "K2", "K3", "K4"):
                    pk = np.where(kl == k)[0]
                    m = int(round(n * len(pk) / len(kl)))
                    if m > 0: idx += list(rng.choice(pk, size=min(m, len(pk)), replace=False))
                idx = np.array(idx)
                w = 1.0 / sd[idx]; w /= w.sum()
                r = w @ X[idx]; sr = pf.sharpe(r)
                sub = C[np.ix_(idx, idx)]; nn = len(idx)
                rho = (sub.sum() - nn) / (nn * (nn - 1)) if nn > 1 else 0.0
                draws.append((scheme, nn, z, sr, rho))
            else:
                sr, rho = one_draw(pool, n)
                draws.append((scheme, n, z, sr, rho))
with open(f"{OUT}/breadth_draws.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["schema", "N", "ziehung", "sr_p", "n_eff", "rho_bar"])
    for (s_, n, z, sr, rho) in draws: w.writerow([s_, n, z, sr, n / (1 + (n - 1) * rho), rho])
D = np.array([(n, sr, rho) for (_, n, _, sr, rho) in draws])
neff = D[:, 0] / (1 + (D[:, 0] - 1) * D[:, 2])
ok = D[:, 1] > 0
xs, ys = np.log(neff[ok]), np.log(D[ok, 1])
A = np.vstack([np.ones(len(xs)), xs]).T
coef = np.linalg.lstsq(A, ys, rcond=None)[0]
pred = A @ coef; r2 = 1 - ((ys - pred) ** 2).sum() / ((ys - ys.mean()) ** 2).sum()
bs = []
for _ in range(1000):
    ix = rng.integers(0, len(xs), len(xs)); Ab = A[ix]
    bs.append(np.linalg.lstsq(Ab, ys[ix], rcond=None)[0][1])
coef2 = np.linalg.lstsq(np.vstack([np.ones(ok.sum()), np.log(D[ok, 0])]).T, ys, rcond=None)[0]
d_aktien = [(n, ne) for (s_, n, z, sr, rho), ne in zip(draws, neff) if s_ == "aktien"]
def mean_neff(Nq, pred=lambda n: True):
    v = [ne for (n, ne) in d_aktien if n == Nq]; return float(np.mean(v)) if v else float("nan")
n_all = max(n for (n, _) in d_aktien)
growth = mean_neff(n_all) / mean_neff(50) - 1
P24 = dict(b=float(coef[1]), b_ci90=[float(np.percentile(bs, 5)), float(np.percentile(bs, 95))], a=float(coef[0]), r2=float(r2),
           b_ln_N=float(coef2[1]), n_draws=int(len(draws)), n_draws_sr_pos=int(ok.sum()), n_subportfolios=int(len(kl)),
           rho_bar_within=within, rho_bar_between=between, n_eff_growth_50_to_all_stocks=float(growth), n_all_stocks=int(n_all),
           passed=bool(0.30 <= coef[1] <= 0.70 and r2 >= 0.60 and growth < 0.15), refuted=bool(coef[1] < 0.15 or r2 < 0.30))
json.dump(P24, open(f"{OUT}/breadth_regression.json", "w"), indent=1)
P("P2.4", {k: P24[k] for k in ("b", "b_ci90", "r2", "n_eff_growth_50_to_all_stocks", "passed")})

# ---------------- P2.5 Sizing
def sd20_of(S):
    ret = np.zeros(S.n); ret[1:] = S.c[1:] / S.c[:-1] - 1
    out = np.full(S.n, np.nan)
    for u in range(20, S.n): out[u] = ret[u - 19:u + 1].std(ddof=1)
    return out
sd20 = {i: sd20_of(series[i]) for i in bp_ids}
for t in arch:
    S = series[t["id"]]; s_i = t["signal_i"]
    sg = sd20[t["id"]][s_i]; kmul = sim.ARCH[t["arch"]]["k"]
    atrp = S.atr[s_i] / S.c[s_i]
    t["f_EN"] = 0.02
    t["f_RP"] = 0.0010 / sg if sg == sg and sg > 0 else 0.0
    t["f_FR"] = 0.005 / (kmul * atrp) if atrp > 0 else 0.0
def run_size(trades, key, g_from=G0):
    return pf.simulate_sized(trades, cal, slots=30, g_from=g_from, frac_key=key)
res_s = {}
for name in ("EN", "RP", "FR"):
    r, eq, tk, ex = run_size(arch, f"f_{name}")
    res_s[name] = dict(r=r, eq=eq, taken=tk, expo=ex)
# QK : 1/4 Kelly je Klasse
r_en_cls = {}
for k in ("K1", "K2", "K3", "K4"):
    sel = [t for t in arch if t["klasse"] == k]
    r, _, _ = pf.simulate(sel, cal, slots=30, g_from=G0)
    r_en_cls[k] = r
fk = {}
for k, r in r_en_cls.items():
    f = np.zeros(cal.n)
    for g in range(G0 + 20, cal.n):
        w_ = r[max(G0, g - 252):g]
        if len(w_) > 20 and w_.var() > 0: f[g] = max(0.0, 0.25 * w_.mean() / w_.var())
    fk[k] = f
for t in arch:
    f = fk[t["klasse"]][max(t["g0"] - 1, 0)] / 30.0
    t["f_QK"] = min(2 * t["f_RP"], f)
r, eq, tk, ex = run_size(arch, "f_QK"); res_s["QK"] = dict(r=r, eq=eq, taken=tk, expo=ex)
sz = {}
for name, v in res_s.items():
    r = v["r"][G0:]
    net = np.array([t["net_bps"] for t in v["taken"]])
    gross = np.array([t["gross_bps"] for t in v["taken"]])
    notional = np.array([t["f"] for t in v["taken"]])
    sz[name] = dict(sr=pf.sharpe(r), sr_confirmation=pf.sharpe(v["r"][G_SPLIT:]), maxdd=pf.max_dd(r), cvar5=pf.cvar5(r),
                    turnover_pa=float((2 * notional).sum() / ((cal.n - G0) / 252.0)), cost_share=float(1 - net.sum() / gross.sum()) if gross.sum() > 0 else None,
                    mean_gross_exposure=float(v["expo"][G0:].mean()), n_trades=len(net), ann_ret=float(r.mean() * 252))
d_rp_en = pf.delta_sharpe_ci(res_s["RP"]["r"][G0:], res_s["EN"]["r"][G0:])
d_en_rp = pf.delta_sharpe_ci(res_s["EN"]["r"][G0:], res_s["RP"]["r"][G0:])
P25 = dict(variants=sz, delta_RP_minus_EN=d_rp_en, delta_EN_minus_RP=d_en_rp,
           delta_RP_minus_EN_confirmation=pf.delta_sharpe_ci(res_s["RP"]["r"][G_SPLIT:], res_s["EN"]["r"][G_SPLIT:]),
           passed=bool(d_rp_en["point"] >= 0.10 and d_rp_en["lo"] > 0 and sz["RP"]["maxdd"] >= sz["EN"]["maxdd"]),   # maxdd negativ: >= heisst kleiner/gleich
           refuted=bool(d_en_rp["lo"] > 0))
json.dump(P25, open(f"{OUT}/sizing_metrics.json", "w"), indent=1, default=float)
with open(f"{OUT}/sizing.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["variante", "sr", "sr_bestaetigung", "maxdd", "cvar5_daily", "turnover_pa", "kostenanteil", "bruttoauslastung", "n_trades", "ann_ret"])
    for n_, v in sz.items(): w.writerow([n_, v["sr"], v["sr_confirmation"], v["maxdd"], v["cvar5"], v["turnover_pa"], v["cost_share"], v["mean_gross_exposure"], v["n_trades"], v["ann_ret"]])
P("P2.5", {n_: (round(v["sr"], 2), round(v["maxdd"], 3)) for n_, v in sz.items()}, "dRP-EN", {k: round(d_rp_en[k], 2) for k in ("point", "lo", "hi")})

# ---------------- H2-Portfolio (eingefroren: Klassen-H = argmax Entdeckung, RP) vs Status-quo-Mathematik (EN, h=1)

def arch_trades_h(h_by_class, ids, extra=None):
    out = []
    for r in rows:
        if r["id"] not in ids: continue
        i = r["id"]; S = series[i]
        hh_ = h_by_class[r["klasse"]]
        for a in common.ARCHS:
            opts = {"H": hh_}; opts.update(extra or {})
            for t in sim.sequential(S, a, allow_short=(r["klasse"] in universe.SHORT_OK), opts=opts):
                g0, v = pf.trade_vector(S, t, cal, ct, i)
                if g0 < G0: continue
                tt = dict(t, id=i, arch=a, klasse=r["klasse"], g0=g0, vec=v, rank=q[i], tie=int(i), key=(i, a),
                          net_bps=ct.net_bps(i, t["gross_bps"], t["side"], t["hold_days"]), q=q[i])
                sg = sd20[i][t["signal_i"]]
                tt["f_RP"] = 0.0010 / sg if sg == sg and sg > 0 else 0.0
                tt["f_EN"] = 0.02
                out.append(tt)
    return out
hstar = {k: p1["P21"]["classes"][k]["argmax_h"] for k in ("K1", "K2", "K3", "K4")}
hstar["K5"] = 1
tr_h2 = arch_trades_h(hstar, bp_ids)
r_h2, eq_h2, tk_h2, ex_h2 = pf.simulate_sized(tr_h2, cal, slots=30, g_from=G0, frac_key="f_RP")
tr_sq = arch_trades_h({k: 1 for k in ("K1", "K2", "K3", "K4", "K5")}, bp_ids)
r_sq, eq_sq, tk_sq, ex_sq = pf.simulate_sized(tr_sq, cal, slots=30, g_from=G0, frac_key="f_EN")
h2p = dict(hstar=hstar, sr_h2_full=pf.sharpe(r_h2[G0:]), sr_h2_confirmation=pf.sharpe(r_h2[G_SPLIT:]),
           sr_statusquo_full=pf.sharpe(r_sq[G0:]), sr_statusquo_confirmation=pf.sharpe(r_sq[G_SPLIT:]),
           maxdd_h2=pf.max_dd(r_h2[G0:]), maxdd_sq=pf.max_dd(r_sq[G0:]),
           delta_confirmation=pf.delta_sharpe_ci(r_h2[G_SPLIT:], r_sq[G_SPLIT:]), delta_full=pf.delta_sharpe_ci(r_h2[G0:], r_sq[G0:]),
           n_trades_h2=len(tk_h2), n_trades_sq=len(tk_sq))
json.dump(h2p, open(f"{OUT}/h2_portfolio.json", "w"), indent=1, default=float)
dates = [str(dt.date(1970, 1, 1) + dt.timedelta(days=d)) for d in cal.days]
with open("results/H2/stage_a/h2_portfolio_daily.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date", "H2_portfolio", "status_quo_math"])
    for g in range(G0, cal.n): w.writerow([dates[g], r_h2[g], r_sq[g]])
P("H2-Portfolio", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in h2p.items() if k.startswith("sr")})
# Regime
reg, ret_ix = common.regime_array(rows, series, cal)
rt = common.regime_table({"H2": r_h2, "StatusQuoMath": r_sq, "EN": res_s["EN"]["r"], "RP": res_s["RP"]["r"]}, G0, reg, ret_ix)
with open(f"{OUT}/regimes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rt[0].keys())); w.writeheader(); w.writerows(rt)

# ---------------- P2.6 Testmacht
from automation.optimizer import sequential as seq
mc = {}
for n_conc, paths in ((1, 10000), (80, 3000)):
    for sr_ in (1.0, 2.0, 3.0, 4.0):
        out = seq.simulate_sequential(n_paths=paths, k_max=26, n_concurrent=n_conc, sr_annual=sr_, seed=7)
        mc[f"n{n_conc}_sr{sr_}"] = {k: out[k] for k in ("threshold", "promotion_rate", "false_promotion_rate") if k in out}
    P("MC n_concurrent", n_conc)
ratio = {f"sr{sr_}": (mc[f"n1_sr{sr_}"]["promotion_rate"] / mc[f"n80_sr{sr_}"]["promotion_rate"]) if mc[f"n80_sr{sr_}"]["promotion_rate"] > 0 else None for sr_ in (1.0, 2.0, 3.0, 4.0)}
# empirisch: letzte 26 Wochen der Bestaetigungsperiode
def weekly(returns_all, n_conc, g_end=cal.n):
    g_first = g_end - 26 * 5
    out = []
    for look in range(1, 27):
        upto = g_first + look * 5
        rr = returns_all[g_first:upto]
        d_ = seq.sequential_decision(list(rr), look_index=look, k_max=26, n_concurrent=n_conc, n_boot=300, seed=7)
        out.append((look, d_.decision, d_.psr))
    return out
emp = {"portfolio_n1": weekly(r_h2, 1)}
subs = []
for key, tl in keys.items():
    d = np.zeros(cal.n)
    for t in tl: d[t["g0"]:t["g0"] + len(t["vec"])] += t["vec"]
    subs.append((key, d))
prom80 = 0; n_eval = 0
for key, d in subs:
    if d[cal.n - 130:].std() == 0: continue
    n_eval += 1
    dec = [x[1] for x in weekly(d, 80)]
    if "PROMOTE" in dec: prom80 += 1
P26 = dict(mc=mc, ratio_n1_over_n80_promotion=ratio, passed=bool(ratio["sr2.0"] is not None and ratio["sr2.0"] >= 10),
           empirical_portfolio_decisions=[dict(look=a, decision=b, psr=c) for (a, b, c) in emp["portfolio_n1"]],
           empirical_portfolio_first_promote_look=next((a for (a, b, c) in emp["portfolio_n1"] if b == "PROMOTE"), None),
           empirical_subportfolios=dict(n_evaluated=n_eval, n_promoted=prom80))
json.dump(P26, open(f"{OUT}/power_mc.json", "w"), indent=1, default=float)
with open(f"{OUT}/power_empirical.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["look", "decision", "psr"]); [w.writerow(x) for x in emp["portfolio_n1"]]
P("P2.6", {"ratio": ratio, "emp_first_promote": P26["empirical_portfolio_first_promote_look"], "subs": P26["empirical_subportfolios"]})
json.dump(dict(P24=P24, P25=P25, H2_portfolio=h2p, P26={k: v for k, v in P26.items() if k != "empirical_portfolio_decisions"}, regimes=rt),
          open(f"{OUT}/h2_part2_metrics.json", "w"), indent=1, default=float)
