"""EBC-H3 §7.4 (P3.4): VolatilityBreakoutPump / FlashCrashReversal auf realen Stundenkerzen, original (Kopie von 5f737ed) vs gepatcht (Anhang B)."""
import csv, datetime as dt, glob, json, os, sys
os.environ["ETORO_CONFIG_DIR"] = os.path.abspath("cfg_H")
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import route_r, universe, costs, portfolio as pf

rows = universe.load(); by_id = {r["id"]: r for r in rows}
ct = costs.CostTable(rows)
sd = json.load(open("cfg_H/strategy_defaults.json"))
CASES = {
 "VBP": dict(cls="VolatilityBreakoutPumpStrategy", cfgc="VolatilityBreakoutConfig",
             orig="experiments.ebc_h3.orig.volatility_breakout_orig", new="automation.strategies.volatility_breakout", over={}),
 "FCR": dict(cls="FlashCrashReversalStrategy", cfgc="FlashCrashReversalConfig",
             orig="experiments.ebc_h3.orig.flash_crash_reversal_orig", new="automation.strategies.flash_crash_reversal", over={}),
 "FCR_blockiert": dict(cls="FlashCrashReversalStrategy", cfgc="FlashCrashReversalConfig",
             orig="experiments.ebc_h3.orig.flash_crash_reversal_orig", new="automation.strategies.flash_crash_reversal", over={"max_daily_trades": 1}),
}
def load_h(i):
    data = list(csv.DictReader(open(f"data/ebc/hourly/{i}.csv")))
    ts = np.array([int(dt.datetime.fromisoformat(d["ts"].replace("Z", "+00:00")).timestamp()) * 10**9 for d in data], dtype=np.int64)
    o, h, l, c = (np.array([float(d[k]) for d in data]) for k in ("open", "high", "low", "close"))
    return ts, o, np.maximum.reduce([h, o, c]), np.minimum.reduce([l, o, c]), c

def stats(trs):
    n = len(trs); sh = [t for t in trs if t["side"] < 0]
    net = np.array([t["net"] for t in trs]) if trs else np.zeros(1)
    nets = np.array([t["net"] for t in sh]) if sh else np.zeros(1)
    return dict(n_positions=n, n_short=len(sh), short_share=len(sh) / n if n else 0.0, exp_bps=float(net.mean()), std_bps=float(net.std()),
                total_bps=float(net.sum()), short_exp_bps=float(nets.mean()) if sh else None, short_total_bps=float(nets.sum()) if sh else 0.0,
                exits={k: int(sum(1 for t in trs if t["reason"] == k)) for k in {t["reason"] for t in trs}})
out = {}
ids = [os.path.basename(f)[:-4] for f in sorted(glob.glob("data/ebc/hourly/*.csv"))]
ids = [i for i in ids if i in by_id]
for name, c in CASES.items():
    agg = {"original": [], "gepatcht": []}
    for i in ids:
        r = by_id[i]; series = load_h(i)
        if len(series[0]) < 300: continue
        for variant, mod in (("original", c["orig"]), ("gepatcht", c["new"])):
            try:
                _, tr = route_r.run(c["cls"], universe.sym(r), series, module=mod, config_class=c["cfgc"], defaults=dict(sd[c["cls"]]),
                                 cfg_overrides=dict(c["over"]), axis="HOUR", want_positions=True)
            except Exception as e:
                print("Fehler", name, i, variant, repr(e)[:120]); continue
            for t in tr:
                s_ = 1 if t["side"] == "BUY" else -1
                if t["px_close"] is None or t["exit_ts"] is None: continue
                gross = s_ * (t["px_close"] / t["px_open"] - 1) * 1e4
                hold = max(0.0, (t["exit_ts"] - t["entry_ts"]) / 86_400e9)
                agg[variant].append(dict(side=s_, net=ct.net_bps(i, gross, s_, hold), reason="n/a", id=i, klasse=r["klasse"]))
    out[name] = {v: stats(agg[v]) for v in agg}
    out[name]["n_instruments"] = len(ids)
    print(name, json.dumps({v: {k: out[name][v][k] for k in ("n_positions", "n_short", "short_share", "exp_bps", "std_bps", "short_exp_bps")} for v in agg}, default=float), flush=True)
os.makedirs("results/H3/stage_a", exist_ok=True)
json.dump(out, open("results/H3/stage_a/leaks_real_data.json", "w"), indent=1, default=float)
with open("results/H3/stage_a/leaks_real_data.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["fall", "variante", "n_instrumente", "n_positionen", "n_short", "short_anteil", "exp_netto_bps", "std_netto_bps", "short_exp_bps", "short_summe_bps"])
    for n_, d in out.items():
        for v in ("original", "gepatcht"):
            s = d[v]; w.writerow([n_, v, d["n_instruments"], s["n_positions"], s["n_short"], s["short_share"], s["exp_bps"], s["std_bps"], s["short_exp_bps"], s["short_total_bps"]])
