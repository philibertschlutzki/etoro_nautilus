"""
eToro API Endpoint-Diagnostik
==============================
Testet alle bekannten und plausiblen eToro REST-Endpoints und gibt aus,
welche erreichbar sind (HTTP 200) und was sie zurückliefern.

Ziel: Korrekten Balance-Endpoint und Real-Trading-Endpoint finden.

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

# ── Kandidaten-Endpoints ──────────────────────────────────────────────────────

PROBE_GET: list[tuple[str, str]] = [
    # ── Account / Balance ─────────────────────────────────────────────────────
    ("Balance (v1 account)",         "https://public-api.etoro.com/api/v1/trading/account/balance"),
    ("Balance (v1 demo mode)",       "https://public-api.etoro.com/api/v1/trading/account/balance?mode=demo"),
    ("Balance (v1 real mode)",       "https://public-api.etoro.com/api/v1/trading/account/balance?mode=real"),
    ("Balance (v1 summary)",         "https://public-api.etoro.com/api/v1/trading/account/summary"),
    ("Balance (v1 portfolio)",       "https://public-api.etoro.com/api/v1/trading/account/portfolio"),
    ("Balance (v2 account)",         "https://public-api.etoro.com/api/v2/trading/account/balance"),
    ("Accounts root",                "https://public-api.etoro.com/api/v1/trading/account"),
    ("User info",                    "https://public-api.etoro.com/api/v1/trading/user"),
    ("User profile",                 "https://public-api.etoro.com/api/v1/trading/user/profile"),

    # ── Positionen / PnL ─────────────────────────────────────────────────────
    ("PnL demo",                     "https://public-api.etoro.com/api/v1/trading/execution/demo/pnl"),
    ("PnL real",                     "https://public-api.etoro.com/api/v1/trading/execution/real/pnl"),
    ("PnL (no env)",                 "https://public-api.etoro.com/api/v1/trading/execution/pnl"),
    ("Positions demo",               "https://public-api.etoro.com/api/v1/trading/execution/demo/positions"),
    ("Positions real",               "https://public-api.etoro.com/api/v1/trading/execution/real/positions"),
    ("Positions (no env)",           "https://public-api.etoro.com/api/v1/trading/execution/positions"),

    # ── Order-Endpoints (GET) ─────────────────────────────────────────────────
    ("Orders demo",                  "https://public-api.etoro.com/api/v1/trading/execution/demo/orders"),
    ("Orders real",                  "https://public-api.etoro.com/api/v1/trading/execution/real/orders"),
    ("Limit orders demo",            "https://public-api.etoro.com/api/v1/trading/execution/demo/limit-orders"),
    ("Limit orders real",            "https://public-api.etoro.com/api/v1/trading/execution/real/limit-orders"),

    # ── Instruments ───────────────────────────────────────────────────────────
    ("Instruments search",           "https://public-api.etoro.com/api/v1/market-data/search"),
    ("Instruments list",             "https://public-api.etoro.com/api/v1/market-data/instruments"),

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
                "body":   body_parsed or body_text[:500],
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
    return RED


def _print_result(r: dict) -> None:
    color  = _status_color(r["status"])
    status = str(r["status"]) if r["status"] else "ERR"
    body   = r["body"]

    # Kompakte Darstellung für bekannte Strukturen
    if isinstance(body, dict):
        # Zeige nur relevante Felder
        relevant = {
            k: v for k, v in body.items()
            if k.lower() in (
                "availablebalance", "available_balance", "balance",
                "totalbalance", "total_balance", "equity",
                "userid", "user_id", "username", "name",
                "errorcode", "errormessage", "message", "detail",
            )
        }
        body_str = json.dumps(relevant or body, ensure_ascii=False)[:300]
    elif isinstance(body, list):
        body_str = f"[{len(body)} items]"
        if body:
            body_str += f"  first: {json.dumps(body[0])[:200]}"
    else:
        body_str = str(body)[:300]

    print(
        f"  {color}{BOLD}HTTP {status:>3}{RESET}  "
        f"{color}{r['label']:<35}{RESET}  "
        f"{CYAN}{body_str}{RESET}"
    )


async def main() -> None:
    print(f"\n{BOLD}eToro API Endpoint-Diagnostik{RESET}")
    print(f"Zeitstempel : {datetime.now().isoformat()}")
    print(f"API_KEY     : {API_KEY[:8]}{'*' * (len(API_KEY) - 8)}")
    print(f"USER_KEY    : {USER_KEY[:8]}{'*' * (len(USER_KEY) - 8)}")
    print(f"Endpoints   : {len(PROBE_GET)} GET-Probes")
    print("─" * 80)

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Sequentiell damit Logs lesbar bleiben
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
        print(f"  {RED}Keine einzige 200-Antwort — API-Key-Problem oder alle URLs falsch.{RESET}")

    print(f"\n{BOLD}{YELLOW}⚠️  Andere Antworten:{RESET}")
    for r in results_other:
        _print_result(r)

    # ── Balance-Zusammenfassung ───────────────────────────────────────────────

    print("\n" + "─" * 80)
    balance_hits = [
        r for r in results_200
        if isinstance(r["body"], dict) and any(
            k in str(r["body"]).lower()
            for k in ("balance", "equity", "available")
        )
    ]
    if balance_hits:
        print(f"\n{BOLD}💰  Balance-Kandidaten:{RESET}")
        for r in balance_hits:
            print(f"  URL  : {r['url']}")
            print(f"  Body : {json.dumps(r['body'], indent=2, ensure_ascii=False)[:600]}")
            print()
    else:
        print(
            f"\n{YELLOW}Kein Balance-Endpoint mit 200-Antwort gefunden.{RESET}\n"
            "Nächste Schritte:\n"
            "  1. Überprüfe ob der API-Key Real-Trading-Berechtigungen hat\n"
            "  2. Kontaktiere eToro Support für die korrekten Real-API-Endpoints\n"
            "  3. Teste mit environment='demo' und dry_run=False als nächsten Schritt\n"
        )

    print("─" * 80)
    print(f"Probe abgeschlossen: {datetime.now().isoformat()}\n")


if __name__ == "__main__":
    asyncio.run(main())