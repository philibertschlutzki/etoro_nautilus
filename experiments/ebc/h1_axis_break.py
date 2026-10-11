"""EBC-H1 §7.7 (P1.4): zuletzt gewaehlte A0-Paare mit eingefrorenen Default-Parametern auf der Stundenachse (API-Fenster, 1000 Kerzen)."""
import csv, datetime as dt, json, os, pickle, sys
os.environ["ETORO_CONFIG_DIR"] = os.path.abspath("cfg_H")
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import api, route_r, universe, costs, portfolio as pf

rows = universe.load(); by_id = {r["id"]: r for r in rows}
ct = costs.CostTable(rows)
st = pickle.load(open("data/ebc/h1_state.pkl", "rb"))
active = st["A0"]["active_end"]
sd = json.load(open("cfg_H/strategy_defaults.json"))
out = []
for (i, strat), v in sorted(active.items()):
    path = f"data/ebc/hourly/{i}.csv"
    if not os.path.exists(path):
        r = api.candles(i, "OneHour", 1000)
        rowsx = []
        for blk in r.get("candles", []):
            for c in blk.get("candles", []):
                rowsx.append((c["fromDate"], c["open"], c["high"], c["low"], c["close"]))
        rowsx.sort()
        with open(path, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["ts", "open", "high", "low", "close"]); w.writerows(rowsx)
    data = list(csv.DictReader(open(path)))
    if len(data) < 200:
        continue
    ts = np.array([int(dt.datetime.fromisoformat(d["ts"].replace("Z", "+00:00")).timestamp()) * 10**9 for d in data], dtype=np.int64)
    o, h, l, c = (np.array([float(d[k]) for d in data]) for k in ("open", "high", "low", "close"))
    h = np.maximum.reduce([h, o, c]); l = np.minimum.reduce([l, o, c])
    r = by_id[i]
    try:
        tr = route_r.run(strat, universe.sym(r), (ts, o, h, l, c), defaults=dict(sd[strat]), axis="HOUR")
    except Exception as e:
        out.append(dict(id=i, symbol=r["symbol"], strategy=strat, klasse=r["klasse"], daily_score=v["psr"], error=repr(e)[:100])); continue
    nets = []
    for t in tr:
        sd_ = 1 if t["side"] == "BUY" else -1
        if sd_ < 0 and r["klasse"] in ("K1", "K5"):
            continue
        gross = sd_ * (t["px_close"] / t["px_open"] - 1) * 1e4
        hold = max(0, (t["exit_ts"] - t["entry_ts"]) / 86_400e9)
        nets.append(ct.net_bps(i, gross, sd_, hold))
    out.append(dict(id=i, symbol=r["symbol"], strategy=strat, klasse=r["klasse"], daily_score=v["psr"], n_hours=len(ts),
                    n_trades=len(nets), net_total_bps=float(sum(nets)), net_mean_bps=float(np.mean(nets)) if nets else 0.0))
os.makedirs("results/H1/stage_a", exist_ok=True)
good = [x for x in out if "error" not in x and x["n_trades"] > 0]
with open("results/H1/stage_a/axis_break.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=sorted({k for x in out for k in x})); w.writeheader(); w.writerows(out)
rho = pf.spearman([x["daily_score"] for x in good], [x["net_total_bps"] for x in good])
summ = dict(n_pairs=len(out), n_evaluable=len(good), spearman_daily_score_vs_hourly_net=rho,
            median_hourly_net_total_bps=float(np.median([x["net_total_bps"] for x in good])) if good else None,
            share_negative=float(np.mean([x["net_total_bps"] < 0 for x in good])) if good else None,
            P1_4_passed=bool(good and -0.10 <= rho <= 0.10),
            note="Stundenfenster = API-Limit (~1000 Kerzen, ~60 Handelstage Aktien / ~42 Tage Krypto); Netto-Summe ueber alle Trades des Fensters, Default-Parameter + PAPER_TRAILING_TP, Zeitbox 7 Bars")
json.dump(summ, open("results/H1/stage_a/axis_break.json", "w"), indent=1)
print(json.dumps(summ, indent=1))
