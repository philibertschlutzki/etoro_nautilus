"""EBC-H1: metrics.json, verdict.json, report.md, preflight.json, costs_calibration.json, Trade-Exporte."""
import csv, datetime as dt, hashlib, json, math, os, pickle, subprocess, sys
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np, pandas as pd
import common, portfolio as pf, universe, costs

OUT = "results/H1"
rows, ct, series, cal = common.setup()
M = json.load(open(f"{OUT}/stage_a/h1_metrics_partial.json"))
null = json.load(open(f"{OUT}/stage_a/null_gate_calibration.json"))
axis = json.load(open(f"{OUT}/stage_a/axis_break.json"))
prereg = json.load(open(f"{OUT}/preregistration.json"))
parity = json.load(open(f"{OUT}/stage_a/parity_route_r_v.json"))
st = pickle.load(open("data/ebc/h1_state.pkl", "rb"))
g_start = prereg["comparison_period"]["g_start"]

# ---- Kontext: Marktbenchmark (gleichgewichtete Aktien/ETF, Close-to-Close) -> Beta/Alpha
ew = np.zeros(cal.n); cnt = np.zeros(cal.n)
for r in rows:
    if r["klasse"] != "K1": continue
    S = series[r["id"]]
    for k in range(1, S.n):
        g = cal.g(S.ts[k]); ew[g] += S.c[k] / S.c[k - 1] - 1; cnt[g] += 1
ew = np.where(cnt > 0, ew / np.maximum(cnt, 1), 0.0)[g_start:]
ctx = {"benchmark_ew_K1_sharpe": pf.sharpe(ew), "benchmark_ew_K1_ann_ret": float(ew.mean() * 252)}
for a in ("A1", "Bplus"):
    r = st[a]["r"][g_start:]
    X = np.vstack([np.ones(len(ew)), ew]).T
    beta = np.linalg.lstsq(X, r, rcond=None)[0]
    res_ = r - X @ beta
    ctx[a] = dict(beta=float(beta[1]), alpha_ann=float(beta[0] * 252), corr=float(np.corrcoef(r, ew)[0, 1]),
                  resid_sharpe=pf.sharpe(res_ + beta[0]))
M["context_benchmark"] = ctx
M["parity"] = {k: v for k, v in parity.items() if k != "detail"}
M["P1.4"] = axis
M["P1.5"] = null
M["period_note"] = "Vergleichszeitraum ab erstem A0-Auswahltag; Datenende 2026-10-10 (Krypto-Wochenende am Montag verbucht)"
json.dump(M, open(f"{OUT}/metrics.json", "w"), indent=1, default=float)

