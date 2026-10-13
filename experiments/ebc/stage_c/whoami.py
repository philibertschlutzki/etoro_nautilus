"""Rein lesende Konto-Erkundung hinter dem API-Key: jeder GET-Endpunkt genau einmal (>= 1,1 s Abstand, 429-Backoff).
Antworten nach results/stage_c/whoami/ (gitignored, NICHT committen). Keine Orders, keine Schluessel in Ausgabe/Dateien.
Aufruf:  python experiments/ebc/stage_c/whoami.py"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import guard

OUT = "results/stage_c/whoami"
SECRET = re.compile(r"(key|token|secret|password|authorization)", re.I)
B = guard.BASE
STOCKS = {"GOOG": 1002, "MSFT": None, "AAPL": None}
ENDPOINTS = [
    ("me", f"{B}/me"),
    ("pnl_real", f"{B}/trading/info/real/pnl"),
    ("pnl_demo", f"{B}/trading/info/demo/pnl"),
    ("portfolio_real", f"{B}/trading/info/real/portfolio"),
    ("history_real", f"{B}/trading/info/real/history?minDate=2024-01-01"),
    ("history_trade", f"{B}/trading/info/trade/history?minDate=2024-01-01&page=1&pageSize=50"),
    ("orders_real", f"{B}/trading/info/real/orders"),
    ("watchlists", f"{B}/watchlists"),
    ("instrument_types", f"{B}/market-data/instrument-types"),
    ("exchanges", f"{B}/market-data/exchanges"),
    ("search_goog", f"{B}/market-data/search?internalSymbolFull=GOOG"),
    ("search_msft", f"{B}/market-data/search?internalSymbolFull=MSFT"),
    ("search_aapl", f"{B}/market-data/search?internalSymbolFull=AAPL"),
    ("instruments_goog", f"{B}/market-data/instruments?instrumentIds=1002"),
    ("rates_goog", f"{B}/market-data/instruments/rates?instrumentIds=1002"),
    ("user_info", f"{B}/user-info/people/me"),
    ("account_info", f"{B}/user-info/account"),
    ("verification", f"{B}/user-info/verification"),
    ("tax", f"{B}/user-info/tax"),
]


def scrub(o):
    if isinstance(o, dict):
        return {k: ("<entfernt>" if SECRET.search(k) else scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


def main():
    guard.load_env()
    os.makedirs(OUT, exist_ok=True)
    res = {}
    for name, url in ENDPOINTS:
        status, body = None, None
        try:
            guard_get = guard.get
            body = guard_get(url); status = 200
        except RuntimeError as e:
            m = re.search(r"HTTP (\d+)", str(e)); status = int(m.group(1)) if m else -1
        res[name] = status
        entry = {"url": url.replace(B, "..."), "status": status, "body": scrub(body) if body is not None else None}
        json.dump(entry, open(f"{OUT}/{name}.json", "w"), indent=1, ensure_ascii=False)
        print(f"{name:18s} {status}{'  (nicht erlaubt)' if status in (401, 403) else ''}")
    me = json.load(open(f"{OUT}/me.json"))["body"] or {}
    pnl = (json.load(open(f"{OUT}/pnl_real.json"))["body"] or {}).get("clientPortfolio", {})
    print("\n== Zusammenfassung ==")
    print("Konto: username=%s, level=%s, realCid=%s, demoCid=%s" % (me.get("username"), me.get("playerLevel"), me.get("realCid"), me.get("demoCid")))
    print("Scopes:", me.get("scopes"))
    print("Guthaben (credit): %s, offene Positionen: %d, Mirrors: %d" % (pnl.get("credit"), len(pnl.get("positions") or []), len(pnl.get("mirrors") or [])))
    for fn in ("instruments_goog", "search_goog", "search_msft", "search_aapl"):
        try:
            b = json.load(open(f"{OUT}/{fn}.json"))["body"]
        except Exception:
            continue
        txt = json.dumps(b)
        flags = {k: v for k, v in re.findall(r'"((?:is|has|min|max)[A-Za-z]*)":\s*([^,}\]]+)', txt) if re.search(r"buy|sell|trad|min|w8|tax|allow|restrict", k, re.I)}
        print(fn, "->", dict(list(flags.items())[:12]) or "keine Handelbarkeits-Felder")
    interesting = []
    for fn in os.listdir(OUT):
        t = open(f"{OUT}/{fn}").read()
        for kw in ("w8", "W-8", "tax", "kyc", "verification", "isDemo", "accountType", "testUser", "country"):
            if re.search(kw, t, re.I): interesting.append((fn, kw))
    print("auffaellige Felder (Datei, Stichwort):", sorted(set(interesting))[:20])
    print("Antworten in", OUT, "(gitignored, nicht committen)")


if __name__ == "__main__":
    main()
