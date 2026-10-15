"""Arm-A-Rohdaten (Route R): die 8 Tagesachsen-Strategien mit strategy_defaults + PAPER_TRAILING_TP je Instrument.
Ausgabe: data/ebc/a_trades/<id>.pkl {strategy: [trade...]} (Roh, inkl. Shorts; A1-Filter spaeter)."""
import json, multiprocessing as mp, os, pickle, sys, time
os.environ["ETORO_CONFIG_DIR"] = os.path.abspath("cfg_A")
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
STRATS = ["SmaCrossoverStrategy", "MeanReversionStrategy", "DynamicBreakoutStrategy", "TrendPullbackStrategy",
          "AdxAtrMomentumStrategy", "SqueezeBreakoutStrategy", "DonchianRegimeBreakoutStrategy", "Rsi2ReversionStrategy"]
OUT = "data/ebc/a_trades"; os.makedirs(OUT, exist_ok=True)

def base_dir(iid):
    return "data/ebc/daily" if os.path.exists(f"data/ebc/daily/{iid}.csv") else "data/ebc/daily_neu"

def is_stale(iid, refresh_days):
    """True, wenn kein Pkl existiert oder (bei refresh_days > 0) die Tagesdatei neuer ist und das Pkl aelter als N Tage."""
    path = f"{OUT}/{iid}.pkl"
    if not os.path.exists(path): return True
    if refresh_days <= 0: return False
    src = f"{base_dir(iid)}/{iid}.csv"
    return os.path.exists(src) and os.path.getmtime(src) > os.path.getmtime(path) and time.time() - os.path.getmtime(path) > refresh_days * 86400

def work(args):
    r, refresh_days = args
    import route_r, universe
    path = f"{OUT}/{r['id']}.pkl"
    if not is_stale(r["id"], refresh_days): return r["id"], "cached"
    sd = json.load(open("cfg_A/strategy_defaults.json"))
    series = route_r.load_daily(r["id"], base=base_dir(r["id"]))
    res = {}
    for s in STRATS:
        try:
            res[s] = route_r.run(s, universe.sym(r), series, defaults=dict(sd[s]))
        except Exception as e:
            res[s] = {"error": repr(e)[:200]}
    tmp = path + ".tmp"
    pickle.dump(res, open(tmp, "wb")); os.replace(tmp, path)          # atomar: ein Abbruch hinterlaesst kein halbes Pkl
    return r["id"], "ok"

def all_rows():
    import universe
    rows = universe.load()
    if os.path.exists("results/H4/universe_neu.csv"):
        seen = {r["id"] for r in rows}
        rows += [r for r in universe.load("results/H4/universe_neu.csv") if r["id"] not in seen]
    return rows

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--ids", default=None, help="Komma-Liste etoro_id (ein Block des Load-Managers)")
    ap.add_argument("--refresh-days", type=float, default=7, help="Pkl neu rechnen, wenn aelter als N Tage und Tagesdatei neuer (0 = nie)")
    ap.add_argument("--list-pending", action="store_true", help="nur die IDs ausgeben, die Arbeit brauchen")
    a = ap.parse_args()
    rows = all_rows()
    if a.ids:
        want = set(a.ids.split(","))
        rows = [r for r in rows if r["id"] in want]
    if a.list_pending:
        print("\n".join(r["id"] for r in rows if is_stale(r["id"], a.refresh_days))); sys.exit(0)
    t0 = time.time()
    with mp.Pool(a.workers) as pool:
        for k, (i, st) in enumerate(pool.imap_unordered(work, [(r, a.refresh_days) for r in rows])):
            if k % 25 == 0: print(k, len(rows), round(time.time() - t0), "s", flush=True)
