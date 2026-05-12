"""
eToro Konto-Balance Abruf
==========================
Ruft Guthaben, offene Positionen und verfügbares Cash für Demo- und Real-Konto ab.

Korrekte Endpoints laut offizieller Doku:
  Demo PnL: GET https://public-api.etoro.com/api/v1/trading/info/demo/pnl
  Real PnL: GET https://public-api.etoro.com/api/v1/trading/info/real/pnl

Ausführung:
    python3 dev_scripts/etoro_balance.py
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

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PNL_ENDPOINTS = {
    "demo": "https://public-api.etoro.com/api/v1/trading/info/demo/pnl",
    "real": "https://public-api.etoro.com/api/v1/trading/info/real/pnl",
}

IDENTITY_URL = "https://public-api.etoro.com/api/v1/trading/info/identity"


def _headers() -> dict:
    return {
        "x-api-key":    API_KEY,
        "x-user-key":   USER_KEY,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


def _calculate_available_cash(data: dict) -> float:
    """Verfügbares Cash gemäss offizieller eToro-Formel:
    Available Cash = credit - (Σ ordersForOpen[mirrorID=0].amount + Σ orders.amount)
    """
    credit = float(data.get("credits", data.get("credit", 0)))
    orders_for_open = data.get("ordersForOpen", [])
    orders          = data.get("orders", [])

    manual_open_amount = sum(
        float(o.get("amount", 0))
        for o in orders_for_open
        if o.get("mirrorID", 0) == 0
    )
    pending_orders_amount = sum(float(o.get("amount", 0)) for o in orders)

    return credit - (manual_open_amount + pending_orders_amount)


def _print_section(title: str) -> None:
    print(f"\n{BOLD}{'─' * 60}{RESET}")
    print(f"{BOLD}{title}{RESET}")
    print(f"{BOLD}{'─' * 60}{RESET}")


async def fetch_identity(session: aiohttp.ClientSession) -> None:
    """Holt User-Identity (GCID, Real-CID, Demo-CID)."""
    _print_section("👤  User Identity")
    try:
        async with session.get(IDENTITY_URL, headers=_headers()) as resp:
            if resp.status == 200:
                data = await resp.json()
                print(f"  GCID           : {data.get('gcid', 'n/a')}")
                print(f"  Real-CID       : {data.get('realCid', data.get('realCustomerId', 'n/a'))}")
                print(f"  Demo-CID       : {data.get('demoCid', data.get('demoCustomerId', 'n/a'))}")
            else:
                body = await resp.text()
                print(f"  {YELLOW}HTTP {resp.status}: {body[:200]}{RESET}")
    except Exception as exc:
        print(f"  {RED}Fehler: {exc}{RESET}")


async def fetch_portfolio(
    session: aiohttp.ClientSession,
    env: str,
) -> None:
    """Holt PnL/Portfolio für Demo oder Real."""
    url = PNL_ENDPOINTS[env]
    label = "🟡  DEMO-Konto" if env == "demo" else "🟢  REAL-Konto"
    _print_section(label)
    print(f"  URL: {CYAN}{url}{RESET}")

    try:
        async with session.get(url, headers=_headers()) as resp:
            status = resp.status
            body_text = await resp.text()

            if status != 200:
                print(f"  {RED}HTTP {status}: {body_text[:300]}{RESET}")
                return

            data: dict = json.loads(body_text)

            # ── Kontostand ────────────────────────────────────────────────────
            credit        = float(data.get("credits", data.get("credit", 0)))
            available     = _calculate_available_cash(data)
            equity_raw    = data.get("equity", data.get("netEquity", None))
            equity        = float(equity_raw) if equity_raw is not None else None
            realized_pnl  = float(data.get("totalRealizedEquity", data.get("realizedPnL", 0)))
            unrealized    = float(data.get("totalUnrealizedEquity", data.get("unrealizedPnL", 0)))

            print(f"\n  {GREEN}{BOLD}Kontostand{RESET}")
            print(f"  Credit (Gesamt)    : {GREEN}{credit:>12.2f} USD{RESET}")
            print(f"  Verfügbares Cash   : {GREEN}{available:>12.2f} USD{RESET}")
            if equity is not None:
                print(f"  Equity             : {GREEN}{equity:>12.2f} USD{RESET}")
            print(f"  Realisierter PnL   : {realized_pnl:>12.2f} USD")
            print(f"  Unrealisierter PnL : {unrealized:>12.2f} USD")

            # ── Offene Positionen ─────────────────────────────────────────────
            positions = data.get("positions", [])
            orders_for_open = data.get("ordersForOpen", [])

            print(f"\n  {BOLD}Offene Positionen  : {len(positions)}{RESET}")
            if positions:
                print(f"  {'InstrID':>8}  {'Side':>5}  {'Units':>10}  {'OpenRate':>10}  {'CurrentRate':>12}  {'PnL':>10}")
                print(f"  {'─'*8}  {'─'*5}  {'─'*10}  {'─'*10}  {'─'*12}  {'─'*10}")
                for pos in positions[:20]:  # max 20 anzeigen
                    side        = "LONG"  if pos.get("isBuy", True) else "SHORT"
                    instrument  = pos.get("instrumentID", pos.get("InstrumentId", "?"))
                    units       = float(pos.get("units", pos.get("amount", 0)))
                    open_rate   = float(pos.get("openRate", pos.get("openPrice", 0)))
                    current     = float(pos.get("currentRate", pos.get("currentPrice", 0)))
                    pnl         = float(pos.get("profit", pos.get("pnl", 0)))
                    pos_id      = pos.get("positionID", pos.get("positionId", "?"))
                    color       = GREEN if pnl >= 0 else RED
                    print(
                        f"  {instrument:>8}  {side:>5}  {units:>10.2f}  "
                        f"{open_rate:>10.5f}  {current:>12.5f}  "
                        f"{color}{pnl:>10.2f}{RESET}  (posID={pos_id})"
                    )
                if len(positions) > 20:
                    print(f"  ... und {len(positions) - 20} weitere Positionen")

            # ── Pending Orders ────────────────────────────────────────────────
            pending = [o for o in orders_for_open if o.get("mirrorID", 0) == 0]
            if pending:
                print(f"\n  {BOLD}Pending Orders (manuell): {len(pending)}{RESET}")
                for o in pending[:10]:
                    print(
                        f"  OrderID={o.get('orderID', '?')}  "
                        f"InstrID={o.get('instrumentID', '?')}  "
                        f"Amount={o.get('amount', 0):.2f}"
                    )

            # ── Rohstruktur (Debug) ───────────────────────────────────────────
            top_level_keys = list(data.keys())
            print(f"\n  {CYAN}Response-Keys: {top_level_keys}{RESET}")

    except Exception as exc:
        import traceback
        print(f"  {RED}Fehler: {exc}{RESET}")
        print(f"  {traceback.format_exc()}")


async def main() -> None:
    print(f"\n{BOLD}eToro Konto-Balance Abruf{RESET}")
    print(f"Zeitstempel : {datetime.now().isoformat()}")
    print(f"API_KEY     : {API_KEY[:8]}{'*' * max(0, len(API_KEY) - 8)}")
    print(f"USER_KEY    : {USER_KEY[:8]}{'*' * 16}...")

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await fetch_identity(session)
        await fetch_portfolio(session, "demo")
        await fetch_portfolio(session, "real")

    print(f"\n{'─' * 60}")
    print(f"Abgeschlossen: {datetime.now().isoformat()}\n")


if __name__ == "__main__":
    asyncio.run(main())