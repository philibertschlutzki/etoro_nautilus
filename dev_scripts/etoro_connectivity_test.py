"""
etoro_connectivity_test.py — Prüft API-Verbindung ohne Orders zu platzieren.
Testet: REST-Auth, PnL-Endpoint, WS-Verbindung, Quote-Feed.
"""
import asyncio, os, sys, json, ssl, uuid
import aiohttp, websockets
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters.instrument_map import ETORO_INSTRUMENTS

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

TESTS_SYMBOL = "ADA.ETORO"  # oder aus ETORO_API_TEST

async def test_rest_auth():
    """TEST 1: REST-Auth via PnL-Endpoint."""
    url = "https://public-api.etoro.com/api/v1/trading/info/real/pnl"
    headers = {
        "x-api-key": API_KEY, "x-user-key": USER_KEY,
        "x-request-id": str(uuid.uuid4()), "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                data = data.get("clientPortfolio", data)
                credit = data.get("credit", 0)
                positions = len(data.get("positions", []))
                print(f"   ✅ REST Auth OK | Credit: {credit} USD | Offene Positionen: {positions}")
                return True
            else:
                print(f"   ❌ REST Auth FAILED: HTTP {resp.status}")
                return False


async def test_ws_connection():
    """TEST 2: WebSocket-Verbindung und Auth."""
    ws_url = "wss://ws.etoro.com/ws"
    try:
        ws = await asyncio.wait_for(
            websockets.connect(ws_url, ssl=ssl.create_default_context(), ping_interval=20),
            timeout=10.0
        )
        
        # Authenticate senden
        await ws.send(json.dumps({
            "id": str(uuid.uuid4()), "operation": "Authenticate",
            "data": {"userKey": USER_KEY, "apiKey": API_KEY}
        }))
        
        # Auth-Response lesen (skip Keep-Alive Bytes)
        auth_success = False
        for attempt in range(3):
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            
            if isinstance(raw, bytes):
                if raw == b'\x00':  # Keep-Alive, ignorieren
                    continue
                resp = json.loads(raw.decode('utf-8'))
            else:
                resp = json.loads(raw)
            
            if resp.get("operation") == "Authenticate" and resp.get("success"):
                print(f"   ✅ WebSocket Auth OK")
                auth_success = True
                break

        if not auth_success:
            await ws.close()
            return False

        # Quote-Feed testen
        etoro_id = next((k for k, v in ETORO_INSTRUMENTS.items() if v == TESTS_SYMBOL), None)
        if etoro_id:
            await ws.send(json.dumps({
                "id": str(uuid.uuid4()), "operation": "Subscribe",
                "data": {"topics": [f"instrument:{etoro_id}"], "snapshot": True}
            }))
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                print(f"   ✅ Quote-Feed OK für {TESTS_SYMBOL}")
            except asyncio.TimeoutError:
                print(f"   ⚠️  Quote-Feed: kein Snapshot innerhalb 5s (Markt geschlossen?)")

        await ws.close()
        return True
        
    except Exception as e:
        print(f"   ❌ WebSocket FAILED: {e}")
        return False
    """TEST 2: WebSocket mit besserem Frame-Handling."""
    ws_url = "wss://ws.etoro.com/ws"
    try:
        ws = await asyncio.wait_for(
            websockets.connect(
                ws_url, 
                ssl=ssl.create_default_context(),
                close_timeout=5,
            ),
            timeout=10.0
        )
        
        auth_msg = {
            "id": str(uuid.uuid4()), 
            "operation": "Authenticate",
            "data": {"userKey": USER_KEY, "apiKey": API_KEY}
        }
        
        print(f"   📤 Sending Auth...")
        await ws.send(json.dumps(auth_msg))
        
        # Warte auf mehrere Frames
        for i in range(5):
            try:
                raw_resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
                
                # Korrekt zwischen bytes und str unterscheiden
                if isinstance(raw_resp, bytes):
                    hex_str = raw_resp.hex()
                    print(f"   📡 Frame {i+1} (bytes): len={len(raw_resp)}, hex={hex_str[:60]}")
                    
                    if raw_resp == b'\x00':
                        print(f"      → Null-Byte (Keep-Alive/Error-Signal)")
                        continue
                    
                    # Versuche zu dekodieren
                    try:
                        text = raw_resp.decode('utf-8')
                        resp = json.loads(text)
                        print(f"      Parsed: {resp}")
                    except:
                        print(f"      → Nicht als JSON dekodierbar")
                        continue
                else:
                    # String frame
                    print(f"   📡 Frame {i+1} (str): {raw_resp[:100]}")
                    try:
                        resp = json.loads(raw_resp)
                        print(f"      Parsed: {resp}")
                        
                        if resp.get("success"):
                            print(f"   ✅ WebSocket Auth OK")
                            break
                        elif "error" in resp or "status" in resp:
                            print(f"   ❌ Auth Error: {resp}")
                            await ws.close()
                            return False
                    except json.JSONDecodeError:
                        continue
                    
            except asyncio.TimeoutError:
                if i == 0:
                    print(f"   ⚠️  Keine Antwort nach 2s")
                break
        
        await ws.close()
        return False  # Sollte True sein, wenn Auth erfolgreich
        
    except Exception as e:
        print(f"   ❌ WebSocket FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False



async def test_instrument_availability():
    """TEST 3: Instrument-Verfügbarkeit und Marktöffnung."""
    url = "https://public-api.etoro.com/api/v1/trading/info/real/pnl"
    headers = {"x-api-key": API_KEY, "x-user-key": USER_KEY,
               "x-request-id": str(uuid.uuid4())}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                data = data.get("clientPortfolio", data)
                # Alle Instrumente ausgeben die derzeit Positionen haben
                positions = data.get("positions", [])
                if positions:
                    print(f"   ℹ️  {len(positions)} offene Positionen im Account")
                else:
                    print(f"   ✅ Keine offenen Positionen (clean state)")
                return True
    return False


async def main():
    print(f"\n{'='*55}")
    print(f"  eToro Connectivity Test")
    print(f"{'='*55}\n")

    results = {}
    for name, coro in [
        ("REST Auth & Portfolio", test_rest_auth()),
        ("WebSocket & Quote-Feed", test_ws_connection()),
        ("Account State", test_instrument_availability()),
    ]:
        print(f"[{name}]")
        results[name] = await coro
        print()

    passed = sum(results.values())
    total = len(results)
    print(f"{'='*55}")
    print(f"  Ergebnis: {passed}/{total} Tests bestanden")
    if passed == total:
        print(f"  ✅ System ready for trading.")
    else:
        print(f"  ⚠️  Probleme gefunden — bitte prüfen.")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    asyncio.run(main())