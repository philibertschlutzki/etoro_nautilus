"""Auswertung Stufe C: Slippage, Gebuehren, Finanzierung je Klasse/Seite gegen das Modell; schreibt cost_recalibration.json (rein lesend auf CSVs)."""
import csv, json, os, sys, collections, datetime as dt
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import costs
OUT = "results/stage_c"
fills = list(csv.DictReader(open(f"{OUT}/fills.csv"))) if os.path.exists(f"{OUT}/fills.csv") else []
mon = list(csv.DictReader(open(f"{OUT}/position_fees_daily.csv"))) if os.path.exists(f"{OUT}/position_fees_daily.csv") else []
CLS = {"K1": "EQUITY", "K2": "FOREX", "K3": "COMMODITY", "K4": "INDEX", "K5": "CRYPTO"}
sl = collections.defaultdict(list); fee = collections.defaultdict(list)
for f in fills:
    if not f["fill_px"] or f["rejection"]: continue
    bid, ask, px, amt = float(f["bid"]), float(f["ask"]), float(f["fill_px"]), float(f["amount_usd"])
    c = CLS[f["klasse"]]
    s = (px - ask) / ask * 1e4 if f["side"] == "BUY" else (bid - px) / bid * 1e4          # positiv = ungünstig
    sl[c].append(s)
    if f["fees_usd"] != "": fee[c].append(float(f["fees_usd"]) / amt * 1e4)
fin = collections.defaultdict(list)
byp = collections.defaultdict(list)
for m in mon:
    if m.get("total_fees") not in (None, ""): byp[m["position_id"]].append(m)
for pid, rows in byp.items():
    rows.sort(key=lambda r: r["ts"])
    a, b = rows[0], rows[-1]
    t0, t1 = dt.datetime.fromisoformat(a["ts"]), dt.datetime.fromisoformat(b["ts"])
    days = (t1 - t0).total_seconds() / 86400
    if days < 0.9: continue
    d_fee = (float(b["total_fees"] or 0) + float(b["total_external_fees"] or 0)) - (float(a["total_fees"] or 0) + float(a["total_external_fees"] or 0))
    fin[(CLS[a["klasse"]], "long" if a["side"] == "BUY" else "short")].append(d_fee / float(a["amount"]) * 1e4 / days)
res = {"slippage_bps_side": {}, "fees_bps_open": {}, "financing_bps_per_day": {}, "flags": []}
for c, v in sl.items():
    mean = float(np.mean(v)); model = costs.FALLBACK_SLIP[c]
    res["slippage_bps_side"][c] = dict(n=len(v), mean=mean, p50=float(np.median(v)), model=model)
    if abs(mean - model) > 2 or (model and abs(mean - model) / model > 0.2): res["flags"].append(f"Slippage {c}: {mean:.2f} vs Modell {model}")
for c, v in fee.items():
    mean = float(np.mean(v)); model = costs.COMMISSION_RT[c] / 2
    res["fees_bps_open"][c] = dict(n=len(v), mean=mean, model_per_side=model)
    if abs(mean - model) > 2 or (model and abs(mean - model) / model > 0.2): res["flags"].append(f"Gebuehr {c}: {mean:.2f} vs Modell {model}")
for (c, side), v in fin.items():
    mean = float(np.mean(v)); model = costs.FIN_SHORT if side == "short" else costs.FIN_LONG[c]
    res["financing_bps_per_day"][f"{c}_{side}"] = dict(n=len(v), mean=mean, model=model)
    if abs(mean - model) > 0.5 * max(model, 0.1): res["flags"].append(f"Finanzierung {c} {side}: {mean:.2f} vs {model}")
res["rerun_required"] = bool(res["flags"])
json.dump(res, open(f"{OUT}/cost_recalibration.json", "w"), indent=1)
print(json.dumps(res, indent=1))
