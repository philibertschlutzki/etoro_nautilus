"""Issue #1372 (GH #1274, Pitfall #494) — der eToro-Candle-Endpunkt ist zählerbasiert:
Pagination-Probe, Einzelabruf ohne ``endTime``, API-Fenster, Horizont-Wächter."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from automation import historical_fetcher as hf

_SYM = "TSLA.ETORO"
_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    qt = tmp_path / "data" / "quote_tick"
    qt.mkdir(parents=True)
    monkeypatch.setattr(hf, "QUOTE_TICK_PATH", qt)
    monkeypatch.setattr(hf, "CATALOG_PATH", tmp_path)
    from automation import api_backfiller as ab
    monkeypatch.setattr(ab, "QUOTE_TICK_PATH", qt)
    monkeypatch.setattr(hf, "INCEPTION_CACHE_PATH", tmp_path / "state" / "inception_bounds.json")

    async def _no_sleep(*_a, **_k):
        return None
    monkeypatch.setattr(hf.asyncio, "sleep", _no_sleep)
    return tmp_path


class _Api:
    """Fixture-Server: ``honors_end_time`` entscheidet, ob ``endTime`` ausgewertet oder ignoriert wird."""

    def __init__(self, honors_end_time: bool, depth_days: float = 400.0):
        self.honors = honors_end_time
        self.depth = timedelta(days=depth_days)
        self.calls: list[tuple[str, datetime | None, int]] = []

    async def __call__(self, session, etoro_id, end_time, api_key, user_key, interval, count=1000):
        self.calls.append((interval, end_time, count))
        top = end_time if (self.honors and end_time is not None) else _NOW
        step = timedelta(hours=1) if interval == "OneHour" else timedelta(days=1)
        t = top.replace(minute=0, second=0, microsecond=0)
        if interval == "OneDay":
            t = t.replace(hour=0)
        if t >= top:
            t -= step
        out = []
        while t >= _NOW - self.depth and len(out) < count:
            out.append({"fromDate": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "open": 1.0, "high": 1.0,
                        "low": 1.0, "close": 1.0, "volume": 1.0})
            t -= step
        return out


# ─── Abnahme 1: Probe stempelt count_only bzw. end_time ───────────────────────────────

@pytest.mark.parametrize("honors,expected", [(False, "count_only"), (True, "end_time")])
def test_probe_stamps_the_pagination_mode(catalog, honors, expected):
    api = _Api(honors)
    mode = asyncio.run(hf.probe_pagination(None, "1", _SYM, "OneHour", api_key="k", user_key="u",
                                           fetch_chunk=api, now=_NOW))
    assert mode == expected
    assert [c[1] is None for c in api.calls] == [True, False]      # A ohne, B mit endTime
    stamp = json.loads(hf.INCEPTION_CACHE_PATH.read_text("utf-8"))[_SYM]["pagination"]["OneHour"]
    assert stamp == {"pagination_mode": expected, "probed_utc": "2026-10-07T12:00:00Z"}
    assert hf.pagination_mode(_SYM, "OneHour") == expected


def test_without_probe_the_mode_is_count_only(catalog):
    assert hf.pagination_mode(_SYM, "OneHour") == "count_only"


def test_old_inception_format_stays_readable(catalog):
    hf.INCEPTION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    hf.INCEPTION_CACHE_PATH.write_text(json.dumps({_SYM: 1_750_000_000_000_000_000}), "utf-8")
    assert hf.pagination_mode(_SYM, "OneDay") == "count_only"
    assert hf.inception_bound(_SYM, "OneHour") == 1_750_000_000_000_000_000


# ─── Abnahme 2: bei count_only kein endTime, genau ein Abruf je Intervall ──────────────

def test_count_only_sends_no_end_time_and_one_call_per_interval(catalog, monkeypatch):
    api = _Api(honors_end_time=False)
    monkeypatch.setattr(hf, "_fetch_candle_chunk", api)
    ok = asyncio.run(hf._fetch_symbol(None, "1", _SYM, 12, "k", "u", 2, 2,
                                      start_ns=int((_NOW - timedelta(days=30)).timestamp() * 1e9)))
    assert ok
    assert sorted(c[0] for c in api.calls) == ["OneDay", "OneHour"]
    assert all(c[1] is None and c[2] == 1000 for c in api.calls)
    win = json.loads(hf.INCEPTION_CACHE_PATH.read_text("utf-8"))[_SYM]["window"]["OneHour"]
    assert win["n_candles"] == 1000 and win["window_span_h"] == pytest.approx(1000.0, abs=2.0)
    assert win["window_oldest_utc"].endswith("Z")


def test_fetchers_omit_end_time_param_when_none():
    """Beide Fetcher bauen ``params`` nur mit ``endTime``, wenn ein Zeitpunkt übergeben wird."""
    import inspect
    from automation import api_backfiller as ab
    for fn in (hf._fetch_candle_chunk, ab._fetch_candles):
        src = inspect.getsource(fn)
        assert "if end_time is not None else {}" in src


def test_forward_step_count_only_is_a_single_request(catalog):
    api = _Api(honors_end_time=False)
    latest = _NOW - timedelta(hours=200)
    out = asyncio.run(hf.fetch_forward_candles(None, "1", _SYM, int(latest.timestamp() * 1e9),
                                               api_key="k", user_key="u", now=_NOW, fetch_chunk=api))
    assert len(api.calls) == 1 and api.calls[0][1] is None
    assert len(out) >= 200


# ─── Abnahme 3: Horizont-Wächter ──────────────────────────────────────────────────────

@pytest.mark.parametrize("factor,expected", [(0.3, None), (0.6, "FORWARD_GAP_NEAR_HORIZON"),
                                              (1.1, "FORWARD_GAP_UNRECOVERABLE")])
def test_horizon_guard(catalog, factor, expected, caplog):
    span_h = 1000.0
    latest = _NOW - timedelta(hours=factor * span_h)
    with caplog.at_level(logging.INFO):
        got = hf.check_forward_gap_horizon(_SYM, "OneHour", int(latest.timestamp() * 1e9), now=_NOW,
                                           window_span_h=span_h)
    assert got == expected
    events = [r.getMessage() for r in caplog.records if "FORWARD_GAP" in r.getMessage()]
    if expected is None:
        assert not events
    else:
        assert any(expected in e for e in events)
    if expected == "FORWARD_GAP_UNRECOVERABLE":
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert any("lost_interval" in e for e in events)


def test_horizon_guard_without_known_window_is_silent(catalog):
    assert hf.check_forward_gap_horizon(_SYM, "OneHour", 0, now=_NOW) is None
