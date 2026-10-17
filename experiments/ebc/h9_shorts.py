"""EBC-H9: Aktien-Shorts, breite Variantensuche auf der Tagesachse (Route V, wie EBC-H3 Stufe A).

Frage: Gibt es eine Short-Regel für Aktien, deren Netto-Erwartung nach Kosten UND nach Abzug der Drift-Basislinie
(zufällige Short-Einstiege mit demselben Ausstieg) ausserhalb der Stichprobe hält? Der Backtest-Befund aus H3 (Z1:
-81 bps je Aktien-Short) gilt nur für die gespiegelten Archetypen T/M/V; hier werden alle Einstiegsfamilien, Filter und
Ausstiege gekreuzt, nichts vorab eingeschränkt.

Stufe 1: alle Einstiegsmengen (Signal x Filter) x kleines Ausstiegsraster (6) + Basislinie.
Stufe 2: Überlebende aus Stufe 1 (Regel steht im Kopf von ``select_survivors``) x volles Ausstiegsraster (48).
Messgrösse: Netto-bps je Short (Kosten wie costs.py, Aktien-Spread 7 bps nach Messung 16.10.), Edge = Variante - Basislinie
des gleichen Ausstiegs, Monats-Cluster-SE, Auswahl nur auf dem ersten Zeitabschnitt (IS), Prüfung auf dem letzten (OOS).
Mehrfachtest-Gegenprobe: Rangkorrelation IS->OOS über alle Varianten und Replikationsquote der IS-Auswahl.

Aufruf (im Repo-Wurzelverzeichnis, mit Tagesdaten unter data/ebc/daily):
  venv/bin/python3 experiments/ebc/h9_shorts.py --stage 1 --workers 4
  venv/bin/python3 experiments/ebc/h9_shorts.py --stage 2 --workers 4
  venv/bin/python3 experiments/ebc/h9_shorts.py --report
"""
import argparse, csv, datetime as dt, json, math, os, sys, time
from multiprocessing import Pool

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import sim, route_r, universe, costs

OUT = "results/H9"
WARMUP = 260                      # Tage Vorlauf (SMA200 + Puffer), Signale davor werden verworfen
M0 = 2015 * 12                    # Monatsindex-Offset
NM = 12 * 20
DAY_NS = 86_400_000_000_000
EQUITY_SPREAD_BPS = 7.0           # gemessen in der US-Sitzung (Median 6-8 bps), Memory 2026-10-16
SPLIT_FRAC = 0.6                  # IS = erste 60 % des Zeitraums nach dem Vorlauf, OOS = Rest

# ─── Ausstiegsraster ────────────────────────────────────────────────────────────────
H_ALL = (1, 2, 3, 5, 10, 20)
STOPS = {"nostop": dict(trailing=False), "k1.5": dict(k=1.5), "k2.5": dict(k=2.5), "k4": dict(k=4.0)}
TTPS = ("P0", "P1")


def exit_grid(full):
    out = []
    if full:
        for h in H_ALL:
            for sn, so in STOPS.items():
                for ttp in TTPS:
                    out.append((f"H{h}_{sn}_{ttp}", dict(H=h, ttp=ttp, **so)))
    else:
        for h in (2, 5, 10):
            for sn in ("nostop", "k2.5"):
                out.append((f"H{h}_{sn}_P0", dict(H=h, ttp="P0", **STOPS[sn])))
    return out


# ─── Signale (Short, am Schlusskurs von Tag t) ───────────────────────────────────────
def _shift(x, n):
    out = np.full(len(x), np.nan)
    if n == 0:
        return np.asarray(x, float).copy()
    if n < len(x):
        out[n:] = x[:-n]
    return out


