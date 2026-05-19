_CRYPTO_SYMBOLS = frozenset({
    "BTC", "ETH", "ADA", "DOGE", "SOL", "XRP", "AVAX",
    "HYPE", "ONDO", "SHIBxM", "AERO", "PEPExM",
})

_FRACTIONAL_SYMBOLS = frozenset({
    "NATGAS", "USDTRY", "USDZAR", "PALL",
})

def get_size_precision(instrument_id_str: str) -> int:
    """
    Gibt die korrekte size_precision zurück, kompatibel mit eToro Live-Execution.
    Crypto: 8 (eToro erlaubt Fractional Crypto)
    Forex/Commodities: 5 (eToro erlaubt Fractions)
    Equity: 0 (eToro rundet auf ganze Units)
    """
    symbol = instrument_id_str.split(".")[0]
    if symbol in _CRYPTO_SYMBOLS:
        return 8
    if symbol in _FRACTIONAL_SYMBOLS:
        return 5
    return 0
