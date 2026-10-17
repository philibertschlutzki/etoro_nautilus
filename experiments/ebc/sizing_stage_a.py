"""Stufe A: risikojustierte Positionsgroesse gegen feste 2 % (B+, Tagesachse, gemessene Kosten).

Aufruf im Klon-Root (z. B. ~/ebc_exp): venv/bin/python3 experiments/ebc/sizing_stage_a.py
Eingaben: data/ebc/** (Serien, arch_trades.pkl), results/H1/preregistration.json. Ausgabe: results/sizing/{metrics.json,report.md}.

Kosten (PR 1368 / Messung 2026-10): Aktien-Spread 7 bps, Slippage 3.5 bps je Seite, Pauschale 1 USD je Seite (NICHT in bps, siehe
FEE_USD), FX 1.0/0.5, Rohstoffe 10/5, Krypto 15/7.5 + 200 bps Gebuehr, Indizes Rueckfallwerte. Filter und Rang wie in H4 mit
Rueckfallkosten (q <= 0.10), Netto-P&L mit gemessenen Kosten.

Alle Signale zur Groessenwahl sind zum Entscheidungszeitpunkt bekannt (ATR der Signalbar, Regime des Vortags, Perzentile nur
aus frueheren Trades). Die Gruppen unterscheiden sich NUR in der Groesse je Trade."""
import bisect, json, math, os, sys
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np
import common, costs, portfolio as pf, sim

OUT = "results/sizing"; os.makedirs(OUT, exist_ok=True)
START_EQ = 100_000.0          # BOT_CAPITAL
EXPO_CAP = 0.60               # max. investiert
SLOTS = 40
W_FIX, W_MIN, W_MAX = 0.02, 0.01, 0.10
FEE_USD = 1.0                 # je Seite, Aktien (gemessen)
MEASURED = {"EQUITY": dict(spread=7.0, slip=3.5, comm=0.0), "FOREX": dict(spread=1.0, slip=0.5),
            "COMMODITY": dict(spread=10.0, slip=5.0), "CRYPTO": dict(spread=15.0, slip=7.5, comm=200.0)}

rows, ct0, series, cal = common.setup()
ct = costs.CostTable(rows, overrides=MEASURED)
by_id = {r["id"]: r for r in rows}
prereg = json.load(open("results/H1/preregistration.json")); g_start = prereg["comparison_period"]["g_start"]
raw_arch, _ = common.build_arch(rows, ct, series, cal)       # Vektoren mit gemessenen Kosten, q hier noch gemessen
q0 = {i: ct0.c_rt(i) / common.atr_bps_median(series[i]) for i in series}
reg, _ret = common.regime_array(rows, series, cal)
trades = [dict(t, rank=q0[t["id"]], q0=q0[t["id"]]) for t in raw_arch if t["g0"] >= g_start and q0[t["id"]] <= 0.10]
trades.sort(key=lambda t: (t["g0"], t["rank"]))
print("Trades B+ (gemessen):", len(trades), flush=True)

# ---------- Merkmale zum Entscheidungszeitpunkt
for t in trades:
    S = series[t["id"]]; k = max(t["entry_i"] - 1, 0)
    atr_pct = float(S.atr_eff[k] / S.c[k])
    kk = sim.ARCH[t["arch"]]["k"]
    t["atr_bps"] = atr_pct * 1e4
    t["stop_pct"] = kk * atr_pct                       # Abstand des ATR-Trailings in Preisanteilen
    t["edge"] = t["atr_bps"] / ct.c_rt(t["id"])        # Ertragschance-Proxy: Bewegungsspielraum je Kosteneinheit
    t["reg"] = reg[max(t["g0"] - 1, 0)]
    t["cls"] = ct.p[t["id"]]["cls"]
    t["rmult"] = t["net_bps"] / max(t["stop_pct"] * 1e4, 1e-9)      # Netto in Einheiten des Stop-Abstands

def expanding_pct(vals_in_order, key):
    """Perzentil jedes Wertes gegen alle FRUEHEREN (Trades sortiert nach g0); < 50 Vorlaeufer: 0.5."""
    past = []; out = []
    for v in vals_in_order:
        out.append(bisect.bisect_left(past, v) / len(past) if len(past) >= 50 else 0.5)
        bisect.insort(past, v)
    return out

for key in ("edge", "stop_pct"):
    for t, p in zip(trades, expanding_pct([t[key] for t in trades], key)): t["p_" + key] = p

# ---------- Groessenregeln: Anteil an der Equity VOR Caps
def w_fixed(t): return W_FIX
def mk_voltarget(rb):
    return lambda t: min(W_MAX, max(W_MIN, rb / t["stop_pct"]))
def w_edge(t): return W_MIN + (W_MAX - W_MIN) * t["p_edge"]
def w_score(t):                                         # Chance hoch UND Risiko niedrig -> gross
    return W_MIN + (W_MAX - W_MIN) * t["p_edge"] * (1.0 - t["p_stop_pct"])
def w_score_reg(t):
    s = w_score(t)
    return max(W_MIN, s * (0.5 if t["reg"] in ("R3", "R4") else 1.0))
def w_edge_voltarget(rb):
    vt = mk_voltarget(rb)
    return lambda t: max(W_MIN, min(W_MAX, vt(t) * (0.5 + t["p_edge"])))

