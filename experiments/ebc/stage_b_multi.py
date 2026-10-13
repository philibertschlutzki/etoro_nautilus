"""Stufe B (Forward-Shadow) fuer die Kandidaten aus EBC-H4 bis H7: taeglich nach Kerzenschluss (>= 22:30 UTC) Kerzen fortschreiben und Arme neu rechnen.
Arme: C30 (= B+), HLNP30 (T inf / M 1 / V inf + NP10, 30 x 2 %), Kombi6P (T, M, V, R, X, W im gemeinsamen Pool 120 x 0,5 %).
Aufruf: python experiments/ebc/stage_b_multi.py [--since 2026-10-12]   Ausgabe: results/H4/stage_b/forward_daily_returns.csv
Nur lesende API-Aufrufe (<= 1 Req/s); es werden keine Orders gesendet. Verbucht nur Trades mit Einstieg ab --since."""
import argparse, csv, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np
import api, h48, h7_arch, portfolio as pf, universe

ap = argparse.ArgumentParser(); ap.add_argument("--since", default="2026-10-12"); a = ap.parse_args()
today = dt.datetime.now(dt.timezone.utc).date()
rows0 = universe.load()
for r in rows0:
    path = f"data/ebc/daily/{r['id']}.csv"
    old = list(csv.DictReader(open(path)))
    gap = (today - dt.date.fromisoformat(old[-1]["date"])).days
    if gap <= 1: continue
    d = api.candles(r["id"], "OneDay", min(1000, gap + 2))
    have = {x["date"] for x in old}; new = []
    for blk in d.get("candles", []):
        for c in blk.get("candles", []):
            ds = c["fromDate"][:10]
            if ds not in have and ds < today.isoformat():
                new.append(dict(date=ds, open=c["open"], high=c["high"], low=c["low"], close=c["close"], volume=c.get("volume", 0)))
    if new:
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"]); w.writeheader(); w.writerows(sorted(old + new, key=lambda x: x["date"]))
h48.CUT = today
rows, ct, series, cal = h48.setup(with_neu=False)
since = (dt.date.fromisoformat(a.since) - dt.date(1970, 1, 1)).days
g_since = next((g for g, d_ in enumerate(cal.days) if d_ >= since), cal.n)
INF = 10**6; NP = (10, 1.0)
def trades(archs, opts=None, rws=rows):
    raw = h48.gen_trades(rws, series, archs=archs, opts_by_arch=opts)
    return h48.build(rws, ct, series, cal, raw, g0min=g_since)[0]
base = trades(("T", "M", "V"))
hl = trades(("T",), {"T": dict(H=INF, np=NP)}) + trades(("M",), {"M": dict(H=1)}) + trades(("V",), {"V": dict(H=INF, np=NP)})
rr = trades(("R",), None, [r for r in rows if r["klasse"] in ("K1", "K4")])
raw = {}
for r in rows:
    if r["klasse"] == "K4": raw[(r["id"], "W")] = h7_arch.w_trades(series[r["id"]])
q0 = {r["id"]: ct.c_rt(r["id"]) / h48.atr_med(series[r["id"]]) for r in rows}
spx = next(r for r in rows if r["symbol"] == "SPX500")["id"]
for i, trs in h7_arch.x_trades(rows, series, cal, {i for i, v in q0.items() if v <= 0.10}, spx).items(): raw[(i, "X")] = trs
rxw = h48.build(rows, ct, series, cal, raw, g0min=g_since)[0]
for t in rxw:
    if t["arch"] == "X": t["rank"] = t["mom_rank"]
out = {}
out["C30"] = pf.simulate(base, cal, slots=30, notional_frac=.02, g_from=g_since)
out["HLNP30"] = pf.simulate(hl, cal, slots=30, notional_frac=.02, g_from=g_since)
r6, e6, t6 = pf.simulate_sleeves([dict(t, sleeve="P") for t in base + rr + rxw], cal, {"P": 120}, notional_frac=.005, cap_total=.6, g_from=g_since)
out["Kombi6P"] = (r6, e6, t6)
os.makedirs("results/H4/stage_b", exist_ok=True)
with open("results/H4/stage_b/forward_daily_returns.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date"] + [f"{k}_r" for k in out] + [f"{k}_equity" for k in out])
    for g in range(g_since, cal.n):
        w.writerow([str(dt.date(1970, 1, 1) + dt.timedelta(days=cal.days[g]))] + [out[k][0][g] for k in out] + [out[k][1][g] for k in out])
for k, v in out.items(): print(k, "Forward-Tage:", cal.n - g_since, "Trades:", len(v[2]), "SR:", round(pf.sharpe(v[0][g_since:]), 2) if cal.n - g_since > 5 else "n/a")
