"""EBC-H1 §7.5 Nullmodell der Paper-Gates (P1.5): zirkulaere Verschiebung der Trade-Positionsreihe (20-230 Handelstage) gegen die
Kursreihe; Gates (IS-Rang -> OOS-Gate des Gewinners je Symbol) am letzten Auswahltag. Beobachtet = Verschiebung 0, gleicher Code."""
import csv, json, math, os, pickle, sys
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import common, portfolio as pf, a_arms

REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 200
rows, ct, series, cal = common.setup()
STRATS = ["SmaCrossoverStrategy", "MeanReversionStrategy", "DynamicBreakoutStrategy", "TrendPullbackStrategy",
          "AdxAtrMomentumStrategy", "SqueezeBreakoutStrategy", "DonchianRegimeBreakoutStrategy", "Rsi2ReversionStrategy"]
prereg = json.load(open("results/H1/preregistration.json"))
g_start = prereg["comparison_period"]["g_start"]
sel_days = list(range(g_start, cal.n, a_arms.REBAL))
d = sel_days[-1]; d_day = cal.days[d]
rng = np.random.default_rng(20261011)
IS_LO, IS_HI, OO_LO = d_day - a_arms.HIST_NEED, d_day - a_arms.OOS_DAYS - a_arms.EMB_DAYS, d_day - a_arms.OOS_DAYS

def tstat_rows(net, mask):
    """net: (R, n) Netto; mask: (R, n) -> n, mean, t, pf je Zeile."""
    m = mask
    n = m.sum(1)
    x = np.where(m, net, 0.0)
    s1 = x.sum(1); s2 = (x * x).sum(1)
    mean = np.where(n > 0, s1 / np.maximum(n, 1), 0.0)
    var = np.where(n > 1, (s2 / np.maximum(n, 1) - mean ** 2) * n / np.maximum(n - 1, 1), 0.0)
    sd = np.sqrt(np.maximum(var, 0))
    t = np.where((sd > 0) & (n > 1), mean / (sd / np.sqrt(np.maximum(n, 1))), -9.0)
    gp = np.where(m & (net > 0), net, 0.0).sum(1); gl = np.where(m & (net < 0), -net, 0.0).sum(1)
    pfac = np.where(gl > 0, gp / np.maximum(gl, 1e-12), np.where(gp > 0, np.inf, 0.0))
    return n, mean, t, pfac

obs_elig = 0; null_elig = np.zeros(REPS); n_sym = 0
for r in rows:
    i = r["id"]; S = series[i]
    if d_day - int(S.ts[0] // pf.DAY_NS) < a_arms.HIST_NEED: continue
    raw = pickle.load(open(f"data/ebc/a_trades/{i}.pkl", "rb"))
    n_sym += 1
    shifts = np.concatenate([[0], rng.integers(20, 231, size=REPS)])      # Replikat 0 = beobachtet
    best_t = np.full(REPS + 1, -np.inf); best_gate = np.zeros(REPS + 1, bool)
    for s in STRATS:
        tr = a_arms.convert_r(S, raw[s])
        tr = [t for t in tr if not (t["side"] < 0 and r["klasse"] in ("K1", "K5"))]
        if not tr: continue
        e = np.array([t["entry_i"] for t in tr]); x = np.array([t["exit_i"] for t in tr]); side = np.array([t["side"] for t in tr])
        hold = np.array([t["hold_days"] for t in tr])
        e2 = (e[None, :] + shifts[:, None]) % S.n; x2 = (x[None, :] + shifts[:, None]) % S.n
        valid = x2 >= e2
        gross = side[None, :] * (S.o[x2] / S.o[e2] - 1) * 1e4
        gross0 = np.array([t["gross_bps"] for t in tr])
        gross[0] = gross0                                     # beobachtet: exakte Trade-Renditen
        c_rt = ct.c_rt(i); fin = np.where(side > 0, ct.p[i]["fin_long"], ct.p[i]["fin_short"])
        net = gross - c_rt - fin[None, :] * hold[None, :]
        exit_day = (S.ts[x2] // pf.DAY_NS).astype(np.int64)
        if True:
            exit_day[0] = (S.ts[x] // pf.DAY_NS).astype(np.int64)
        m_is = valid & (exit_day >= IS_LO) & (exit_day < IS_HI)
        m_oo = valid & (exit_day >= OO_LO) & (exit_day <= d_day)
        n1, mean1, t1, pf1 = tstat_rows(net, m_is)
        n2, mean2, t2, pf2 = tstat_rows(net, m_oo)
        gate = (n2 >= a_arms.MIN_TRADES) & (pf2 >= a_arms.MIN_PF) & (mean2 >= a_arms.MIN_EXP) & (t2 >= a_arms.MIN_T)
        better = (n1 >= 3) & (t1 > best_t)
        best_t = np.where(better, t1, best_t); best_gate = np.where(better, gate, best_gate)
    obs_elig += int(best_gate[0]); null_elig += best_gate[1:].astype(float)
null_frac = null_elig / max(n_sym, 1)
obs_frac = obs_elig / max(n_sym, 1)
out = dict(selection_day=int(d_day), n_symbols=n_sym, observed_eligible=obs_elig, observed_fraction=obs_frac,
           null_fraction_mean=float(null_frac.mean()), null_fraction_ci90=[float(np.percentile(null_frac, 5)), float(np.percentile(null_frac, 95))],
           ratio_null_over_observed=float(null_frac.mean() / obs_frac) if obs_frac > 0 else None, replicates=REPS,
           threshold=0.80, P1_5_passed=bool(obs_frac > 0 and null_frac.mean() / obs_frac >= 0.80),
           note="Gates: >=8 Trades, PF>=1, Expectancy>=0, t>=0,5 (PSR>=0,5 aequivalent zu Sharpe>=0, in Expectancy enthalten); Replikat 0 = beobachtet")
json.dump(out, open("results/H1/stage_a/null_gate_calibration.json", "w"), indent=1)
print(json.dumps(out, indent=1))
