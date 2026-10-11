"""Stufe B (Folgearbeit, kein Blocker): taeglich nach Kerzenschluss Tageskerzen fortschreiben und den B+-Schatten-Lauf neu rechnen.
Aufruf (cron, nach 22:30 UTC):  python experiments/ebc/stage_b_daily.py [--since 2026-10-12]
Schreibt results/H1/stage_b/forward_daily_returns.csv (Datum, B+, Anzahl offener Positionen). Nur lesende API-Aufrufe (<= 1 Req/s).
Hinweis: aktualisiert nur Kerzen mit Luecke (count = min(1000, Luecke + 2)); verbucht nur Trades mit Einstieg ab --since."""
import argparse, csv, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import api, common, portfolio as pf, sim, universe

ap = argparse.ArgumentParser(); ap.add_argument("--since", default="2026-10-12"); a = ap.parse_args()
rows = universe.load()
today = dt.datetime.now(dt.timezone.utc).date()
for r in rows:
    path = f"data/ebc/daily/{r['id']}.csv"
    old = list(csv.DictReader(open(path)))
    last = dt.date.fromisoformat(old[-1]["date"])
    gap = (today - last).days
    if gap <= 1: continue
    d = api.candles(r["id"], "OneDay", min(1000, gap + 2))
    have = {x["date"] for x in old}
    new = []
    for blk in d.get("candles", []):
        for c in blk.get("candles", []):
            ds = c["fromDate"][:10]
            if ds not in have and ds < today.isoformat():
                new.append(dict(date=ds, open=c["open"], high=c["high"], low=c["low"], close=c["close"], volume=c.get("volume", 0)))
    if new:
        allr = sorted(old + new, key=lambda x: x["date"])
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"]); w.writeheader(); w.writerows(allr)
rows, ct, series, cal = common.setup()
since = (dt.date.fromisoformat(a.since) - dt.date(1970, 1, 1)).days
g_since = next((g for g, d_ in enumerate(cal.days) if d_ >= since), cal.n)
tr = []
q = {r["id"]: common.q_of(r, ct, series[r["id"]]) for r in rows}
for r in rows:
    if q[r["id"]] > 0.10: continue
    i = r["id"]; S = series[i]
    for arch in common.ARCHS:
        for t in sim.sequential(S, arch, allow_short=(r["klasse"] in universe.SHORT_OK)):
            g0, v = pf.trade_vector(S, t, cal, ct, i)
            if g0 >= g_since:
                tr.append(dict(t, id=i, g0=g0, vec=v, rank=q[i], tie=int(i), key=(i, arch)))
r, eq, taken = pf.simulate(tr, cal, slots=30, g_from=g_since)
os.makedirs("results/H1/stage_b", exist_ok=True)
with open("results/H1/stage_b/forward_daily_returns.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date", "Bplus", "equity"])
    for g in range(g_since, cal.n):
        w.writerow([str(dt.date(1970, 1, 1) + dt.timedelta(days=cal.days[g])), r[g], eq[g]])
print("Forward-Tage:", cal.n - g_since, "Trades:", len(taken))
