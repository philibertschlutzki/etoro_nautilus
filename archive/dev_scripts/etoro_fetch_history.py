import asyncio
import os
import sys
import uuid
import json
import aiohttp
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Fügt das Hauptverzeichnis zum Path hinzu
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from archive.config.setups import ETORO_API_TEST

# Lade Umgebungsvariablen
load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

async def fetch_transaction_history(
    api_key: str, 
    user_key: str, 
    days_back: int = 30,
    page_size: int = 50
) -> None:
    """
    Ruft die letzten Transaktionen/Trades von der eToro API ab, basierend auf der offiziellen OpenAPI Spec.
    """
    
    # Exakter Endpunkt laut OpenAPI Dokumentation für das "Real" Environment
    url = "https://public-api.etoro.com/api/v1/trading/info/trade/history"
    
    headers = {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    
    # Berechne das minDate (Pflichtparameter laut Spec!)
    min_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    params = {
        "minDate": min_date,
        "pageSize": page_size,
        "page": 1
    }

    print(f"🔄 Rufe Transaktionshistorie ab (ab {min_date})...")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, params=params) as response:
                if response.status != 200:
                    error_text = await response.text()
                    print(f"❌ Fehler bei der API-Abfrage: HTTP {response.status}")
                    print(f"Details: {error_text}")
                    return

                # Laut Spec ist die Response direkt ein Array von Objekten
                transactions = await response.json()
                
                # Fallback, falls eToro es doch in ein Dictionary packt
                if isinstance(transactions, dict):
                    transactions = transactions.get("History", transactions.get("history", transactions))
                
                if not transactions or not isinstance(transactions, list):
                    print("ℹ️ Keine Transaktionen in diesem Zeitraum gefunden.")
                    return

                print(f"✅ {len(transactions)} Transaktionen erfolgreich geladen:\n")
                
                # Ausgabe der Transaktionen formatieren (Felder laut OpenAPI Spec)
                for tx in transactions:
                    tx_id = tx.get("positionId", "N/A")
                    symbol_id = tx.get("instrumentId", "N/A")
                    is_buy = tx.get("isBuy")
                    action = "BUY" if is_buy else "SELL" if is_buy is False else "N/A"
                    open_price = tx.get("openRate", 0.0)
                    close_price = tx.get("closeRate", 0.0)
                    profit = tx.get("netProfit", 0.0)
                    units = tx.get("units", 0.0)
                    close_time = tx.get("closeTimestamp", "N/A")
                    
                    print(f"🔹 ID: {tx_id} | Symbol-ID: {symbol_id} | Aktion: {action} ({units} Units)")
                    print(f"   Eröffnung: {open_price} | Schließung: {close_price} | PnL: {profit} USD")
                    print(f"   Geschlossen am: {close_time}")
                    print("-" * 60)
                    
        except Exception as e:
            print(f"❌ Unerwarteter Fehler bei der Verbindung: {e}")

async def main():
    if not API_KEY or not USER_KEY:
        print("FEHLER: ETORO_API_KEY oder ETORO_USER_KEY fehlen in der .env.")
        sys.exit(1)
    
    # Historie der letzten 7 Tage abrufen (maximal 50 Einträge)
    await fetch_transaction_history(API_KEY, USER_KEY, days_back=7, page_size=50)

if __name__ == "__main__":
    asyncio.run(main())