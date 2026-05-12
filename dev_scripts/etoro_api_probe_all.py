"""
eToro API Endpoint-Diagnostik (Extended Edition)
=================================================
Testet umfassend alle bekannten und aus der Dokumentation abgeleiteten 
eToro REST-Endpoints (GET). Gibt aus, welche erreichbar sind (HTTP 200).

Besonderer Fokus: Prüfung, ob das "Agent Portfolios" Feature aktiv ist.
Hinweis: POST/PUT/DELETE Endpunkte (Trades, Erstellung, Löschung) werden 
aus Sicherheitsgründen in diesem reinen Diagnose-Script übersprungen.

Ausführung:
    python3 dev_scripts/etoro_api_probe.py
"""

import asyncio
import json
import os
import uuid
from datetime import datetime

import aiohttp
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("ETORO_API_KEY",  "")
USER_KEY = os.getenv("ETORO_USER_KEY", "")

if not API_KEY or not USER_KEY:
    raise SystemExit("❌  ETORO_API_KEY oder ETORO_USER_KEY fehlen in der .env")

# ── Kandidaten-Endpoints (Umfassende Liste) ───────────────────────────────────

PROBE_GET: list[tuple[str, str]] = [
    # ── Agent Portfolios (Spezieller Fokus) ───────────────────────────────────
    ("Agent Portfolios (v1)",        "https://public-api.etoro.com/api/v1/agent-portfolios"),
    ("Agent Portfolios (v2)",        "https://public-api.etoro.com/api/v2/agent-portfolios"),
    ("Agent Portfolios Trading",     "https://public-api.etoro.com/api/v1/trading/agent-portfolios"),
    
    # ── Identity & User Info ──────────────────────────────────────────────────
    ("Identity GCID",                "https://public-api.etoro.com/api/v1/identity"),
    ("User Info (Trading)",          "https://public-api.etoro.com/api/v1/trading/user"),
    ("User Profile",                 "https://public-api.etoro.com/api/v1/trading/user/profile"),
    ("Users Profile (me)",           "https://public-api.etoro.com/api/v1/users/me/profile"),
    ("Users Portfolio (me)",         "https://public-api.etoro.com/api/v1/users/me/portfolio"),
    ("Users Trade Info (me)",        "https://public-api.etoro.com/api/v1/users/me/trade-info"),
    ("PI Data Copiers",              "https://public-api.etoro.com/api/v1/pi-data/copiers"),

    # ── Account / Balance ─────────────────────────────────────────────────────
    ("Balance (v1 account)",         "https://public-api.etoro.com/api/v1/trading/account/balance"),
    ("Balance (v1 demo mode)",       "https://public-api.etoro.com/api/v1/trading/account/balance?mode=demo"),
    ("Balance (v1 real mode)",       "https://public-api.etoro.com/api/v1/trading/account/balance?mode=real"),
    ("Balance (v2 account)",         "https://public-api.etoro.com/api/v2/trading/account/balance"),
    ("Account Summary",              "https://public-api.etoro.com/api/v1/trading/account/summary"),

    # ── Trading: Demo ─────────────────────────────────────────────────────────
    ("Demo Portfolio",               "https://public-api.etoro.com/api/v1/trading/execution/demo/portfolio"),
    ("Demo PnL",                     "https://public-api.etoro.com/api/v1/trading/execution/demo/pnl"),
    ("Demo Positions",               "https://public-api.etoro.com/api/v1/trading/execution/demo/positions"),
    ("Demo Orders",                  "https://public-api.etoro.com/api/v1/trading/execution/demo/orders"),
    ("Demo Limit Orders",            "https://public-api.etoro.com/api/v1/trading/execution/demo/limit-orders"),

    # ── Trading: Real ─────────────────────────────────────────────────────────
    ("Real Portfolio",               "https://public-api.etoro.com/api/v1/trading/execution/real/portfolio"),
    ("Real PnL",                     "https://public-api.etoro.com/api/v1/trading/execution/real/pnl"),
    ("Real Positions",               "https://public-api.etoro.com/api/v1/trading/execution/real/positions"),
    ("Real Orders",                  "https://public-api.etoro.com/api/v1/trading/execution/real/orders"),
    ("Real Trading History",         "https://public-api.etoro.com/api/v1/trading/execution/real/history"),

    # ── Market Data ───────────────────────────────────────────────────────────
    ("Instruments Search",           "https://public-api.etoro.com/api/v1/market-data/search"),
    ("Instruments List",             "https://public-api.etoro.com/api/v1/market-data/instruments"),
    ("Instrument Types (Asset Cl.)", "https://public-api.etoro.com/api/v1/market-data/instrument-types"),
    ("Exchanges",                    "https://public-api.etoro.com/api/v1/market-data/exchanges"),
    ("Industries",                   "https://public-api.etoro.com/api/v1/market-data/industries"),
    ("Historical Closing Prices",    "https://public-api.etoro.com/api/v1/market-data/historical-closing-prices"),

    # ── Watchlists ────────────────────────────────────────────────────────────
    ("Watchlists (All)",             "https://public-api.etoro.com/api/v1/watchlists"),
    ("Watchlists Default",           "https://public-api.etoro.com/api/v1/watchlists/default"),
    ("Watchlists Curated",           "https://public-api.etoro.com/api/v1/watchlists/curated"),
    ("Watchlists Recommendations",   "https://public-api.etoro.com/api/v1/watchlists/recommendations"),

    # ── Feeds / Social ────────────────────────────────────────────────────────
    ("User Feeds",                   "https://public-api.etoro.com/api/v1/feeds/user"),
    ("User Feeds (me)",              "https://public-api.etoro.com/api/v1/feeds/users/me/posts"),

    # ── API root / health ─────────────────────────────────────────────────────
    ("API root v1",                  "https://public-api.etoro.com/api/v1"),
    ("API root v2",                  "https://public-api.etoro.com/api/v2"),
    ("Health check",                 "https://public-api.etoro.com/health"),
]

