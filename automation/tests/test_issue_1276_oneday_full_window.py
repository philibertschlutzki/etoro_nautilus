"""Issue #1276 (GH #1149, Katalog #1374) — OneDay-Vollfenster speichern und täglich fortschreiben."""
import asyncio
import datetime as dt
import hashlib

import pytest

from automation import api_backfiller as ab
from automation import historical_fetcher as hf
from automation.optimizer import sweep

_UTC = dt.timezone.utc
_NOW = dt.datetime(2026, 10, 6, 21, 0, tzinfo=_UTC)


def _daily(day: dt.date) -> dict:
    return {"fromDate": f"{day.isoformat()}T00:00:00Z", "open": 100.0, "high": 102.0, "low": 99.0,
            "close": 101.0, "volume": 10.0}


def _days(n: int, end: dt.date) -> list[dict]:
    return [_daily(end - dt.timedelta(days=i)) for i in range(n)]


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    qt = tmp_path / "quote_tick"
    monkeypatch.setattr(hf, "QUOTE_TICK_PATH", qt)
    monkeypatch.setattr(ab, "QUOTE_TICK_PATH", qt)
    monkeypatch.setattr(hf, "_save_api_window", lambda *a, **k: None)
    # Issue #1382: ohne Session-Fenster (24/7-Verhalten) — dieses Modul testet den Abruf-/Fortschreibe-Pfad,
    # die Session-Expansion hat test_issue_1284_bar_axis.py.
    monkeypatch.setattr(ab, "oneday_session_window_for", lambda symbol: None)
    return qt


def _fetch_returning(candles, calls):
    async def _fetch(session, eid, end_time, api_key, user_key, interval, count=1000):
        calls.append({"end_time": end_time, "interval": interval, "count": count})
        return candles
    return _fetch


def test_forward_count_is_interval_aware():
    assert hf.forward_fill_count(24 * 3, "OneDay") == 5          # ceil(gap_d) + 2
    assert hf.forward_fill_count(24 * 5000, "OneDay") == 1000
    assert hf.forward_fill_count(10.0) == 34                      # Stundenformel unverändert


def test_full_window_saves_everything_without_target_start_cut(catalog):
    calls = []
    candles = _days(600, dt.date(2026, 10, 5))        # weit älter als 12 Monate
    info = asyncio.run(hf.fetch_oneday_full_window(
        None, "1", "TSLA.ETORO", 2, 2, api_key="k", user_key="u", now=_NOW,
        fetch_chunk=_fetch_returning(candles, calls)))
    assert calls == [{"end_time": None, "interval": "OneDay", "count": 1000}]
    assert info["oneday_n_candles"] == 600
    assert info["oneday_oldest_utc"] == (dt.date(2026, 10, 5) - dt.timedelta(days=599)).isoformat()
    assert info["oneday_effective_span_days"] == 600.0
    assert not (catalog / "TSLA.ETORO" / "OneHour").exists()      # OneHour nie angefasst


def test_full_window_leaves_onehour_file_bit_identical(catalog):
    hour = catalog / "TSLA.ETORO" / "OneHour" / "data.parquet"
    hour.parent.mkdir(parents=True)
    hour.write_bytes(b"onehour-bytes")
    before = hashlib.sha256(hour.read_bytes()).hexdigest()
    asyncio.run(hf.fetch_oneday_full_window(
        None, "1", "TSLA.ETORO", 2, 2, api_key="k", user_key="u", now=_NOW,
        fetch_chunk=_fetch_returning(_days(5, dt.date(2026, 10, 5)), [])))
    assert hashlib.sha256(hour.read_bytes()).hexdigest() == before


