import asyncio
import aiohttp
import json
import os
import uuid
from dotenv import load_dotenv

# Konfiguration laden
load_dotenv()

API_KEY = os.getenv("ETORO_API_KEY", "")
USER_KEY = os.getenv("ETORO_USER_KEY", "")

# API-Endpunkt für Agent Portfolios
CREATE_URL = "https://public-api.etoro.com/api/v1/agent-portfolios"

async def create_agent_portfolio(name: str, amount: float):
    """
    Erstellt ein neues Agent Portfolio inkl. User-Token und weist Kapital zu.
    """
    headers = {
        "x-api-key": API_KEY,
        "x-user-key": USER_KEY,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

    # Payload exakt nach OpenAPI-Spezifikation
    payload = {
        "agentPortfolioName": name,         # Wichtig: Muss laut Doku 6-10 Zeichen lang sein!
        "investmentAmountInUsd": amount,
        "userTokenName": f"{name}-token",   # Das fehlende Pflichtfeld
        "scopeIds": [200, 202]              # Pflichtfeld: Lese- und Schreibrechte für Real-Trading
    }

    print(f"🚀 Erstelle Agent Portfolio '{name}' mit {amount} USD...")
    print(f"📦 Sende Payload: {json.dumps(payload)}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(CREATE_URL, headers=headers, json=payload) as resp:
                status = resp.status
                result = await resp.json()

                if 200 <= status < 300:
                    print(f"\n✅ Erfolg! HTTP {status}")
                    print(f"Agent Portfolio ID: {result.get('agentPortfolioId')}")
                    print(f"Virtuelle Balance : {result.get('agentPortfolioVirtualBalance')} USD")
                    print(f"Mirror ID         : {result.get('mirrorId')}")
                    
                    # Token-Daten ausgeben
                    tokens = result.get('userTokens', [])
                    if tokens:
                        print("\n🔑 NEUER TOKEN GENERIERT:")
                        print(f"Token Name : {tokens[0].get('userTokenName')}")
                        print(f"Token ID   : {tokens[0].get('userTokenId')}")
                        print(f"Secret     : {tokens[0].get('userToken')}")
                        print("⚠️ ACHTUNG: Speichere dieses Secret ('userToken') jetzt! Es wird nie wieder angezeigt.")
                    
                    print("-" * 50)
                else:
                    print(f"\n❌ Fehler! HTTP {status}")
                    print(f"Details: {json.dumps(result, indent=2)}")
        except Exception as e:
            print(f"❌ Request-Fehler: {str(e)}")

if __name__ == "__main__":
    if not API_KEY or not USER_KEY:
        print("❌ Bitte ETORO_API_KEY und ETORO_USER_KEY in der .env Datei setzen.")
    else:
        # Erstellt ein Portfolio. "TestUSD1" ist 8 Zeichen lang (erlaubt sind 6-10).
        asyncio.run(create_agent_portfolio("TestUSD1", 200.0))