# ---- Verdikt
p11, p12 = M["P1.1"], M["P1.2"]
preds = [
 dict(id="P1.1", metric="delta_sharpe_Bplus_minus_A1", value=p11["point"], ci90=[p11["lo"], p11["hi"]], threshold="point>0 and ci_low>-0.20", passed=p11["passed"]),
 dict(id="P1.2", metric="PBO(A1) median / Spearman rho", value={"pbo": p12["pbo"], "rho": p12["rho"]}, ci90=M["rank_persistence"]["ci90_iid"], threshold="PBO>=0.50 and rho<=0.10", passed=p12["passed"]),
 dict(id="P1.3", metric="SR_netto je Regime (B+ vs A1)", value={"regimes_B+_pos": M["P1.3"]["n_regimes_SR_pos_Bplus"], "regimes_A1_pos": M["P1.3"]["n_regimes_SR_pos_A1"]}, ci90=None, threshold="SR(B+)>0 in >=3 of 4 and A1 not", passed=M["P1.3"]["passed"]),
 dict(id="P1.4", metric="Spearman(Tages-Score, Stunden-Netto)", value=axis["spearman_daily_score_vs_hourly_net"], ci90=None, threshold="-0.10<=rho<=0.10", passed=axis["P1_4_passed"]),
 dict(id="P1.5", metric="eligible unter Null / beobachtet", value=null["ratio_null_over_observed"], ci90=null["null_fraction_ci90"], threshold=">=0.80", passed=null["P1_5_passed"]),
 dict(id="P1.6", metric="Klassen mit B+ >= A1 (Anteil Instrumente mit positiver Expectancy)", value={"classes_Bplus_ge_A1": M["P1.6"]["classes_Bplus_ge_A1"], "of": M["P1.6"]["classes_represented"]}, ci90=None, threshold="Mehrheit", passed=M["P1.6"]["passed"]),
]
refuted = M["refuted_a"] or M["refuted_b"]
verdict = "WIDERLEGT" if refuted else ("BESTAETIGT" if (p11["passed"] and p12["passed"]) else "UNENTSCHIEDEN")
reasons = [
 f"P1.1: dSR(B+ - A1) = {p11['point']:.2f} (90%-KI {p11['lo']:.2f} .. {p11['hi']:.2f}); B+ SR {M['arms']['Bplus']['sr']:.2f} vs A1 SR {M['arms']['A1']['sr']:.2f} (A0 {M['arms']['A0']['sr']:.2f}).",
 f"P1.2: PBO(A1) Median {p12['pbo']:.3f} ueber {M['PBO_A1']['n_symbols']} Symbole; Rangpersistenz rho = {p12['rho']:.3f} (n={M['rank_persistence']['n']}).",
 "Verdikt gilt vorlaeufig: nur Stufe A; Stufe B (>=30 Handelstage Forward) und Stufe C (Demo-Kalibrierung) nicht ausgefuehrt.",
 "Einschraenkungen: Survivorship (nur heute handelbare Instrumente), Stichprobe ~21 Monate mit ueberwiegend Aufwaertsregime (Beta zu gleichgewichtetem Aktienindex siehe metrics.json), Kosten aus Rueckfallwerten.",
]
verdict_doc = dict(hypothesis="H1", prompt_version="1.0.0", repo_commit="5f737ed495e70eab85b2dbbb7673eb7369c428ce",
    preregistration_sha256="38c6e23397e9bff9cfe28aea8981bc49f3efbd2f31ac508043df16375dc69086",
    preregistration_sha256_regenerated_file=hashlib.sha256(open(f"{OUT}/preregistration.json", "rb").read()).hexdigest(),
    period={"stage_a_oos": M["period"], "stage_b": None},
    universe={"n_total": M["universe"]["n_total"], "n_after_cost_filter": M["universe"]["n_after_cost_filter"], "by_class": M["universe"]["by_class_after_filter"]},
    predictions=preds, stage_c={"status": "NICHT_GESTARTET (wartet auf Freigabe)", "n_fills": 0, "slippage_bps_by_class": {}, "fees_bps_by_class": {}},
    verdict=verdict, verdict_scope="vorlaeufig, nur Stufe A", reasons=reasons, protocol_deviations=prereg["protocol_deviations"])
json.dump(verdict_doc, open(f"{OUT}/verdict.json", "w"), indent=1, ensure_ascii=False, default=float)

# ---- preflight.json (bereits erzeugt)
# ---- costs_calibration.json
am = ct.all_measured
cc = dict(rule=prereg["cost_model"]["rule"], per_class={}, snapshots_note="Wochenend-Snapshot(s), siehe data/ebc/spreads.jsonl (nicht committet)")
for k in ("EQUITY", "FOREX", "COMMODITY", "INDEX", "CRYPTO"):
    ids = [r["id"] for r in rows if r["cost_class"] == k]
    m = [am[i][0] for i in ids if i in am]
    cc["per_class"][k] = dict(n=len(ids), spread_model_bps=costs.FALLBACK_SPREAD[k], slippage_side_bps=costs.FALLBACK_SLIP[k], commission_rt_bps=costs.COMMISSION_RT[k],
                              c_rt_bps=costs.FALLBACK_SPREAD[k] + costs.COMMISSION_RT[k] + 2 * costs.FALLBACK_SLIP[k],
                              measured_snapshot_spread_p50_median_bps=float(np.median(m)) if m else None, measured_n=len(m))
json.dump(cc, open(f"{OUT}/costs_calibration.json", "w"), indent=1)

# ---- Trade-Exporte (Parquet je Arm)
os.makedirs(f"{OUT}/stage_a/trades", exist_ok=True)
for a in ("A0", "A1", "B", "Bplus"):
    tk = st[a]["taken"]
    df = pd.DataFrame([dict(instrument=t["key"][0], strategy=t["key"][1], side=t["side"], entry_i=t["entry_i"], exit_i=t["exit_i"],
                            gross_bps=t["gross_bps"], net_bps=t["net_bps"], reason=t["reason"], hold_days=t["hold_days"]) for t in tk])
    df.to_parquet(f"{OUT}/stage_a/trades/{a}.parquet")
print(json.dumps(verdict_doc["predictions"], indent=1, default=float)[:1500]); print(verdict)
print(json.dumps(ctx, indent=1))
