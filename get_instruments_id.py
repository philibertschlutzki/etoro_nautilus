import os
import requests
import uuid
from dotenv import load_dotenv

# Lade die Keys aus der .env Datei
load_dotenv()

API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")


def get_etoro_instrument_id(symbol: str) -> dict | None:
    """
    Sucht die eToro Instrument-ID für ein einzelnes Symbol.
    Gibt ein Dict mit 'symbol' und 'instrumentId' zurück, oder None bei Fehler.
    """
    if not API_KEY or not USER_KEY:
        print("❌ Fehler: API_KEY oder USER_KEY konnten nicht aus der .env geladen werden.")
        return None

    url = "https://public-api.etoro.com/api/v1/market-data/search"

    params = {"internalSymbolFull": symbol}
    headers = {
        "x-api-key": API_KEY,
        "x-user-key": USER_KEY,
        "x-request-id": str(uuid.uuid4()),
    }

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        items = response.json().get("items", [])
        instrument = next(
            (item for item in items if item.get("internalSymbolFull") == symbol), None
        )
        if instrument:
            return {"symbol": symbol, "instrumentId": str(instrument["instrumentId"])}
        else:
            return {"symbol": symbol, "instrumentId": None, "error": "Nicht gefunden"}
    else:
        return {
            "symbol": symbol,
            "instrumentId": None,
            "error": f"HTTP {response.status_code}",
        }


def get_multiple_instrument_ids(symbols: list[str]) -> None:
    """
    Sucht die eToro Instrument-IDs für eine Liste von Symbolen und gibt
    die Ergebnisse formatiert aus – inklusive einem fertigen Code-Snippet
    für adapters/instrument_map.py.
    """
    print(f"\n🔍 Starte Batch-Suche für {len(symbols)} Symbole ...\n")
    print("-" * 50)

    results = []
    for symbol in symbols:
        print(f"  Suche: {symbol} ...", end="", flush=True)
        result = get_etoro_instrument_id(symbol)
        results.append(result)
        if result and result["instrumentId"]:
            print(f" ✅  ID = {result['instrumentId']}")
        else:
            error = result.get("error", "Unbekannter Fehler") if result else "Kein Ergebnis"
            print(f" ❌  {error}")

    # ── Zusammenfassung ──────────────────────────────────────────────────────
    successful = [r for r in results if r and r["instrumentId"]]
    failed = [r for r in results if not r or not r["instrumentId"]]

    print("\n" + "=" * 50)
    print(f"  Ergebnis: {len(successful)}/{len(symbols)} Symbole gefunden")
    print("=" * 50)

    if failed:
        print("\n⚠️  Nicht gefunden:")
        for r in failed:
            print(f"     - {r['symbol']}: {r.get('error', '?')}")

    if successful:
        print("\n📋  Fertige Einträge für adapters/instrument_map.py:")
        print("-" * 50)
        print("ETORO_INSTRUMENTS = {")
        for r in successful:
            print(f'    "{r["instrumentId"]}": "{r["symbol"]}.ETORO",')
        print("}")
        print("-" * 50)
        print()


if __name__ == "__main__":
    # ── Symbole die abgefragt werden sollen ─────────────────────────────────
    SYMBOLS_TO_LOOKUP = [
        "TSLA",   # Tesla
        "HUT",    # Hut 8 Corp
        "RIOT",   # Riot Platforms
        "NVDA",   # Nvidia
        "FSLY",   # Fastly
        "INSM",   # Insmed
        "BTC",   # Bitcoin
        "ETH",   # Ethereum
        "ADA",   # Cardano
        "XRP",   # Ripple
        "AERO",  # Aeodrome Finance
        "HYPE",  # Hyperliquid
        "ONDO",  # Ondo Finance
        "01211.HK",    # BYD
        "XPEV",     # XPeng
        "PLTR",     # Palantir
        "RHM.DE",   # Rheinmetall
        "NVS",  # Novartis
        "ROP.ZU",   # Roche
        "DOGE",   #DOGE
        "SHIBxM",  #SHIBA
        "PEPExM", #PEPE
        "SOL",    #SOLANA
        "AVAX",   #Avalanche
        "NATGAS", # Erdgas 
        "PALL",   # Palladium
        "USDTRY", # USD/TRY
        "USDZAR", # USD/ZAR
    ]

    get_multiple_instrument_ids(SYMBOLS_TO_LOOKUP)