RULES = {"fix2": w_fixed, "vol0.4": mk_voltarget(0.004), "vol0.6": mk_voltarget(0.006), "vol0.8": mk_voltarget(0.008),
         "edge": w_edge, "score": w_score, "score_reg": w_score_reg, "vol0.6_edge": w_edge_voltarget(0.006)}


def simulate(rule, trs, cash_mode=False):
    by_start = {}
    for t in trs: by_start.setdefault(t["g0"], []).append(t)
    E = START_EQ; active = []; r = np.zeros(cal.n); eq = np.zeros(cal.n); expo = np.zeros(cal.n); taken = []
    for g in range(g_start, cal.n):
        new = by_start.get(g, [])
        busy = {a["key"] for a in active}
        invested = sum(a["N"] for a in active)
        for t in sorted(new, key=lambda t: t["rank"]):
            if t["key"] in busy or len(active) >= SLOTS: continue
            w = rule(t)
            N = w * ((E - invested) if cash_mode else E)
            N = min(N, EXPO_CAP * E - invested, 0.98 * (E - invested))
            if N < W_MIN * E * 0.999 * (1 if not cash_mode else 0.0) or N <= 0: continue
            if t["cls"] == "EQUITY" and N < 1000.0: continue
            active.append(dict(t, N=N, fee=FEE_USD if t["cls"] == "EQUITY" else 0.0)); busy.add(t["key"]); invested += N
            taken.append(active[-1])
        pnl = 0.0; keep = []
        for a in active:
            k = g - a["g0"]
            if k < len(a["vec"]): pnl += a["N"] * a["vec"][k]
            if k == 0: pnl -= a["fee"]
            if g == a["g0"] + len(a["vec"]) - 1: pnl -= a["fee"]
            else: keep.append(a)
        active = keep
        expo[g] = sum(a["N"] for a in active) / E
        r[g] = pnl / E; E += pnl; eq[g] = E
    return r[g_start:], eq[g_start:], expo[g_start:], taken


def summarize(name, r, eq, expo, taken):
    cum = eq[-1] / START_EQ - 1; yrs = len(r) / 252
    return dict(name=name, sharpe=pf.sharpe(r), maxdd=pf.max_dd(r), cvar5=pf.cvar5(r), total_ret=cum,
                cagr=(eq[-1] / START_EQ) ** (1 / yrs) - 1, n_trades=len(taken), mean_expo=float(expo.mean()),
                mean_w=float(np.mean([a["N"] for a in taken]) / START_EQ) if taken else 0.0,
                ret_per_expo=cum / max(float(expo.mean()), 1e-9), calmar=((eq[-1] / START_EQ) ** (1 / yrs) - 1) / max(-pf.max_dd(r), 1e-9),
                sr_h1=pf.sharpe(r[:len(r) // 2]), sr_h2=pf.sharpe(r[len(r) // 2:]))


res = {}; R = {}
for name, rule in RULES.items():
    r, eq, expo, tk = simulate(rule, trades); R[name] = r; res[name] = summarize(name, r, eq, expo, tk)
    print(name, {k: round(v, 3) for k, v in res[name].items() if k != "name"}, flush=True)
r, eq, expo, tk = simulate(RULES["score"], trades, cash_mode=True)
R["score_cash"] = r; res["score_cash"] = summarize("score_cash", r, eq, expo, tk)
cmp_ = {n: pf.delta_sharpe_ci(R[n], R["fix2"]) for n in R if n != "fix2"}
for n, c in cmp_.items(): print("dSR", n, "-fix2", {k: round(v, 3) for k, v in c.items() if k in ("point", "lo", "hi")})

# ---------- Kontrolle: traegt das Signal ueberhaupt? (Netto je Trade nach Terzilen)
def tercile_table(key):
    v = np.array([t[key] for t in trades]); lo, hi = np.percentile(v, [33.3, 66.7]); out = []
    for lab, m in (("niedrig", v <= lo), ("mittel", (v > lo) & (v <= hi)), ("hoch", v > hi)):
        sel = [t for t, f in zip(trades, m) if f]
        out.append(dict(terz=lab, n=len(sel), mean_net_bps=float(np.mean([t["net_bps"] for t in sel])),
                        hit=float(np.mean([t["net_bps"] > 0 for t in sel])), mean_rmult=float(np.mean([t["rmult"] for t in sel])),
                        sd_net_bps=float(np.std([t["net_bps"] for t in sel]))))
    return out

def spearman(a, b):
    ra = np.argsort(np.argsort(a)); rb = np.argsort(np.argsort(b)); return float(np.corrcoef(ra, rb)[0, 1])

signal = {k: dict(terzile=tercile_table(k), spearman_net_bps=spearman([t[k] for t in trades], [t["net_bps"] for t in trades]),
                  spearman_rmult=spearman([t[k] for t in trades], [t["rmult"] for t in trades])) for k in ("edge", "stop_pct", "atr_bps")}
for k, v in signal.items(): print("Signal", k, "rho_net", round(v["spearman_net_bps"], 3), "rho_R", round(v["spearman_rmult"], 3), v["terzile"])
json.dump(dict(rules=res, dSR_vs_fix2=cmp_, signal=signal, n_trades_pool=len(trades), g_start=g_start,
               cost_note="gemessen (PR 1368), Aktien-Pauschale 1 USD je Seite", expo_cap=EXPO_CAP, slots=SLOTS),
          open(f"{OUT}/metrics.json", "w"), indent=1, default=float)
print("OK", OUT)
