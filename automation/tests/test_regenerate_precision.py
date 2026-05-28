import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path

from automation.regenerate_precision import regenerate_file, _encode_qty_fsb16, _decode_qty_fsb16

def test_encode_decode_roundtrip():
    # Test our helper functions
    qty = 12.34

    # Old encoding (precision 0 - will round 12.34 to 12)
    b0 = _encode_qty_fsb16(qty, 0)
    assert _decode_qty_fsb16(b0, 0) == 12.0

    # New encoding (precision 2)
    b2 = _encode_qty_fsb16(qty, 2)
    assert _decode_qty_fsb16(b2, 2) == 12.34

def test_regenerate_equity_file(tmp_path):
    filepath = tmp_path / "AAPL.ETORO" / "data.parquet"
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Create fake data with size_precision=0
    _FSB16 = pa.binary(16)
    qty1 = 15.0
    qty2 = 25.0

    b1_0 = _encode_qty_fsb16(qty1, 0)
    b2_0 = _encode_qty_fsb16(qty2, 0)

    bid_sizes = pa.array([b1_0, b2_0], type=_FSB16)
    ask_sizes = pa.array([b1_0, b2_0], type=_FSB16)

    schema = pa.schema([
        ("bid_size", _FSB16),
        ("ask_size", _FSB16)
    ], metadata={
        b"instrument_id": b"AAPL.ETORO",
        b"size_precision": b"0"
    })

    table = pa.Table.from_arrays([bid_sizes, ask_sizes], schema=schema)
    pq.write_table(table, filepath)

    # Regenerate
    assert regenerate_file(filepath, inplace=True) == True

    # Verify
    new_table = pq.read_table(filepath)
    assert new_table.schema.metadata[b"size_precision"] == b"2"

    new_bid_sizes = new_table.column("bid_size").to_pylist()
    assert _decode_qty_fsb16(new_bid_sizes[0], 2) == 15.0
    assert _decode_qty_fsb16(new_bid_sizes[1], 2) == 25.0

    # Run again - should be idempotent
    assert regenerate_file(filepath) == False

def test_regenerate_crypto_file_ignored(tmp_path):
    filepath = tmp_path / "BTC.ETORO" / "data.parquet"
    filepath.parent.mkdir(parents=True, exist_ok=True)

    schema = pa.schema([], metadata={
        b"instrument_id": b"BTC.ETORO",
        b"size_precision": b"8"
    })

    table = pa.Table.from_arrays([], schema=schema)
    pq.write_table(table, filepath)

    # Should ignore because it's crypto
    assert regenerate_file(filepath) == False
