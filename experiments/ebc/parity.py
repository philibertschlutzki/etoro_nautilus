"""Route-V-Gegenprobe (EBC-H1 §7.1): 30 zufaellige Instrumente (Seed 20261011), Archetypen T/M/V, Route R vs Route V.
Toleranzen: Trade-Anzahl +-2 %, Brutto-Rendite je Trade +-5 bps (Netto = Brutto - identische Kosten)."""
import json, multiprocessing as mp, os, random, sys
os.environ["ETORO_CONFIG_DIR"] = os.path.abspath("cfg_B")
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
MODS = {"T": ("automation.strategies.ebc_trend", "EbcTrendConfig", "EbcTrendStrategy"),
        "M": ("automation.strategies.ebc_pullback", "EbcPullbackConfig", "EbcPullbackStrategy"),
        "V": ("automation.strategies.ebc_squeeze", "EbcSqueezeConfig", "EbcSqueezeStrategy")}

def work(r):
    import route_r, sim, universe
    series = route_r.load_daily(r["id"]); S = sim.Series(*series)
    out = []
    for arch, (m, cc, cl) in MODS.items():
        shorts = r["klasse"] in universe.SHORT_OK
        R = route_r.run(cl, universe.sym(r), series, module=m, config_class=cc, cfg_overrides=dict(allow_short=shorts))
        V = sim.sequential(S, arch, allow_short=shorts)
        day = 86400 * 10**9
        ri = {int(np.searchsorted(series[0], (t["entry_ts"] // day) * day)): t for t in R}
        vi = {t["entry_i"]: t for t in V}
        common = set(ri) & set(vi)
        d = []
        for e in common:
            a = ri[e]; b = vi[e]
            ra = (a["px_close"] / a["px_open"] - 1) * 1e4 * (1 if a["side"] == "BUY" else -1)
            d.append(abs(ra - b["gross_bps"]))
        out.append(dict(id=r["id"], klasse=r["klasse"], arch=arch, n_r=len(R), n_v=len(V), n_common=len(common),
                        max_abs_diff_bps=float(max(d)) if d else 0.0, n_over_5bps=int(sum(1 for x in d if x > 5.0))))
    return out

if __name__ == "__main__":
    import universe
    rows = universe.load(); rnd = random.Random(20261011)
    sample = rnd.sample(rows, 30)
    with mp.Pool(4) as p:
        res = [x for sub in p.map(work, sample) for x in sub]
    n_r = sum(x["n_r"] for x in res); n_v = sum(x["n_v"] for x in res); nc = sum(x["n_common"] for x in res)
    n5 = sum(x["n_over_5bps"] for x in res)
    summ = dict(n_instruments=30, trades_R=n_r, trades_V=n_v, trades_common=nc, rel_diff_trade_count=abs(n_r - n_v) / max(n_r, 1),
                trades_over_5bps=n5, share_over_5bps=n5 / max(nc, 1), tolerance_trade_count=0.02, tolerance_bps=5.0,
                passed=bool(abs(n_r - n_v) / max(n_r, 1) <= 0.02 and n5 / max(nc, 1) <= 0.02), detail=res)
    json.dump(summ, open("results/H1/stage_a/parity_route_r_v.json", "w"), indent=1)
    print({k: v for k, v in summ.items() if k != "detail"})
