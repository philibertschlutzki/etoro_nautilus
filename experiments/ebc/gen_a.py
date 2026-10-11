"""Arm-A-Rohdaten (Route R): die 8 Tagesachsen-Strategien mit strategy_defaults + PAPER_TRAILING_TP je Instrument.
Ausgabe: data/ebc/a_trades/<id>.pkl {strategy: [trade...]} (Roh, inkl. Shorts; A1-Filter spaeter)."""
import json, multiprocessing as mp, os, pickle, sys, time
os.environ["ETORO_CONFIG_DIR"] = os.path.abspath("cfg_A")
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
STRATS = ["SmaCrossoverStrategy", "MeanReversionStrategy", "DynamicBreakoutStrategy", "TrendPullbackStrategy",
          "AdxAtrMomentumStrategy", "SqueezeBreakoutStrategy", "DonchianRegimeBreakoutStrategy", "Rsi2ReversionStrategy"]
OUT = "data/ebc/a_trades"; os.makedirs(OUT, exist_ok=True)

def work(r):
    import route_r, universe
    path = f"{OUT}/{r['id']}.pkl"
    if os.path.exists(path): return r["id"], "cached"
    sd = json.load(open("cfg_A/strategy_defaults.json"))
    series = route_r.load_daily(r["id"])
    res = {}
    for s in STRATS:
        try:
            res[s] = route_r.run(s, universe.sym(r), series, defaults=dict(sd[s]))
        except Exception as e:
            res[s] = {"error": repr(e)[:200]}
    pickle.dump(res, open(path, "wb"))
    return r["id"], "ok"

if __name__ == "__main__":
    import universe
    rows = universe.load()
    t0 = time.time()
    with mp.Pool(int(sys.argv[1]) if len(sys.argv) > 1 else 5) as pool:
        for k, (i, st) in enumerate(pool.imap_unordered(work, rows)):
            if k % 25 == 0: print(k, len(rows), round(time.time() - t0), "s", flush=True)
