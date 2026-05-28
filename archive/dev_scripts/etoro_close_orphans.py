"""
etoro_close_orphans.py — Schliesst alle offenen Positionen und Limit-Orders
für ein bestimmtes Instrument via direkter REST-Calls.

Usage:
    python3 dev_scripts/etoro_close_orphans.py [--symbol ADA.ETORO] [--dry-run]
"""

import argparse
import asyncio
import os
import sys
import uuid

import aiohttp
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from archive.adapters.instrument_map import ETORO_INSTRUMENTS

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

BASE_URL = "https://public-api.etoro.com/api/v1/trading"
PNL_URL = f"{BASE_URL}/info/real/pnl"
EXEC_URL = f"{BASE_URL}/execution"

HEADERS_BASE = {
    "x-api-key": API_KEY or "",
    "x-user-key": USER_KEY or "",
    "Content-Type": "application/json",
}


def make_headers() -> dict:
    return {**HEADERS_BASE, "x-request-id": str(uuid.uuid4())}


async def fetch_portfolio(session: aiohttp.ClientSession) -> dict:
    async with session.get(PNL_URL, headers=make_headers()) as resp:
        if resp.status != 200:
            raise RuntimeError(f"PnL fetch failed: HTTP {resp.status}")
        data = await resp.json()
        return data.get("clientPortfolio", data)


async def close_position(
    session: aiohttp.ClientSession,
    pos_id: int | str,
    etoro_id: int,
    dry_run: bool,
) -> bool:
    url = f"{EXEC_URL}/market-close-orders/positions/{pos_id}"
    payload = {"InstrumentID": etoro_id, "UnitsToDeduct": None}
    if dry_run:
        print(f"   [DRY-RUN] POST {url} | {payload}")
        return True
    async with session.post(url, json=payload, headers=make_headers()) as resp:
        if resp.status in range(200, 300):
            return True
        err = await resp.text()
        print(f"   ❌ HTTP {resp.status}: {err[:200]}")
        return False


async def cancel_limit_order(
    session: aiohttp.ClientSession,
    order_id: int | str,
    dry_run: bool,
) -> bool:
    url = f"{EXEC_URL}/limit-orders/{order_id}"
    if dry_run:
        print(f"   [DRY-RUN] DELETE {url}")
        return True
    async with session.delete(url, headers=make_headers()) as resp:
        if resp.status in range(200, 300) or resp.status == 404:
            return True
        err = await resp.text()
        print(f"   ❌ HTTP {resp.status}: {err[:200]}")
        return False


async def main(symbol: str, dry_run: bool) -> None:
    if not API_KEY or not USER_KEY:
        print("FEHLER: ETORO_API_KEY oder ETORO_USER_KEY fehlen.")
        sys.exit(1)

    etoro_id: int | None = None
    for k, v in ETORO_INSTRUMENTS.items():
        if v == symbol:
            etoro_id = int(k)
            break

    if etoro_id is None:
        print(f"FEHLER: Symbol '{symbol}' nicht in ETORO_INSTRUMENTS.")
        sys.exit(1)

    print(f"\n🔍 Suche offene Positionen und Orders für {symbol} (InstrumentID={etoro_id})...")
    if dry_run:
        print("   ⚠️  DRY-RUN — keine echten Requests werden gesendet.\n")

    async with aiohttp.ClientSession() as session:
        data = await fetch_portfolio(session)

        positions = data.get("positions", data.get("Positions", []))
        orders_for_open = (
            data.get("ordersForOpen", data.get("OrdersForOpen", []))
            + data.get("entryOrders", data.get("EntryOrders", []))
        )

        # Positionen filtern
        target_positions = [
            p for p in positions
            if str(p.get("instrumentID", p.get("InstrumentID", p.get("instrumentId", "")))) == str(etoro_id)
        ]

        # Limit-Orders filtern
        target_orders = [
            o for o in orders_for_open
            if str(o.get("instrumentID", o.get("InstrumentID", o.get("instrumentId", "")))) == str(etoro_id)
        ]

        print(f"\n📊 Gefunden: {len(target_positions)} Position(en), {len(target_orders)} Limit-Order(s)\n")

        # Positionen schliessen
        closed = 0
        for p in target_positions:
            pos_id = p.get("positionID", p.get("positionId", ""))
            units = p.get("units", p.get("Units", "?"))
            open_rate = p.get("openRate", p.get("OpenRate", "?"))
            is_settled = p.get("isSettled", False)
            settled_str = " [settled]" if is_settled else ""
            print(f"   -> Schliesse Position {pos_id}{settled_str} ({units} Units @ {open_rate})...")
            ok = await close_position(session, pos_id, etoro_id, dry_run)
            if ok:
                print(f"   ✅ Position {pos_id} geschlossen.")
                closed += 1
            else:
                print(f"   ❌ Position {pos_id} konnte nicht geschlossen werden.")

        # Limit-Orders stornieren
        canceled = 0
        for o in target_orders:
            ord_id = o.get("orderID", o.get("orderId", ""))
            rate = o.get("rate", o.get("Rate", "?"))
            print(f"   -> Storniere Limit-Order {ord_id} (@ {rate})...")
            ok = await cancel_limit_order(session, ord_id, dry_run)
            if ok:
                print(f"   ✅ Limit-Order {ord_id} storniert.")
                canceled += 1
            else:
                print(f"   ❌ Limit-Order {ord_id} konnte nicht storniert werden.")

        print(f"\n{'[DRY-RUN] ' if dry_run else ''}Abgeschlossen: {closed}/{len(target_positions)} Positionen geschlossen, "
              f"{canceled}/{len(target_orders)} Orders storniert.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Schliesst Orphan-Positionen bei eToro.")
    parser.add_argument("--symbol", default="ADA.ETORO", help="Nautilus-Symbol (z.B. ADA.ETORO)")
    parser.add_argument("--dry-run", action="store_true", help="Nur simulieren, keine echten Requests")
    parser.add_argument("--all-instruments", action="store_true",
                        help="Alle offenen Positionen aller Instrumente schliessen")
    args = parser.parse_args()

    if args.all_instruments:
        print("⚠️  --all-instruments noch nicht implementiert. Bitte --symbol verwenden.")
        sys.exit(1)

    asyncio.run(main(args.symbol, args.dry_run))