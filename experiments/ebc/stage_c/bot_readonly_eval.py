"""Rein lesende Auswertung der realen Positionen des laufenden Paper-Bots (nur GET /pnl und /rates): Gebuehren-Anteil und implizite
Finanzierung je Klasse und Seite. Kein POST, keine Aenderung am Bot. Ausgabe results/stage_c/bot_fees_readonly.{csv,json}"""
import csv, datetime as dt, json, os, sys, collections
sys.path.insert(0, os.path.dirname(__file__))
import guard
import numpy as np
guard.load_env(); guard.verify_account()
imap = json.load(open(os.path.expanduser("~/etoro_nautilus/automation/config/instrument_map.json")))["instruments"]
meta = json.load(open("data/ebc/metadata.json"))["InstrumentDisplayDatas"] if os.path.exists("data/ebc/metadata.json") else []
tid = {str(m["InstrumentID"]): m["InstrumentTypeID"] for m in meta}
CLS = {1: "FOREX", 2: "COMMODITY", 4: "INDEX", 5: "EQUITY", 6: "EQUITY", 10: "CRYPTO"}
rows = []
now = dt.datetime.now(dt.timezone.utc)
for p in guard.positions():
    iid = str(p["instrumentID"])
    cls = CLS.get(tid.get(iid)) or (imap.get(iid, {}).get("asset_class") or "?").upper()
    opened = dt.datetime.fromisoformat(p["openDateTime"].replace("Z", "+00:00"))
    age = (now - opened).total_seconds() / 86400
    amt = float(p.get("initialAmountInDollars") or p.get("amount") or 0)
    fees = float(p.get("totalFees") or 0) + float(p.get("totalExternalFees") or 0)
    rows.append(dict(position_id=p["positionID"], instrument=iid, klasse=cls, side="long" if p["isBuy"] else "short", amount=amt, age_days=round(age, 3),
                     total_fees=fees, fees_bps=fees / amt * 1e4 if amt else None, tsl=p.get("isTslEnabled"), has_sl=not p.get("isNoStopLoss", True)))
os.makedirs("results/stage_c", exist_ok=True)
if rows:
    with open("results/stage_c/bot_fees_readonly.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
agg = collections.defaultdict(list)
for r in rows:
    if r["fees_bps"] is not None: agg[(r["klasse"], r["side"])].append(r["fees_bps"])
out = {"ts": now.isoformat(), "n_positions": len(rows), "fees_bps_open_position": {f"{k[0]}_{k[1]}": dict(n=len(v), mean=float(np.mean(v))) for k, v in agg.items()},
       "note": "Gebuehren seit Eroeffnung (Eroeffnungsgebuehr + Finanzierung bisher); Slippage ist ohne Quote zum Sendezeitpunkt nicht ableitbar."}
json.dump(out, open("results/stage_c/bot_fees_readonly.json", "w"), indent=1)
print(json.dumps(out, indent=1))
