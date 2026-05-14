import asyncio
import os
import sys
import uuid
import json
import aiohttp
from dotenv import load_dotenv

# Fügt das Hauptverzeichnis zum Path hinzu
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.setups import ETORO_API_TEST

# Lade Umgebungsvariablen
load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

async def fetch_transaction_history(
    api_key: str, 
    user_key: str, 
    environment: str, 
    limit: int = 50
) -> None:
    """
    Ruft die letzten Transaktionen/Trades von der eToro API ab.
    """
    
    base_url = "https://public-api.etoro.com/api/v1/trading"
    
    # Liste der möglichen eToro API Endpunkte für die Historie
    possible_endpoints = [
        f"{base_url}/info/trade/history",                # Offizieller Doc-Endpunkt
        f"{base_url}/info/{environment}/trade/history",  # Analog zum PnL-Endpunkt
        f"{base_url}/info/account/history"               # Fallback
    ]
    
    headers = {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    
    params = {
        "limit": limit
    }

    print(f"🔄 Rufe Transaktionshistorie ab (Environment: {environment.upper()})...")

    async with aiohttp.ClientSession() as session:
        success = False
        for url in possible_endpoints:
            try:
                # x-request-id für jeden Versuch neu generieren
                headers["x-request-id"] = str(uuid.uuid4())
                
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 404:
                        # Endpunkt existiert nicht, probiere den nächsten
                        continue
                    
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"❌ Fehler beim Abrufen von {url}: HTTP {response.status}")
                        print(f"Details: {error_text}")
                        return

                    # Wenn wir hier sind, war der Request erfolgreich (HTTP 200)
                    success = True
                    data = await response.json()
                    
                    # Extrahiere die Transaktionen
                    transactions = data.get("History", data.get("history", data))
                    
                    if not transactions or (isinstance(transactions, dict) and not transactions):
                        print(f"✅ Verbindung zu {url} erfolgreich, aber keine Transaktionen gefunden.")
                        print("Rohdaten der API:")
                        print(json.dumps(data, indent=2))
                        return

                    print(f"✅ Letzte {len(transactions)} Transaktionen erfolgreich via {url} geladen:\n")
                    
                    for tx in transactions:
                        tx_id = tx.get("PositionID", tx.get("positionId", tx.get("TradeID", "N/A")))
                        symbol = tx.get("InstrumentID", tx.get("instrumentId", "N/A"))
                        action = tx.get("Action", tx.get("action", tx.get("Type", "N/A")))
                        open_price = tx.get("OpenRate", tx.get("openRate", tx.get("OpenPrice", 0.0)))
                        close_price = tx.get("CloseRate", tx.get("closeRate", tx.get("ClosePrice", 0.0)))
                        profit = tx.get("NetProfit", tx.get("netProfit", tx.get("Profit", 0.0)))
                        
                        print(f"🔹 ID: {tx_id} | Symbol-ID: {symbol} | Aktion: {action}")
                        print(f"   Eröffnung: {open_price} | Schließung: {close_price} | PnL: {profit} USD")
                        print("-" * 50)
                    
                    break # Erfolgreich, Schleife abbrechen
                    
            except Exception as e:
                print(f"❌ Unerwarteter Fehler bei der Verbindung zu {url}: {e}")
        
        if not success:
            print("❌ Konnte die Transaktionshistorie über keinen der bekannten Endpunkte abrufen. (Alle gaben HTTP 404 zurück)")

async def main():
    if not API_KEY or not USER_KEY:
        print("FEHLER: ETORO_API_KEY oder ETORO_USER_KEY fehlen in der .env.")
        sys.exit(1)

    environment = ETORO_API_TEST.get("environment", "demo")
    
    await fetch_transaction_history(API_KEY, USER_KEY, environment, limit=20)

if __name__ == "__main__":
    asyncio.run(main())