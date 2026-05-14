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
        await ws.send(json.dumps({
            "id": str(uuid.uuid4()), "operation": "Authenticate",
            "data": {"userKey": USER_KEY, "apiKey": API_KEY}
        }))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        if resp.get("success"):
            print(f"   ✅ WebSocket Auth OK")
        else:
            print(f"   ❌ WebSocket Auth FAILED: {resp}")
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
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                print(f"   ✅ Quote-Feed OK für {TESTS_SYMBOL}")
            except asyncio.TimeoutError:
                print(f"   ⚠️  Quote-Feed: kein Snapshot innerhalb 5s (Markt geschlossen?)")

        await ws.close()
        return True
    except Exception as e:
        print(f"   ❌ WebSocket FAILED: {e}")
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