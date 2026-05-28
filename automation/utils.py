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
        return 2, 8
    if sym in _FRACTIONAL_SYMBOLS:
        return 5, 5
    # Equity-Default
    return 2, 2


__all__ = ["_fallback_precisions"]
