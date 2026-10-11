"""EBC-H1 §4: Universum U_breit ziehen (Seed 20261011), Tageskerzen laden (1000/Instrument), Qualitaetsfilter.
Direkter, rein lesender API-Abruf (Abweichung: nicht ueber historical_fetcher/Nautilus-Katalog, siehe protocol_deviations)."""
from concurrent.futures import ThreadPoolExecutor
import csv, datetime as dt, json, os, random, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import api

SEED = 20261011
OUT = "data/ebc/daily"
os.makedirs(OUT, exist_ok=True)
meta = {str(i["InstrumentID"]): i for i in json.load(open("data/ebc/metadata.json"))["InstrumentDisplayDatas"]}
smap = json.load(open("automation/config/instrument_map.json"))["instruments"]
STABLE = {"USDC", "USDT", "DAI", "TUSD", "BUSD", "USDP", "PYUSD", "FDUSD"}
PREC = re.compile(r"^(GOLD|SILVER)[A-Z]{3}$")
TODAY = dt.datetime.now(dt.timezone.utc).date()

def base_ok(m):
    if m.get("IsInternalInstrument") or m.get("HasExpirationDate") or m.get("IsDelisted"):
        return False
    if m.get("InstrumentTypeID") not in (5, 6) and str(m.get("PriceSource") or "eToro").lower() != "etoro":
        return False   # bei Aktien/ETFs traegt PriceSource den Boersennamen
    return True

def sym(m): return str(m.get("SymbolFull") or "").upper()

def spot_ok(m, cls):
    s = sym(m)
    if not s or "_" in s or " " in s: return False
    if cls in ("fx", "crypto", "commodity") and "." in s: return False
    if cls == "crypto" and ("/" in str(m.get("InstrumentDisplayName") or "") or s in STABLE): return False
    if cls == "commodity" and PREC.match(s): return False
    return True

def cand(types, exch=None, cls=None):
    out = []
    for iid, m in meta.items():
        if m.get("InstrumentTypeID") in types and base_ok(m) and spot_ok(m, cls):
            if exch is None or exch(m.get("ExchangeID")):
                out.append(iid)
    return sorted(out, key=int)

US = lambda e: e in (4, 5)
strata = {  # name: (kandidaten, n, mode)
    "us_stock": (cand({5}, US, "stock"), 150, "shuffle"),
    "non_us_stock": (cand({5}, lambda e: e not in (4, 5, 19), "stock"), 50, "shuffle"),
    "etf": (cand({6}, None, "etf"), 40, "shuffle"),
    "index": (cand({4}, None, "index"), 10**6, "shuffle"),
    "fx": (cand({1}, None, "fx"), 50, "shuffle"),
    "commodity": (cand({2}, None, "commodity"), 20, "shuffle"),
    "crypto": (cand({10}, None, "crypto"), 30, "id"),   # Top 30 nach Verfuegbarkeit: aelteste Listings (kleinste ID)
}

def fetch(iid):
    path = f"{OUT}/{iid}.csv"
    if os.path.exists(path):
        return path
    r = api.candles(iid, "OneDay", 1000)
    rows = []
    for blk in r.get("candles", []):
        for c in blk.get("candles", []):
            d = c["fromDate"][:10]
            if d >= TODAY.isoformat():   # unfertige Kerze verwerfen
                continue
            rows.append((d, c["open"], c["high"], c["low"], c["close"], c.get("volume", 0)))
    rows.sort()
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "open", "high", "low", "close", "volume"]); w.writerows(rows)
    return path

def quality(iid, cls):
    import numpy as np
    rows = list(csv.DictReader(open(f"{OUT}/{iid}.csv")))
    n = len(rows)
    if n == 0: return dict(n=0, ok=False, why="leer")
    d = [dt.date.fromisoformat(r["date"]) for r in rows]
    hi = np.array([float(r["high"]) for r in rows]); lo = np.array([float(r["low"]) for r in rows])
    cl = np.array([float(r["close"]) for r in rows])
    zero = float(np.mean(hi == lo))
    if cls == "crypto":
        gaps = [(b - a).days for a, b in zip(d, d[1:])]
    else:
        gaps = [int(np.busday_count(a, b)) for a, b in zip(d, d[1:])]
    maxgap = max(gaps) if gaps else 0
    why = []
    if n < 750: why.append("<750 Kerzen")
    if zero > 0.02: why.append("Nullspannen>2%")
    if maxgap > 10: why.append("Luecke>10")
    if (cl <= 0).any(): why.append("Preis<=0")
    return dict(n=n, oldest=rows[0]["date"], newest=rows[-1]["date"], maxgap=maxgap, zero_range_share=round(zero, 4),
                ok=not why, why=";".join(why))

def klass(cls):
    return {"us_stock": "K1", "non_us_stock": "K1", "etf": "K1", "fx": "K2", "commodity": "K3", "index": "K4", "crypto": "K5"}[cls]

rng = random.Random(SEED)
chosen = {}   # iid -> (stratum, quality)
log = open("data/ebc/universe_build.log", "w")
def P(*a):
    print(*a, file=log, flush=True)

for name, (cands, n, mode) in strata.items():
    order = list(cands)
    if mode == "shuffle": rng.shuffle(order)
    got = 0; tried = 0
    P(f"== {name}: {len(order)} Kandidaten, Ziel {n}")
    pool = ThreadPoolExecutor(4)
    futs = {}
    for idx, iid in enumerate(order):
        if got >= n: break
        if idx % 12 == 0:
            for j in order[idx:idx + 12]: futs[j] = pool.submit(fetch, j)
        tried += 1
        try:
            futs[iid].result()
        except Exception as e:
            P("FEHLER", iid, repr(e)[:100]); continue
        q = quality(iid, "crypto" if name == "crypto" else "x")
        q["stratum"] = name
        if q["ok"]:
            chosen[iid] = q; got += 1
        elif tried % 20 == 0:
            P(f"  {name} tried={tried} got={got}")
    P(f"   {name}: gezogen {got} von {tried} versuchten")

# Status quo: alle Instrumente der instrument_map
for iid, e in smap.items():
    if iid in chosen: continue
    try: fetch(iid)
    except Exception as ex:
        P("FEHLER sq", iid, repr(ex)[:100]); continue
    cls = e["asset_class"]
    q = quality(iid, "crypto" if cls == "crypto" else "x")
    q["stratum"] = "status_quo"
    if q["ok"]: chosen[iid] = q

with open("results/H1/universe.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["etoro_id", "symbol", "name", "stratum", "klasse", "in_status_quo", "n_candles", "oldest", "newest",
                "max_gap", "zero_range_share"])
    for iid, q in sorted(chosen.items(), key=lambda kv: int(kv[0])):
        m = meta.get(iid, {})
        t = m.get("InstrumentTypeID")
        k = {5: "K1", 6: "K1", 1: "K2", 2: "K3", 4: "K4", 10: "K5"}.get(t, "?")
        w.writerow([iid, sym(m), m.get("InstrumentDisplayName"), q["stratum"], k, iid in smap, q["n"], q["oldest"], q["newest"],
                    q["maxgap"], q["zero_range_share"]])
P("FERTIG", len(chosen))
