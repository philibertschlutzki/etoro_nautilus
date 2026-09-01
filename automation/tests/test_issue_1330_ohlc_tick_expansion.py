"""Issue #1330 (GH #1224) — ``_candles_to_arrow_table`` verwirft High und Low nicht mehr und
schreibt eine geordnete O/L/H/C-Tick-Sequenz je Kerze statt eines Einzeltickers auf dem Close.

Akzeptanzkriterien (siehe Issue-Body):
- eine Kerze mit O=100, H=103, L=98, C=101 erzeugt exakt vier Ticks in der Reihenfolge
  100, 98, 103, 101 mit streng monoton steigenden ``ts_event``.
- fehlender ``open``-Key ⇒ drei Ticks (L, H, C), keine Exception, kein stiller Verlust der Kerze.
- ``ts_event`` zweier aufeinanderfolgender Kerzen ueberlappen nicht.
- ``intrabar_path`` erscheint in den Katalog-Metadaten.
"""
from datetime import datetime, timezone

from automation import api_backfiller as bf
from automation.catalog_paths import decode_fsb16_price

_NS_PER_HOUR = 3_600_000_000_000
_START_DT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _decode_prices(table):
    return [
        round((decode_fsb16_price(bp) ), 6)
        for bp in table.column("bid_price").to_pylist()
    ]


def test_full_ohlc_candle_produces_four_ticks_in_adverse_first_order():
    candles = [
        {
            "fromDate": "2026-01-01T13:00:00Z",
            "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0,
        }
    ]
    table = bf._candles_to_arrow_table(candles, "TEST.ETORO", 2, 2, _START_DT, interval="OneHour")
    assert table is not None
    assert len(table) == 4

    prices = _decode_prices(table)
    assert prices == [100.0, 98.0, 103.0, 101.0]

    ts_events = table.column("ts_event").to_pylist()
    assert ts_events == sorted(ts_events)
    assert len(set(ts_events)) == 4  # streng monoton steigend, keine Kollisionen


def test_missing_open_key_yields_three_ticks_low_high_close_no_exception():
    candles = [
        {
            "fromDate": "2026-01-01T13:00:00Z",
            "high": 103.0, "low": 98.0, "close": 101.0,
        }
    ]
    table = bf._candles_to_arrow_table(candles, "TEST.ETORO", 2, 2, _START_DT, interval="OneHour")
    assert table is not None
    assert len(table) == 3
    prices = _decode_prices(table)
    assert prices == [98.0, 103.0, 101.0]


def test_missing_open_falls_back_to_previous_candle_close():
    candles = [
        {"fromDate": "2026-01-01T13:00:00Z", "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0},
        # Zweite Kerze ohne 'open' -> muss auf den Close der Vorgaengerkerze (101.0) zurueckfallen.
        {"fromDate": "2026-01-01T14:00:00Z", "high": 105.0, "low": 100.0, "close": 104.0},
    ]
    table = bf._candles_to_arrow_table(candles, "TEST.ETORO", 2, 2, _START_DT, interval="OneHour")
    assert table is not None
    assert len(table) == 8  # 4 + 4 (Vorgaenger-Close als O-Tick der zweiten Kerze)
    prices = _decode_prices(table)
    assert prices[4] == 101.0  # O-Tick der zweiten Kerze == Close der ersten


def test_consecutive_candles_ts_events_do_not_overlap():
    candles = [
        {"fromDate": "2026-01-01T13:00:00Z", "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0},
        {"fromDate": "2026-01-01T14:00:00Z", "open": 101.0, "high": 106.0, "low": 99.0, "close": 105.0},
    ]
    table = bf._candles_to_arrow_table(candles, "TEST.ETORO", 2, 2, _START_DT, interval="OneHour")
    ts_events = table.column("ts_event").to_pylist()
    first_candle_ts = ts_events[:4]
    second_candle_ts = ts_events[4:]
    assert max(first_candle_ts) < min(second_candle_ts)
    assert ts_events == sorted(ts_events)


def test_bar_interval_ns_column_present_and_correct():
    candles = [
        {"fromDate": "2026-01-01T13:00:00Z", "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0},
    ]
    table = bf._candles_to_arrow_table(candles, "TEST.ETORO", 2, 2, _START_DT, interval="OneHour")
    assert "bar_interval_ns" in table.schema.names
    assert set(table.column("bar_interval_ns").to_pylist()) == {_NS_PER_HOUR}


def test_intrabar_path_stamped_in_schema_metadata():
    candles = [
        {"fromDate": "2026-01-01T13:00:00Z", "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0},
    ]
    table = bf._candles_to_arrow_table(candles, "TEST.ETORO", 2, 2, _START_DT, interval="OneHour")
    meta = table.schema.metadata
    assert meta[b"intrabar_path"] == bf.INTRABAR_PATH_SYNTHETIC.encode()


def test_close_tick_is_last_representable_instant_of_candle():
    candles = [
        {"fromDate": "2026-01-01T14:00:00Z", "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0},
    ]
    table = bf._candles_to_arrow_table(candles, "TEST.ETORO", 2, 2, _START_DT, interval="OneHour")
    ts_events = table.column("ts_event").to_pylist()
    candle_start_ns = int(datetime(2026, 1, 1, 14, tzinfo=timezone.utc).timestamp() * 1e9)
    assert ts_events[-1] == candle_start_ns + _NS_PER_HOUR - 1


def test_low_precedes_high_regardless_of_direction():
    """Sperrvermerk #7 (Issue #1246): die Reihenfolge low->high ist FEST, unabhaengig von
    close > open oder close < open."""
    bullish = [{"fromDate": "2026-01-01T13:00:00Z", "open": 100.0, "high": 103.0, "low": 98.0, "close": 102.0}]
    bearish = [{"fromDate": "2026-01-01T13:00:00Z", "open": 100.0, "high": 103.0, "low": 98.0, "close": 99.0}]
    t_bull = bf._candles_to_arrow_table(bullish, "TEST.ETORO", 2, 2, _START_DT, interval="OneHour")
    t_bear = bf._candles_to_arrow_table(bearish, "TEST.ETORO", 2, 2, _START_DT, interval="OneHour")
    assert _decode_prices(t_bull)[1:3] == [98.0, 103.0]
    assert _decode_prices(t_bear)[1:3] == [98.0, 103.0]
