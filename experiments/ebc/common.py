"""Gemeinsame Ladefunktionen fuer H1/H2/H3 (Universum, Serien, Kalender, Kosten, Archetyp-Trades mit Vektoren)."""
import os, pickle, sys
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import sim, route_r, universe, costs, portfolio as pf

ARCHS = ("T", "M", "V")


def setup():
    rows = universe.load()
    ct = costs.CostTable(rows)
    series = {r["id"]: sim.Series(*route_r.load_daily(r["id"])) for r in rows}
    cal = pf.Calendar(series)
    return rows, ct, series, cal


def atr_bps_median(S, last=250):
    a = (S.atr / S.c * 1e4)[S.atr_ok]
    return float(np.median(a[-last:]))


def q_of(r, ct, S):
    return ct.c_rt(r["id"]) / atr_bps_median(S)


def build_arch(rows, ct, series, cal, trades=None):
    """Archetyp-Trades (aus gen_arch.py) mit Netto, Vektor, Kostenquote q als Rang. Rueckgabe Liste dict."""
    raw = trades if trades is not None else pickle.load(open("data/ebc/arch_trades.pkl", "rb"))
    q = {r["id"]: q_of(r, ct, series[r["id"]]) for r in rows}
    out = []
    for r in rows:
        i = r["id"]; S = series[i]
        for arch in ARCHS:
            for t in raw.get((i, arch), []):
                g0, v = pf.trade_vector(S, t, cal, ct, i)
                out.append(dict(t, id=i, arch=arch, klasse=r["klasse"], g0=g0, vec=v, rank=q[i], tie=int(i), key=(i, arch),
                                net_bps=ct.net_bps(i, t["gross_bps"], t["side"], t["hold_days"]), q=q[i]))
    return out, q


def regime_array(rows, series, cal):
    """R1-R4 je globalem Tag (EBC-H1 §7.6; Referenz SPX500). Rueckgabe (reg, index_ret)."""
    spx = next(r_ for r_ in rows if r_["symbol"] == "SPX500")
    S = series[spx["id"]]
    ix = np.full(cal.n, np.nan)
    for k in range(S.n):
        ix[cal.g(S.ts[k])] = S.c[k]
    for k in range(1, cal.n):
        if np.isnan(ix[k]):
            ix[k] = ix[k - 1]
    ret = np.concatenate([[0], np.diff(ix) / ix[:-1]])
    sma = np.full(cal.n, np.nan); vol = np.full(cal.n, np.nan)
    for k in range(200, cal.n): sma[k] = ix[k - 199:k + 1].mean()
    for k in range(20, cal.n): vol[k] = ret[k - 19:k + 1].std(ddof=1)
    med = np.nanmedian(vol)
    reg = np.full(cal.n, "R4", dtype=object)
    for k in range(220, cal.n):
        if ix[k] > sma[k] and vol[k] <= med: reg[k] = "R1"
        elif ix[k] > sma[k]: reg[k] = "R2"
        elif ix[k] < sma[k] and sma[k] < sma[k - 20]: reg[k] = "R3"
    return reg, ret


def regime_table(r_by_name, g_from, reg, ret_ix):
    big = np.abs(ret_ix) >= np.nanpercentile(np.abs(ret_ix[g_from:]), 95)
    out = []
    for name, mask in [("R1", reg[g_from:] == "R1"), ("R2", reg[g_from:] == "R2"), ("R3", reg[g_from:] == "R3"),
                       ("R4", reg[g_from:] == "R4"), ("top5pct_abs_index_ret", big[g_from:])]:
        row = {"regime": name, "n_days": int(mask.sum())}
        for a, r in r_by_name.items():
            rr = np.asarray(r)[g_from:][mask]
            row[f"SR_{a}"] = pf.sharpe(rr) if len(rr) > 5 else None
            row[f"mean_daily_bps_{a}"] = float(rr.mean() * 1e4) if len(rr) else None
        out.append(row)
    return out