# ── Farb-Codes (ANSI) ─────────────────────────────────────────────────────────

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
MAGENTA= "\033[95m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def _make_headers(req_id: str | None = None) -> dict:
    return {
        "x-api-key":    API_KEY,
        "x-user-key":   USER_KEY,
        "x-request-id": req_id or str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


async def probe_endpoint(
    session: aiohttp.ClientSession,
    label: str,
    url: str,
) -> dict:
    """GET request gegen einen einzelnen Endpoint. Gibt Result-Dict zurück."""
    try:
        async with session.get(url, headers=_make_headers()) as resp:
            status    = resp.status
            body_text = await resp.text()

            body_parsed: dict | list | None = None
            try:
                body_parsed = json.loads(body_text)
            except json.JSONDecodeError:
                pass

            return {
                "label":  label,
                "url":    url,
                "status": status,
                "body":   body_parsed if body_parsed is not None else body_text[:500],
            }
    except Exception as exc:
        return {
            "label":  label,
            "url":    url,
            "status": None,
            "body":   str(exc),
        }


def _status_color(status: int | None) -> str:
    if status is None:
        return RED
    if 200 <= status < 300:
        return GREEN
    if status in (401, 403):
        return YELLOW
    if status == 404:
        return MAGENTA
    return RED


def _print_result(r: dict) -> None:
    color  = _status_color(r["status"])
    status = str(r["status"]) if r["status"] else "ERR"
    body   = r["body"]

    # Kompakte Darstellung für bekannte Strukturen
    if isinstance(body, dict):
        relevant = {
            k: v for k, v in body.items()
            if k.lower() in (
                "availablebalance", "available_balance", "balance",
                "totalbalance", "total_balance", "equity",
                "userid", "user_id", "username", "name", "id",
                "errorcode", "errormessage", "message", "detail",
                "agentportfoliovirtualbalance", "portfolios", "data"
            )
        }
        body_str = json.dumps(relevant or body, ensure_ascii=False)[:300]
    elif isinstance(body, list):
        body_str = f"[{len(body)} items]"
        if body:
            body_str += f"  first: {json.dumps(body[0], ensure_ascii=False)[:200]}"
    else:
        body_str = str(body)[:300]
        
    # Kürze den String, falls er zu lang ist
    if len(body_str) > 300:
        body_str = body_str[:297] + "..."

    print(
        f"  {color}{BOLD}HTTP {status:>3}{RESET}  "
        f"{color}{r['label']:<32}{RESET}  "
        f"{CYAN}{body_str}{RESET}"
    )


async def main() -> None:
    print(f"\n{BOLD}eToro API Endpoint-Diagnostik (Extended){RESET}")
    print(f"Zeitstempel : {datetime.now().isoformat()}")
    print(f"API_KEY     : {API_KEY[:8]}{'*' * (len(API_KEY) - 8)}")
    print(f"USER_KEY    : {USER_KEY[:8]}{'*' * (len(USER_KEY) - 8)}")
    print(f"Endpoints   : {len(PROBE_GET)} GET-Probes")
    print("─" * 85)

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Sequentieller Aufruf, damit Logs sauber bleiben und Rate Limits nicht sofort getriggert werden
        results_200:   list[dict] = []
        results_other: list[dict] = []

        for label, url in PROBE_GET:
            r = await probe_endpoint(session, label, url)
            if r["status"] and 200 <= r["status"] < 300:
                results_200.append(r)
            else:
                results_other.append(r)

    # ── Ausgabe der Standard-Probes ───────────────────────────────────────────

    print(f"\n{BOLD}{GREEN}✅  Erfolgreiche Endpoints (HTTP 2xx):{RESET}")
    if results_200:
        for r in results_200:
            _print_result(r)
    else:
        print(f"  {RED}Keine 200-Antwort — Prüfe deine API-Keys.{RESET}")

    print(f"\n{BOLD}{YELLOW}⚠️  Andere Antworten (401=Unauthorized, 403=Forbidden, 404=Not Found):{RESET}")
    for r in results_other:
        _print_result(r)

    # ── Analyse: Agent Portfolios (Dein Fokus) ────────────────────────────────

    print("\n" + "─" * 85)
    print(f"{BOLD}🤖  ANALYSE: AGENT PORTFOLIOS{RESET}")
    
    agent_hits = [r for r in results_200 if "agent portfolio" in r["label"].lower()]
    agent_denied = [r for r in results_other if "agent portfolio" in r["label"].lower()]
    
    if agent_hits:
        print(f"{GREEN}{BOLD}► STATUS: AKTIV{RESET}")
        print("Es wurde mindestens ein Agent Portfolio Endpoint erfolgreich (HTTP 200) angesprochen.\n")
        for r in agent_hits:
            print(f"  URL  : {r['url']}")
            print(f"  Body : {json.dumps(r['body'], indent=2, ensure_ascii=False)[:600]}\n")
    else:
        print(f"{YELLOW}{BOLD}► STATUS: INAKTIV ODER NICHT AUTORISIERT{RESET}")
        print("Die Agent Portfolio Endpoints haben keine 200 OK Antwort geliefert. Resultate:\n")
        for r in agent_denied:
            status = str(r["status"]) if r["status"] else "ERR"
            msg = r["body"].get("message", r["body"]) if isinstance(r["body"], dict) else r["body"]
            print(f"  HTTP {status} | {r['url']} -> {str(msg)[:150]}")
            
        print("\nNächste Schritte für Agent Portfolios:")
        print("  1. Prüfe in der eToro Entwicklerkonsole, ob die 'Agent Portfolios' Scopes für diesen Key aktiv sind.")
        print("  2. Ein Agent Portfolio muss meistens via POST (`/api/v1/agent-portfolios`) erstellt werden, bevor es im GET sichtbar ist.")

    # ── Analyse: Balance-Zusammenfassung ──────────────────────────────────────

    print("\n" + "─" * 85)
    balance_hits = [
        r for r in results_200
        if isinstance(r["body"], dict) and any(
            k in str(r["body"]).lower()
            for k in ("balance", "equity", "available", "total_balance")
        ) and "agent portfolio" not in r["label"].lower()
    ]
    
    if balance_hits:
        print(f"{BOLD}💰  Balance-Kandidaten (Standard Accounts):{RESET}")
        for r in balance_hits:
            print(f"  [{r['label']}] -> URL: {r['url']}")
            print(f"  Preview: {json.dumps(r['body'], ensure_ascii=False)[:200]}\n")
    else:
        print(f"{YELLOW}Kein klassischer Balance-Endpoint mit 200-Antwort gefunden.{RESET}")

    print("─" * 85)
    print(f"Probe abgeschlossen: {datetime.now().isoformat()}\n")


if __name__ == "__main__":
    asyncio.run(main())