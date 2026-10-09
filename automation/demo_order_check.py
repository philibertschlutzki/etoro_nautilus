"""End-to-End-Prüfung der Demo-API (10k-USD-Paper-Trading-Konto) mit den Keys aus ``.env``.

Fest auf das Paper-Konto verdrahtet: Demo-Endpunkte, oder — nur mit ``ETORO_PAPER_ACCOUNT_CID`` und passender ``realCid``
aus ``/me`` (account_guard) — die Real-Endpunkte eines Spielgeld-Kontos. ``ETORO_ENV`` != demo bricht ab.

Stufen:
 1. ``python -m automation.demo_order_check``                    nur lesend: Keys, PnL-/Portfolio-Endpunkt, Guthaben.
 2. ``python -m automation.demo_order_check --place-test-order``  öffnet eine kleine Long-Position (Default BTC,
    50 USD, 24/7 handelbar), wartet bis sie im Portfolio erscheint, schliesst sie wieder und wartet, bis sie weg ist.

Exit-Code 0 nur, wenn alle angeforderten Stufen funktioniert haben; eine geöffnete Test-Position wird auch bei
einem Fehler zu schliessen versucht (``finally``)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from automation.papertrading import PaperTradingError, assert_demo_environment

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BASES = {
    "demo": ("https://public-api.etoro.com/api/v1/trading/execution/demo",
             "https://public-api.etoro.com/api/v1/trading/info/demo/pnl"),
    # Nur für ein per Konto-ID angeheftetes Paper-Konto (account_guard); ohne Anheftung nie benutzt.
    "real": ("https://public-api.etoro.com/api/v1/trading/execution",
             "https://public-api.etoro.com/api/v1/trading/info/real/pnl"),
}
REST_BASE, PNL_URL = _BASES["demo"]


def load_instrument_id(symbol: str, map_path: Path | None = None) -> int:
    data = json.loads((map_path or PROJECT_ROOT / "automation" / "config" / "instrument_map.json").read_text("utf-8"))
    for key, spec in data["instruments"].items():
        if spec.get("symbol") == symbol:
            return int(key)
    raise PaperTradingError(f"Symbol {symbol!r} steht nicht in instrument_map.json.")


def headers(api_key: str, user_key: str) -> dict[str, str]:
    return {"x-api-key": api_key, "x-user-key": user_key, "x-request-id": str(uuid.uuid4()),
            "Content-Type": "application/json"}


def open_payload(instrument_id: int, amount_usd: float) -> dict:
    return {"InstrumentID": instrument_id, "IsBuy": True, "Leverage": 1, "IsNoStopLoss": True,
            "IsNoTakeProfit": True, "Amount": round(float(amount_usd), 2)}


def close_payload(instrument_id: int) -> dict:
    return {"InstrumentID": instrument_id, "UnitsToDeduct": None}


def open_positions(pnl: dict) -> dict[str, int]:
    """``{position_id: instrument_id}`` aus einer PnL-Antwort (dieselben Feld-Fallbacks wie der Adapter)."""
    data = pnl.get("clientPortfolio", pnl)
    out: dict[str, int] = {}
    for item in data.get("Positions", data.get("positions", [])) or []:
        pid = item.get("PositionID", item.get("positionID", item.get("positionId")))
        iid = item.get("InstrumentID", item.get("instrumentID", item.get("instrumentId", 0)))
        if pid:
            out[str(pid)] = int(iid or 0)
    return out


def credit(pnl: dict) -> float:
    data = pnl.get("clientPortfolio", pnl)
    return float(data.get("credit") or data.get("credits") or data.get("availableCash") or 0) or 0.0


async def _get_pnl(session, api_key, user_key) -> dict:
    async with session.get(PNL_URL, headers=headers(api_key, user_key)) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"PnL-Abfrage HTTP {resp.status}: {body[:300]}")
        return json.loads(body)


async def _post(session, url, payload, api_key, user_key) -> tuple[int, str]:
    async with session.post(url, json=payload, headers=headers(api_key, user_key)) as resp:
        return resp.status, (await resp.text())[:500]


async def _wait_for(session, api_key, user_key, predicate, *, attempts=20, interval=3.0):
    for _ in range(attempts):
        positions = open_positions(await _get_pnl(session, api_key, user_key))
        if predicate(positions):
            return positions
        await asyncio.sleep(interval)
    return None


async def run_check(api_key: str, user_key: str, *, place_test_order: bool, symbol: str, amount: float,
                    out=print, session_factory=None) -> int:
    import aiohttp
    factory = session_factory or (lambda: aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)))
    async with factory() as session:
        try:
            pnl = await _get_pnl(session, api_key, user_key)
        except Exception as exc:
            out(f"FEHLER Stufe 1 (lesen): {exc}")
            return 1
        before = open_positions(pnl)
        out(f"OK Stufe 1: Demo-Konto erreichbar, Guthaben {credit(pnl):.2f} USD, {len(before)} offene Position(en).")
        if not place_test_order:
            return 0
        instrument_id = load_instrument_id(symbol)
        new_id, rc = None, 0
        try:
            status, body = await _post(session, f"{REST_BASE}/market-open-orders/by-amount",
                                       open_payload(instrument_id, amount), api_key, user_key)
            if status not in (200, 201):
                out(f"FEHLER Stufe 2 (Order öffnen): HTTP {status}: {body}")
                return 1
            out(f"OK Order gesendet (HTTP {status}); warte auf Position …")
            after = await _wait_for(session, api_key, user_key,
                                    lambda p: any(k not in before and v == instrument_id for k, v in p.items()))
            if after is None:
                out("FEHLER: Position erscheint nicht im Portfolio (Timeout) — bitte im eToro-Demo-Konto prüfen.")
                rc = 1
            else:
                new_id = next(k for k, v in after.items() if k not in before and v == instrument_id)
                out(f"OK Stufe 2: Position {new_id} ({symbol}, {amount:.2f} USD) ist offen.")
        except Exception as exc:
            out(f"FEHLER Stufe 2: {exc}")
            rc = 1
        if new_id is not None:
            rc |= await _close(session, api_key, user_key, new_id, instrument_id, out)
        return rc


async def _close(session, api_key, user_key, position_id, instrument_id, out) -> int:
    try:
        status, body = await _post(session, f"{REST_BASE}/market-close-orders/positions/{position_id}",
                                   close_payload(instrument_id), api_key, user_key)
        if status not in (200, 201):
            out(f"FEHLER Stufe 3 (schliessen): HTTP {status}: {body} — Position {position_id} manuell schliessen!")
            return 1
        gone = await _wait_for(session, api_key, user_key, lambda p: position_id not in p)
    except Exception as exc:
        out(f"FEHLER Stufe 3: {exc} — Position {position_id} manuell prüfen/schliessen!")
        return 1
    if gone is None:
        out(f"FEHLER: Position {position_id} nach dem Schliessen noch offen — manuell prüfen!")
        return 1
    out("OK Stufe 3: Position geschlossen.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Demo-API End-to-End-Prüfung (nur Demo-Konto)")
    parser.add_argument("--place-test-order", action="store_true",
                        help="öffnet und schliesst eine kleine Test-Position im Demo-Konto")
    parser.add_argument("--symbol", default="BTC.ETORO")
    parser.add_argument("--amount", type=float, default=50.0, help="USD-Betrag der Test-Position")
    args = parser.parse_args(argv)
    try:
        assert_demo_environment(os.environ.get("ETORO_ENV"))
    except PaperTradingError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    from dotenv import load_dotenv
    load_dotenv(str(PROJECT_ROOT / ".env"))
    api_key, user_key = os.getenv("ETORO_API_KEY", ""), os.getenv("ETORO_USER_KEY", "")
    if not api_key or not user_key:
        print("FEHLER: ETORO_API_KEY / ETORO_USER_KEY fehlen in .env.", file=sys.stderr)
        return 2
    global REST_BASE, PNL_URL
    from automation.account_guard import PaperAccountError as _AccountError, verify_paper_account
    try:
        REST_BASE, PNL_URL = _BASES[verify_paper_account(api_key, user_key)]
    except _AccountError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(run_check(api_key, user_key, place_test_order=args.place_test_order,
                                 symbol=args.symbol, amount=args.amount))


if __name__ == "__main__":
    sys.exit(main())
