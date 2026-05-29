from __future__ import annotations
from decimal import Decimal
from nautilus_trader.model.objects import Price, Quantity

_RAW_SCALE = 10 ** 16  # High-Precision i128 build

# Build-Guard: laut fehlschlagen statt erneut still korrumpieren (binding-stabile String-Ctor)
assert Price.from_str("1").raw == _RAW_SCALE, (
    f"Unerwartete Nautilus-Fixed-Precision (Price.raw={Price.from_str('1').raw}). "
    "Katalog nutzt pa.binary(16) → High-Precision-Build (i128, 10^16) zwingend erforderlich."
)
assert Quantity.from_str("1").raw == _RAW_SCALE, "Quantity-Fixed-Precision != 10^16."

def _to_fsb16(value: float, precision: int) -> bytes:
    # Decimal(str(value)) vermeidet Float-Artefakte; quantize spiegelt die Anzeige-Precision;
    # × 10^16 ist exakte Integer-Arithmetik. KEIN Float * 1e16 (Genauigkeitsverlust bei 8-Dezimal-Symbolen).
    q = Decimal(str(value)).quantize(Decimal(1).scaleb(-int(precision)))
    raw = int(q * _RAW_SCALE)
    return raw.to_bytes(16, byteorder="little", signed=True)

def encode_price_fsb16(value: float, precision: int) -> bytes: return _to_fsb16(value, precision)
def encode_qty_fsb16(value: float, precision: int) -> bytes:   return _to_fsb16(value, precision)

# (Optional konsistenter Decoder für Tests:)
def decode_fsb16(b: bytes, precision: int) -> float:
    return int.from_bytes(b[:16], "little", signed=True) / _RAW_SCALE
