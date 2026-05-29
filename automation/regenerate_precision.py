#!/usr/bin/env python3
import json
import logging
from pathlib import Path
import struct
import pyarrow as pa
import pyarrow.parquet as pq

from automation.utils import _fallback_precisions

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("regenerate_precision")

def _encode_qty_fsb16(qty: float, precision: int) -> bytes:
    """Encodes a quantity into FixedSizeBinary(16)."""
    raw = round(qty * (10 ** precision))
    raw = max(0, min(2 ** 63 - 1, raw))
    return struct.pack("<q", raw) + b"\x00" * 8

def _decode_qty_fsb16(b: bytes, precision: int) -> float:
    """Decodes a quantity from FixedSizeBinary(16) bytes."""
    raw_int64 = struct.unpack("<q", b[:8])[0]
    return float(raw_int64) / (10 ** precision)

def regenerate_file(filepath: Path) -> bool:
    """Regenerates a single parquet file if needed."""
    try:
        table = pq.read_table(filepath)
    except Exception as e:
        logger.error(f"{filepath} konnte nicht gelesen werden: {e}")
        return False

    meta = table.schema.metadata or {}

    # Check if this is an equity. We'll use fallback precisions to determine if it should be updated.
    symbol = meta.get(b"instrument_id", b"").decode()
    if not symbol:
        # Try to infer from path
        if "quote_tick" in str(filepath):
            symbol = filepath.parent.name
        else:
            logger.warning(f"{filepath} hat kein instrument_id, überspringe.")
            return False

    _, expected_size_prec = _fallback_precisions(symbol)
    if expected_size_prec != 2:
        # Not an equity that needs 2 precision
        return False

    current_size_prec_bytes = meta.get(b"size_precision")
    current_size_prec = int(current_size_prec_bytes.decode()) if current_size_prec_bytes else 0

    if current_size_prec == 2:
        return False # Already updated

    # Needs update from 0 to 2
    logger.info(json.dumps({
        "event": "REGENERATE_PRECISION",
        "symbol": symbol,
        "file": str(filepath),
        "old_precision": current_size_prec,
        "new_precision": 2,
        "rows": table.num_rows
    }))

    if table.num_rows == 0:
        # Just update metadata
        new_meta = meta.copy()
        new_meta[b"size_precision"] = b"2"
        new_table = table.replace_schema_metadata(new_meta)
        pq.write_table(new_table, filepath, compression="snappy")
        return True

    # Need to rescale raw_int64 values
    _FSB16 = pa.binary(16)

    bid_sizes_old = table.column("bid_size").to_pylist()
    ask_sizes_old = table.column("ask_size").to_pylist()

    bid_sizes_new = []
    ask_sizes_new = []

    for bs, as_ in zip(bid_sizes_old, ask_sizes_old):
        # Decode using old precision
        bs_float = _decode_qty_fsb16(bs, current_size_prec)
        as_float = _decode_qty_fsb16(as_, current_size_prec)

        # Encode using new precision
        bid_sizes_new.append(_encode_qty_fsb16(bs_float, 2))
        ask_sizes_new.append(_encode_qty_fsb16(as_float, 2))

    new_arrays = []
    for i, field in enumerate(table.schema):
        if field.name == "bid_size":
            new_arrays.append(pa.array(bid_sizes_new, type=_FSB16))
        elif field.name == "ask_size":
            new_arrays.append(pa.array(ask_sizes_new, type=_FSB16))
        else:
            new_arrays.append(table.column(i))

    new_meta = meta.copy()
    new_meta[b"size_precision"] = b"2"
    new_schema = table.schema.with_metadata(new_meta)
    new_table = pa.Table.from_arrays(new_arrays, schema=new_schema)

    return new_table

