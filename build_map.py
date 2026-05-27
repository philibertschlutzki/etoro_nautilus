import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from adapters.instrument_map import ETORO_INSTRUMENTS
from automation.utils import _fallback_precisions, _CRYPTO_SYMBOLS, _FRACTIONAL_SYMBOLS

def get_asset_class(symbol: str) -> str:
    if symbol in _CRYPTO_SYMBOLS:
        return "crypto"
    if symbol in _FRACTIONAL_SYMBOLS or symbol in ("NATGAS.ETORO", "PALL.ETORO", "USDTRY.ETORO", "USDZAR.ETORO"):
        if symbol in ("NATGAS.ETORO", "PALL.ETORO"):
            return "commodity"
        if symbol in ("USDTRY.ETORO", "USDZAR.ETORO"):
            return "forex"
        return "commodity" # Or forex, but instructions say "commodity" or "forex"
    return "equity"

def main():
    instruments_dict = {}
    for eid, symbol in ETORO_INSTRUMENTS.items():
        if not symbol:
            continue
        asset_class = get_asset_class(symbol)
        pp, sp = _fallback_precisions(symbol)
        instruments_dict[str(eid)] = {
            "symbol": symbol,
            "asset_class": asset_class,
            "price_precision": pp,
            "size_precision": sp,
        }

    output = {
        "_schema": {
            "description": "eToro Instrument-ID zu Nautilus-Symbol-Map. Einzige Source of Truth für automation/. Generiert aus adapters/instrument_map.py.",
            "key": "eToro numeric instrument ID (string)",
            "fields": {
                "symbol": "Nautilus InstrumentId string (SYMBOL.VENUE)",
                "asset_class": "equity | crypto | forex | commodity",
                "price_precision": "int — aus automation/utils._fallback_precisions",
                "size_precision": "int — aus automation/utils._fallback_precisions"
            }
        },
        "instruments": instruments_dict
    }

    out_path = Path("automation/config/instrument_map.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
