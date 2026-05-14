"""
etoro_portfolio_monitor.py — Live-Monitor für eToro Portfolio.
Aktualisiert alle 30s. Ctrl+C zum Beenden.

Usage:
    python3 dev_scripts/etoro_portfolio_monitor.py [--interval 30]
"""

import argparse, asyncio, os, sys, uuid
from datetime import datetime
import aiohttp
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters.instrument_map import ETORO_INSTRUMENTS

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

# Reverse-Map: InstrumentID → Symbol
ID_TO_SYMBOL = {str(k): v for k, v in ETORO_INSTRUMENTS.items()}


async def fetch_and_display(session: aiohttp.ClientSession, args) -> None:
    url = "https://public-api.etoro.com/api/v1/trading/info/real/pnl"
    headers = {"x-api-key": API_KEY, "x-user-key": USER_KEY,
               "x-request-id": str(uuid.uuid4())}

    async with session.get(url, headers=headers) as resp:
        if resp.status != 200:
            print(f"⚠️ PnL fetch failed: HTTP {resp.status}")
            return
        data = await resp.json()
        data = data.get("clientPortfolio", data)

    credit = float(data.get("credit", 0) or 0)
    unrealized = float(data.get("unrealizedPnL", 0) or 0)
    positions = data.get("positions", [])
    entry_orders = data.get("entryOrders", []) + data.get("ordersForOpen", [])

    # Header
    print(f"\033[H\033[J", end="")  # Clear screen
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{'='*70}")
    print(f"  eToro Portfolio Monitor  |  {now}")
    print(f"{'='*70}")
    print(f"  Credit: {credit:>10.2f} USD    Unrealized PnL: {unrealized:>+10.4f} USD")
    print(f"{'='*70}")

    # Positionen
    if positions:
        print(f"\n  📊 OFFENE POSITIONEN ({len(positions)}):")
        print(f"  {'Symbol':<12} {'Side':<6} {'Units':>10} {'Open@':>10} {'Curr@':>10} {'PnL':>10} {'Settled'}")
        print(f"  {'-'*70}")
        for p in positions:
            instr_id = str(p.get("instrumentID", ""))
            symbol = ID_TO_SYMBOL.get(instr_id, f"ID:{instr_id}")[:11]
            side = "LONG" if p.get("isBuy", True) else "SHORT"
            units = float(p.get("units", 0))
            open_rate = float(p.get("openRate", 0))
            curr_rate = float(p.get("currentRate", 0) or 0)
            pnl_pct = ((curr_rate / open_rate) - 1) * 100 if open_rate else 0
            settled = "✓" if p.get("isSettled") else " "
            print(f"  {symbol:<12} {side:<6} {units:>10.4f} {open_rate:>10.5f} "
                  f"{curr_rate:>10.5f} {pnl_pct:>+9.2f}% {settled:^8}")
    else:
        print(f"\n  📊 Keine offenen Positionen.")

    # Limit-Orders
    if entry_orders:
        print(f"\n  📋 OFFENE LIMIT-ORDERS ({len(entry_orders)}):")
        print(f"  {'Symbol':<12} {'Rate':>10} {'Amount':>10} {'OrderID'}")
        print(f"  {'-'*50}")
        for o in entry_orders:
            instr_id = str(o.get("instrumentID", ""))
            symbol = ID_TO_SYMBOL.get(instr_id, f"ID:{instr_id}")[:11]
            rate = float(o.get("rate", o.get("Rate", 0)))
            amount = float(o.get("amount", o.get("Amount", 0)))
            order_id = o.get("orderID", o.get("orderId", "?"))
            print(f"  {symbol:<12} {rate:>10.5f} {amount:>10.2f} USD  {order_id}")
    else:
        print(f"\n  📋 Keine offenen Limit-Orders.")

    print(f"\n{'='*70}")
    print(f"  Nächste Aktualisierung in {args.interval}s...  (Ctrl+C zum Beenden)")


async def main(args):
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await fetch_and_display(session, args)
                await asyncio.sleep(args.interval)
            except KeyboardInterrupt:
                print("\n\nMonitor beendet.")
                break
            except Exception as e:
                print(f"\n⚠️ Fehler: {e}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=30, help="Update-Interval in Sekunden")
    args = parser.parse_args()
    asyncio.run(main(args))