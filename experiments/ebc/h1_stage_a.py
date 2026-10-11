"""EBC-H1 Stufe A: Arme A0, A1, B, B+ -> Portfolio, PBO, Rangpersistenz, Regime, Klassen. Ausgabe: results/H1/stage_a + data/ebc/h1_state.pkl"""
import csv, json, math, os, pickle, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import common, universe, portfolio as pf, a_arms, sim

t0 = time.time()
def P(*a): print(round(time.time() - t0), "s", *a, flush=True)

OUT = "results/H1/stage_a"; os.makedirs(OUT, exist_ok=True)
prereg = json.load(open("results/H1/preregistration.json"))
g_start = prereg["comparison_period"]["g_start"]
rows, ct, series, cal = common.setup()
by_id = {r["id"]: r for r in rows}
STRATS = ["SmaCrossoverStrategy", "MeanReversionStrategy", "DynamicBreakoutStrategy", "TrendPullbackStrategy",
          "AdxAtrMomentumStrategy", "SqueezeBreakoutStrategy", "DonchianRegimeBreakoutStrategy", "Rsi2ReversionStrategy"]
first_day = {i: int(series[i].ts[0] // pf.DAY_NS) for i in series}

# ---------------- Arm A: Pair-Objekte
raw = {}
for r in rows:
    raw[r["id"]] = pickle.load(open(f"data/ebc/a_trades/{r['id']}.pkl", "rb"))
pairs = {"A0": {}, "A1": {}}
for r in rows:
    i = r["id"]; S = series[i]
    for arm in ("A0", "A1"):
        pairs[arm][i] = {}
    for s in STRATS:
        tr = a_arms.convert_r(S, raw[i][s])
        pairs["A0"][i][s] = a_arms.Pair(i, s, tr, S, cal, ct, i)
        tr1 = [t for t in tr if not (t["side"] < 0 and r["klasse"] in ("K1", "K5"))]
        pairs["A1"][i][s] = a_arms.Pair(i, s, tr1, S, cal, ct, i)
P("Pairs gebaut")

res = {}
for arm in ("A0", "A1"):
    log, taken, persistence, active_end = a_arms.run_selection(pairs[arm], rows, cal, first_day, g_start, STRATS)
    r, eq, tk = pf.simulate(taken, cal, slots=30, g_from=g_start)
    res[arm] = dict(log=log, taken=tk, r=r, eq=eq, persistence=persistence, all_taken=taken, active_end=active_end)
    P(arm, "Auswahltage", len(log), "Trades", len(tk), "SR", round(pf.sharpe(r[g_start:]), 3))

# ---------------- Arm B / B+
arch, q = common.build_arch(rows, ct, series, cal)
arch = [t for t in arch if t["g0"] >= g_start]
for name, thr in (("B", None), ("Bplus", 0.10), ("Bplus_q05", 0.05), ("Bplus_q20", 0.20)):
    sel = [t for t in arch if thr is None or t["q"] <= thr]
    r, eq, tk = pf.simulate(sel, cal, slots=30, g_from=g_start)
    res[name] = dict(r=r, eq=eq, taken=tk, n_inst=len({t["id"] for t in sel}))
    P(name, "Instrumente", res[name]["n_inst"], "Trades", len(tk), "SR", round(pf.sharpe(r[g_start:]), 3))
res["q"] = q
pickle.dump({k: {kk: vv for kk, vv in v.items() if kk not in ("all_taken",)} if isinstance(v, dict) else v for k, v in res.items()},
            open("data/ebc/h1_state.pkl", "wb"))
P("State gespeichert")

# ======================= Kennzahlen =======================
import datetime as dt
from automation.optimizer.cpcv import cpcv_group_boundaries, cpcv_paths, probability_of_backtest_overfitting
dates = [str(dt.date(1970, 1, 1) + dt.timedelta(days=d)) for d in cal.days]
arms = ["A0", "A1", "B", "Bplus"]
R = {a: res[a]["r"][g_start:] for a in arms}
n_days = len(R["A1"])
metrics = {"period": [dates[g_start], dates[-1]], "n_days": n_days}

# --- tägliche Renditen
with open(f"{OUT}/portfolio_daily_returns.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date", "A0", "A1", "A2", "B", "Bplus"])
    for k in range(g_start, cal.n):
        w.writerow([dates[k], res["A0"]["r"][k], res["A1"]["r"][k], "", res["B"]["r"][k], res["Bplus"]["r"][k]])

def arm_metrics(a):
    r = R[a]; tk = res[a]["taken"]
    net = np.array([t["net_bps"] for t in tk]) if tk else np.zeros(1)
    gross = np.array([t["gross_bps"] for t in tk]) if tk else np.zeros(1)
    return dict(sr=pf.sharpe(r), psr=pf.psr(r), ann_ret=float(r.mean() * 252), ann_vol=float(r.std(ddof=1) * math.sqrt(252)),
                maxdd=pf.max_dd(r), cvar5_daily=pf.cvar5(r), n_trades=len(tk), expectancy_bps=float(net.mean()),
                gross_expectancy_bps=float(gross.mean()), pf=a_arms.profit_factor(net), hit=float((net > 0).mean()),
                turnover_per_year=float(len(tk) * 2 * 0.02 / (n_days / 252.0)),
                cost_share=float(1 - net.sum() / gross.sum()) if gross.sum() > 0 else None,
                end_equity=float(res[a]["eq"][-1]))
metrics["arms"] = {a: arm_metrics(a) for a in arms}

# --- P1.1
metrics["P1.1"] = pf.delta_sharpe_ci(R["Bplus"], R["A1"])
metrics["P1.1"]["passed"] = bool(metrics["P1.1"]["point"] > 0 and metrics["P1.1"]["lo"] > -0.20)
metrics["dSR_Bplus_minus_A0"] = pf.delta_sharpe_ci(R["Bplus"], R["A0"])
metrics["dSR_B_minus_A1"] = pf.delta_sharpe_ci(R["B"], R["A1"])
metrics["dSR_Bplus_minus_B"] = pf.delta_sharpe_ci(R["Bplus"], R["B"])
metrics["sr_ci90"] = {a: pf.sharpe_ci(R[a]) for a in arms}
P("P1.1", metrics["P1.1"])

# --- P1.2: PBO(A1) via CSCV je Symbol, Median
G, K = 12, 6
paths = cpcv_paths(G, K)
Pm = np.zeros((len(paths), G)); Qm = np.zeros((len(paths), G))
for pi, (tr, te) in enumerate(paths):
    for g_ in tr: Pm[pi, g_] = 1
    for g_ in te: Qm[pi, g_] = 1
bounds = cpcv_group_boundaries(n_days, G)
pbo_rows = []
for r_ in rows:
    i = r_["id"]
    X = np.array([pairs["A1"][i][s].daily[g_start:] for s in STRATS])      # 8 x n_days
    active = np.where(X.std(axis=1) > 0)[0]
    if len(active) < 6: continue
    X = X[active]
    nn = np.array([b - a for (a, b) in bounds], float)
    s1 = np.array([[X[c, a:b].sum() for (a, b) in bounds] for c in range(len(active))])
    s2 = np.array([[(X[c, a:b] ** 2).sum() for (a, b) in bounds] for c in range(len(active))])
    def perf(M):
        n_ = M @ nn; m1 = M @ s1.T; m2 = M @ s2.T
        mean = m1 / n_[:, None]; var = m2 / n_[:, None] - mean ** 2
        return np.where(var > 1e-24, mean / np.sqrt(np.maximum(var, 1e-24)), 0.0)
    pbo_i = probability_of_backtest_overfitting(perf(Pm), perf(Qm))
    pbo_rows.append((i, r_["symbol"], r_["klasse"], len(active), pbo_i))
with open(f"{OUT}/pbo.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["etoro_id", "symbol", "klasse", "n_configs_evaluable", "pbo"]); w.writerows(pbo_rows)
pbos = np.array([x[4] for x in pbo_rows])
metrics["PBO_A1"] = dict(median=float(np.median(pbos)), mean=float(pbos.mean()), n_symbols=len(pbos),
                         by_class={k: float(np.median([x[4] for x in pbo_rows if x[2] == k])) for k in ("K1", "K2", "K3", "K4", "K5")
                                   if any(x[2] == k for x in pbo_rows)})
# --- Rangpersistenz
per = res["A1"]["persistence"]
rho = pf.spearman([p[1] for p in per], [p[2] for p in per])
with open(f"{OUT}/rank_persistence.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["etoro_id", "strategy", "is_score_oos_psr", "net_return_next_period"])
    for (k, sc, ret) in per: w.writerow([k[0], k[1], sc, ret])
metrics["rank_persistence"] = dict(spearman=rho, n=len(per))
metrics["P1.2"] = dict(pbo=metrics["PBO_A1"]["median"], rho=rho,
                       passed=bool(metrics["PBO_A1"]["median"] >= 0.50 and rho <= 0.10))
rng = np.random.default_rng(20261011)
sc = np.array([p[1] for p in per]); rt = np.array([p[2] for p in per])
bs = []
for _ in range(500):
    ix = rng.integers(0, len(sc), len(sc)); bs.append(pf.spearman(sc[ix], rt[ix]))
metrics["rank_persistence"]["ci90_iid"] = [float(np.nanpercentile(bs, 5)), float(np.nanpercentile(bs, 95))]
P("P1.2", metrics["P1.2"])
d1 = metrics["P1.1"]
metrics["refuted_a"] = bool(d1["point"] <= -0.30 and d1["hi"] < 0)
metrics["refuted_b"] = bool(metrics["PBO_A1"]["median"] < 0.30 and rho >= 0.25)

with open(f"{OUT}/selection_log.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["arm", "g", "date", "n_candidates_gated", "n_active", "n_retired"])
    for a in ("A0", "A1"):
        for l in res[a]["log"]:
            w.writerow([a, l["g"], str(dt.date(1970, 1, 1) + dt.timedelta(days=l["date"])), l["n_cand"], l["n_active"], l["n_retired"]])

# --- Regimen (Referenz SPX500)
spx = next(r_ for r_ in rows if r_["symbol"] == "SPX500")
S = series[spx["id"]]
ix_close = np.full(cal.n, np.nan)
for k in range(S.n): ix_close[cal.g(S.ts[k])] = S.c[k]
for k in range(1, cal.n):
    if np.isnan(ix_close[k]): ix_close[k] = ix_close[k - 1]
ret_ix = np.concatenate([[0], np.diff(ix_close) / ix_close[:-1]])
sma200 = np.full(cal.n, np.nan); vol20 = np.full(cal.n, np.nan)
for k in range(200, cal.n): sma200[k] = ix_close[k - 199:k + 1].mean()
for k in range(20, cal.n): vol20[k] = ret_ix[k - 19:k + 1].std(ddof=1)
med_vol = np.nanmedian(vol20)
reg = np.full(cal.n, "R4", dtype=object)
for k in range(220, cal.n):
    if ix_close[k] > sma200[k] and vol20[k] <= med_vol: reg[k] = "R1"
    elif ix_close[k] > sma200[k]: reg[k] = "R2"
    elif ix_close[k] < sma200[k] and sma200[k] < sma200[k - 20]: reg[k] = "R3"
reg_g = reg[g_start:]
big = np.abs(ret_ix) >= np.nanpercentile(np.abs(ret_ix[g_start:]), 95)
reg_rows = []
for name, mask in [("R1", reg_g == "R1"), ("R2", reg_g == "R2"), ("R3", reg_g == "R3"), ("R4", reg_g == "R4"),
                   ("top5pct_abs_index_ret", big[g_start:])]:
    row = {"regime": name, "n_days": int(mask.sum())}
    for a in arms:
        rr = R[a][mask]
        row[f"SR_{a}"] = pf.sharpe(rr) if len(rr) > 5 else None
        row[f"mean_daily_bps_{a}"] = float(rr.mean() * 1e4) if len(rr) else None
    reg_rows.append(row)
with open(f"{OUT}/regimes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(reg_rows[0].keys())); w.writeheader(); w.writerows(reg_rows)
metrics["regimes"] = reg_rows
pos_b = sum(1 for x in reg_rows[:4] if x["SR_Bplus"] is not None and x["SR_Bplus"] > 0)
pos_a = sum(1 for x in reg_rows[:4] if x["SR_A1"] is not None and x["SR_A1"] > 0)
metrics["P1.3"] = dict(n_regimes_SR_pos_Bplus=pos_b, n_regimes_SR_pos_A1=pos_a, passed=bool(pos_b >= 3 and pos_a < 3))
cls_rows = []
for k in ("K1", "K2", "K3", "K4", "K5"):
    row = {"klasse": k}
    for a in ("A1", "Bplus"):
        by_inst = {}
        for t in res[a]["taken"]:
            iid = t["key"][0]
            if by_id[iid]["klasse"] == k: by_inst.setdefault(iid, []).append(t["net_bps"])
        row[f"n_inst_{a}"] = len(by_inst)
        row[f"share_pos_exp_{a}"] = float(np.mean([np.mean(v) > 0 for v in by_inst.values()])) if by_inst else None
        row[f"mean_exp_bps_{a}"] = float(np.mean([x for v in by_inst.values() for x in v])) if by_inst else None
    cls_rows.append(row)
metrics["P1.6_classes"] = cls_rows
rep = [x for x in cls_rows if x["n_inst_Bplus"] > 0]
wins = sum(1 for x in rep if (x["share_pos_exp_Bplus"] or 0) >= (x["share_pos_exp_A1"] or 0))
metrics["P1.6"] = dict(classes_represented=len(rep), classes_Bplus_ge_A1=wins, passed=bool(wins > len(rep) / 2))
with open(f"{OUT}/classes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(cls_rows[0].keys())); w.writeheader(); w.writerows(cls_rows)
q_ = res["q"]
metrics["universe"] = dict(n_total=len(rows), n_after_cost_filter=int(sum(1 for i in q_ if q_[i] <= 0.10)),
                           by_class_after_filter={k: int(sum(1 for r_ in rows if r_["klasse"] == k and q_[r_["id"]] <= 0.10)) for k in ("K1", "K2", "K3", "K4", "K5")})
with open("results/H1/universe_q.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["etoro_id", "symbol", "klasse", "c_rt_bps", "q"])
    for r_ in rows: w.writerow([r_["id"], r_["symbol"], r_["klasse"], round(ct.c_rt(r_["id"]), 2), round(q_[r_["id"]], 4)])
json.dump(metrics, open(f"{OUT}/h1_metrics_partial.json", "w"), indent=1, default=float)
P("fertig; P1.3", metrics["P1.3"], "P1.6", metrics["P1.6"])
print(json.dumps({k: metrics[k] for k in ("P1.1", "P1.2", "PBO_A1", "rank_persistence", "universe")}, indent=1, default=float))