def build_signals(S):
    c, o, h, l = S.c, S.o, S.h, S.l
    n = S.n
    atr = np.where(S.atr_ok, S.atr, np.nan)
    sma20, sma50 = sim.roll_mean(c, 20), sim.roll_mean(c, 50)
    sd20 = sim.roll_std(c, 20)
    ret1 = np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
    up = np.concatenate([[False], c[1:] > c[:-1]])
    rng_ = h - l
    ibs = np.where(rng_ > 0, (c - l) / np.where(rng_ > 0, rng_, 1.0), np.nan)
    rsi = np.where(S.rsi_ok, S.rsi2, np.nan)
    sg = {}
    for N in (10, 20, 50, 100):                                             # F1 Donchian-Tief
        pm = sim.prior_min(c, N); sg[f"donch_low{N}"] = c < pm
    for th in (90, 95, 98):                                                 # F2 RSI2 überkauft x Trend
        r = rsi > th
        sg[f"rsi2_{th}"] = r
        sg[f"rsi2_{th}_below200"] = r & (c < S.sma200)
        sg[f"rsi2_{th}_below50"] = r & (c < sma50)
        sg[f"rsi2_{th}_above200"] = r & (c > S.sma200)
    for th in (0.8, 0.9, 0.95):                                             # F3 IBS
        sg[f"ibs_{th}"] = ibs > th
    sg["bb_up2"] = c > sma20 + 2.0 * sd20                                   # F4 Bollinger oben
    sg["bb_up3"] = c > sma20 + 3.0 * sd20
    for nn in (3, 4, 5):                                                    # F5 aufeinanderfolgende Aufwärtstage
        run = np.ones(n, bool)
        for j in range(nn):
            run &= np.nan_to_num(_shift(up.astype(float), j), nan=0.0) > 0
        sg[f"up_streak{nn}"] = run
    for a in (1.5, 2.5):                                                    # F6 grosser Aufwärtstag (ATR-Einheiten)
        sg[f"bigup_{a}atr"] = (ret1 * c / atr) > a
    for a in (2, 3, 4):                                                     # F7 Überdehnung über SMA20 / SMA50
        sg[f"ext20_{a}atr"] = (c - sma20) / atr > a
    for a in (4, 6):
        sg[f"ext50_{a}atr"] = (c - sma50) / atr > a
    for nm_, ma in (("sma50", sma50), ("sma200", S.sma200)):                # F8 Bruch unter gleitenden Durchschnitt
        below = c < ma
        sg[f"cross_below_{nm_}"] = below & ~np.nan_to_num(_shift(below.astype(float), 1), nan=0.0).astype(bool)
    for N in (120, 252):                                                    # F9 Mehrmonats-Tief
        sg[f"low{N}"] = c < sim.prior_min(c, N)
    sg["squeeze_down"] = S.signals("V")[1]                                  # F10 Squeeze-Ausbruch nach unten
    pc = _shift(c, 1)
    sg["gapup_reversal"] = (o > pc + atr) & (c < o)                         # F11 Gap-up-Umkehrkerze
    mom = _shift(c, 21) / _shift(c, 252) - 1.0
    sg["mom12_1_neg"] = mom < 0                                             # F12 Momentum-Verlierer
    sg["ret6m_lt_m20"] = (c / _shift(c, 126) - 1.0) < -0.20
    sg["ret6m_gt_p40"] = (c / _shift(c, 126) - 1.0) > 0.40                  # Überhitzte Gewinner
    sg["BASE_all"] = (np.arange(n) % 3 == 0)                                # Basislinie: jeder 3. Tag
    for k, v in sg.items():
        sg[k] = np.nan_to_num(np.asarray(v, float), nan=0.0).astype(bool)
    return sg


def build_gates(S, spx):
    c = S.c
    n = S.n
    g = {"none": np.ones(n, bool)}
    g["below200"] = np.nan_to_num((c < S.sma200).astype(float)).astype(bool)
    g["down_regime"] = S.down_regime
    atrp = np.where(S.atr_ok, S.atr / c, np.nan)
    med = np.nanmedian(atrp[WARMUP:]) if n > WARMUP else np.nanmedian(atrp)
    g["highvol"] = np.nan_to_num((atrp > med).astype(float)).astype(bool)
    idx = np.searchsorted(spx["ts"], S.ts)
    idx = np.clip(idx, 0, len(spx["ts"]) - 1)
    ok = spx["ts"][idx] == S.ts
    g["index_down"] = ok & spx["down"][idx]
    g["index_dayred"] = ok & spx["red"][idx]
    return g


