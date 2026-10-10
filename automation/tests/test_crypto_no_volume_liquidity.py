"""Krypto-Kerzen ohne Volumen: Quote-Größe 0 ließ den simulierten Handelsplatz jede Order mit
"no market" ablehnen (LINK: 825 Orders, 0 Füllungen). Kerzen ohne Volumen bekommen eine synthetische
Top-of-Book-Größe, und schon geschriebene Ticks mit Größe 0 werden beim Laden ersetzt."""
import datetime as dt
from unittest.mock import MagicMock

import pyarrow as pa
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from automation import api_backfiller as ab
from automation.backtest_runner import load_ticks_from_catalog

_UTC = dt.timezone.utc


def _candle(day, volume=None):
    c = {"fromDate": f"{day.isoformat()}T00:00:00Z", "open": 10.0, "low": 9.0, "high": 11.0, "close": 10.5}
    if volume is not None:
        c["volume"] = volume
    return c


def _sizes(table: pa.Table) -> set[float]:
    return {round(int.from_bytes(b, "little", signed=True) / 1e16, 6) for b in table.column("bid_size").to_pylist()}


def test_candles_without_volume_get_synthetic_liquidity():
    table = ab._candles_to_arrow_table([_candle(dt.date(2026, 9, 1)), _candle(dt.date(2026, 9, 2), volume=0)],
                                       "LINK.ETORO", 4, 4, dt.datetime(2026, 1, 1, tzinfo=_UTC), interval="OneDay")
    assert _sizes(table) == {ab.SYNTHETIC_TOB_SIZE}
    assert table.schema.metadata[b"volume_available"] == b"false"


def test_candles_with_volume_keep_real_size():
    table = ab._candles_to_arrow_table([_candle(dt.date(2026, 9, 1), volume=400)],
                                       "ASML.ETORO", 2, 2, dt.datetime(2026, 1, 1, tzinfo=_UTC), interval="OneDay")
    assert _sizes(table) == {100.0}
    assert table.schema.metadata[b"volume_available"] == b"true"


def test_loader_replaces_zero_size_ticks_from_old_catalogs():
    iid = InstrumentId.from_str("LINK.ETORO")
    tick = QuoteTick(instrument_id=iid, bid_price=Price(10.0, 2), ask_price=Price(10.0, 2),
                     bid_size=Quantity(0.0, 2), ask_size=Quantity(0.0, 2), ts_event=1000, ts_init=1000)
    catalog = MagicMock()
    catalog.quote_ticks.return_value = [tick]
    out = load_ticks_from_catalog(catalog, "LINK.ETORO", None, None)
    assert round(out[0].bid_size.as_double()) == ab.SYNTHETIC_TOB_SIZE
    assert round(out[0].ask_size.as_double()) == ab.SYNTHETIC_TOB_SIZE
