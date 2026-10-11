"""Archetypen T/M/V (Route V) auf allen Universums-Instrumenten, sequentiell (eine Position je Instrument/Archetyp).
Ausgabe: data/ebc/arch_trades.pkl  {(id, arch): [trade, ...]}  (brutto; Kosten werden spaeter angesetzt)."""
import os, pickle, sys
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np, sim, route_r, universe

rows = universe.load()
out = {}
for r in rows:
    S = sim.Series(*route_r.load_daily(r["id"]))
    for arch in ("T", "M", "V"):
        out[(r["id"], arch)] = sim.sequential(S, arch, allow_short=(r["klasse"] in universe.SHORT_OK))
pickle.dump(out, open("data/ebc/arch_trades.pkl", "wb"))
print("Instrumente", len(rows), "Trades", sum(len(v) for v in out.values()))
