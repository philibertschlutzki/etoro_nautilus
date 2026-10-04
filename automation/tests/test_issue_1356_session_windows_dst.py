"""Issue #1356 (GH #1252, P0, Termin 2026-11-01) — Session-Fenster und Opening-Range-Anker in
BÖRSEN-LOKALZEIT statt UTC-Konstanten.

``13:30-20:00 UTC`` ist NYSE-RTH nur während EDT: ab Mo 2026-11-02 (DST-Ende 2026-11-01) nahm das fixe
UTC-Fenster die 13:00-Kerze (08:00-09:00 ET, Pre-Market) auf und verwarf die Schlussstunde (20:00-Kerze,
15:00-16:00 ET); ``OpeningRangeBreakout`` bildete seine Range im Winter aus 08:00-11:00 ET.

Abnahme:
* 2026-10-30 (EDT): behaltene Kerzen 13:00-19:00 UTC; 2026-11-02 (EST): 14:00-20:00 UTC; je 7.
* Opening Range: an beiden Tagen ist die erste Range-Kerze diejenige, die 09:30 ET überlappt.
* ``BARS_PER_TRADING_DAY`` bleibt 7 (EDT und EST).
* NYSE-Feiertage sind Nicht-Handelstage (Coverage-Nenner, ``compute_holdout_bar_count``), kein Datenloch.
"""
from __future__ import annotations

import json
import logging
import types
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from automation import session_windows as sw
from automation.optimizer import _contracts
from automation.optimizer.trial_config import config_dir

_H = 3_600_000_000_000
_NY = {"tz": "America/New_York", "open": "09:30", "close": "16:00"}
_EDT_DAY = date(2026, 10, 30)    # Freitag, letzter EDT-Handelstag vor dem DST-Ende
_EST_DAY = date(2026, 11, 2)     # Montag, erster EST-Handelstag
_THANKSGIVING = date(2026, 11, 26)


