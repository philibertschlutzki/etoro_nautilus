#!/usr/bin/env python3
"""
automation/utils.py
===================
Gemeinsame Hilfsfunktionen für das automation-Paket.

Wird von api_backfiller.py, catalog_service.py, daily_orchestrator.py
und backtesting/run_backtest.py importiert. Kein Import aus adapters/.

Exports:
    _fallback_precisions(symbol) -> (price_precision, size_precision)
"""

from __future__ import annotations

# ─── Precision-Sets ──────────────────────────────────────────────────────────
_CRYPTO_SYMBOLS = frozenset({
    "BTC", "ETH", "ADA", "DOGE", "SOL", "XRP", "AVAX",
    "HYPE", "ONDO", "SHIBxM", "AERO", "PEPExM",
})
# Preis-Precision je Krypto-Symbol. Pauschal 2 Stellen waren für Kleinpreis-Coins zu grob (DOGE ≈ 0.2 USD:
# Tick 0.01 = 5 % des Preises ⇒ 1h-Returns, ATR und Stops sind Rundungsrauschen). Richtwert: Tick ≤ ca. 1-2 bp.
_CRYPTO_PRICE_PRECISION = {
    "BTC": 2, "ETH": 2, "SOL": 3, "AVAX": 3, "HYPE": 3,
    "XRP": 4, "ADA": 4, "ONDO": 4, "AERO": 4, "DOGE": 5,
    "SHIBxM": 8, "PEPExM": 8,
}
_FRACTIONAL_SYMBOLS = frozenset({
    "NATGAS", "USDTRY", "USDZAR", "PALL",
})


def _fallback_precisions(symbol: str) -> tuple[int, int]:
    """Fallback-Precisions basierend auf Symbolname (keine API-Abfrage).

    Wird verwendet wenn:
      - Die eToro API keine Precision-Felder liefert.
      - Parquet-Metadaten fehlen oder nicht lesbar sind.

    Priorität:
      1. SHIBxM / PEPExM (PEPE): price=8, size=8
      2. Bekannte Crypto-Symbole:  price=2, size=8
      3. Fractional Commodities:   price=5, size=5
      4. Equity (Default):         price=2, size=2

    Args:
        symbol: Nautilus-Symbol (z.B. "ETH.ETORO") oder Basis-Symbol (z.B. "ETH")

    Returns:
        (price_precision, size_precision)
    """
    sym = symbol.split(".")[0]
    if "SHIB" in sym or "PEPE" in sym:
        return 8, 8
    if sym in _CRYPTO_SYMBOLS:
        return _CRYPTO_PRICE_PRECISION.get(sym, 2), 8
    if sym in _FRACTIONAL_SYMBOLS:
        return 5, 5
    # Equity-Default
    return 2, 2


def apply_price_precision_floor(symbol: str, precisions: tuple[int, int]) -> tuple[int, int]:
    """Hebt die Preis-Precision eines Krypto-Symbols auf mindestens ``_CRYPTO_PRICE_PRECISION`` an.

    Auch eine von der API (oder einem alten Default) gelieferte zu grobe Precision (z. B. 2 für DOGE) würde
    die Preise quantisieren. Nicht-Krypto-Symbole bleiben unverändert."""
    sym = symbol.split(".")[0]
    floor = _CRYPTO_PRICE_PRECISION.get(sym)
    price_prec, size_prec = precisions
    if floor is not None and price_prec < floor:
        return floor, size_prec
    return price_prec, size_prec


__all__ = ["_fallback_precisions", "apply_price_precision_floor", "_CRYPTO_SYMBOLS", "_FRACTIONAL_SYMBOLS"]
