"""Gueltige Spread-Stichproben (EBC-H4 §5): nur Mo-Fr, Quote-Zeitstempel qts hoechstens 5 Minuten vor dem Abruf ts. Instrumente mit >= 20 gueltigen
Stichproben an >= 2 Handelstagen erhalten (p50, p75, n). Ausgabe data/ebc/spreads_valid.json und results/H4/spread_measured.json (Klassen-Zusammenfassung)."""
import collections, datetime as dt, json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np
import costs, universe

by = collections.defaultdict(list); days = collections.defaultdict(set); total = valid = 0
for line in open("data/ebc/spreads.jsonl"):
    x = json.loads(line); total += 1
    ts = dt.datetime.fromisoformat(x["ts"]); 
    if ts.weekday() >= 5 or not x.get("qts"): continue
    q = dt.datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", x["qts"].replace("Z", "+00:00")))
    if not (0 <= (ts - q).total_seconds() <= 300): continue
    mid = 0.5 * (x["bid"] + x["ask"])
    if mid <= 0: continue
    by[x["id"]].append((x["ask"] - x["bid"]) / mid * 1e4); days[x["id"]].add(ts.date()); valid += 1
ok = {i: (float(np.percentile(v, 50)), float(np.percentile(v, 75)), len(v)) for i, v in by.items() if len(v) >= 20 and len(days[i]) >= 2}
rows = {r["id"]: r for r in universe.load()}
if os.path.exists("results/H4/universe_neu.csv"): rows.update({r["id"]: r for r in universe.load("results/H4/universe_neu.csv")})
cls = collections.defaultdict(list)
for i, (p50, p75, n) in ok.items():
    if i in rows: cls[rows[i]["klasse"]].append(p50)
summary = {k: dict(n_instruments=len(v), median_p50_bps=float(np.median(v)), p75_of_p50_bps=float(np.percentile(v, 75)),
                   model_fallback_bps=costs.FALLBACK_SPREAD[universe.COST_CLASS[k]]) for k, v in cls.items()}
json.dump(ok, open("data/ebc/spreads_valid.json", "w"))
json.dump(dict(generated=dt.datetime.now(dt.timezone.utc).isoformat(), samples_total=total, samples_valid=valid, n_instruments_measured=len(ok), by_class=summary),
          open("results/H4/spread_measured.json", "w"), indent=1)
print(f"Stichproben {total}, gueltig {valid}, Instrumente mit Messung {len(ok)}"); print(json.dumps(summary, indent=1))
