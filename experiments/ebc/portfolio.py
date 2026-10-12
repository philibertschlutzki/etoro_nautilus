"""Ereignis-Portfoliosimulator (EBC-H1 §7.3) und Kennzahlen (§10)."""
import math
import numpy as np

DAY_NS = 86_400_000_000_000


class Calendar:
    """Globaler Handelstag-Index (Mo-Fr); Wochenend-Tage der 24/7-Instrumente fallen auf den folgenden Montag."""

    def __init__(self, series_by_id):
        days = set()
        for S in series_by_id.values():
            for t in S.ts:
                d = int(t // DAY_NS)
                days.add(self.roll(d))
        self.days = sorted(days)
        self.idx = {d: i for i, d in enumerate(self.days)}
        self.n = len(self.days)

    @staticmethod
    def roll(d):   # d = Tage seit 1970-01-01 (Do=0)
        wd = (d + 3) % 7        # 0=Mo
        return d + (7 - wd) if wd >= 5 else d

    def g(self, ts):
        return self.idx[self.roll(int(ts // DAY_NS))]


def trade_vector(S, tr, cal, cost, i, sign_cost=True):
    """Tages-P&L des Trades als Anteil des Notionals je globalem Tag: (g_start, vektor). Kosten: halbe c_RT am Entry-, halbe am
    Exit-Tag, Finanzierung nach Kalendertagen auf die Haltetage verteilt."""
    e, x, side = tr["entry_i"], tr["exit_i"], tr["side"]
    ep = tr["entry_px"]
    days = []; vals = []
    if x == e:
        days.append(cal.g(S.ts[e])); vals.append(side * (tr["exit_px"] - ep) / ep)
    else:
        days.append(cal.g(S.ts[e])); vals.append(side * (S.c[e] - ep) / ep)
        for j in range(e + 1, x):
            days.append(cal.g(S.ts[j])); vals.append(side * (S.c[j] - S.c[j - 1]) / ep)
        days.append(cal.g(S.ts[x])); vals.append(side * (tr["exit_px"] - S.c[x - 1]) / ep)
    if tr.get("partial_adj"):   # P3: gewichtete Bruttorendite -> Differenz am Exit-Tag
        vals[-1] += tr["partial_adj"]
    g0 = days[0]; g1 = days[-1]
    v = np.zeros(g1 - g0 + 1)
    for d, val in zip(days, vals):
        v[d - g0] += val
    cp = cost.p[i]
    half = (cost.c_rt(i) / 2.0) / 1e4
    v[0] -= half; v[-1] -= half
    # Finanzierung: Kalendertage je Handelstag-Sprung
    fin = cp["fin_long"] if side > 0 else cp["fin_short"]
    if fin:
        for j in range(e + 1, x + 1):
            gap = int((S.ts[j] - S.ts[j - 1]) // DAY_NS) if j > e else 0
            v[cal.g(S.ts[j]) - g0] -= gap * fin / 1e4
    return g0, v


def simulate(trades, cal, slots=30, notional_frac=0.02, start=10_000.0, g_from=0, stats=None):
    """trades: Liste dict mit 'g0','vec','rank' (kleiner = Vorrang) und 'key' (Instrument/Archetyp). Rueckgabe: (r_daily, equity, taken)."""
    by_start = {}
    for t in trades:
        by_start.setdefault(t["g0"], []).append(t)
    E = start
    active = []
    r = np.zeros(cal.n); eq = np.zeros(cal.n)
    taken = []
    for g in range(g_from, cal.n):
        new = by_start.get(g, [])
        if new:
            free = slots - len(active)
            if free > 0:
                busy = {a["key"] for a in active}
                cand = sorted((t for t in new if t["key"] not in busy), key=lambda t: (t["rank"], t.get("tie", 0)))
                for t in cand[:free]:
                    active.append(dict(t, N=notional_frac * E))
                    taken.append(dict(t, N=notional_frac * E))
                if stats is not None:
                    stats["n_cand"] = stats.get("n_cand", 0) + len(cand); stats["n_taken"] = stats.get("n_taken", 0) + min(free, len(cand))
                    stats.setdefault("dropped", []).extend(cand[free:])
            elif stats is not None:
                busy = {a["key"] for a in active}
                cand = [t for t in new if t["key"] not in busy]
                stats["n_cand"] = stats.get("n_cand", 0) + len(cand)
                stats.setdefault("dropped", []).extend(cand)
        pnl = 0.0
        keep = []
        for a in active:
            k = g - a["g0"]
            if k < len(a["vec"]):
                pnl += a["N"] * a["vec"][k]
            if g < a["g0"] + len(a["vec"]) - 1:
                keep.append(a)
        active = keep
        r[g] = pnl / E if E > 0 else 0.0
        E += pnl
        eq[g] = E
        if stats is not None:
            stats.setdefault("open", []).append(len(active))
    return r, eq, taken


def sharpe(r):
    r = np.asarray(r, float)
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0


def max_dd(r):
    eq = np.cumprod(1 + np.asarray(r, float))
    peak = np.maximum.accumulate(eq)
    return float(((eq - peak) / peak).min()) if len(eq) else 0.0


def cvar5(x):
    x = np.sort(np.asarray(x, float))
    k = max(1, int(math.ceil(0.05 * len(x))))
    return float(x[:k].mean())


def psr(r):
    from automation.optimizer.deflation import probabilistic_sharpe_ratio
    r = np.asarray(r, float)
    if len(r) < 3 or r.std(ddof=1) == 0:
        return float("nan")
    sr = r.mean() / r.std(ddof=1)
    m = r - r.mean(); sk = float((m ** 3).mean() / r.std() ** 3); ku = float((m ** 4).mean() / r.std() ** 4)
    return probabilistic_sharpe_ratio(sr, len(r), skew=sk, kurtosis=ku)


def boot_indices(n, block, B, rng):
    """Vektorisierter Stationary Bootstrap (Politis/Romano, Wrap-around): B x n Indizes."""
    p = 1.0 / max(1.0, float(block))
    starts = rng.integers(0, n, size=(B, n))
    restart = rng.random((B, n)) < p
    restart[:, 0] = True
    idx = np.empty((B, n), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for i in range(1, n):
        idx[:, i] = np.where(restart[:, i], starts[:, i], (idx[:, i - 1] + 1) % n)
    return idx


def delta_sharpe_ci(r1, r2, B=5000, seed=20261011, level=0.90):
    from automation.optimizer.bootstrap import optimal_block_length
    r1 = np.asarray(r1, float); r2 = np.asarray(r2, float)
    d = r1 - r2
    block = optimal_block_length(d)
    rng = np.random.default_rng(seed)
    idx = boot_indices(len(d), block, B, rng)
    a, b = r1[idx], r2[idx]
    sa = a.mean(1) / np.where(a.std(1, ddof=1) > 0, a.std(1, ddof=1), np.nan) * math.sqrt(252)
    sb = b.mean(1) / np.where(b.std(1, ddof=1) > 0, b.std(1, ddof=1), np.nan) * math.sqrt(252)
    ds = sa - sb
    lo, hi = np.nanpercentile(ds, [100 * (1 - level) / 2, 100 * (1 + level) / 2])
    return dict(point=sharpe(r1) - sharpe(r2), lo=float(lo), hi=float(hi), block=int(block), B=B)


def sharpe_ci(r, B=5000, seed=20261011, level=0.90):
    from automation.optimizer.bootstrap import optimal_block_length
    r = np.asarray(r, float)
    rng = np.random.default_rng(seed)
    idx = boot_indices(len(r), optimal_block_length(r), B, rng)
    a = r[idx]
    s = a.mean(1) / np.where(a.std(1, ddof=1) > 0, a.std(1, ddof=1), np.nan) * math.sqrt(252)
    lo, hi = np.nanpercentile(s, [100 * (1 - level) / 2, 100 * (1 + level) / 2])
    return dict(point=sharpe(r), lo=float(lo), hi=float(hi))


def spearman(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    if len(x) < 3: return float("nan")
    def rank(a):
        o = np.argsort(a, kind="mergesort"); rk = np.empty(len(a)); rk[o] = np.arange(len(a))
        # Durchschnittsraenge bei Gleichstand
        vals, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
        csum = np.cumsum(cnt) - (cnt - 1) / 2.0 - 1
        return csum[inv]
    rx, ry = rank(x), rank(y)
    if rx.std() == 0 or ry.std() == 0: return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def simulate_sized(trades, cal, slots=30, start=10_000.0, g_from=0, cap_total=0.60, cap_pos=0.10, frac_key="frac"):
    """Wie simulate, aber Notional je Position = trade[frac_key] * Equity (gedeckelt auf cap_pos); Gesamtdeckel cap_total
    (bindet er, wird die neue Position auf den Rest gekuerzt, bei Rest <= 0 uebersprungen). Rueckgabe r, eq, taken, exposure."""
    by_start = {}
    for t in trades:
        by_start.setdefault(t["g0"], []).append(t)
    E = start
    active = []
    r = np.zeros(cal.n); eq = np.zeros(cal.n); expo = np.zeros(cal.n)
    taken = []
    for g in range(g_from, cal.n):
        new = by_start.get(g, [])
        if new:
            free = slots - len(active)
            if free > 0:
                busy = {a["key"] for a in active}
                cand = sorted((t for t in new if t["key"] not in busy), key=lambda t: (t["rank"], t.get("tie", 0)))
                used = sum(a["N"] for a in active) / E
                for t in cand[:free]:
                    f = min(float(t[frac_key]), cap_pos)
                    f = min(f, cap_total - used)
                    if f <= 1e-6:
                        continue
                    used += f
                    active.append(dict(t, N=f * E, f=f))
                    taken.append(dict(t, f=f))
        pnl = 0.0
        keep = []
        for a in active:
            k = g - a["g0"]
            if k < len(a["vec"]):
                pnl += a["N"] * a["vec"][k]
            if g < a["g0"] + len(a["vec"]) - 1:
                keep.append(a)
        expo[g] = sum(a["N"] for a in active) / E
        active = keep
        r[g] = pnl / E if E > 0 else 0.0
        E += pnl
        eq[g] = E
    return r, eq, taken, expo


def contrast_sharpe_ci(series, weights, B=5000, seed=20261011, level=0.90):
    """KI fuer sum_i w_i * SR(series_i); gemeinsamer Stationary Bootstrap. Fuer zwei Reihen mit (+1, -1) identisch zu delta_sharpe_ci."""
    from automation.optimizer.bootstrap import optimal_block_length
    S = [np.asarray(s, float) for s in series]
    d = sum(w * s for w, s in zip(weights, S))
    block = optimal_block_length(d)
    rng = np.random.default_rng(seed)
    idx = boot_indices(len(d), block, B, rng)
    tot = 0.0
    for w, s in zip(weights, S):
        a = s[idx]
        sd = a.std(1, ddof=1)
        tot = tot + w * (a.mean(1) / np.where(sd > 0, sd, np.nan) * math.sqrt(252))
    lo, hi = np.nanpercentile(tot, [100 * (1 - level) / 2, 100 * (1 + level) / 2])
    return dict(point=sum(w * sharpe(s) for w, s in zip(weights, S)), lo=float(lo), hi=float(hi), block=int(block), B=B)


def simulate_sleeves(trades, cal, quotas, notional_frac=0.005, cap_total=0.60, start=10_000.0, g_from=0, stats=None):
    """Mehrere Sleeves (trade['sleeve']) mit eigener Platzquote, gemeinsame Equity, Gesamtdeckel cap_total (Plaetze = cap_total/notional_frac).
    Je Tag nimmt jeder Sleeve (in der Reihenfolge von quotas) seine freien Plaetze nach rank. Fuer einen Sleeve mit Quote N entspricht das
    simulate(slots=N)."""
    total_slots = int(round(cap_total / notional_frac))
    by_start = {}
    for t in trades:
        by_start.setdefault(t["g0"], []).append(t)
    E = start; active = []
    r = np.zeros(cal.n); eq = np.zeros(cal.n); taken = []
    for g in range(g_from, cal.n):
        new = by_start.get(g, [])
        if new:
            busy = {a["key"] for a in active}
            for sl, quota in quotas.items():
                n_act = sum(1 for a in active if a["sleeve"] == sl)
                free = min(quota - n_act, total_slots - len(active))
                cand = sorted((t for t in new if t["sleeve"] == sl and t["key"] not in busy), key=lambda t: (t["rank"], t.get("tie", 0)))
                if stats is not None:
                    d = stats.setdefault(sl, dict(n_cand=0, n_taken=0, dropped=[]))
                    d["n_cand"] += len(cand)
                for t in (cand[:free] if free > 0 else []):
                    a = dict(t, N=notional_frac * E); active.append(a); taken.append(a); busy.add(t["key"])
                    if stats is not None: stats[sl]["n_taken"] += 1
                if stats is not None:
                    stats[sl]["dropped"].extend(cand[max(free, 0):])
        pnl = 0.0; keep = []
        for a in active:
            k = g - a["g0"]
            if k < len(a["vec"]): pnl += a["N"] * a["vec"][k]
            if g < a["g0"] + len(a["vec"]) - 1: keep.append(a)
        active = keep
        r[g] = pnl / E if E > 0 else 0.0
        E += pnl; eq[g] = E
        if stats is not None:
            for sl in quotas:
                stats.setdefault(sl, dict(n_cand=0, n_taken=0, dropped=[])).setdefault("open", []).append(sum(1 for a in active if a["sleeve"] == sl))
    return r, eq, taken