def load_spx(rows):
    r = next((x for x in rows if x["symbol"] == "SPX500"), None)
    if r is None:
        return {"ts": np.array([0], np.int64), "down": np.array([False]), "red": np.array([False])}
    S = sim.Series(*route_r.load_daily(r["id"]))
    red = np.concatenate([[False], S.c[1:] < S.c[:-1]])
    return {"ts": S.ts, "down": S.down_regime, "red": red}


# ─── Worker ─────────────────────────────────────────────────────────────────────────
def month_idx(ts_ns):
    d = dt.date(1970, 1, 1) + dt.timedelta(days=int(ts_ns // DAY_NS))
    return d.year * 12 + d.month - 1 - M0


def run_instrument(job):
    r, set_names, exits, split_ts, spx, spread = job
    ct = costs.CostTable([r], overrides={"EQUITY": {"spread": spread}})
    i = r["id"]
    try:
        S = sim.Series(*route_r.load_daily(i))
    except (OSError, ValueError):
        return None
    if S.n < WARMUP + 30:
        return None
    sg, gates = build_signals(S), build_gates(S, spx)
    nS, nE = len(set_names), len(exits)
    sums = np.zeros((nS, nE, 2, NM)); cnts = np.zeros((nS, nE, 2, NM))
    lock = 2.0 * ct.p[i]["spread"] + ct.p[i]["comm"]
    for si, name in enumerate(set_names):
        sig_name, gate_name = name.split("|")
        arr = sg[sig_name] & gates[gate_name]
        edges = [t for t in range(max(WARMUP, 1), S.n - 1) if arr[t] and not arr[t - 1]]
        for t in edges:
            per = 0 if S.ts[t + 1] < split_ts else 1
            for ei, (en, eo) in enumerate(exits):
                o = dict(eo, signal_exit=False, min_lock_bps=lock)
                tr = sim.walk(S, "M", t, -1, o)
                if tr is None:
                    continue
                net = ct.net_bps(i, tr["gross_bps"], -1, tr["hold_days"])
                m = month_idx(S.ts[tr["entry_i"]])
                if 0 <= m < NM:
                    sums[si, ei, per, m] += net; cnts[si, ei, per, m] += 1
    return sums, cnts


# ─── Auswertung ─────────────────────────────────────────────────────────────────────
def cluster_stats(s, n):
    """mean, cluster-SE (Monate), N aus Monatssummen s und Monatszahlen n."""
    N = n.sum()
    if N < 2:
        return float("nan"), float("nan"), int(N)
    mu = s.sum() / N
    se = math.sqrt(((s - mu * n) ** 2).sum()) / N
    return float(mu), float(se), int(N)


def split_ts_of(rows):
    old = sorted(r["oldest"] for r in rows if r.get("oldest"))
    new = max(r["newest"] for r in rows if r.get("newest"))
    t0 = dt.datetime.fromisoformat(old[len(old) // 2][:10]) + dt.timedelta(days=WARMUP * 7 // 5)
    t1 = dt.datetime.fromisoformat(new[:10])
    cut = t0 + (t1 - t0) * SPLIT_FRAC
    return int(cut.replace(tzinfo=dt.timezone.utc).timestamp()) * 10**9


def write_stage(stage, set_names, exits, sums, cnts):
    os.makedirs(OUT, exist_ok=True)
    base_i = [i for i, n in enumerate(set_names) if n.startswith("BASE_all|none")][0]
    path = f"{OUT}/stage{stage}.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["set", "exit", "n_is", "mean_is", "edge_is", "t_is", "n_oos", "mean_oos", "edge_oos", "t_oos"])
        for si, name in enumerate(set_names):
            for ei, (en, _) in enumerate(exits):
                row = [name, en]
                for per in (0, 1):
                    mu, se, N = cluster_stats(sums[si, ei, per], cnts[si, ei, per])
                    bmu, bse, _ = cluster_stats(sums[base_i, ei, per], cnts[base_i, ei, per])
                    edge = mu - bmu
                    t = edge / math.sqrt(se ** 2 + bse ** 2) if se == se and se > 0 else float("nan")
                    row += [N, round(mu, 2), round(edge, 2), round(t, 2)]
                w.writerow(row)
    return path


def select_survivors(rows_, min_n=200, min_t=2.0):
    """Vorregistrierte Regel: IS-Edge-t >= 2 und IS-N >= 200 (Basislinie ausgenommen). Gesamtzahl der Varianten gehört in den Bericht."""
    keep = []
    for r in rows_:
        if r["set"].startswith("BASE_all"):
            continue
        if int(r["n_is"]) >= min_n and r["t_is"] not in ("nan", "") and float(r["t_is"]) >= min_t:
            keep.append(r["set"])
    return sorted(set(keep))


def run_stage(stage, workers, limit):
    rows_all = universe.load()
    rows = [r for r in rows_all if r["klasse"] == "K1"]
    if limit:
        rows = rows[:limit]
    spx = load_spx(rows_all)
    split_ts = split_ts_of(rows)
    if stage == 1:
        sig_names = sorted(build_signals(sim.Series(*_demo_series(rows))).keys())
        gate_names = ["none", "below200", "down_regime", "highvol", "index_down", "index_dayred"]
        set_names = [f"{s}|{g}" for s in sig_names if s != "BASE_all" for g in gate_names] + ["BASE_all|none"]
    else:
        prev = list(csv.DictReader(open(f"{OUT}/stage1.csv")))
        set_names = select_survivors(prev) + ["BASE_all|none"]
    exits = exit_grid(full=(stage == 2))
    print(f"Stufe {stage}: {len(rows)} Aktien, {len(set_names)} Einstiegsmengen, {len(exits)} Ausstiege, Split {split_ts}", flush=True)
    jobs = [(r, set_names, exits, split_ts, spx, EQUITY_SPREAD_BPS) for r in rows]
    sums = np.zeros((len(set_names), len(exits), 2, NM)); cnts = np.zeros_like(sums)
    t0 = time.time()
    with Pool(workers) as pool:
        for k, res in enumerate(pool.imap_unordered(run_instrument, jobs, chunksize=1), 1):
            if res is not None:
                sums += res[0]; cnts += res[1]
            if k % 20 == 0:
                print(f"  {k}/{len(jobs)} Instrumente, {round(time.time() - t0)} s", flush=True)
    print("geschrieben:", write_stage(stage, set_names, exits, sums, cnts))


def _demo_series(rows):
    for r in rows:
        try:
            return route_r.load_daily(r["id"])
        except (OSError, ValueError):
            continue
    raise SystemExit("keine Tagesdaten unter data/ebc/daily gefunden")


def report():
    from statistics import NormalDist
    nd = NormalDist()
    s1 = list(csv.DictReader(open(f"{OUT}/stage1.csv")))
    s2 = list(csv.DictReader(open(f"{OUT}/stage2.csv"))) if os.path.exists(f"{OUT}/stage2.csv") else []
    def f(x):
        try:
            return float(x)
        except ValueError:
            return float("nan")
    def rank_corr(rows_):
        pairs = [(f(r["edge_is"]), f(r["edge_oos"])) for r in rows_ if not r["set"].startswith("BASE_all")]
        pairs = [(a, b) for a, b in pairs if a == a and b == b]
        if len(pairs) < 10:
            return None
        ra = np.argsort(np.argsort([a for a, _ in pairs])); rb = np.argsort(np.argsort([b for _, b in pairs]))
        return float(np.corrcoef(ra, rb)[0, 1])
    def bh(rows_):
        p = []
        for r in rows_:
            t = f(r["t_oos"])
            if t == t and not r["set"].startswith("BASE_all"):
                p.append((1 - nd.cdf(t), r))
        p.sort(key=lambda x: x[0]); m = len(p); keep = []
        for k, (pv, r) in enumerate(p, 1):
            if pv <= 0.10 * k / m:
                keep = p[:k]
        return [r for _, r in keep], m
    L = ["# EBC-H9 Aktien-Shorts: breite Variantensuche (Tagesachse)", ""]
    n1 = len({r["set"] for r in s1 if not r["set"].startswith("BASE")})
    L += [f"Stufe 1: {n1} Einstiegsmengen x 6 Ausstiege = {len(s1) - 6} Varianten (ohne Basislinie).", ""]
    L += ["Netto-bps je Short nach Kosten (Aktien-Spread 7 bps, Finanzierung 0,79 bps/Tag). Edge = Variante minus Basislinie "
          "(jeder 3. Tag, gleicher Ausstieg). t mit Monats-Cluster-SE. IS = erste 60 %, OOS = letzte 40 % des Zeitraums.", ""]
    bl = [r for r in s1 if r["set"].startswith("BASE_all")]
    L += ["## Basislinie (zufällige Short-Einstiege)", "", "| Ausstieg | Ø netto IS | Ø netto OOS |", "|---|---|---|"]
    L += [f"| {r['exit']} | {r['mean_is']} | {r['mean_oos']} |" for r in bl] + [""]
    rc = rank_corr(s1)
    L += [f"IS->OOS-Rangkorrelation des Edge über alle Stufe-1-Varianten: {rc:.2f}" if rc is not None else "Rangkorrelation: zu wenige Varianten", ""]
    surv = [r for r in s1 if not r["set"].startswith("BASE") and f(r["t_is"]) >= 2 and int(r["n_is"]) >= 200]
    rep = [r for r in surv if f(r["edge_oos"]) > 0 and f(r["t_oos"]) >= 1.64]
    L += [f"IS-Überlebende (t_IS >= 2, N_IS >= 200): {len(surv)}; davon im OOS mit Edge > 0 und t >= 1,64: {len(rep)} "
          f"({len(rep) / len(surv):.0%} gegen ca. 5 % unter reinem Zufall)." if surv else "Keine IS-Überlebenden.", ""]
    top = sorted([r for r in s1 if not r["set"].startswith("BASE") and int(r["n_oos"]) >= 30], key=lambda r: -f(r["t_oos"]))[:15]
    L += ["## Beste 15 Varianten nach OOS-t (N_OOS >= 30; Stufe 1, nicht für die Auswahl nutzbar)", "",
          "| Einstieg \\| Filter | Ausstieg | N IS | Edge IS | t IS | N OOS | Edge OOS | t OOS |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['set']} | {r['exit']} | {r['n_is']} | {r['edge_is']} | {r['t_is']} | {r['n_oos']} | {r['edge_oos']} | {r['t_oos']} |" for r in top] + [""]
    keep, m = bh(s1)
    L += [f"Benjamini-Hochberg (FDR 10 %) auf OOS-t über {m} Varianten: {len(keep)} Treffer.", ""]
    if s2:
        sv = {r["set"] for r in s2 if not r["set"].startswith("BASE")}
        L += [f"## Stufe 2: {len(sv)} Überlebende x 48 Ausstiege", ""]
        keep2, m2 = bh(s2)
        rc2 = rank_corr(s2)
        L += [f"IS->OOS-Rangkorrelation: {rc2:.2f}" if rc2 is not None else "", f"BH (FDR 10 %) über {m2} Varianten: {len(keep2)} Treffer.", ""]
        top2 = sorted([r for r in s2 if not r["set"].startswith("BASE") and f(r["t_is"]) >= 2 and int(r["n_oos"]) >= 30], key=lambda r: -f(r["t_oos"]))[:15]
        L += ["| Einstieg \\| Filter | Ausstieg | Edge IS | t IS | N OOS | Edge OOS | t OOS |", "|---|---|---|---|---|---|---|"]
        L += [f"| {r['set']} | {r['exit']} | {r['edge_is']} | {r['t_is']} | {r['n_oos']} | {r['edge_oos']} | {r['t_oos']} |" for r in top2] + [""]
    L += ["## Lesart", "",
          "- Eine Short-Regel gilt nur dann als Kandidat, wenn sie die Auswahl auf IS besteht UND im OOS Edge > 0 mit t >= 1,64 hat UND "
          "in der BH-Liste steht. Alles andere ist bei dieser Variantenzahl erwartbar Zufall.",
          "- Ein hoher Wert der Rangkorrelation (>0,3) spräche für echte Struktur; nahe 0 oder negativ für Auswahl-Verzerrung.",
          "- Nächster Schritt für Kandidaten: Portfolio-Simulation (2 % je Position, <= 40 Short-Plätze) und Stufe C (Leerverkaufbarkeit, Finanzierung).", ""]
    open(f"{OUT}/report.md", "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, choices=(1, 2))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="nur die ersten N Aktien (Rauchtest)")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
    elif a.stage:
        run_stage(a.stage, a.workers, a.limit)
    else:
        ap.error("--stage 1|2 oder --report")