def test_two_forward_steps_grow_oneday_by_exactly_one_finished_candle(catalog):
    asyncio.run(hf.fetch_oneday_full_window(
        None, "1", "TSLA.ETORO", 2, 2, api_key="k", user_key="u", now=_NOW,
        fetch_chunk=_fetch_returning(_days(10, dt.date(2026, 10, 5)), [])))
    dest = catalog / "TSLA.ETORO" / "OneDay" / "data.parquet"
    n0 = hf.oneday_span_days("TSLA.ETORO")["oneday_n_candles"]
    assert n0 == 10

    async def _step(day_end: dt.date, now: dt.datetime) -> int:
        latest = ab._get_latest_ts(dest)
        candles = await hf.fetch_forward_candles(
            None, "1", "TSLA.ETORO", latest, api_key="k", user_key="u", interval="OneDay", now=now,
            mode=hf.PAGINATION_COUNT_ONLY,
            fetch_chunk=_fetch_returning(_days(4, day_end), []))
        table = ab._candles_to_arrow_table(
            candles, "TSLA.ETORO", 2, 2, dt.datetime(2026, 9, 1, tzinfo=_UTC), interval="OneDay",
            asof_ns=int(now.timestamp() * 1e9))
        ab._merge_and_save(ab.log, table, "TSLA.ETORO", 2, 2, interval="OneDay")
        return hf.oneday_span_days("TSLA.ETORO")["oneday_n_candles"]

    # 07.10. 00:30Z: die Tageskerze vom 06.10. ist beendet, die vom 07.10. läuft noch.
    n1 = asyncio.run(_step(dt.date(2026, 10, 7), dt.datetime(2026, 10, 7, 0, 30, tzinfo=_UTC)))
    n2 = asyncio.run(_step(dt.date(2026, 10, 8), dt.datetime(2026, 10, 8, 0, 30, tzinfo=_UTC)))
    assert (n1, n2) == (n0 + 1, n0 + 2)


def test_forward_step_skips_when_no_finished_day_candle_is_missing():
    now = dt.datetime(2026, 10, 6, 12, 0, tzinfo=_UTC)
    latest_ns = int(dt.datetime(2026, 10, 6, 0, 0, tzinfo=_UTC).timestamp() * 1e9) - 1
    out = asyncio.run(hf.fetch_forward_candles(
        None, "1", "TSLA.ETORO", latest_ns, api_key="k", user_key="u", interval="OneDay", now=now,
        mode=hf.PAGINATION_COUNT_ONLY, fetch_chunk=_fetch_returning([], [])))
    assert out == []


def test_oneday_freshness_threshold_is_in_days():
    now = _NOW
    fresh = sweep.check_catalog_freshness(int((now - dt.timedelta(days=3)).timestamp() * 1e9),
                                          now=now, interval="OneDay")
    stale = sweep.check_catalog_freshness(int((now - dt.timedelta(days=5)).timestamp() * 1e9),
                                          now=now, interval="OneDay")
    assert fresh["passed"] is True and stale["passed"] is False
    assert fresh["max_staleness_h"] == 96.0 and "OneDay" in stale["reason"]
    # OneHour bleibt bit-identisch (96 h, Wortlaut mit "OneHour").
    h = sweep.check_catalog_freshness(int((now - dt.timedelta(hours=100)).timestamp() * 1e9), now=now)
    assert h["passed"] is False and "OneHour-Tick" in h["reason"]


def test_measurement_pass_reports_oneday_fields(catalog, tmp_path, monkeypatch):
    asyncio.run(hf.fetch_oneday_full_window(
        None, "1", "TSLA.ETORO", 2, 2, api_key="k", user_key="u", now=_NOW,
        fetch_chunk=_fetch_returning(_days(30, dt.date(2026, 10, 5)), [])))
    monkeypatch.setattr(sweep, "_load_symbol_bar_quality_sample", lambda s: None)
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "check_catalog_resolution_homogeneity",
                        lambda sym, **kw: {"effective_span_days": 29.0, "target_interval": kw["target_interval"]})
    rep = sweep.run_measurement_pass(symbols=["TSLA.ETORO"], run_id="t1276")
    m = rep["measurements"]["TSLA.ETORO"]
    assert m["oneday_n_candles"] == 30 and m["oneday_effective_span_days"] == 29.0
    assert m["oneday_oldest_utc"] == (dt.date(2026, 10, 5) - dt.timedelta(days=29)).isoformat()
