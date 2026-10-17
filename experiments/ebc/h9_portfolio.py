"""EBC-H9 Teil 2: Portfolio-Simulation und Robustheit für Aktien-Short-Kandidaten.

Voraussetzung: ``h9_shorts.py --stage 1 --monthly`` hat ``results/H9/monthly.npz`` geschrieben (alle Einstiegsmengen x 48 Ausstiege).

A) Walk-forward-Portfolio (ehrlich): Alle ``--step`` Monate werden aus den vorangegangenen ``--train`` Monaten die besten
   ``--k`` Varianten gewählt (Edge-t >= 2, N >= 200, je Einstiegsmenge nur die beste), im folgenden Block als Short-Portfolio
   gehandelt (2 % je Position, höchstens ``--slots`` gleichzeitig, eine Position je Aktie, Rangfolge per Zufallsschlüssel).
   Die aneinandergehängten Testblöcke sind die einzige Reihe, die nicht aus der Auswahl heraus beschönigt ist.
B) Hindsight-Portfolio (nur Gefahrenprobe): die über ALLE Quartale stabilen Varianten (Edge > 0 in >= 75 % der Quartale, t >= 2
   auf der gesamten Stichprobe), die besten ``--k`` Einstiegsmengen; Kennzahlen plus Konzentration (Anteil der besten 10
   Einstiegstage und Aktien am Gewinn, Ergebnis ohne sie) und Kalenderjahre. Diese Reihe ist durch die Auswahl auf denselben Daten verzerrt.
C) Kontrolle: dasselbe Portfolio mit zufälligen Short-Einstiegen (Basislinie) und gleichem Ausstieg.
"""
import argparse, json, math, os, sys, time

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import sim, route_r, universe, costs, portfolio as pf
import h9_shorts as h9

OUT = h9.OUT
BASE = "BASE_all|none"


class World:
    """Aktien-Tagesreihen, Signale, Kosten und Kalender; Trades je Variante werden lazy gerechnet und gecacht."""

    def __init__(self, limit=0):
        rows_all = universe.load()
        self.rows = [r for r in rows_all if r["klasse"] == "K1"]
        if limit:
            self.rows = self.rows[:limit]
        self.spx = h9.load_spx(rows_all)
        self.ct = costs.CostTable(self.rows, overrides={"EQUITY": {"spread": h9.EQUITY_SPREAD_BPS}})
        self.series = {}
        for r in self.rows:
            try:
                S = sim.Series(*route_r.load_daily(r["id"]))
            except (OSError, ValueError):
                continue
            if S.n >= h9.WARMUP + 30:
                self.series[r["id"]] = S
        self.cal = pf.Calendar(self.series)
        self.month_of_g = np.array([h9.month_idx(d * pf.DAY_NS) for d in self.cal.days])
        self.sig = {i: (h9.build_signals(S), h9.build_gates(S, self.spx)) for i, S in self.series.items()}
        self.exits = dict(h9.exit_grid(full=True))
        self.cache = {}

    def trades(self, set_name, exit_name):
        key = (set_name, exit_name)
        if key in self.cache:
            return self.cache[key]
        sig_name, gate_name = set_name.split("|")
        out = []
        for i, S in self.series.items():
            sg, gates = self.sig[i]
            arr = sg[sig_name] & gates[gate_name]
            lock = 2.0 * self.ct.p[i]["spread"] + self.ct.p[i]["comm"]
            for t in range(max(h9.WARMUP, 1), S.n - 1):
                if not (arr[t] and not arr[t - 1]):
                    continue
                o = dict(self.exits[exit_name], signal_exit=False, min_lock_bps=lock)
                tr = sim.walk(S, "M", t, -1, o)
                if tr is None:
                    continue
                g0, v = pf.trade_vector(S, tr, self.cal, self.ct, i)
                out.append(dict(g0=g0, vec=v, key=i, tie=int(i), rank=(int(i) * 1000003 + t * 7919) % 99991,
                                month=int(self.month_of_g[g0]), net_bps=self.ct.net_bps(i, tr["gross_bps"], -1, tr["hold_days"])))
        self.cache[key] = out
        return out


