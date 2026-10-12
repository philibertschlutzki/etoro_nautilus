"""Spread-Stichproben (lesend): GET /market-data/instruments/rates in Batches, Anhang an data/ebc/spreads.jsonl.
Aufruf: sample_spreads.py [--loop MINUTEN] [--max-snapshots N]"""
import argparse, json, os, sys, time, datetime as dt
sys.path.insert(0, os.path.dirname(__file__))
import api, universe

ap = argparse.ArgumentParser(); ap.add_argument("--loop", type=float, default=0); ap.add_argument("--max-snapshots", type=int, default=1); ap.add_argument("--universe", action="append", default=[])
a = ap.parse_args()
ids = [r["id"] for r in universe.load()]
for u in a.universe:
    ids += [r["id"] for r in universe.load(u) if r["id"] not in ids]
out = "data/ebc/spreads.jsonl"
for snap in range(a.max_snapshots):
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    with open(out, "a") as f:
        for i in range(0, len(ids), 50):
            try:
                r = api.rates(ids[i:i + 50])
            except Exception as e:
                print("Fehler", repr(e)[:100], file=sys.stderr); continue
            for x in r.get("rates", []):
                bid, ask = x.get("bid"), x.get("ask")
                if bid and ask and ask >= bid > 0:
                    f.write(json.dumps({"ts": ts, "id": str(x["instrumentID"]), "bid": bid, "ask": ask, "qts": x.get("date")}) + "\n")
    print(ts, "Snapshot", snap + 1, flush=True)
    if a.loop and snap + 1 < a.max_snapshots:
        time.sleep(a.loop * 60)
