"""EBC-H2: Signale S_MOM / S_REV (gestaffelte Teilportfolios), Fenster-Trades, Bootstrap-Hilfen."""
import math
import numpy as np
import portfolio as pf

DAY = pf.DAY_NS


def sig(S, kind, long_only):
    c = S.c; n = S.n
    lr = np.zeros(n)
    if kind == "MOM":
        lr[60:] = np.log(c[60:] / c[:-60]); s = np.sign(lr); s[:60] = 0
    else:
        lr[5:] = np.log(c[5:] / c[:-5]); s = -np.sign(lr); s[:5] = 0
    return np.maximum(s, 0.0) if long_only else s


def sig_windows(S, kind, h, long_only, c_rt_bps, fin_long, fin_short):
    """Rueckgabe: daily (Netto-Tagesrenditen je Instrument-Tag u, Laenge n), windows dict (t, gross, net, p, ic_x, ic_y)."""
    n = S.n; o = S.o
    s = sig(S, kind, long_only)
    # Tagesgewichte
    cs = np.concatenate([[0.0], np.cumsum(s)])
    w = np.zeros(n)
    for u in range(1, n):
        lo = max(0, u - h)
        w[u] = (cs[u] - cs[lo]) / h
    d = np.zeros(n); d[:-1] = o[1:] / o[:-1] - 1
    gross_d = w * d
    # Kosten am Umschichtungstag u = t+1 : (c_RT/2)(1/h)|s_t - s_{t-h}|
    cost = np.zeros(n)
    for t in range(0, n - 1):
        prev = s[t - h] if t - h >= 0 else 0.0
        cost[t + 1] = (c_rt_bps / 2e4) * abs(s[t] - prev) / h
    gap = np.ones(n)
    gap[:-1] = (S.ts[1:] - S.ts[:-1]) / DAY
    fin = gap * (fin_long * np.maximum(w, 0) + fin_short * np.maximum(-w, 0)) / 1e4
    net_d = gross_d - cost - fin
    net_d[-1] = 0.0
    net_d[:61 if kind == "MOM" else 6] = 0.0
    # Fenster
    ts_ = np.arange(0, n - 1 - h)
    valid = ts_ >= (60 if kind == "MOM" else 5)
    t = ts_[valid]
    p = s[t]
    gross = p * (o[t + 1 + h] / o[t + 1] - 1) * 1e4
    prev = np.where(t - h >= 0, s[np.maximum(t - h, 0)], 0.0)
    nxt = np.where(t + h < n, s[np.minimum(t + h, n - 1)], 0.0)
    nxt = np.where(t + h <= n - 2 - h, nxt, 0.0)
    cost_w = (c_rt_bps / 2.0) * (np.abs(p - prev) + np.abs(nxt - p))
    days = np.maximum((S.ts[t + h] - S.ts[t]) / DAY, 1.0)
    fin_w = days * np.where(p > 0, fin_long, np.where(p < 0, fin_short, 0.0))
    net_w = gross - cost_w - fin_w
    # IC
    ret1 = np.zeros(n); ret1[1:] = np.log(S.c[1:] / S.c[:-1])
    sd60 = np.full(n, np.nan)
    for u in range(60, n): sd60[u] = ret1[u - 59:u + 1].std(ddof=1)
    fwd = np.log(o[t + 1 + h] / o[t + 1])
    ic_y = fwd / (sd60[t] * math.sqrt(h))
    ok = np.isfinite(ic_y)
    return dict(daily=net_d, t=t, gross=gross, net=net_w, p=p, ic_x=s[t][ok], ic_y=ic_y[ok], sd1=float(np.nanmean(sd60[60:])))


def month_cluster_ci(values, months, B=5000, seed=20261011, level=0.90):
    """Cluster-Bootstrap nach Kalendermonat; Mittelwert."""
    values = np.asarray(values, float); months = np.asarray(months)
    if len(values) == 0:
        return dict(mean=float("nan"), lo=float("nan"), hi=float("nan"), n=0)
    um, inv = np.unique(months, return_inverse=True)
    sums = np.bincount(inv, weights=values, minlength=len(um)); cnt = np.bincount(inv, minlength=len(um)).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(um), size=(B, len(um)))
    means = sums[idx].sum(1) / np.maximum(cnt[idx].sum(1), 1)
    return dict(mean=float(values.mean()), lo=float(np.percentile(means, 100 * (1 - level) / 2)),
                hi=float(np.percentile(means, 100 * (1 + level) / 2)), n=int(len(values)))