def metrics(r):
    r = np.asarray(r, float)
    if len(r) < 20 or r.std(ddof=1) == 0:
        return dict(n_days=len(r), sr=None)
    eq = np.cumprod(1 + r)
    years = len(r) / 252.0
    return dict(n_days=len(r), sr=round(pf.sharpe(r), 2), ann_ret=round(float(eq[-1] ** (1 / years) - 1), 4),
                maxdd=round(pf.max_dd(r), 4), total=round(float(eq[-1] - 1), 4), cvar5_daily=round(pf.cvar5(r), 5))


def window_edge(S, C, b, W):
    s, c = S[:, :, W], C[:, :, W]
    N = c.sum(2)
    with np.errstate(invalid="ignore", divide="ignore"):
        mu = s.sum(2) / N
        bN = c[b:b + 1].sum(2); bmu = s[b:b + 1].sum(2) / bN
        se = np.sqrt(((s - mu[..., None] * c) ** 2).sum(2)) / N
        bse = np.sqrt(((s[b:b + 1] - bmu[..., None] * c[b:b + 1]) ** 2).sum(2)) / bN
        edge = mu - bmu
        t = edge / np.sqrt(se ** 2 + bse ** 2)
    return edge, t, N


def pick_variants(S, C, sets, exits, b, W, k, min_n=200, min_t=2.0):
    """Beste Variante je Einstiegsmenge (nach Edge-t im Fenster W), davon die k besten Einstiegsmengen."""
    e, t, N = window_edge(S, C, b, W)
    ok = (N >= min_n) & np.isfinite(t) & (t >= min_t)
    ok[b, :] = False
    best = []
    for si in range(len(sets)):
        if ok[si].any():
            ei = int(np.nanargmax(np.where(ok[si], t[si], -np.inf)))
            best.append((float(t[si, ei]), sets[si], exits[ei]))
    best.sort(reverse=True)
    return best[:k]


def run_portfolio(world, variants, g_lo, g_hi, slots, frac, start=10_000.0):
    """Portfolio der Varianten, nur Einstiege mit g0 in [g_lo, g_hi). Rückgabe (r[g_lo:g_hi], taken)."""
    trades = []
    for sname, ename in variants:
        for t in world.trades(sname, ename):
            if g_lo <= t["g0"] < g_hi:
                trades.append(dict(t, key=t["key"], tag=(sname, ename)))
    r, eq, taken = pf.simulate(trades, world.cal, slots=slots, notional_frac=frac, start=start, g_from=g_lo)
    return r[g_lo:g_hi], taken


