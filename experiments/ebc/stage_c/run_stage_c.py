"""EBC Stufe C (Paper-Konto, /real/-Pfad per account_guard). STANDARD = DRY-RUN: es werden nur GET-Aufrufe gemacht, nie ein POST.
Scharf nur mit:  --live --confirm-account <ETORO_PAPER_ACCOUNT_CID>

Phasen:  plan | c4 | c1 | c5 | fin | monitor | closeall
  plan     Kandidaten bestimmen, results/stage_c/test_symbols.json schreiben (fuer den Bot-Thread), nichts senden
  c4       30 Zufallsaktien: Short-Order <= 20 USD, Orderstatus pruefen, Position sofort schliessen   (nur US-Handelszeit)
  c1       zwei Long-Orders <= 50 USD (IsTslEnabled vs IsTrailingStop, gleicher StopLossRate); danach 'monitor' alle 5 Min
  c5       eine Position <= 50 USD oeffnen und mit UnitsToDeduct halbieren
  fin      kleine Positionen je Klasse/Seite fuer Finanzierungsmessung (<= 100 USD); danach taeglich 'monitor'
  monitor  rein lesend: protokolliert Gebuehren-/Stop-Felder aller eigenen Testpositionen
  closeall schliesst alle eigenen Testpositionen per positionId
"""
from __future__ import annotations

import argparse, csv, datetime as dt, json, os, random, sys, time
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.getcwd())
import guard

OUT = "results/stage_c"
SEED = 20261011


def now(): return dt.datetime.now(dt.timezone.utc).isoformat()


def append(path, header, row):
    os.makedirs(OUT, exist_ok=True)
    p = f"{OUT}/{path}"; new = not os.path.exists(p)
    with open(p, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(header)
        w.writerow(row)


def universe():
    return list(csv.DictReader(open("results/H1/universe.csv")))


def rates(ids):
    r = guard.get(f"{guard.BASE}/market-data/instruments/rates?instrumentIds=" + ",".join(str(i) for i in ids))
    return {int(x["instrumentID"]): (x["bid"], x["ask"]) for x in r.get("rates", [])}


def find_key(obj, name):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == name and not isinstance(v, (dict, list)): return v
            r = find_key(v, name)
            if r is not None: return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, name)
            if r is not None: return r
    return None


def new_position(before_ids, instrument_id, tries=20):
    for _ in range(tries):
        for p in guard.positions():
            if int(p["instrumentID"]) == int(instrument_id) and str(p["positionID"]) not in before_ids:
                return p
        time.sleep(3)
    return None


def candidates(args, gate_excl):
    open_ids = {int(p["instrumentID"]) for p in guard.positions()}
    return [r for r in universe() if int(r["etoro_id"]) not in gate_excl and int(r["etoro_id"]) not in open_ids]


def open_order(g: guard.Gate, r, amount, is_buy, extra=None, tag=""):
    iid = int(r["etoro_id"])
    g.check_open(iid, amount, is_buy, r["klasse"])
    bid, ask = rates([iid])[iid]
    payload = {"InstrumentID": iid, "IsBuy": is_buy, "Leverage": 1, "IsNoStopLoss": True, "IsNoTakeProfit": True, "Amount": round(amount, 2)}
    payload.update(extra or {})
    if extra and "StopLossRate" in extra: payload["IsNoStopLoss"] = False
    before = {str(p["positionID"]) for p in guard.positions()}
    submit_ts = now()
    status, body = g.post(f"{guard.EXEC}/market-open-orders/by-amount", payload)
    if not g.live:
        return None
    oid = find_key(body, "orderid")
    rej = None; info = {}
    if status not in (200, 201):
        rej = f"http_{status}: {json.dumps(body)[:200]}"
    elif oid:
        time.sleep(2)
        from automation.adapters.etoro_execution import order_rejection_reason
        try:
            info = guard.get(f"{guard.ORDER_INFO}/{oid}"); rej = order_rejection_reason(info)
        except Exception as e:
            rej = f"orderinfo_error:{e}"
    pos = new_position(before, iid) if not rej else None
    if rej:
        append("rejections.csv", ["ts", "order_id", "instrument", "klasse", "side", "amount_usd", "status_id", "error_code", "message", "tag"],
               [now(), oid, iid, r["klasse"], "BUY" if is_buy else "SELL", amount, find_key(info, "statusid"), find_key(info, "errorcode"), rej, tag])
    fees = (pos.get("totalFees", 0) + pos.get("totalExternalFees", 0)) if pos else ""
    append("fills.csv", ["order_id", "position_id", "instrument", "klasse", "side", "submit_ts", "bid", "ask", "fill_px", "units", "amount_usd", "fees_usd", "overnight_fees_usd", "http", "rejection", "tag"],
           [oid, pos["positionID"] if pos else "", iid, r["klasse"], "BUY" if is_buy else "SELL", submit_ts, bid, ask, pos["openRate"] if pos else "", pos["units"] if pos else "", amount, fees, 0, status, rej or "", tag])
    if pos:
        g.state["positions"][str(pos["positionID"])] = {"instrument": iid, "klasse": r["klasse"], "is_buy": is_buy, "opened": submit_ts, "amount": amount, "tag": tag, "units": pos["units"]}
        g.save()
    return pos, rej


