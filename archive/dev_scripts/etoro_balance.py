"""
Kombinierter eToro Portfolio Monitor
====================================
Live-Monitor für das eToro Portfolio (Real oder Demo). 
Zeigt Kontostand, Agent Portfolios (Mirrors), Offene Positionen und Limit-Orders an.
Aktualisiert automatisch. Ctrl+C zum Beenden.

Usage:
    python3 dev_scripts/etoro_combined_monitor.py [--interval 30] [--env real]
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
import traceback
from datetime import datetime

import aiohttp
from dotenv import load_dotenv

# Pfad für eigene Module (wie adapters) hinzufügen
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Versuche Instrument-Mapping zu laden
try:
    from archive.adapters.instrument_map import ETORO_INSTRUMENTS
    ID_TO_SYMBOL = {str(k): v for k, v in ETORO_INSTRUMENTS.items()}
except ImportError:
    print("⚠️ Warnung: adapters.instrument_map konnte nicht geladen werden. IDs werden verwendet.")
    ID_TO_SYMBOL = {}

load_dotenv()

API_KEY  = os.getenv("ETORO_API_KEY",  "")
USER_KEY = os.getenv("ETORO_USER_KEY", "")

if not API_KEY or not USER_KEY:
    raise SystemExit("❌ ETORO_API_KEY oder ETORO_USER_KEY fehlen in der .env")

# Farben für die Ausgabe
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
CYAN    = "\033[96m"
MAGENTA = "\033[95m"
BOLD    = "\033[1m"
RESET   = "\033[0m"

IDENTITY_URL = "https://public-api.etoro.com/api/v1/me"
PNL_ENDPOINTS = {
    "demo": "https://public-api.etoro.com/api/v1/trading/info/demo/pnl",
    "real": "https://public-api.etoro.com/api/v1/trading/info/real/pnl",
}

def _headers() -> dict:
    return {
        "x-api-key":    API_KEY,
        "x-user-key":   USER_KEY,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

def _calculate_available_cash(port: dict) -> float:
    """Berechnet verfügbares Cash gemäss offizieller eToro-Formel."""
    credit = float(port.get("credits", port.get("credit", 0)))
    orders_for_open = port.get("ordersForOpen", [])
    orders          = port.get("orders", [])

    manual_open_amount = sum(
        float(o.get("amount", 0))
        for o in orders_for_open
        if o.get("mirrorID", 0) == 0
    )
    pending_orders_amount = sum(float(o.get("amount", 0)) for o in orders)

    return credit - (manual_open_amount + pending_orders_amount)

async def fetch_identity(session: aiohttp.ClientSession) -> dict:
    """Holt die User-Identity einmalig beim Start."""
    try:
        async with session.get(IDENTITY_URL, headers=_headers()) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception:
        pass
    return {}

async def fetch_and_display(session: aiohttp.ClientSession, args, identity_data: dict) -> None:
    url = PNL_ENDPOINTS[args.env]
    
    async with session.get(url, headers=_headers()) as resp:
        status = resp.status
        body_text = await resp.text()

        # Bildschirm löschen & Header drucken
        print(f"\033[H\033[J", end="")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{BOLD}{'='*80}{RESET}")
        print(f"{BOLD} 🚀 eToro Portfolio Monitor | {CYAN}{args.env.upper()}-Konto{RESET}{BOLD} | {now} {RESET}")
        print(f"{BOLD}{'='*80}{RESET}")

        # Identity anzeigen
        gcid = identity_data.get('gcid', 'n/a')
        real_cid = identity_data.get('realCid', 'n/a')
        demo_cid = identity_data.get('demoCid', 'n/a')
        print(f" 👤 User Identity : GCID={gcid} | Real-CID={real_cid} | Demo-CID={demo_cid}")
        print(f"{'-'*80}")

        if status != 200:
            print(f"\n ⚠️ {RED}Fehler beim Abruf der Portfolio-Daten (HTTP {status}){RESET}")
            print(f" {body_text[:300]}")
            print(f"\n{'-'*80}\n Nächste Aktualisierung in {args.interval}s... (Ctrl+C zum Beenden)")
            return

        data = json.loads(body_text)
        port = data.get("clientPortfolio", data)

        # ── 1. Kontostand ──────────────────────────────────────────────────────────
        credit        = float(port.get("credits", port.get("credit", 0)))
        available     = _calculate_available_cash(port)
        realized_pnl  = float(port.get("totalRealizedEquity", port.get("realizedPnL", 0)))
        unrealized    = float(port.get("totalUnrealizedEquity", port.get("unrealizedPnL", 0)))
        
        # Fallback-Berechnung für Equity
        equity_raw    = port.get("equity", port.get("netEquity", None))
        if equity_raw is not None:
            equity = float(equity_raw)
        else:
            equity = credit + unrealized 

        print(f"\n {BOLD}💰 KONTO-ÜBERSICHT{RESET}")
        print(f" Credit (Gesamt)    : {credit:>12.2f} USD")
        print(f" Verfügbares Cash   : {available:>12.2f} USD")
        print(f" Gesamt-Equity      : {equity:>12.2f} USD")
        print(f" Realisierter PnL   : {realized_pnl:>12.2f} USD")
        
        unrealized_color = GREEN if unrealized >= 0 else RED
        print(f" Offener PnL        : {unrealized_color}{unrealized:>+12.2f} USD{RESET}")

        # ── 2. Agent Portfolios (Mirrors) ──────────────────────────────────────────
        mirrors = port.get("mirrors", [])
        if mirrors:
            print(f"\n {MAGENTA}{BOLD}🤖 AGENT PORTFOLIOS ({len(mirrors)}){RESET}")
            for m in mirrors:
                m_name = m.get('parentUsername', 'Unknown')
                m_avail = float(m.get('availableAmount', 0))
                m_pnl = float(m.get('closedPositionsNetProfit', 0))
                print(f" ► {m_name:<15} | Cash: {m_avail:>8.2f} USD | PnL: {m_pnl:>+8.2f} USD")

        # ── 3. Offene Positionen ───────────────────────────────────────────────────
        positions = port.get("positions", [])
        mirror_positions = []
        for m in mirrors:
            mirror_positions.extend(m.get("positions", []))
        all_positions = positions + mirror_positions

        print(f"\n {BOLD}📊 OFFENE POSITIONEN ({len(all_positions)}){RESET}")
        if all_positions:
            print(f" {'Symbol':<12} {'Side':<6} {'Units':>10} {'Open@':>10} {'Curr@':>10} {'PnL %':>10} {'Settled'}")
            print(f" {'-'*80}")
            for p in all_positions:
                instr_id = str(p.get("instrumentID", p.get("InstrumentId", "")))
                symbol = ID_TO_SYMBOL.get(instr_id, f"ID:{instr_id}")[:11]
                side = "LONG" if p.get("isBuy", True) else "SHORT"
                units = float(p.get("units", p.get("amount", 0)))
                open_rate = float(p.get("openRate", p.get("openPrice", 0)))
                curr_rate = float(p.get("currentRate", p.get("currentPrice", 0)) or 0)
                
                # PnL %
                api_pnl_pct = p.get("netProfitLossPercentage")
                if api_pnl_pct is not None:
                    pnl_pct = float(api_pnl_pct)
                else:
                    if open_rate > 0:
                        pnl_pct = ((curr_rate / open_rate) - 1) * 100 if side == "LONG" else ((open_rate / curr_rate) - 1) * 100
                    else:
                        pnl_pct = 0.0

                settled = "✓" if p.get("isSettled") else " "
                color = GREEN if pnl_pct >= 0 else RED
                
                print(f" {symbol:<12} {side:<6} {units:>10.4f} {open_rate:>10.5f} {curr_rate:>10.5f} {color}{pnl_pct:>+9.2f}%{RESET} {settled:^8}")
        else:
            print("  (Keine offenen Positionen gefunden)")

        # ── 4. Limit Orders ────────────────────────────────────────────────────────
        orders_for_open = port.get("ordersForOpen", [])
        entry_orders = data.get("entryOrders", []) 
        pending = [o for o in (orders_for_open + entry_orders) if o.get("mirrorID", 0) == 0]
        
        # Deduplizieren, falls eToro sie in beiden Arrays liefert
        unique_pending = {o.get("orderID", o.get("orderId")): o for o in pending}.values()

        if unique_pending:
            print(f"\n {BOLD}📋 OFFENE LIMIT-ORDERS ({len(unique_pending)}){RESET}")
            print(f" {'Symbol':<12} {'Rate':>10} {'Amount':>10} {'OrderID'}")
            print(f" {'-'*80}")
            for o in unique_pending:
                instr_id = str(o.get("instrumentID", ""))
                symbol = ID_TO_SYMBOL.get(instr_id, f"ID:{instr_id}")[:11]
                rate = float(o.get("rate", o.get("Rate", 0)))
                amount = float(o.get("amount", o.get("Amount", 0)))
                order_id = o.get("orderID", o.get("orderId", "?"))
                print(f" {symbol:<12} {rate:>10.5f} {amount:>10.2f} USD  {order_id}")

        print(f"\n{'-'*80}")
        print(f" Nächste Aktualisierung in {args.interval}s... (Ctrl+C zum Beenden)")


async def main(args):
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        identity_data = await fetch_identity(session)
        
        while True:
            try:
                await fetch_and_display(session, args, identity_data)
                await asyncio.sleep(args.interval)
            except asyncio.CancelledError:
                # Wird durch Ctrl+C von asyncio getriggert
                break
            except Exception as e:
                print(f"\n{RED}⚠️ Kritischer Fehler: {e}{RESET}")
                print(traceback.format_exc())
                await asyncio.sleep(5)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kombinierter eToro Portfolio Monitor")
    parser.add_argument("--interval", type=int, default=30, help="Update-Intervall in Sekunden (Default: 30)")
    parser.add_argument("--env", type=str, choices=["real", "demo"], default="real", help="Umgebung: 'real' oder 'demo' (Default: real)")
    args = parser.parse_args()
    
    # Sicherer Graceful-Shutdown-Block
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Monitor durch Benutzer beendet (Graceful Shutdown).{RESET}")
        sys.exit(0)