def _ns(day: date, hour: int, minute: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


def _candle_hours(day: date, window) -> list[int]:
    return [h for h in range(24) if sw.interval_overlaps_session(_ns(day, h), _ns(day, h) + _H, window)]


def _ticks(day: date, minutes=(0,)) -> list:
    return [types.SimpleNamespace(ts_event=_ns(day, h, m)) for h in range(24) for m in minutes]


def _kept_candles(ticks) -> list[int]:
    return sorted({datetime.fromtimestamp(t.ts_event // 1_000_000_000, tz=timezone.utc).hour for t in ticks})


# ─── Konfiguration ────────────────────────────────────────────────────────────────────

def _backtest_cfg() -> dict:
    return json.loads((config_dir() / "backtest.json").read_text("utf-8"))


def test_backtest_json_declares_exchange_local_windows_and_drops_the_utc_hour_table():
    cfg = _backtest_cfg()
    for ac in ("EQUITY", "COMMODITY"):
        assert cfg["session_hours_by_asset_class"][ac] == _NY
    for ac in ("FOREX", "CRYPTO", "DEFAULT"):
        assert cfg["session_hours_by_asset_class"][ac] is None
    assert "opening_range_session_open_hour_by_asset_class" not in cfg


def test_contracts_reference_window_matches_backtest_json():
    """``_contracts`` dupliziert das EQUITY-Fenster bewusst (kein Datei-I/O) — dieser Test hält beide synchron."""
    resolved = sw.resolve_session_window("EQUITY", _backtest_cfg()["session_hours_by_asset_class"])
    assert resolved == _contracts._EQUITY_SESSION_WINDOW


def test_new_form_resolves_to_ny_window_with_nyse_calendar():
    w = sw.parse_session_window(_NY)
    assert (w.tz, w.open, w.close, w.calendar, w.legacy_utc) == ("America/New_York", "09:30", "16:00", "NYSE", False)


def test_legacy_utc_form_is_read_as_utc_with_a_warning_never_reinterpreted(caplog):
    sw._legacy_warned.clear()
    with caplog.at_level(logging.WARNING, logger="automation.session_windows"):
        w = sw.parse_session_window({"open_utc": "13:30", "close_utc": "20:00"}, label="EQUITY")
    assert (w.tz, w.open, w.close, w.legacy_utc) == ("UTC", "13:30", "20:00", True)
    assert any(sw.LEGACY_UTC_EVENT in r.getMessage() for r in caplog.records)
    # Das UTC-Fenster behält im Winter die Pre-Market-Kerze (das ist das Alt-Verhalten, sichtbar gemacht).
    assert _candle_hours(_EST_DAY, w) == [13, 14, 15, 16, 17, 18, 19]


@pytest.mark.parametrize("entry", [
    {"tz": "America/New_York", "open": "09:30", "close": "16:00", "open_utc": "13:30"},
    {"tz": "America/New_York", "open": "09:30"},
    {"tz": "Mars/Olympus", "open": "09:30", "close": "16:00"},
    {"tz": "America/New_York", "open": "16:00", "close": "09:30"},
    {"tz": "America/New_York", "open": "9h30", "close": "16:00"},
    {"tz": "America/New_York", "open": "09:30", "close": "16:00", "zone": "x"},
])
def test_invalid_entries_raise(entry):
    with pytest.raises(sw.SessionWindowConfigError):
        sw.parse_session_window(entry)


def test_null_entry_means_continuous_trading():
    assert sw.resolve_session_window("CRYPTO", {"CRYPTO": None}) is None
    assert sw.resolve_session_window("EQUITY", {}) is None
    assert sw.resolve_session_window(None, {"EQUITY": _NY}) is None


# ─── Abnahme: Kerzen je Handelstag ────────────────────────────────────────────────────────

def test_session_bounds_follow_dst():
    w = sw.parse_session_window(_NY)
    assert sw.session_bounds_utc_ns(_EDT_DAY, w) == (_ns(_EDT_DAY, 13, 30), _ns(_EDT_DAY, 20))
    assert sw.session_bounds_utc_ns(_EST_DAY, w) == (_ns(_EST_DAY, 14, 30), _ns(_EST_DAY, 21))


def test_candles_kept_edt_1300_to_1900_and_est_1400_to_2000_seven_each():
    w = sw.parse_session_window(_NY)
    assert _candle_hours(_EDT_DAY, w) == [13, 14, 15, 16, 17, 18, 19]
    assert _candle_hours(_EST_DAY, w) == [14, 15, 16, 17, 18, 19, 20]


@pytest.mark.parametrize("minutes", [(0,), (0, 15, 30, 45)])
def test_backtest_tick_filter_keeps_the_dst_correct_candles(minutes):
    """Der echte Tick-Filter des Backtests (``_filter_ticks_to_session_hours`` mit der ausgelieferten
    ``backtest.json``) — Stundenraster (Snap 09:30→09:00) und 15-Minuten-Raster (kein Snap nötig)."""
    from automation.backtest_runner import _filter_ticks_to_session_hours

    session_cfg = _backtest_cfg()["session_hours_by_asset_class"]
    for day, expected in ((_EDT_DAY, list(range(13, 20))), (_EST_DAY, list(range(14, 21)))):
        out: dict = {}
        kept = _filter_ticks_to_session_hours(_ticks(day, minutes), session_cfg, "EQUITY", out=out)
        assert _kept_candles(kept) == expected, day
        assert len(_kept_candles(kept)) == 7
        assert out["session_window_tz"] == "America/New_York"


def test_sweep_candle_membership_uses_the_same_window():
    from automation.optimizer import sweep

    w = sweep._resolve_session_window("EQUITY", _backtest_cfg()["session_hours_by_asset_class"])
    for day, expected in ((_EDT_DAY, list(range(13, 20))), (_EST_DAY, list(range(14, 21)))):
        got = [h for h in range(24) if sweep._candle_interval_overlaps_session_utc(_ns(day, h), _H, w)]
        assert got == expected


def test_point_test_uses_integer_ns_at_the_exact_boundaries():
    w = sw.parse_session_window(_NY)
    open_ns, close_ns = sw.session_bounds_utc_ns(_EST_DAY, w)
    assert sw.is_within_session(open_ns, w)
    assert not sw.is_within_session(open_ns - 1, w)
    assert sw.is_within_session(close_ns - 1, w)
    assert not sw.is_within_session(close_ns, w)
    mask = sw.SessionMask(w)
    assert [mask(t) for t in (open_ns - 1, open_ns, close_ns - 1, close_ns)] == [False, True, True, False]


# ─── BARS_PER_TRADING_DAY ─────────────────────────────────────────────────────────────

def test_bars_per_trading_day_stays_seven_in_edt_and_est():
    w = sw.parse_session_window(_NY)
    assert sw.bars_per_trading_day(w, _H, day=_EDT_DAY) == 7
    assert sw.bars_per_trading_day(w, _H, day=_EST_DAY) == 7
    assert sw.bars_per_trading_day(w, _H) == 7
    assert _contracts.BARS_PER_TRADING_DAY == 7


def test_a_dst_dependent_bar_count_raises_instead_of_rounding():
    w = sw.SessionWindow("America/New_York", "09:00", "15:00")
    assert sw.bars_per_trading_day(w, 2 * _H, day=_EDT_DAY) == 4
    assert sw.bars_per_trading_day(w, 2 * _H, day=_EST_DAY) == 3
    with pytest.raises(sw.SessionWindowConfigError, match="DST"):
        sw.bars_per_trading_day(w, 2 * _H)


# ─── Feiertage ────────────────────────────────────────────────────────────────────────

def test_nyse_holiday_table_covers_2025_to_2027_on_weekdays_only():
    holidays, coverage = sw.load_exchange_holidays()
    assert coverage["NYSE"] == (2025, 2027)
    nyse = holidays["NYSE"]
    assert all(d.weekday() < 5 for d in nyse)
    assert {y: sum(1 for d in nyse if d.year == y) for y in (2025, 2026, 2027)} == {2025: 11, 2026: 10, 2027: 10}
    assert {date(2026, 4, 3), date(2026, 7, 3), _THANKSGIVING, date(2027, 12, 24)} <= nyse


def test_holiday_is_a_non_trading_day_not_a_data_hole():
    from automation.backtest_runner import _filter_ticks_to_session_hours
    from automation.optimizer import sweep

    w = sw.parse_session_window(_NY)
    assert not sw.is_trading_day(_THANKSGIVING, w)
    assert _candle_hours(_THANKSGIVING, w) == []
    # Ticks NUR am Feiertag: leeres Ergebnis ohne SessionFilterEmptyError (wie ein Wochenende).
    session_cfg = _backtest_cfg()["session_hours_by_asset_class"]
    assert _filter_ticks_to_session_hours(_ticks(_THANKSGIVING), session_cfg, "EQUITY") == []
    # Coverage-Nenner: Woche Mo 23.11.-So 29.11.2026 = 4 Handelstage (Do Feiertag) × 7.
    start = pd.Timestamp("2026-11-23T14:00Z")
    end = pd.Timestamp("2026-11-29T20:00Z")
    assert sweep._bar_coverage_expected_bins(start, end, w) == 4 * 7
    # compute_holdout_bar_count mit bekanntem Fensterende: exakt (Feiertag ausgenommen).
    t = sweep.compute_holdout_bar_count(
        6, session_cfg, "EQUITY", end_ns=_ns(date(2026, 11, 29), 20))
    assert t == 4 * 7
    # Ohne Ende: Erwartungswert der Handelstage aus der Feiertagstabelle (Issue #1367; vorher 5/7).
    assert sweep.compute_holdout_bar_count(60, session_cfg, "EQUITY") == round(
        60 * sw.expected_trading_day_fraction(sw.parse_session_window(_NY)) * 7) == 288


def test_dates_beyond_the_holiday_table_warn_once_and_count_as_trading_days(caplog):
    w = sw.parse_session_window(_NY)
    sw._out_of_range_warned.clear()
    with caplog.at_level(logging.WARNING, logger="automation.session_windows"):
        assert sw.is_trading_day(date(2028, 11, 23), w)       # Thanksgiving 2028, Tabelle endet 2027
        assert sw.is_trading_day(date(2028, 11, 24), w)
    hits = [r for r in caplog.records if sw.HOLIDAYS_OUT_OF_RANGE_EVENT in r.getMessage()]
    assert len(hits) == 1


# ─── Opening Range ────────────────────────────────────────────────────────────────────

def _range_candles(day: date, *, or_bars: int = 3) -> list[int]:
    """Simuliert den Range-Aufbau von ``OpeningRangeBreakoutStrategy.on_bar`` über eine UNGEFILTERTE
    24h-Bar-Folge (``ts_event`` = Kerzenschluss): Start-Stunden der Kerzen, die die Range bilden."""
    from automation.strategies.opening_range_breakout import session_day_key

    w = sw.parse_session_window(_NY)
    or_day, count, out = None, 0, []
    for h in range(24):
        key = session_day_key(_ns(day, h) + _H, anchor="trading_day", session_window=w, bar_interval_ns=_H)
        if key is None:
            continue
        if key != or_day:
            or_day, count = key, 0
        if count < or_bars:
            out.append(h)
            count += 1
    return out


def test_first_range_candle_overlaps_0930_et_on_both_days():
    w = sw.parse_session_window(_NY)
    for day, first_hour in ((_EDT_DAY, 13), (_EST_DAY, 14)):
        candles = _range_candles(day)
        assert candles[0] == first_hour, day
        start = _ns(day, candles[0])
        open_ns = sw.session_bounds_utc_ns(day, w)[0]
        assert start <= open_ns < start + _H          # die Kerze überlappt 09:30 ET
        assert candles == [first_hour, first_hour + 1, first_hour + 2]


def test_trading_day_key_is_the_local_date_and_none_outside_the_session():
    from automation.strategies.opening_range_breakout import session_day_key

    w = sw.parse_session_window(_NY)
    assert session_day_key(_ns(_EST_DAY, 15), anchor="trading_day", session_window=w,
                           bar_interval_ns=_H) == _EST_DAY
    # 13:00-14:00 UTC am Wintertag = 08:00-09:00 ET (Pre-Market) ⇒ keine Range-Kerze.
    assert session_day_key(_ns(_EST_DAY, 14), anchor="trading_day", session_window=w,
                           bar_interval_ns=_H) is None
    assert session_day_key(_ns(_THANKSGIVING, 16), anchor="trading_day", session_window=w,
                           bar_interval_ns=_H) is None


def test_trading_day_without_window_equals_calendar_day():
    from automation.strategies.opening_range_breakout import session_day_key

    ts = _ns(_EST_DAY, 5)
    assert session_day_key(ts, anchor="trading_day") == session_day_key(ts, anchor="calendar_day") == 2


def test_utc_hour_anchor_is_gone():
    from automation.strategies.opening_range_breakout import OpeningRangeBreakoutConfig, session_day_key

    with pytest.raises(ValueError, match="1356"):
        session_day_key(_ns(_EST_DAY, 15), anchor="session_open_hour")
    fields = set(getattr(OpeningRangeBreakoutConfig, "__struct_fields__", ()) or ())
    if fields:   # echte msgspec-Struct (unter einem nautilus-Mock aus anderen Tests nicht prüfbar)
        assert "opening_range_session_open_hour" not in fields
        assert "session_window" in fields


def test_session_window_param_round_trip():
    w = sw.parse_session_window(_NY)
    param = sw.session_window_to_param(w)
    assert isinstance(param, str)
    assert sw.session_window_from_param(param) == w
    assert sw.session_window_from_param(None) is None
    assert sw.session_window_to_param(None) is None


def test_worker_no_longer_threads_the_utc_hour_table():
    import inspect

    import automation.backtest_runner as br

    assert "opening_range_session_open_hour_by_asset_class" not in inspect.signature(
        br.run_single_backtest_worker).parameters
    assert not hasattr(br, "resolve_opening_range_session_open_hour")
    assert "params[\"session_window\"] = session_window_to_param(" in inspect.getsource(br)


def test_snap_happens_in_local_time():
    w = sw.parse_session_window(_NY)
    snapped = sw.snap_window_to_grid(w, 3600.0)
    assert (snapped.tz, snapped.open, snapped.close) == ("America/New_York", "09:00", "16:00")
    assert sw.snap_window_to_grid(w, None) is w
    assert sw.snap_window_to_grid(w, 900.0).open == "09:30"


def test_bar_interval_parsed_from_bar_type():
    from automation.strategies.hourly_strategy_base import _bar_interval_ns_of

    assert _bar_interval_ns_of("BRK-B.ETORO-1-HOUR-MID-INTERNAL") == _H
    assert _bar_interval_ns_of("X.ETORO-15-MINUTE-MID-INTERNAL") == 15 * 60 * 1_000_000_000
    assert _bar_interval_ns_of(None) == _H
