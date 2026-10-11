"""Erzeugt cfg_A (Status quo, max_handelstage 5, PAPER_TRAILING_TP) und cfg_B (Archetypen, max_handelstage 60) aus automation/config
plus Universum: klon-lokale instrument_map (E<id>.ETORO), Klasse INDEX in allen *_by_asset_class-Tabellen, Spread je Symbol."""
import json, os, shutil, sys
sys.path.insert(0, os.path.dirname(__file__))
import universe, costs

rows = universe.load()
ct = costs.CostTable(rows)
imap = {"_schema": json.load(open("automation/config/instrument_map.json"))["_schema"], "instruments": {}}
for r in rows:
    imap["instruments"][r["id"]] = {"symbol": universe.sym(r), "asset_class": r["cost_class"].lower(),
                                    "price_precision": 5, "size_precision": 8}
for name, mh, ttp in (("cfg_A", 5, True), ("cfg_B", 60, False)):
    os.makedirs(name, exist_ok=True)
    for f in os.listdir("automation/config"):
        if f.endswith(".json"):
            shutil.copy(f"automation/config/{f}", f"{name}/{f}")
    json.dump(imap, open(f"{name}/instrument_map.json", "w"))
    b = json.load(open(f"{name}/backtest.json"))
    b["bar_axis"] = "OneDay"; b["max_handelstage"] = mh
    b["spread_bps_by_asset_class"]["INDEX"] = costs.FALLBACK_SPREAD["INDEX"]
    b["slippage_bps_by_asset_class"]["INDEX"] = costs.FALLBACK_SLIP["INDEX"]
    b["atr_floor_bps_by_asset_class"]["INDEX"] = 2.0
    b["session_hours_by_asset_class"]["INDEX"] = None
    b["overnight_financing_bps_per_day_by_asset_class"]["INDEX"] = {"long": 0.79, "short": 0.79}
    for c in ("FOREX", "COMMODITY"):
        b["overnight_financing_bps_per_day_by_asset_class"][c]["long"] = 0.79
    b["spread_bps_by_symbol"] = {universe.sym(r): round(ct.p[r["id"]]["spread"], 3) for r in rows}
    json.dump(b, open(f"{name}/backtest.json", "w"), indent=1)
    sd = json.load(open(f"{name}/strategy_defaults.json"))
    if ttp:
        for k, v in sd.items():
            if not k.startswith("_"):
                v.update({"trailing_tp_activation_atr": 0.5, "trailing_tp_trail_atr": 0.3, "trailing_tp_min_lock_spread_mult": 2.0})
    json.dump(sd, open(f"{name}/strategy_defaults.json", "w"), indent=1)
print("cfg ok", len(rows))
