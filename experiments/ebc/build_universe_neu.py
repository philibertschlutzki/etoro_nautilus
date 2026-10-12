"""EBC-H4 §4.2: U_neu ziehen (Seed 20261012), nur Aktien/ETF, disjunkt zu U_breit; Tageskerzen lesend laden (1000/Instrument, 1 Req/s)
nach data/ebc/daily_neu/, Qualitaetsfilter wie H1 §4.1. Ausgabe results/H4/universe_neu.csv (Spalten wie results/H1/universe.csv)."""
import csv, datetime as dt, json, os, random, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import api

SEED = 20261012
OUT = "data/ebc/daily_neu"; os.makedirs(OUT, exist_ok=True)
STRATA = [("us_stock", 300), ("nonus_stock", 100), ("etf", 60)]
meta = {str(i["InstrumentID"]): i for i in json.load(open("data/ebc/metadata.json"))["InstrumentDisplayDatas"]}
breit = {r["etoro_id"] for r in csv.DictReader(open("results/H1/universe.csv"))}
CUT = dt.date(2026, 10, 10)
TODAY = dt.datetime.now(dt.timezone.utc).date()

def base_ok(m):
    if m.get("IsInternalInstrument") or m.get("HasExpirationDate") or m.get("IsDelisted"): return False
    return True

def sym_ok(m):
    s = str(m.get("SymbolFull") or "").upper()
    return bool(s) and "_" not in s and " " not in s

def stratum(m):
    t, ex = m["InstrumentTypeID"], m["ExchangeID"]
    if t == 6: return "etf"
    if t == 5: return "us_stock" if ex in (4, 5) else ("nonus_stock" if ex != 19 else None)
    return None

def fetch(iid):
    path = f"{OUT}/{iid}.csv"
    if os.path.exists(path): return path
    r = api.candles(iid, "OneDay", 1000)
    rows = []
    for blk in r.get("candles", []):
        for c in blk.get("candles", []):
            d = c["fromDate"][:10]
            if d >= TODAY.isoformat(): continue
            rows.append((d, c["open"], c["high"], c["low"], c["close"], c.get("volume", 0)))
    rows.sort()
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "open", "high", "low", "close", "volume"]); w.writerows(rows)
    return path

def quality(iid):
    rows = [r for r in csv.DictReader(open(f"{OUT}/{iid}.csv")) if dt.date.fromisoformat(r["date"]) <= CUT]
    if len(rows) < 750: return None
    zr = sum(1 for r in rows if float(r["high"]) == float(r["low"])) / len(rows)
    if zr > 0.02: return None
    d = [dt.date.fromisoformat(r["date"]) for r in rows]
    gap = max((sum(1 for k in range(1, (b - a).days) if (a + dt.timedelta(k)).weekday() < 5) for a, b in zip(d, d[1:])), default=0)
    if gap > 10 or (CUT - d[-1]).days > 5: return None
    return dict(n_candles=len(rows), oldest=d[0], newest=d[-1], max_gap=gap, zero_range_share=round(zr, 4))

out = []; drawn = {}
for name, n in STRATA:
    cand = sorted(i for i, m in meta.items() if i not in breit and base_ok(m) and sym_ok(m) and stratum(m) == name and m["InstrumentTypeID"] in (5, 6))
    random.Random(SEED).shuffle(cand)
    taken = tried = 0
    for i in cand:
        tried += 1
        try: fetch(i)
        except Exception as e:
            print("FEHLER", i, type(e).__name__, flush=True); continue
        q = quality(i)
        if q is None: continue
        m = meta[i]
        out.append(dict(etoro_id=i, symbol=m["SymbolFull"], name=m["InstrumentDisplayName"], stratum=name, klasse="K1",
                        in_status_quo="False", **q))
        taken += 1
        if taken >= n: break
    drawn[name] = (len(cand), tried, taken)
    print(name, "Kandidaten", len(cand), "geladen", tried, "aufgenommen", taken, flush=True)
with open("results/H4/universe_neu.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
json.dump(drawn, open("results/H4/universe_neu_draw.json", "w"))
print("fertig", len(out))
