"""Issue #1382 (GH #1284, Pitfall #503) — EINE Quelle für die Bar-Achse (``automation/bar_axis.py``), Tages-Tick-
Expansion in der Session, Session-Semantik OneDay, Study-Identität, Live-Sperre, Profil ``daily``."""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pyarrow.compute as pc
import pytest

from automation import api_backfiller as ab
from automation import bar_axis
from automation import session_windows as sw
from automation.optimizer import config_profile, deployment_gate, invariants, report

_REPO = Path(__file__).resolve().parents[2]
_UTC = dt.timezone.utc
_NYSE = sw.SessionWindow("America/New_York", "09:30", "16:00", calendar="NYSE")


# ─── Achsen-Tabelle ───────────────────────────────────────────────────────────────────

def test_axis_table_is_the_single_source():
    h, d = bar_axis.AXES["OneHour"], bar_axis.AXES["OneDay"]
    assert (h.bar_interval_ns, h.bars_per_trading_day, h.bar_type_suffix) == (3_600_000_000_000, 7, "1-HOUR")
    assert (d.bar_interval_ns, d.bars_per_trading_day, d.bar_type_suffix) == (86_400_000_000_000, 1, "1-DAY")
    assert d.bar_type("TSLA.ETORO") == "TSLA.ETORO-1-DAY-MID-INTERNAL"
    assert h.bars_per_year == 252 * 7 and d.bars_per_year == 252
    assert bar_axis.get_axis(None) is h and not h.trading_day_bars and d.trading_day_bars
    with pytest.raises(bar_axis.BarAxisConfigError):
        bar_axis.get_axis("OneMinute")


_AXIS_CONSUMERS = [
    *sorted((_REPO / "automation" / "optimizer").glob("*.py")),
    _REPO / "automation" / "backtest_runner.py", _REPO / "automation" / "catalog_paths.py",
    *sorted((_REPO / "automation" / "strategies").glob("*.py")),
    _REPO / "automation" / "momentum_ls_run.py", _REPO / "automation" / "adapters" / "etoro_execution.py",
    _REPO / "automation" / "ai_loop" / "backtest_bridge.py",
]


