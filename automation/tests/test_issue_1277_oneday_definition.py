"""Issue #1277 (GH #1150, Katalog #1375, Pitfall #496) — OneDay-Kerzendefinition vermessen (read-only)."""
import datetime as dt
import hashlib
import json

import pytest

from automation import api_backfiller as ab
from automation import session_windows as sw
from automation.optimizer import oneday_definition as od

_UTC = dt.timezone.utc
_WINDOW = sw.SessionWindow(tz="America/New_York", open="09:30", close="16:00")
_DAYS = [dt.date(2026, 7, 6) + dt.timedelta(days=i) for i in range(5)]     # Mo–Fr, EDT


def _t(day, hour):
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=_UTC)


def _hourly_candles():
    out = []
    for n, day in enumerate(_DAYS):
        for h in range(24):
            p = 100.0 + n * 1.3 + h * 0.37
            out.append({"fromDate": _t(day, h).strftime("%Y-%m-%dT%H:%M:%SZ"), "open": p, "high": p + 0.5,
                        "low": p - 0.5, "close": p + 0.1, "volume": 1.0})
    return out


def _daily_from(hourly, lo_h, hi_h, day_offset=0):
    """Tageskerze je Tag aus den Stundenkerzen [lo_h, hi_h] (UTC-Stunden des Tages)."""
    out = []
    for n, day in enumerate(_DAYS):
        sel = [c for c in hourly if c["fromDate"].startswith(day.isoformat()) and lo_h <= int(c["fromDate"][11:13]) <= hi_h]
        out.append({"fromDate": f"{day.isoformat()}T00:00:00Z", "open": sel[0]["open"],
                    "high": max(c["high"] for c in sel), "low": min(c["low"] for c in sel),
                    "close": sel[-1]["close"], "volume": 1.0})
    return out


def _catalog(tmp_path, monkeypatch, daily):
    qt = tmp_path / "data" / "quote_tick"
    monkeypatch.setattr(ab, "QUOTE_TICK_PATH", qt)
    hourly = _hourly_candles()
    for candles, itv in ((hourly, "OneHour"), (daily, "OneDay")):
        table = ab._candles_to_arrow_table(candles, "TSLA.ETORO", 2, 2, dt.datetime(2026, 1, 1, tzinfo=_UTC),
                                           interval=itv)
        ab._merge_and_save(ab.log, table, "TSLA.ETORO", 2, 2, interval=itv)
    return tmp_path


@pytest.mark.parametrize("lo,hi,expected", [(13, 19, "rth_session"), (0, 23, "etoro_trading_day")])
def test_class_follows_the_hypothesis_the_daily_candle_was_built_from(tmp_path, monkeypatch, lo, hi, expected):
    cat = _catalog(tmp_path, monkeypatch, _daily_from(_hourly_candles(), lo, hi))
    res = od.run_oneday_definition(["TSLA.ETORO"], run_id="t1277", work_dir=tmp_path / "work", catalog_path=cat,
                                   windows={"TSLA.ETORO": _WINDOW}, overlap_start=_DAYS[0])
    sym = res["symbols"]["TSLA.ETORO"]
    assert sym["oneday_definition"] == expected
    table = sym["delta_table"]
    assert set(table) == set(od.HYPOTHESES)                       # vollständige Δ-Tabelle aller Hypothesen
    assert table[expected]["close_median_abs_delta_bps"] <= 1.0
    if expected == "etoro_trading_day":     # EDT: 20:00 Börsenzeit == 00:00Z ⇒ identisch mit dem UTC-Tag
        assert sym["tied_hypotheses"] == ["etoro_trading_day", "utc_day"]
    else:
        assert sym["tied_hypotheses"] == [expected]
    assert sym["n_oneday_candles"] == 5 and sym["non_trading_day_share"] == 0.0
    assert sym["from_date_utc_mode"] == "00:00" and sym["from_date_local_mode"] == "20:00"   # 00:00Z = 20:00 EDT Vortag
    rep = json.loads((tmp_path / "work" / "reports" / "oneday_definition_t1277.json").read_text("utf-8"))
    assert rep["symbols"]["TSLA.ETORO"]["oneday_definition"] == expected


def test_run_is_read_only(tmp_path, monkeypatch):
    cat = _catalog(tmp_path, monkeypatch, _daily_from(_hourly_candles(), 13, 19))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (cat / "data").rglob("*") if p.is_file()}
    od.run_oneday_definition(["TSLA.ETORO"], run_id="ro", work_dir=tmp_path / "work", catalog_path=cat,
                             windows={"TSLA.ETORO": _WINDOW}, overlap_start=_DAYS[0])
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (cat / "data").rglob("*") if p.is_file()}
    assert before == after


def test_classify_inconclusive_when_no_hypothesis_reaches_one_bps():
    assert od.classify({"rth_session": 3.2, "etoro_trading_day": 7.0, "utc_day": 12.0}) == "inconclusive"
    assert od.classify({"rth_session": 0.4, "etoro_trading_day": 0.2, "utc_day": 5.0}) == "etoro_trading_day"
    assert od.classify({"rth_session": None, "etoro_trading_day": None, "utc_day": None}) == "inconclusive"


def test_missing_data_is_inconclusive_not_an_error(tmp_path):
    res = od.run_oneday_definition(["NOPE.ETORO"], run_id="x", work_dir=tmp_path, catalog_path=tmp_path,
                                   windows={"NOPE.ETORO": _WINDOW})
    assert res["symbols"]["NOPE.ETORO"]["oneday_definition"] == "inconclusive"


def test_measurement_pass_embeds_the_classes(tmp_path, monkeypatch):
    from automation.optimizer import sweep
    monkeypatch.setattr(sweep, "_load_symbol_bar_quality_sample", lambda s: None)
    monkeypatch.setattr(sweep, "_oneday_measurement", lambda s: {})
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(od, "run_oneday_definition", lambda syms, **kw: {
        "symbols": {s: {"oneday_definition": "rth_session"} for s in syms}, "section_de": "## x"})
    rep = sweep.run_measurement_pass(symbols=["TSLA.ETORO"], run_id="m", oneday_definition=True)
    assert rep["oneday_definition"] == {"TSLA.ETORO": "rth_session"}
    assert "oneday_definition" not in sweep.run_measurement_pass(symbols=["TSLA.ETORO"], run_id="m2")