def concentration(taken, world, top=10):
    per_trade = np.array([t["N"] * t["vec"].sum() for t in taken])
    if not len(per_trade):
        return {}
    total = float(per_trade.sum())
    days, stocks = {}, {}
    for t, p in zip(taken, per_trade):
        days[t["g0"]] = days.get(t["g0"], 0.0) + p
        stocks[t["key"]] = stocks.get(t["key"], 0.0) + p
    best_days = sorted(days, key=lambda g: -days[g])[:top]
    best_stocks = sorted(stocks, key=lambda i: -stocks[i])[:top]
    drop_days = sum(days[g] for g in best_days)
    drop_stocks = sum(stocks[i] for i in best_stocks)
    return dict(n_trades=len(taken), total_pnl_usd=round(total, 1), mean_pnl_per_trade_usd=round(float(per_trade.mean()), 2),
                share_top10_entry_days=round(drop_days / total, 2) if total else None,
                pnl_without_top10_entry_days=round(total - drop_days, 1),
                share_top10_stocks=round(drop_stocks / total, 2) if total else None,
                pnl_without_top10_stocks=round(total - drop_stocks, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=18)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--slots", type=int, default=40)
    ap.add_argument("--frac", type=float, default=0.02)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--baseline-exit", default="H10_nostop_P0")
    a = ap.parse_args()
    t0 = time.time()
    z = np.load(f"{OUT}/monthly.npz", allow_pickle=False)
    S, C, sets, exits = z["sums"], z["cnts"], list(z["sets"]), list(z["exits"])
    b = sets.index(BASE)
    months = [m for m in range(S.shape[2]) if C[b, :, m].sum() > 0]
    lo, hi = months[0], months[-1] + 1
    world = World(a.limit)
    print(f"Welt geladen: {len(world.series)} Aktien, {world.cal.n} Handelstage, {round(time.time() - t0)} s", flush=True)

    # A) Walk-forward
    folds, r_parts, oos_base = [], [], []
    k = lo + a.train
    while k + a.step <= hi:
        tr_m, te_m = list(range(k - a.train, k)), list(range(k, k + a.step))
        picks = pick_variants(S, C, sets, exits, b, tr_m, a.k)
        gs = int(np.searchsorted(world.month_of_g, k)); ge = int(np.searchsorted(world.month_of_g, k + a.step))
        rb, _ = run_portfolio(world, [(BASE, a.baseline_exit)], gs, ge, a.slots, a.frac)
        if picks:
            r, taken = run_portfolio(world, [(s, e) for _, s, e in picks], gs, ge, a.slots, a.frac)
        else:
            r, taken = np.zeros(ge - gs), []
        r_parts.append(r); oos_base.append(rb)
        folds.append(dict(block=f"{h9_ym(k)} bis {h9_ym(k + a.step - 1)}", n_picked=len(picks), n_trades=len(taken),
                          ret=round(float(np.prod(1 + r) - 1), 4), base_ret=round(float(np.prod(1 + rb) - 1), 4),
                          picks=[f"{s} / {e}" for _, s, e in picks[:5]]))
        print("Fold", folds[-1]["block"], folds[-1]["n_picked"], folds[-1]["ret"], flush=True)
        k += a.step
    r_oos = np.concatenate(r_parts) if r_parts else np.zeros(0)
    r_base = np.concatenate(oos_base) if oos_base else np.zeros(0)
    res = dict(walk_forward=dict(metrics=metrics(r_oos), baseline_metrics=metrics(r_base), folds=folds,
                                 sr_ci90=(pf.sharpe_ci(r_oos) if len(r_oos) > 60 else None)))

    # B) Hindsight (Gefahrenprobe)
    qs = [list(range(q, q + 3)) for q in range(lo, hi - 2, 3)]
    pos = np.zeros(S.shape[:2]); cnt = np.zeros(S.shape[:2])
    for W in qs:
        e, t, N = window_edge(S, C, b, W)
        v = (N >= 30) & np.isfinite(e)
        pos += (v & (e > 0)); cnt += v
    e_all, t_all, N_all = window_edge(S, C, b, months)
    stable = (cnt >= max(4, len(qs) - 2)) & (pos / np.maximum(cnt, 1) >= 0.75) & (t_all >= 2) & (N_all >= 300)
    stable[b, :] = False
    cands = {}
    for si, ei in zip(*np.nonzero(stable)):
        if sets[si] not in cands or e_all[si, ei] > cands[sets[si]][0]:
            cands[sets[si]] = (float(e_all[si, ei]), exits[ei])
    top = sorted(cands.items(), key=lambda kv: -kv[1][0])[:a.k]
    res["hindsight"] = dict(n_stable_variants=int(stable.sum()), n_stable_sets=len(cands), chosen=[f"{s} / {x[1]} (Edge {x[0]:.0f})" for s, x in top])
    if top:
        gs, ge = 0, world.cal.n
        r_h, taken = run_portfolio(world, [(s, x[1]) for s, x in top], gs, ge, a.slots, a.frac)
        first = int(np.nonzero(r_h != 0)[0][0]) if (r_h != 0).any() else 0
        r_h = r_h[first:]
        res["hindsight"].update(metrics=metrics(r_h), concentration=concentration(taken, world))
        yrs = {}
        for g, rr in zip(range(first, ge), r_h):
            yrs.setdefault(1970 + int(world.cal.days[g] // 365.25), []).append(rr)
        res["hindsight"]["by_year"] = {str(y): dict(n=len(v), ret=round(float(np.prod(1 + np.array(v)) - 1), 4)) for y, v in sorted(yrs.items())}
        r_hb, _ = run_portfolio(world, [(BASE, a.baseline_exit)], gs, ge, a.slots, a.frac)
        res["hindsight"]["baseline_metrics"] = metrics(r_hb[first:])
    os.makedirs(OUT, exist_ok=True)
    json.dump(res, open(f"{OUT}/portfolio.json", "w"), indent=1, default=str)
    write_md(res, a)
    print(open(f"{OUT}/portfolio.md", encoding="utf-8").read())


def h9_ym(m):
    return f"{(h9.M0 + m) // 12}-{(h9.M0 + m) % 12 + 1:02d}"


def write_md(res, a):
    wf, hs = res["walk_forward"], res["hindsight"]
    f = lambda d, k_: "n/a" if not d or d.get(k_) is None else d[k_]
    L = ["# EBC-H9 Portfolio-Simulation Aktien-Shorts", "",
         f"Short-Portfolio, {a.frac:.0%} je Position, höchstens {a.slots} Positionen, eine Position je Aktie, Kosten wie costs.py (Spread 7 bps, Finanzierung 0,79 bps/Tag).", "",
         f"## A) Walk-forward ({a.train} Monate Training, {a.step} Monate Test, die besten {a.k} Einstiegsmengen)", "",
         "Nur diese Reihe ist nicht durch die Auswahl verzerrt.", "",
         "| Kennzahl | Auswahl-Portfolio | Zufalls-Shorts (Basislinie) |", "|---|---|---|"]
    for k_, lab in (("n_days", "Handelstage"), ("sr", "Sharpe"), ("ann_ret", "Rendite p. a."), ("maxdd", "Max. Drawdown"), ("total", "Gesamtrendite")):
        L.append(f"| {lab} | {f(wf['metrics'], k_)} | {f(wf['baseline_metrics'], k_)} |")
    if wf.get("sr_ci90"):
        c = wf["sr_ci90"]
        L += ["", f"Sharpe 90-%-Intervall (Block-Bootstrap): {c['lo']:.2f} bis {c['hi']:.2f}."]
    L += ["", "| Block | Auswahl | Trades | Rendite | Basislinie | Beispiele |", "|---|---|---|---|---|---|"]
    L += [f"| {x['block']} | {x['n_picked']} | {x['n_trades']} | {x['ret']:.2%} | {x['base_ret']:.2%} | {'; '.join(x['picks'][:2])} |" for x in wf["folds"]]
    L += ["", f"## B) Hindsight-Portfolio (verzerrt, nur Gefahrenprobe): {hs['n_stable_sets']} stabile Einstiegsmengen, {hs['n_stable_variants']} Varianten", ""]
    if "metrics" in hs:
        L += ["| Kennzahl | Hindsight | Basislinie |", "|---|---|---|"]
        for k_, lab in (("sr", "Sharpe"), ("ann_ret", "Rendite p. a."), ("maxdd", "Max. Drawdown"), ("total", "Gesamtrendite")):
            L.append(f"| {lab} | {f(hs['metrics'], k_)} | {f(hs['baseline_metrics'], k_)} |")
        c = hs["concentration"]
        L += ["", f"Konzentration: {c.get('n_trades')} Trades, Gewinn gesamt {c.get('total_pnl_usd')} USD; die 10 besten Einstiegstage tragen "
              f"{c.get('share_top10_entry_days')} davon (ohne sie {c.get('pnl_without_top10_entry_days')} USD), die 10 besten Aktien "
              f"{c.get('share_top10_stocks')} (ohne sie {c.get('pnl_without_top10_stocks')} USD).", "",
              "Rendite je Kalenderjahr: " + "; ".join(f"{y}: {v['ret']:.1%} ({v['n']} Tage)" for y, v in hs["by_year"].items()), "",
              "Gewählt: " + "; ".join(hs["chosen"][:8])]
    L += ["", "## Lesart", "",
          "- Trägt A) nicht (Sharpe nahe 0 oder unter der Basislinie, Intervall schliesst 0 ein), gibt es keinen Beleg für eine überdauernde Short-Regel.",
          "- B) ist durch die Auswahl auf denselben Daten beschönigt; sein Wert liegt in der Konzentration: tragen wenige Tage oder Aktien den Gewinn, ist es kein Edge.",
          "- Offen auch bei positivem A): Leerverkaufbarkeit je Aktie und gemessene Finanzierung (Stufe C, Demo-Orders durch Phili).", ""]
    open(f"{OUT}/portfolio.md", "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    main()
