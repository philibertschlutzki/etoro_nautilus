"""EBC-H2 §7.4 (P2.3): Stundenachse, S_MOM (60 Bars) / S_REV (5 Bars), h in {1,3,7,14,35} Bars, Instrumente mit Stundenhistorie (Stichprobe)."""
import csv, datetime as dt, glob, json, os, sys
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np
import universe, costs, h2_lib

rows = universe.load(); by_id = {r["id"]: r for r in rows}
ct = costs.CostTable(rows)
class S_: pass
res = {}
ids = [os.path.basename(f)[:-4] for f in sorted(glob.glob("data/ebc/hourly/*.csv"))]
for i in ids:
    if i not in by_id: continue
    r = by_id[i]
    data = list(csv.DictReader(open(f"data/ebc/hourly/{i}.csv")))
    if len(data) < 300: continue
    S = S_()
    S.ts = np.array([int(dt.datetime.fromisoformat(d["ts"].replace("Z", "+00:00")).timestamp()) * 10**9 for d in data], dtype=np.int64)
    S.o = np.array([float(d["open"]) for d in data]); S.c = np.array([float(d["close"]) for d in data]); S.n = len(S.ts)
    if r["klasse"] == "K2":   # FX ohne Wochenendbars (Freitag 21 UTC bis Sonntag 22 UTC), wie forex_weekend_closed
        wd = np.array([(dt.datetime.fromtimestamp(t / 1e9, dt.timezone.utc).weekday(), dt.datetime.fromtimestamp(t / 1e9, dt.timezone.utc).hour) for t in S.ts])
        keep = ~((wd[:, 0] == 5) | ((wd[:, 0] == 4) & (wd[:, 1] >= 21)) | ((wd[:, 0] == 6) & (wd[:, 1] < 22)))
        S.ts, S.o, S.c = S.ts[keep], S.o[keep], S.c[keep]; S.n = len(S.ts)
    n_days = (S.ts[-1] - S.ts[0]) / 86_400e9
    lo = r["klasse"] not in ("K2", "K3", "K4")
    cp = ct.p[i]
    for kind in ("MOM", "REV"):
        for h in (1, 3, 7, 14, 35):
            R = h2_lib.sig_windows(S, kind, h, lo, ct.c_rt(i), cp["fin_long"], cp["fin_short"])
            m = R["p"] != 0
            res.setdefault((r["klasse"], kind, h), dict(net=[], gross=[], icx=[], icy=[], inst=set(), days=[]))
            d = res[(r["klasse"], kind, h)]
            d["net"].append(R["net"][m]); d["gross"].append(R["gross"][m]); d["icx"].append(R["ic_x"]); d["icy"].append(R["ic_y"]); d["inst"].add(i); d["days"].append(n_days)
out = []
for (k, kind, h), d in sorted(res.items()):
    net = np.concatenate(d["net"]); gr = np.concatenate(d["gross"]); icx = np.concatenate(d["icx"]); icy = np.concatenate(d["icy"])
    ic = float(np.corrcoef(icx, icy)[0, 1]) if len(icx) > 10 and icx.std() > 0 else None
    out.append(dict(klasse=k, signal=f"S_{kind}", h_bars=h, n_instruments=len(d["inst"]), n_trades=int(len(net)), exp_net_bps=float(net.mean()) if len(net) else None,
                    exp_gross_bps=float(gr.mean()) if len(gr) else None, ic=ic, median_days=float(np.median(d["days"]))))
os.makedirs("results/H2/stage_a", exist_ok=True)
with open("results/H2/stage_a/hourly_axis.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
def cell(k, kind, h): return next((r for r in out if r["klasse"] == k and r["signal"] == f"S_{kind}" and r["h_bars"] == h), None)
p23 = {}
for k, name in (("K2", "FX"), ("K3", "Rohstoffe"), ("K5", "Krypto"), ("K1", "Aktien")):
    cs = [cell(k, kind, 7) for kind in ("MOM", "REV")]
    p23[name] = {c["signal"]: dict(exp_net_bps=c["exp_net_bps"], ic=c["ic"], n=c["n_trades"]) for c in cs if c}
neg = all(all(v["exp_net_bps"] is not None and v["exp_net_bps"] < 0 for v in p23[n].values()) for n in ("FX", "Rohstoffe", "Krypto") if p23[n])
eq_ok = all((v["exp_net_bps"] is not None and v["exp_net_bps"] <= 0) or (v["ic"] is not None and v["ic"] >= 0.10) for v in p23["Aktien"].values())
P23 = dict(cells=p23, passed=bool(neg and eq_ok), note="Stichprobe: %d Instrumente mit Stundenhistorie (API-Fenster ~1000 Bars)" % len(ids))
json.dump(P23, open("results/H2/stage_a/p23.json", "w"), indent=1, default=float)
print(json.dumps(P23, indent=1, default=float))
