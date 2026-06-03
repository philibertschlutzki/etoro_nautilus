from automation.utils import _fallback_precisions

def get_size_precision(instrument_id_str: str) -> int:
    """
    Gibt die korrekte size_precision zurück, kompatibel mit eToro Live-Execution.
    Greift nun zentral auf automation.utils._fallback_precisions zu.
    """
    symbol = instrument_id_str.split(".")[0]
    return _fallback_precisions(symbol)[1]
