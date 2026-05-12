import asyncio
import aiohttp
import json
import os
import uuid
from datetime import datetime
from dotenv import load_dotenv

# Konfiguration laden
load_dotenv()

API_KEY = os.getenv("ETORO_API_KEY", "")
USER_KEY = os.getenv("ETORO_USER_KEY", "")

# Der von dir gewünschte Endpoint
PORTFOLIO_URL = "https://public-api.etoro.com/api/v1/trading/info/portfolio"

async def fetch_portfolio():
    headers = {
        "x-api-key": API_KEY,
        "x-user-key": USER_KEY,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

    print(f"🔍 Rufe Portfolio ab...")
    print(f"Zeitstempel: {datetime.now().isoformat()}")
    print("-" * 50)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(PORTFOLIO_URL, headers=headers) as resp:
                status = resp.status
                data = await resp.json()

                if status == 200:
                    print(f"✅ Erfolg! (HTTP {status})")
                    
                    # Extrahiere Kern-Informationen (Struktur kann je nach Token variieren)
                    portfolio = data.get("clientPortfolio", data)
                    
                    # Zusammenfassung anzeigen
                    print(f"\n💰 KONTO-ÜBERSICHT:")
                    print(f"Verfügbares Cash : {portfolio.get('availableCash', 0.0):.2f} USD")
                    print(f"Gesamt-Equity    : {portfolio.get('equity', 0.0):.2f} USD")
                    print(f"Offener PnL      : {portfolio.get('unrealizedPnL', 0.0):.2f} USD")
                    
                    # Positionen auflisten
                    positions = portfolio.get("positions", [])
                    print(f"\n📊 OFFENE POSITIONEN ({len(positions)}):")
                    
                    if not positions:
                        print("  (Keine offenen Positionen gefunden)")
                    else:
                        print(f"{'Asset ID':<10} | {'Units':<10} | {'Buy Price':<12} | {'PnL (%)':<10}")
                        print("-" * 50)
                        for pos in positions:
                            asset_id = pos.get("instrumentId", "N/A")
                            units = pos.get("units", 0.0)
                            avg_price = pos.get("averagePrice", 0.0)
                            pnl_pct = pos.get("netProfitLossPercentage", 0.0)
                            print(f"{asset_id:<10} | {units:<10.4f} | {avg_price:<12.4f} | {pnl_pct:<10.2f}%")
                    
                    print("\nFull JSON Response (gekürzt):")
                    print(json.dumps(data, indent=2)[:1000] + "...")
                
                elif status == 404:
                    print(f"❌ Fehler 404: Endpoint nicht gefunden.")
                    print("Hinweis: Versuche es mit 'https://public-api.etoro.com/api/v1/trading/info/real/portfolio' falls dieser fehlschlägt.")
                else:
                    print(f"❌ Fehler! HTTP {status}")
                    print(json.dumps(data, indent=2))

        except Exception as e:
            print(f"❌ Kritischer Fehler: {str(e)}")

if __name__ == "__main__":
    if not API_KEY or not USER_KEY:
        print("❌ API_KEY oder USER_KEY fehlen in der .env")
    else:
        asyncio.run(fetch_portfolio())