def regenerate_file(filepath: Path, inplace: bool = False, dry_run: bool = False) -> bool:
    """Regenerates a single parquet file if needed."""
    try:
        table = pq.read_table(filepath)
    except Exception as e:
        logger.error(f"{filepath} konnte nicht gelesen werden: {e}")
        return False

    meta = table.schema.metadata or {}

    # Check if this is an equity. We'll use fallback precisions to determine if it should be updated.
    symbol = meta.get(b"instrument_id", b"").decode()
    if not symbol:
        # Try to infer from path
        if "quote_tick" in str(filepath):
            symbol = filepath.parent.name
        else:
            logger.warning(f"{filepath} hat kein instrument_id, überspringe.")
            return False

    _, expected_size_prec = _fallback_precisions(symbol)
    if expected_size_prec != 2:
        # Not an equity that needs 2 precision
        return False

    current_size_prec_bytes = meta.get(b"size_precision")
    current_size_prec = int(current_size_prec_bytes.decode()) if current_size_prec_bytes else 0

    if current_size_prec == 2:
        return False # Already updated

    # Needs update from 0 to 2
    logger.info(json.dumps({
        "event": "REGENERATE_PRECISION",
        "symbol": symbol,
        "file": str(filepath),
        "old_precision": current_size_prec,
        "new_precision": 2,
        "rows": table.num_rows
    }))

    if dry_run:
        return True

    if table.num_rows == 0:
        # Just update metadata
        new_meta = meta.copy()
        new_meta[b"size_precision"] = b"2"
        new_table = table.replace_schema_metadata(new_meta)
    else:
        new_table = _rescale_table(table, current_size_prec)

    if inplace:
        # Atomic write
        tmp_path = filepath.with_suffix(".tmp.parquet")
        pq.write_table(new_table, tmp_path, compression="snappy")
        tmp_path.replace(filepath)
    else:
        logger.info(f"Skipping write to {filepath} because --inplace was not provided.")

    return True

def _rescale_table(table, current_size_prec):
    # Need to rescale raw_int64 values
    _FSB16 = pa.binary(16)

    bid_sizes_old = table.column("bid_size").to_pylist()
    ask_sizes_old = table.column("ask_size").to_pylist()

    bid_sizes_new = []
    ask_sizes_new = []

    for bs, as_ in zip(bid_sizes_old, ask_sizes_old):
        # Decode using old precision
        bs_float = _decode_qty_fsb16(bs, current_size_prec)
        as_float = _decode_qty_fsb16(as_, current_size_prec)

        # Encode using new precision
        bid_sizes_new.append(_encode_qty_fsb16(bs_float, 2))
        ask_sizes_new.append(_encode_qty_fsb16(as_float, 2))

    new_arrays = []
    for i, field in enumerate(table.schema):
        if field.name == "bid_size":
            new_arrays.append(pa.array(bid_sizes_new, type=_FSB16))
        elif field.name == "ask_size":
            new_arrays.append(pa.array(ask_sizes_new, type=_FSB16))
        else:
            new_arrays.append(table.column(i))

    new_meta = table.schema.metadata.copy()
    new_meta[b"size_precision"] = b"2"
    new_schema = table.schema.with_metadata(new_meta)
    new_table = pa.Table.from_arrays(new_arrays, schema=new_schema)
    return new_table

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Regenerate parquet size_precision for equities.")
    parser.add_argument("--inplace", action="store_true", help="Apply changes in place")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files, just log")
    parser.add_argument("--data-dir", default="data/nautilus/data", help="Path to data directory")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        logger.error(f"Verzeichnis {data_dir} existiert nicht.")
        return

    updated_count = 0
    for ext in ["**/*.parquet"]:
        for filepath in data_dir.rglob(ext):
            if "tmp" in filepath.name:
                continue
            if regenerate_file(filepath, inplace=args.inplace, dry_run=args.dry_run):
                updated_count += 1

    if args.dry_run:
        logger.info(f"Dry-run abgeschlossen. {updated_count} Dateien würden aktualisiert.")
    elif not args.inplace:
        logger.info(f"Regeneration simuliert. {updated_count} Dateien würden aktualisiert. Füge --inplace hinzu, um die Dateien zu schreiben.")
    else:
        logger.info(f"Regeneration abgeschlossen. {updated_count} Dateien aktualisiert.")

if __name__ == "__main__":
    main()
