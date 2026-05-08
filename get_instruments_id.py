import os
import requests
import uuid
from dotenv import load_dotenv

# Lade die Keys aus der .env Datei
load_dotenv()

API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

def get_etoro_instrument_id(symbol: str):
    if not API_KEY or not USER_KEY:
        print("Fehler: API_KEY oder USER_KEY konnten nicht aus der .env geladen werden.")
        return

    url = "https://public-api.etoro.com/api/v1/market-data/search"
    
    params = {
        "internalSymbolFull": symbol
    }
    
    headers = {
        "x-api-key": API_KEY,
        "x-user-key": USER_KEY,
        "x-request-id": str(uuid.uuid4())
    }

    print(f"Suche nach Instrumenten-ID für: {symbol} ...")
    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        data = response.json()
        
        # Finde den exakten Match in der zurückgegebenen Liste
        items = data.get('items', [])
        instrument = next((item for item in items if item.get('internalSymbolFull') == symbol), None)
        
        if instrument:
            instr_id = instrument['instrumentId']
            print(f"✅ Erfolg! Die eToro Instrument ID für {symbol} ist: {instr_id}")
            print(f"-> Trage diese ID in deine adapters/etoro_data.py und Tracker ein.")
        else:
            print(f"❌ Instrument '{symbol}' wurde in den Suchergebnissen nicht gefunden.")
    else:
        print(f"❌ Fehler bei der API-Anfrage: HTTP {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    # Suche speziell nach Tesla
    get_etoro_instrument_id("TSLA")