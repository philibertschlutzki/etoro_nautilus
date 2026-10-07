"""Issue #1373 (GH #1275, Pitfall #495) — unfertige Kerzen werden nicht geschrieben;
``check_catalog_freshness`` meldet ein negatives Alter als FAIL ``FUTURE_TICK``."""
import datetime as dt
import logging

import pyarrow.compute as pc

from automation import api_backfiller as ab
from automation.optimizer import sweep

_UTC = dt.timezone.utc
_ASOF = dt.datetime(2026, 10, 6, 19, 32, tzinfo=_UTC)       # Vorwärtsschritt um 19:32Z


def _candle(hour: int) -> dict:
    t = dt.datetime(2026, 10, 6, hour, tzinfo=_UTC)
    return {"fromDate": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 4.0}


def _convert(**kw):
    candles = [_candle(h) for h in (16, 17, 18, 19)]
    return ab._candles_to_arrow_table(candles, "TSLA.ETORO", 2, 2, dt.datetime(2026, 10, 1, tzinfo=_UTC), **kw)


def test_forward_step_at_1932_writes_up_to_the_1800_candle_only():
    table = _convert(asof_ns=int(_ASOF.timestamp() * 1e9))
    newest = pc.max(table.column("ts_event")).as_py()
    assert newest == int(dt.datetime(2026, 10, 6, 19, tzinfo=_UTC).timestamp() * 1e9) - 1   # Close der 18:00-Kerze
    assert len(table) == 12                                                                 # 3 Kerzen × O/L/H/C


def test_candle_is_written_once_it_has_ended():
    later = dt.datetime(2026, 10, 6, 20, 0, 1, tzinfo=_UTC)
    table = _convert(asof_ns=int(later.timestamp() * 1e9))
    assert len(table) == 16


def test_without_asof_nothing_is_dropped():
    assert len(_convert()) == 16            # #1330/#1332-Verhalten bleibt bit-identisch


def test_dropped_count_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        _convert(asof_ns=int(_ASOF.timestamp() * 1e9))
    assert any("n_incomplete_dropped=1" in r.getMessage() for r in caplog.records)
    assert any("INCOMPLETE_CANDLES_DROPPED" in r.getMessage() for r in caplog.records)


def test_future_tick_fails_the_freshness_check():
    newest = dt.datetime(2026, 10, 6, 19, 59, 59, 999000, tzinfo=_UTC)
    res = sweep.check_catalog_freshness(int(newest.timestamp() * 1e9), now=_ASOF.replace(minute=32, second=55))
    assert res["passed"] is False and res["age_h"] < 0
    assert res["reason_code"] == "FUTURE_TICK" and res["rejection_code"] == "REJECT_FUTURE_TICK"
    assert "REJECT_FUTURE_TICK" in res["reason"]


def test_fresh_and_stale_behaviour_is_unchanged():
    now = _ASOF
    ok = sweep.check_catalog_freshness(int((now - dt.timedelta(hours=2)).timestamp() * 1e9), now=now)
    stale = sweep.check_catalog_freshness(int((now - dt.timedelta(hours=200)).timestamp() * 1e9), now=now)
    assert ok["passed"] is True and stale["passed"] is False
    assert stale["reason"].startswith("REJECT_DATA_STALE") and "rejection_code" not in stale
