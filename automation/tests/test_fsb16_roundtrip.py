import os
import shutil
import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from automation._serde import encode_price_fsb16, encode_qty_fsb16

def test_encode_decode_roundtrip_with_nautilus(tmp_path):
    catalog_path = tmp_path / "nautilus"
    tick_dir = catalog_path / "data" / "quote_tick" / "AAPL.ETORO"
    tick_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = tick_dir / "data.parquet"

    price_val = 123.45
    qty_val = 1.0
    price_prec = 2
    size_prec = 2

    bid_prices = [encode_price_fsb16(price_val, price_prec)]
    ask_prices = [encode_price_fsb16(price_val, price_prec)]
    bid_sizes = [encode_qty_fsb16(qty_val, size_prec)]
    ask_sizes = [encode_qty_fsb16(qty_val, size_prec)]

    import time
    ts = int(time.time() * 1e9)
    ts_events = [ts]
    ts_inits = [ts]

    _FSB16 = pa.binary(16)
    schema = pa.schema([
        pa.field("bid_price", _FSB16),
        pa.field("ask_price", _FSB16),
        pa.field("bid_size",  _FSB16),
        pa.field("ask_size",  _FSB16),
        pa.field("ts_event",  pa.uint64()),
        pa.field("ts_init",   pa.uint64()),
    ])

    meta = {
        b"price_precision": str(price_prec).encode(),
        b"size_precision":  str(size_prec).encode(),
        b"instrument_id":   b"AAPL.ETORO",
    }

    table = pa.table(
        {
            "bid_price": pa.array(bid_prices, type=_FSB16),
            "ask_price": pa.array(ask_prices, type=_FSB16),
            "bid_size":  pa.array(bid_sizes,  type=_FSB16),
            "ask_size":  pa.array(ask_sizes,  type=_FSB16),
            "ts_event":  pa.array(ts_events,  type=pa.uint64()),
            "ts_init":   pa.array(ts_inits,   type=pa.uint64()),
        },
        schema=schema,
    )

    table = table.replace_schema_metadata(meta)
    pq.write_table(table, str(parquet_file))

    catalog = ParquetDataCatalog(str(catalog_path))
    ticks = catalog.quote_ticks("AAPL.ETORO")

    assert len(ticks) == 1
    t = ticks[0]

    # Assert values
    assert abs(float(t.bid_price) - 123.45) < 1e-9
    assert float(t.bid_size) == 1.0