def _literal_offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text("utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Constant) or id(n) in docstrings:
            continue
        v = n.value
        if v == "OneHour" or v == 3_600_000_000_000 or (isinstance(v, str) and "-1-HOUR-" in v):
            out.append(f"{path.name}:{n.lineno}: {v!r}")
    return out


def test_no_hourly_literals_outside_the_axis_table():
    offenders = [o for p in _AXIS_CONSUMERS for o in _literal_offenders(p)]
    assert not offenders, offenders


# ─── Produktion bleibt bit-identisch ──────────────────────────────────────────────────

def test_production_constants_and_names_are_unchanged():
    from automation.optimizer import _contracts as c
    assert (c.BARS_PER_TRADING_DAY, c.TIME_BOX_BARS, c.MAX_BARS_IN_TRADE_HARD_CAP) == (7, 7.0, 7)
    assert c._HOURLY_BAR_INTERVAL_NS == 3_600_000_000_000
    assert bar_axis.active_axis_name() == "OneHour"
    assert bar_axis.study_suffix() == "" and bar_axis.fingerprint_component() is None


def test_result_fingerprint_is_bit_identical_on_the_default_axis_and_differs_on_oneday(monkeypatch):
    rows = [{"strategy": "S", "symbol": "TSLA.ETORO", "n_trials": 3, "best_reward": 1.0, "n_eligible": 2}]
    expected = hashlib.sha256("\x1e".join(["S", "TSLA.ETORO", "3", "1.0", "2"]).encode()).hexdigest()
    assert report.compute_result_fingerprint(rows) == expected
    monkeypatch.setattr(bar_axis, "active_axis_name", lambda cfg_dir=None: "OneDay")
    assert report.compute_result_fingerprint(rows) != expected
    assert bar_axis.study_suffix() == "_OneDay"


# ─── Profil daily ─────────────────────────────────────────────────────────────────────

def test_daily_profile_derives_the_axis_and_geometry(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(_REPO / "automation" / "config", root / "automation" / "config")
    overlay = config_profile.materialize("daily", project_root=root)
    bt = json.loads((overlay / "backtest.json").read_text("utf-8"))
    wf = bt["walk_forward"]
    assert bt["bar_axis"] == "OneDay" and bt["max_handelstage"] == 5
    assert wf["is_window_days"] + wf["embargo_period_days"] + wf["splits"] * wf["oos_window_days"] \
        + wf["holdout_days"] + wf["holdout_embargo_days"] == 1280 <= 1456
    code = ("from automation.optimizer import _contracts as c, trial_config as t\n"
            "from automation import bar_axis\n"
            "print(json.dumps([bar_axis.active_axis_name(), c.BARS_PER_TRADING_DAY, c.TIME_BOX_BARS, "
            "c.MAX_BARS_IN_TRADE_HARD_CAP, t.holdout_embargo_floor_days(), bar_axis.study_suffix()]))")
    res = subprocess.run([sys.executable, "-c", "import json\n" + code], cwd=_REPO, capture_output=True,
                         text=True, env={**os.environ, "ETORO_CONFIG_DIR": str(overlay)})
    assert json.loads(res.stdout) == ["OneDay", 1, 5.0, 5, 6, "_OneDay"], res.stderr[-400:]
    assert wf["holdout_embargo_days"] >= 6                          # Untergrenze aus holdout_embargo_floor_days


# ─── Tick-Expansion OneDay in der Session ─────────────────────────────────────────────

def _daily(day: dt.date) -> dict:
    return {"fromDate": f"{day.isoformat()}T00:00:00Z", "open": 100.0, "high": 103.0, "low": 98.0,
            "close": 101.0, "volume": 8.0}


def test_oneday_ticks_lie_inside_the_session_of_the_trading_day():
    friday, saturday = dt.date(2026, 7, 10), dt.date(2026, 7, 11)       # EDT
    table = ab._candles_to_arrow_table(
        [_daily(friday), _daily(saturday)], "TSLA.ETORO", 2, 2, dt.datetime(2026, 1, 1, tzinfo=_UTC),
        interval="OneDay", oneday_session_window=_NYSE)
    ts = sorted(table.column("ts_event").to_pylist())
    open_ns, close_ns = sw.session_bounds_utc_ns(friday, _NYSE)
    assert len(ts) == 4                                                   # Samstag nicht geschrieben
    assert ts[0] == open_ns and ts[-1] == close_ns - 1                    # O bei Open, C bei Close − 1 ns
    assert all(open_ns <= t < close_ns for t in ts)
    assert set(table.column("bar_interval_ns").to_pylist()) == {86_400_000_000_000}


def test_oneday_without_window_keeps_the_legacy_expansion():
    table = ab._candles_to_arrow_table([_daily(dt.date(2026, 7, 11))], "X", 2, 2,
                                       dt.datetime(2026, 1, 1, tzinfo=_UTC), interval="OneDay")
    assert len(table) == 4 and pc.min(table.column("ts_event")).as_py() == int(
        dt.datetime(2026, 7, 11, tzinfo=_UTC).timestamp()) * 10 ** 9


def test_invariant_passes_for_session_ticks_and_fails_for_legacy_or_weekend_ticks():
    day = dt.date(2026, 7, 10)
    good = ab._candles_to_arrow_table([_daily(day)], "X", 2, 2, dt.datetime(2026, 1, 1, tzinfo=_UTC),
                                      interval="OneDay", oneday_session_window=_NYSE).column("ts_event").to_pylist()
    legacy = ab._candles_to_arrow_table([_daily(day)], "X", 2, 2, dt.datetime(2026, 1, 1, tzinfo=_UTC),
                                        interval="OneDay").column("ts_event").to_pylist()
    weekend = [sw.session_bounds_utc_ns(dt.date(2026, 7, 11), _NYSE)[0] + 1]
    assert invariants.check_oneday_ticks_within_session(good, _NYSE).passed is True
    bad = invariants.check_oneday_ticks_within_session(legacy, _NYSE)
    assert bad.passed is False and bad.severity == "blocking" and bad.actual["n_outside_session"] > 0
    assert invariants.check_oneday_ticks_within_session(weekend, _NYSE).passed is False
    assert invariants.check_oneday_ticks_within_session(good + good, _NYSE).passed is False   # zwei Kerzen/Tag
    assert invariants.check_oneday_ticks_within_session(good, None).passed is None
    assert "REJECT_ONEDAY_TICKS_OUTSIDE_SESSION" in invariants.PER_SYMBOL_REJECTION_CODES


# ─── Session-Semantik OneDay ──────────────────────────────────────────────────────────

def test_oneday_axis_uses_the_trading_day_not_a_point_test(monkeypatch):
    from automation.backtest_runner import _filter_ticks_to_session_hours
    monkeypatch.setattr(bar_axis, "active_axis", lambda cfg_dir=None: bar_axis.AXES["OneDay"])
    cfg = {"EQUITY": {"tz": "America/New_York", "open": "09:30", "close": "16:00", "calendar": "NYSE"}}

    def tick(day, hour):
        return types.SimpleNamespace(ts_event=int(dt.datetime(day.year, day.month, day.day, hour,
                                                              tzinfo=_UTC).timestamp()) * 10 ** 9)
    ticks = [tick(dt.date(2026, 7, 10), 14), tick(dt.date(2026, 7, 10), 22),       # Freitag: beide behalten
             tick(dt.date(2026, 7, 11), 14), tick(dt.date(2026, 7, 3), 14)]          # Samstag, Feiertag: verworfen
    kept = _filter_ticks_to_session_hours(ticks, cfg, "EQUITY")
    assert [t.ts_event for t in kept] == [ticks[0].ts_event, ticks[1].ts_event]


# ─── Katalog-/Engine-Sichten sind je Auflösung getrennt ───────────────────────────────

def test_engine_views_never_mix_oneday_and_onehour_ticks(tmp_path, monkeypatch):
    from automation.catalog_paths import engine_catalog_view
    import pyarrow.parquet as pq
    qt = tmp_path / "data" / "quote_tick"
    monkeypatch.setattr(ab, "QUOTE_TICK_PATH", qt)
    hourly = [{"fromDate": "2026-07-10T14:00:00Z", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1}]
    for candles, itv in ((hourly, "OneHour"), ([_daily(dt.date(2026, 7, 10))], "OneDay")):
        table = ab._candles_to_arrow_table(candles, "TSLA.ETORO", 2, 2, dt.datetime(2026, 1, 1, tzinfo=_UTC),
                                           interval=itv)
        ab._merge_and_save(ab.log, table, "TSLA.ETORO", 2, 2, interval=itv)
    for itv, ns in ((None, 3_600_000_000_000), ("OneDay", 86_400_000_000_000)):
        with engine_catalog_view(tmp_path, "TSLA.ETORO", interval=itv) as view:
            col = pq.read_table(str(view.data_file), columns=["bar_interval_ns"]).column("bar_interval_ns")
            assert set(col.to_pylist()) == {ns}


# ─── Live gesperrt ────────────────────────────────────────────────────────────────────

def test_live_lock_clause_blocks_non_hourly_records_and_accepts_legacy_records():
    clause = deployment_gate._clause_bar_axis_live_supported
    assert clause({"bar_axis": "OneHour"}) is True
    assert clause({"bar_axis": "OneDay"}) is False
    assert clause({"status": "ready_for_pr"}) is True            # Record vor #1382: Stundenachse per Konstruktion
    assert clause(None) is None
    assert deployment_gate.DEPLOYMENT_CLAUSES[-1] == "bar_axis_live_supported"
    assert bar_axis.LIVE_AXIS == "OneHour"
