"""Stundenkerzen (1000) fuer eine geschichtete Stichprobe (Seed 20261011) + die Instrumente aus h1_axis_break; lesend, 1 Req/s."""
import csv, os, random, sys
sys.path.insert(0, os.path.dirname(__file__))
import api, universe
rows = universe.load(); rnd = random.Random(20261011)
want = {"K1": 30, "K2": 10, "K3": 5, "K4": 5, "K5": 10}
sel = []
for k, n in want.items():
    pool = [r for r in rows if r["klasse"] == k]
    sel += rnd.sample(pool, min(n, len(pool)))
os.makedirs("data/ebc/hourly", exist_ok=True)
for r in sel:
    path = f"data/ebc/hourly/{r['id']}.csv"
    if os.path.exists(path): continue
    d = api.candles(r["id"], "OneHour", 1000)
    out = []
    for blk in d.get("candles", []):
        for c in blk.get("candles", []):
            out.append((c["fromDate"], c["open"], c["high"], c["low"], c["close"]))
    out.sort()
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts", "open", "high", "low", "close"]); w.writerows(out)
print("fertig", len(sel))
