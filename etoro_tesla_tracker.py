import asyncio
import websockets
import json
import uuid
import os
import ssl
from dotenv import load_dotenv

# Lade Umgebungsvariablen
load_dotenv()

USER_KEY = os.getenv("ETORO_USER_KEY")
API_KEY = os.getenv("ETORO_API_KEY")
WS_URL = "wss://ws.etoro.com/ws"
INSTRUMENT_ID = "1"

async def etoro_tesla_tracker():
    if not USER_KEY or not API_KEY:
        print("Fehler: USER_KEY oder API_KEY fehlen in der .env Datei.")
        return

    # SSL Kontext (falls Zertifikatsprobleme bestehen bleiben)
    ssl_context = ssl.create_default_context()
    # Falls Weg 1 aus der vorigen Nachricht nicht geht, entkommentiere die nächsten 2 Zeilen:
    # ssl_context.check_hostname = False
    # ssl_context.verify_mode = ssl.CERT_NONE

    try:
        async with websockets.connect(WS_URL, ssl=ssl_context) as websocket:
            print(f"Verbunden mit {WS_URL}")

            # 1. Authentifizierung
            auth_request = {
                "id": str(uuid.uuid4()),
                "operation": "Authenticate",
                "data": {
                    "userKey": USER_KEY,
                    "apiKey": API_KEY
                }
            }
            await websocket.send(json.dumps(auth_request))
            print("Authentifizierungsanfrage gesendet...")

            # 2. Abonnement
            subscribe_request = {
                "id": str(uuid.uuid4()),
                "operation": "Subscribe",
                "data": {
                    "topics": [f"instrument:{INSTRUMENT_ID}"],
                    "snapshot": True
                }
            }
            
            await asyncio.sleep(1) 
            await websocket.send(json.dumps(subscribe_request))
            print(f"Abonnement für Instrument {INSTRUMENT_ID} gesendet...")

            # Nachrichten-Loop
            while True:
                response = await websocket.recv()
                
                if not response:
                    print("Leere Nachricht erhalten, überspringe...")
                    continue

                try:
                    data = json.loads(response)
                    print("Empfangene Daten:")
                    print(json.dumps(data, indent=4))
                except json.JSONDecodeError:
                    print(f"Server antwortete mit keinem validen JSON: {response}")

    except websockets.exceptions.ConnectionClosed as e:
        print(f"Verbindung wurde vom Server geschlossen: {e}")
    except Exception as e:
        print(f"Ein Fehler ist aufgetreten: {e}")

if __name__ == "__main__":
    asyncio.run(etoro_tesla_tracker())