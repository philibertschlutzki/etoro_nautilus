"""
automation/fractional_trading.py
==================================
Lösung für Pitfall #14: Fractional Trading erzwingen (10k USD Konto).

Kernproblem:
    Bei einem 10k-USD-Demo-Konto und Equities wie FICO (~$1600/Aktie) oder
    TSLA (~$450/Aktie) ist trade_amount_usd < Preis einer Einheit.
    Nautilus' instrument.make_qty(units) wirft dann einen harten ValueError,
    da size_precision=0 und die berechneten Stückzahlen < 1 (< size_increment).

Lösung:
    1. Alle Market-Orders über den eToro "by-amount"-Endpunkt routen
       (InvestmentAmount in USD statt Units).
    2. size_increment via eToro-Instruments-API abfragen und persistent cachen.
    3. get_size_precision() für Crypto/Forex korrekt beibehalten; Equities
       gehen ausschließlich über den by-amount-Pfad.


Wichtige Endpunkte:
    - Instruments-Suche: GET /api/v1/market-data/instruments (mit instrumentIds)
    - By-Amount Open:    POST /api/v1/trading/execution/market-open-orders/by-amount
    - By-Amount Close:   POST /api/v1/trading/execution/market-close-orders/by-units
                         (mit UnitsToDeduct=null → voller Close)

Dieses Modul wird vom Orchestrator-Backtest verwendet, um den 10k-Limit
korrekt zu verwalten und im Live-Bot sicherzustellen, dass keine Orders
wegen size_precision-Problemen crashen.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import aiohttp

from automation.utils import _fallback_precisions

logger = logging.getLogger(__name__)

# Persistenter Cache für size_increment-Werte
_SIZE_INCREMENT_CACHE_PATH = Path(__file__).parent.parent / "data" / "state" / "size_increment_cache.json"
_size_increment_cache: dict[str, float] = {}


def load_size_increment_cache() -> None:
    """Lädt den persistenten size_increment-Cache vom Disk."""
    global _size_increment_cache
    if _SIZE_INCREMENT_CACHE_PATH.exists():
        try:
            with open(str(_SIZE_INCREMENT_CACHE_PATH), "r", encoding="utf-8") as f:
                _size_increment_cache = json.load(f)
            logger.debug(f"[FractionalTrading] size_increment-Cache geladen: {len(_size_increment_cache)} Einträge.")
        except Exception as e:
            logger.warning(f"[FractionalTrading] Cache-Lade-Fehler: {e}")
            _size_increment_cache = {}


def save_size_increment_cache() -> None:
    """Speichert den size_increment-Cache persistent."""
    _SIZE_INCREMENT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = _SIZE_INCREMENT_CACHE_PATH.with_suffix(".tmp.json")
        with open(str(tmp), "w", encoding="utf-8") as f:
            json.dump(_size_increment_cache, f, indent=2, ensure_ascii=False)
        tmp.rename(_SIZE_INCREMENT_CACHE_PATH)
        logger.debug(f"[FractionalTrading] Cache gespeichert: {len(_size_increment_cache)} Einträge.")
    except Exception as e:
        logger.error(f"[FractionalTrading] Cache-Schreib-Fehler: {e}")


def get_size_increment(symbol: str, etoro_id: str) -> float:
    """
    Gibt den kleinsten kaufbaren Teil (size_increment) zurück.

    Reihenfolge:
    1. Aus Cache (persistent)
    2. Asset-Klassen-Default (Crypto=0.00000001, Forex=0.00001, Equity=1.0)

    Für Live-Queries via API: _fetch_size_increment_from_api() verwenden.
    """
    key = f"{etoro_id}:{symbol}"
    if key in _size_increment_cache:
        return _size_increment_cache[key]

    # Default-Werte nach Asset-Klasse (inline, kein adapters/-Import)
    prec = _fallback_precisions(symbol)[1]

    if prec >= 8:
        default = 1e-8   # Crypto
    elif prec >= 5:
        default = 1e-5   # Forex/Commodity
    else:
        default = 1.0    # Equity (eToro by-amount Route verhindert den Crash)
        # Even with prec=2, the lot_size/default increment remains 1.0 for the by-amount route.

    _size_increment_cache[key] = default
    return default


async def fetch_size_increments_from_api(
    etoro_ids: list[str],
    api_key: str,
    user_key: str,
) -> dict[str, float]:
    """
    Fragt den kleinsten kaufbaren Teil (size_increment / minQuantity) für
    eine Liste von eToro-IDs über die eToro Instruments-API ab.

    Endpunkt: GET /api/v1/market-data/instruments?instrumentIds={ids}

    Für Equities ist size_increment immer 1.0 (ganze Aktien via by-units).
    Mit dem by-amount Endpunkt wird USD direkt investiert, sodass der
    size_increment nicht für Stückzahl-Berechnung benötigt wird.

    Gibt Dict {etoro_id: size_increment} zurück.
    """
    if not etoro_ids:
        return {}

    url = "https://public-api.etoro.com/api/v1/market-data/instruments"
    headers = {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

    results: dict[str, float] = {}
    timeout = aiohttp.ClientTimeout(total=20.0)

    # In Batches von 20 IDs (API-Limit)
    batch_size = 20
    id_batches = [etoro_ids[i:i+batch_size] for i in range(0, len(etoro_ids), batch_size)]

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for batch in id_batches:
            params = {"instrumentIds": ",".join(batch)}
            for attempt in range(3):
                try:
                    async with session.get(url, params=params, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            instruments = (
                                data.get("instruments")
                                or data.get("Instruments")
                                or (data if isinstance(data, list) else [])
                            )
                            for inst in instruments:
                                if not isinstance(inst, dict):
                                    continue
                                inst_id = str(inst.get("instrumentId", inst.get("InstrumentId", "")))
                                # minQuantity oder lotSize → size_increment
                                min_qty = inst.get("minQuantity") or inst.get("MinQuantity") or 1.0
                                results[inst_id] = float(min_qty)
                            break
                        elif resp.status == 429:
                            retry_after = int(resp.headers.get("Retry-After", 30))
                            await asyncio.sleep(retry_after)
                        else:
                            logger.warning(f"[FractionalTrading] API HTTP {resp.status} für Batch {batch[:3]}...")
                            break
                except Exception as e:
                    logger.warning(f"[FractionalTrading] API-Fehler (Versuch {attempt+1}): {e}")
                    await asyncio.sleep(5)
            await asyncio.sleep(1.1)  # Rate-Limit

    return results





def build_by_amount_payload(
    etoro_id: str,
    investment_amount_usd: float,
    is_buy: bool = True,
    leverage: int = 1,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    current_rate: float | None = None,
) -> dict:
    """
    Erstellt einen validen Payload für den eToro by-amount Endpunkt.

    POST /api/v1/trading/execution/market-open-orders/by-amount

    Dieses Format ermöglicht Fractional Trading für Equities ohne
    dass Nautilus Stückzahlen berechnen muss. eToro konvertiert den
    USD-Betrag intern in die korrekte Stückzahl.

    Pitfall #14: Mit diesem Endpunkt können auch $11 in TSLA (≈$450/Aktie)
    investiert werden — eToro kauft dann ~0.024 Anteile.
    """
    payload: dict[str, Any] = {
        "InstrumentId":     int(etoro_id),
        "IsBuy":            is_buy,
        "InvestmentAmount": investment_amount_usd,
        "Leverage":         leverage,
        "Tags":             [],
    }

    # Stop-Loss hinzufügen (optional)
    if stop_loss_pct is not None and current_rate is not None:
        if is_buy:
            sl_rate = current_rate * (1 - stop_loss_pct)
        else:
            sl_rate = current_rate * (1 + stop_loss_pct)
        payload["StopLossRate"] = round(sl_rate, 5)

    # Take-Profit hinzufügen (optional)
    if take_profit_pct is not None and current_rate is not None:
        if is_buy:
            tp_rate = current_rate * (1 + take_profit_pct)
        else:
            tp_rate = current_rate * (1 - take_profit_pct)
        payload["TakeProfitRate"] = round(tp_rate, 5)

    return payload


def get_dynamic_size_precision(symbol: str, etoro_id: str) -> int:
    """
    Gibt die dynamische size_precision zurück — kompatibel mit Pitfall #14 Fix.

    Für den by-amount Endpunkt spielt size_precision keine Rolle für Equities,
    da USD direkt angegeben wird. Diese Funktion wird hauptsächlich für den
    Backtesting-Kontext verwendet.
    """
    base_prec = _fallback_precisions(symbol)[1]  # inline, kein adapters/-Import
    return base_prec
