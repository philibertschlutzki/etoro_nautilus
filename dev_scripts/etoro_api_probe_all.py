"""
eToro API Endpoint-Diagnostik (Full Spec Edition)
=================================================
Testet umfassend alle GET-Endpoints gemäss der offiziellen eToro API-Dokumentation.
Dies schliesst auch Endpunkte ein, die eventuell Parameter benötigen, um zu prüfen,
ob die Route existiert (Erwartung: 400 Bad Request statt 404 Route Not Found).

Hinweis: POST/PUT/DELETE Endpunkte werden aus Sicherheitsgründen ignoriert.

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

# ── Endpoints gemäss offizieller Dokumentations-Liste ─────────────────────────

PROBE_GET: list[tuple[str, str]] = [
    # ── Identity & Agent Portfolios ───────────────────────────────────────────
    ("Identity (me)",                "https://public-api.etoro.com/api/v1/me"),
    ("Agent Portfolios (v1)",        "https://public-api.etoro.com/api/v1/agent-portfolios"),
    
    # ── Market Data ───────────────────────────────────────────────────────────
    ("Instruments Search",           "https://public-api.etoro.com/api/v1/market-data/search"),
    ("Instruments List",             "https://public-api.etoro.com/api/v1/market-data/instruments"),
    ("Instrument Types (Asset Cl.)", "https://public-api.etoro.com/api/v1/market-data/instrument-types"),
    ("Exchanges",                    "https://public-api.etoro.com/api/v1/market-data/exchanges"),
    ("Industries",                   "https://public-api.etoro.com/api/v1/market-data/industries"),
    ("Historical Closing Prices",    "https://public-api.etoro.com/api/v1/market-data/historical-closing-prices"),
    ("Historical Candles",           "https://public-api.etoro.com/api/v1/market-data/candles"),
    ("Market Rates",                 "https://public-api.etoro.com/api/v1/market-data/rates"),

    # ── Trading: Portfolios & PnL ─────────────────────────────────────────────
    ("General Portfolio",            "https://public-api.etoro.com/api/v1/trading/info/portfolio"),
    ("Real Portfolio & PnL",         "https://public-api.etoro.com/api/v1/trading/info/real/pnl"),
    ("Demo Portfolio & PnL",         "https://public-api.etoro.com/api/v1/trading/info/demo/pnl"),
    ("Real Trading History",         "https://public-api.etoro.com/api/v1/trading/info/real/history"),
    ("Demo Trading History",         "https://public-api.etoro.com/api/v1/trading/info/demo/history"),

    # ── Users Info & Social (Feeds/PI) ────────────────────────────────────────
    ("PI Data Copiers",              "https://public-api.etoro.com/api/v1/pi-data/copiers"),
    ("Users Search",                 "https://public-api.etoro.com/api/v1/users-info/search"),
    ("Feeds (Generic)",              "https://public-api.etoro.com/api/v1/feeds"),

    # ── Watchlists ────────────────────────────────────────────────────────────
    ("Watchlists (All)",             "https://public-api.etoro.com/api/v1/watchlists"),
    ("Watchlists Default",           "https://public-api.etoro.com/api/v1/watchlists/default"),
    ("Watchlists Curated",           "https://public-api.etoro.com/api/v1/watchlists/curated"),
    ("Watchlists Recommendations",   "https://public-api.etoro.com/api/v1/watchlists/recommendations"),
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
                if body_text:
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
    if status in (400, 401, 403, 422): # 400/422 sind legitime Antworten (fehlende Params/Logik)
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
                "userid", "user_id", "username", "name", "id", "gcid", "realcid",
                "errorcode", "errormessage", "message", "detail",
                "agentportfoliovirtualbalance", "portfolios", "data", "status", "reason"
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
        f"{color}{r['label']:<30}{RESET}  "
        f"{CYAN}{body_str}{RESET}"
    )


async def main() -> None:
    print(f"\n{BOLD}eToro API Endpoint-Diagnostik (Full Spec){RESET}")
    print(f"Zeitstempel : {datetime.now().isoformat()}")
    print(f"API_KEY     : {API_KEY[:8]}{'*' * max(0, len(API_KEY) - 8)}")
    print(f"USER_KEY    : {USER_KEY[:8]}{'*' * 16}...")
    print(f"Endpoints   : {len(PROBE_GET)} GET-Probes")
    print("─" * 90)

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results_200:   list[dict] = []
        results_other: list[dict] = []

        for label, url in PROBE_GET:
            r = await probe_endpoint(session, label, url)
            if r["status"] and 200 <= r["status"] < 300:
                results_200.append(r)
            else:
                results_other.append(r)

    # ── Ausgabe ───────────────────────────────────────────────────────────────

    print(f"\n{BOLD}{GREEN}✅  Erfolgreiche Endpoints (HTTP 2xx):{RESET}")
    if results_200:
        for r in results_200:
            _print_result(r)
    else:
        print(f"  {RED}Keine 200-Antwort — Prüfe deine API-Keys.{RESET}")

    print(f"\n{BOLD}{YELLOW}⚠️  Andere validierte Endpoints (400=Bad Req, 401/403=Auth, 422=Unprocessable):{RESET}")
    for r in [res for res in results_other if res["status"] != 404]:
        _print_result(r)

    print(f"\n{BOLD}{MAGENTA}❌  Nicht gefunden (HTTP 404 - Pfad inkorrekt oder veraltet):{RESET}")
    for r in [res for res in results_other if res["status"] == 404]:
        _print_result(r)

    print("\n" + "─" * 90)
    print(f"Probe abgeschlossen: {datetime.now().isoformat()}\n")


if __name__ == "__main__":
    asyncio.run(main())