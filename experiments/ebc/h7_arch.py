"""EBC-H7 §6.1: Archetyp W (Monatswechsel) und X (12-1-Momentum im Querschnitt), Trades im Format von sim.walk."""
import datetime as dt
import numpy as np
import sim, portfolio as pf

INF = 10**6


def _ym(ts):
    d = dt.datetime.fromtimestamp(int(ts) // 10**9, dt.timezone.utc)
    return d.year * 12 + d.month


def w_trades(S):
    """Signal am Schluss des vorletzten Handelstags des Monats, Einstieg Open des letzten Handelstags, Exit Open des 4. Handelstags des Folgemonats."""
    ym = np.array([_ym(t) for t in S.ts])
    out = []
    months = sorted(set(ym))
    first = {m: int(np.argmax(ym == m)) for m in months}
    last = {m: int(len(ym) - 1 - np.argmax(ym[::-1] == m)) for m in months}
    for m in months[:-1]:
        nxt = m + 1
        if nxt not in first or last[nxt] - first[nxt] < 4: continue
        if m == months[-1]: continue
        e = last[m]; t = e - 1
        if t < 1 or first[m] > t: continue
        x = first[nxt] + 3
        tr = sim.walk(S, "W", t, 1, dict(H=x - e, trailing=False, signal_exit=False))
        if tr is None: continue
        tr["signal_i"] = t
        out.append(tr)
    return out


def x_trades(rows, series, cal, qok, spx_id, top=20, buf=40):
    """Querschnitts-Momentum auf Monatsenden. rows: Instrumente (K1, Kostenfilter bestanden)."""
    ids = [r["id"] for r in rows if r["klasse"] == "K1" and r["id"] in qok]
    g_of = {i: np.array([cal.g(t) for t in series[i].ts]) for i in ids}
    S_spx = series[spx_id]; gspx = np.array([cal.g(t) for t in S_spx.ts]); spx_sma = S_spx.sma200
    # Monatsenden: letzter globaler Handelstag mit SPX-Kerze je Kalendermonat
    me = {}
    for k, g in enumerate(gspx):
        me[_ym(S_spx.ts[k])] = (k, g)
    me_list = sorted(me.values(), key=lambda x: x[1])[:-1]    # letzten (unvollstaendigen) Monat weglassen
    ranks = {}   # (id) -> {j: rank}
    month_info = []
    for (ks, gD) in me_list:
        mom = {}
        for i in ids:
            gi = g_of[i]
            j = int(np.searchsorted(gi, gD, side="right")) - 1
            if j < 252 or gi[j] != gD: continue
            c = series[i].c
            mom[i] = (c[j - 21] / c[j - 252] - 1.0, j)
        order = sorted(mom, key=lambda i: (-mom[i][0], int(i)))
        rk = {i: n + 1 for n, i in enumerate(order)}
        month_info.append((gD, ks, mom, rk))
        for i, (m_, j) in mom.items():
            ranks.setdefault(i, {})[j] = rk[i]
    flags = {i: np.zeros(series[i].n, bool) for i in ids}
    for i, d in ranks.items():
        for j, rkv in d.items():
            if rkv > buf: flags[i][j] = True
    out = {i: [] for i in ids}; held = {}
    for (gD, ks, mom, rk) in month_info:
        held = {i: gx for i, gx in held.items() if gx > gD}
        if not (S_spx.c[ks] > spx_sma[ks]) or np.isnan(spx_sma[ks]): continue
        for i in sorted(rk, key=lambda i: rk[i]):
            if rk[i] > top: break
            if i in held: continue
            j = mom[i][1]
            tr = sim.walk(series[i], "X", j, 1, dict(H=INF, exit_flags=flags[i], k=4.0))
            if tr is None: continue
            tr["signal_i"] = j; tr["mom_rank"] = rk[i]
            out[i].append(tr)
            held[i] = cal.g(series[i].ts[tr["exit_i"]])
    return out