def close_position(g: guard.Gate, position_id, units_to_deduct=None):
    g.check_close(position_id)
    iid = g.state["positions"][str(position_id)]["instrument"]
    status, body = g.post(f"{guard.EXEC}/market-close-orders/positions/{position_id}", {"InstrumentId": iid, "UnitsToDeduct": units_to_deduct})
    if g.live and status in (200, 201) and units_to_deduct is None:
        for _ in range(20):
            if str(position_id) not in {str(p["positionID"]) for p in guard.positions()}:
                g.state["positions"].pop(str(position_id), None); g.save(); break
            time.sleep(3)
    return status


def monitor():
    g_pos = {str(p["positionID"]): p for p in guard.positions()}
    state = json.load(open(f"{OUT}/state.json")) if os.path.exists(f"{OUT}/state.json") else {"positions": {}}
    ids = [v["instrument"] for v in state["positions"].values()]
    rt = rates(ids) if ids else {}
    for pid, meta in list(state["positions"].items()):
        p = g_pos.get(pid)
        if not p:
            append("position_fees_daily.csv", ["ts", "position_id", "instrument", "klasse", "status"], [now(), pid, meta["instrument"], meta["klasse"], "nicht mehr offen"])
            state["positions"].pop(pid, None); json.dump(state, open(f"{OUT}/state.json", "w"), indent=1); continue
        bid, ask = rt.get(meta["instrument"], ("", ""))
        row = [now(), pid, meta["instrument"], meta["klasse"], "BUY" if p["isBuy"] else "SELL", p["units"], p["openRate"], p.get("stopLossRate"), p.get("isTslEnabled"),
               p.get("totalFees"), p.get("totalExternalFees"), p.get("amount"), bid, ask, meta.get("tag", "")]
        append("position_fees_daily.csv", ["ts", "position_id", "instrument", "klasse", "side", "units", "open_rate", "stop_loss_rate", "is_tsl_enabled", "total_fees", "total_external_fees", "amount", "bid", "ask", "tag"], row)
        if meta.get("tag", "").startswith("c1"):
            append("tsl_field_check.csv", ["ts", "position_id", "tag", "stop_loss_rate", "is_tsl_enabled", "bid", "ask", "open_rate"], [now(), pid, meta["tag"], p.get("stopLossRate"), p.get("isTslEnabled"), bid, ask, p["openRate"]])
    print(f"monitor: {len(state['positions'])} eigene Testpositionen protokolliert")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["plan", "c4", "c1", "c5", "fin", "monitor", "closeall"])
    ap.add_argument("--live", action="store_true", help="Orders wirklich senden (Standard: Dry-Run)")
    ap.add_argument("--confirm-account", default=None, help="muss gleich ETORO_PAPER_ACCOUNT_CID sein")
    ap.add_argument("--whitelist", default=os.path.expanduser("~/etoro_nautilus/data/state/incubation_whitelist.json"))
    ap.add_argument("--bot-map", default=os.path.expanduser("~/etoro_nautilus/automation/config/instrument_map.json"))
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--classes", default="K1,K5", help="freigegebene Klassen (Standard K1,K5; K2 braucht >= 1000 USD und wird bei kleineren Caps verweigert)")
    a = ap.parse_args()
    guard.load_env()
    acct = guard.verify_account()
    print(f"/me OK (Paper-Konto {acct}); Modus: {'LIVE' if a.live else 'DRY-RUN'}")
    if a.phase == "monitor":
        return monitor()
    excl = guard.bot_excluded_ids(a.whitelist, a.bot_map)
    ph = {"c4": "c4", "c1": "c1", "c5": "c5", "fin": "c2", "closeall": "c2", "plan": "c2"}[a.phase]
    g = guard.Gate(a.live, a.confirm_account, excl, f"{OUT}/state.json", ph, lambda m: print(m))
    g.classes = tuple(a.classes.split(','))
    pool = [r for r in candidates(a, excl) if r['klasse'] in g.classes]
    rnd = random.Random(SEED)
    eq = [r for r in pool if r["klasse"] == "K1" and guard.market_open("K1")]
    if a.phase == "plan":
        cls = {k: len([r for r in pool if r["klasse"] == k]) for k in ("K1", "K2", "K3", "K4", "K5")}
        sel = {"c4": [r["symbol"] for r in rnd.sample([r for r in pool if r["klasse"] == "K1"], min(a.n, len(pool)))],
               "c1": [r["symbol"] for r in pool if r["klasse"] == "K1"][:1], "classes": list(g.classes)}
        os.makedirs(OUT, exist_ok=True)
        json.dump({"written": now(), "excluded_bot_instruments": len(excl), "pool_by_class": cls, "planned": sel,
                   "hinweis": "Diese Symbole werden nur vom EBC-Stufe-C-Skript gehandelt; der Bot-Thread sollte sie ausschliessen."}, open(f"{OUT}/test_symbols.json", "w"), indent=1)
        print(json.dumps({"pool_by_class": cls, "planned": sel}, indent=1)); return
    if a.phase == "closeall":
        for pid in list(g.state["positions"]):
            print("close", pid, close_position(g, pid)); 
        return
    if a.phase == "c4":
        for r in rnd.sample(eq, min(a.n, len(eq))):
            try:
                res = open_order(g, r, 20.0, False, tag="c4")
                if res and res[0]: close_position(g, res[0]["positionID"])
                append("shortability.csv", ["ts", "instrument", "symbol", "accepted", "rejection"], [now(), r["etoro_id"], r["symbol"], bool(res and res[0]), (res[1] if res else "dryrun")])
            except guard.Refused as e:
                print("uebersprungen:", r["symbol"], e)
            time.sleep(1.1)
    elif a.phase == "c1":
        r = next((x for x in pool if x["klasse"] == "K2" and guard.market_open("K2")), None) or (eq[0] if eq else None)   # K2 nur wenn per --classes freigegeben
        if r is None: print("kein geeignetes Instrument/Markt geschlossen"); return
        bid, ask = rates([int(r["etoro_id"])])[int(r["etoro_id"])]
        sl = round(ask * 0.985, 5)
        open_order(g, r, 50.0, True, {"IsTslEnabled": True, "StopLossRate": sl}, tag="c1_IsTslEnabled")
        open_order(g, r, 50.0, True, {"IsTrailingStop": True, "StopLossRate": sl}, tag="c1_IsTrailingStop")
    elif a.phase == "c5":
        r = next((x for x in pool if x["klasse"] == "K2" and guard.market_open("K2")), None) or (eq[0] if eq else None)   # K2 nur wenn per --classes freigegeben
        if r is None: print("kein geeignetes Instrument/Markt geschlossen"); return
        res = open_order(g, r, 50.0, True, tag="c5")
        if res and res[0]:
            half = round(res[0]["units"] / 2.0, 6)
            close_position(g, res[0]["positionID"], units_to_deduct=half)
            time.sleep(4)
            rest = [p for p in guard.positions() if str(p["positionID"]) == str(res[0]["positionID"])]
            json.dump({"opened_units": res[0]["units"], "deducted": half, "remaining": rest[0]["units"] if rest else 0,
                       "fees_after": (rest[0].get("totalFees"), rest[0].get("totalExternalFees")) if rest else None}, open(f"{OUT}/partial_close.json", "w"), indent=1)
    elif a.phase == "fin":
        plan = [(k, b) for k in g.classes for b in ((True,) if k == "K5" else (True, False))]
        for k, buy in plan:
            r = next((x for x in pool if x["klasse"] == k and guard.market_open(k)), None)
            if r is None: print("kein Instrument/Markt geschlossen fuer", k, buy); continue
            try: open_order(g, r, 100.0, buy, tag=f"fin_{k}_{'long' if buy else 'short'}")
            except guard.Refused as e: print("uebersprungen:", k, e)
            pool = [x for x in pool if x["etoro_id"] != r["etoro_id"]]
    print("fertig; Modus", "LIVE" if a.live else "DRY-RUN")

if __name__ == "__main__":
    main()
