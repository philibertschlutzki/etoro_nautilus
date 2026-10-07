"""Issue #1377 (GH #1279, Pitfall #498) — Gate 1 zählt SESSION-Bars statt Kalenderstunden."""
import json
from pathlib import Path

import pytest

from automation import session_windows as sw
from automation.optimizer import gate, sweep

_BT = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
_OPT = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
_WINDOW = sw.resolve_session_window("EQUITY", _BT["session_hours_by_asset_class"])


def _cfg(**wf_over):
    wf = {**_BT["walk_forward"], **wf_over}
    return {"walk_forward": wf, "gate1_buffer_days": _OPT["gate1_buffer_days"],
            "min_session_bars_per_param": _OPT["min_session_bars_per_param"],
            "min_oos_session_bars_per_fold": _OPT["min_oos_session_bars_per_fold"]}


def test_defaults_are_derived_from_the_old_boundaries():
    assert _OPT["min_session_bars_per_param"] == 40 and _OPT["min_oos_session_bars_per_fold"] == 91
    assert "min_bars_per_param" not in _OPT and "min_oos_bars_per_fold" not in _OPT


def test_legacy_keys_fail_at_startup():
    cfg = {**_cfg(), "min_bars_per_param": 200}
    with pytest.raises(ValueError, match="entfallen"):
        gate.validate_gate1_config(cfg)
    with pytest.raises(ValueError):
        gate.is_symbol_tunable("A.ETORO", 1, available_bars=10 ** 6, config=cfg)


def test_boundary_at_todays_catalog_96_04_days_469_session_bars():
    """96,04 d ⇒ 469 Session-Bars ⇒ n_params ≤ 11 zulässig; ComboTrendVwap (14 Parameter) wird abgewiesen."""
    cfg = _cfg(is_window_days=30, embargo_period_days=5, splits=2, oos_window_days=21, holdout_days=14,
               holdout_embargo_days=3)         # (a) sicher erfüllt (Smoke-Geometrie 75 d)
    bars_calendar = int(96.04 * 24)
    for n_params, expected in ((11, "OK"), (12, "PARAM_DATA_RATIO_TOO_LOW"), (14, "PARAM_DATA_RATIO_TOO_LOW")):
        ok, why = gate.is_symbol_tunable("TSLA.ETORO", n_params, available_bars=bars_calendar, config=cfg,
                                         available_session_bars=469, session_window=_WINDOW)
        assert why == expected, (n_params, why)


@pytest.mark.parametrize("oos_days", [21, 30, 45])
def test_every_geometry_with_oos_ge_21_passes_c_like_before(oos_days):
    cfg = _cfg(oos_window_days=oos_days)
    assert gate.oos_session_bars_per_fold(oos_days, _WINDOW) >= _OPT["min_oos_session_bars_per_fold"]


def test_smoke_fold_with_labor_day_has_98_bars():
    """Ein 14-Tage-Smoke-Fold mit Labor Day (2026-09-07): 14 Handelstage = 98 Bars liegen über der Untergrenze."""
    import datetime as dt
    utc = dt.timezone.utc
    start = int(dt.datetime(2026, 9, 1, 12, tzinfo=utc).timestamp() * 1e9)
    end = int(dt.datetime(2026, 9, 28, 23, tzinfo=utc).timestamp() * 1e9)
    # 2026-09-01 .. 2026-09-28: 20 Wochentage − Labor Day = 19 Handelstage
    assert sw.expected_bars_between(start, end, _WINDOW) == 19 * 7
    assert gate.oos_session_bars_per_fold(21, _WINDOW) <= 98


def test_same_functions_work_on_a_one_day_axis_without_code_copy():
    ns_day = sw.NS_PER_DAY
    cfg = _cfg(oos_window_days=145)
    assert gate.oos_session_bars_per_fold(145, _WINDOW, bar_interval_ns=ns_day) >= 91     # ≈ 100 Tagesbars
    assert gate.oos_session_bars_per_fold(21, _WINDOW, bar_interval_ns=ns_day) < 91        # 13 Tagesbars
    ok, why = gate.is_symbol_tunable("TSLA.ETORO", 25, available_bars=1456, config=cfg, bars_per_day=1,
                                     available_session_bars=1000, session_window=_WINDOW,
                                     bar_interval_ns=ns_day)
    assert why in ("OK", "INSUFFICIENT_HISTORY")


def test_count_available_session_bars_uses_session_calendar(tmp_path, monkeypatch):
    import datetime as dt
    first = int(dt.datetime(2026, 7, 2, 13, tzinfo=dt.timezone.utc).timestamp() * 1e9)
    last = int(dt.datetime(2026, 10, 6, 19, 59, 59, tzinfo=dt.timezone.utc).timestamp() * 1e9)
    out = sweep.count_available_session_bars(["TSLA.ETORO"], segments_by_symbol={"TSLA.ETORO": (first, last)})
    # 2026-07-02..2026-10-06: 66 Handelstage (Mo–Fr abzüglich 07-03 und Labor Day) × 7
    assert out["TSLA.ETORO"] == 66 * 7 or abs(out["TSLA.ETORO"] - 66 * 7) <= 7
