import pytest
import logging
from unittest.mock import MagicMock

from automation.optimizer import gate, sweep

_DAY_NS = 86400 * 1_000_000_000
START_NS = 1700000000_000000000

def test_holdout_preflight_fail_open():
    """Test 1: test_holdout_preflight_fail_open"""
    assert gate.data_reaches_holdout_window(None, START_NS) == (True, "HOLDOUT_PREFLIGHT_UNAVAILABLE")
    assert gate.data_reaches_holdout_window(START_NS, None) == (True, "HOLDOUT_PREFLIGHT_UNAVAILABLE")
    assert gate.data_reaches_holdout_window(None, None) == (True, "HOLDOUT_PREFLIGHT_UNAVAILABLE")

def test_holdout_preflight_unreachable():
    """Test 2: test_holdout_preflight_unreachable"""
    newest = START_NS - 10 * _DAY_NS
    holdout_start = START_NS
    assert gate.data_reaches_holdout_window(newest, holdout_start) == (False, "HOLDOUT_WINDOW_UNREACHABLE")

def test_holdout_preflight_ok():
    """Test 3: test_holdout_preflight_ok"""
    newest = START_NS + 10 * _DAY_NS
    holdout_start = START_NS
    assert gate.data_reaches_holdout_window(newest, holdout_start) == (True, "OK")
    assert gate.data_reaches_holdout_window(START_NS, START_NS) == (True, "OK")

def test_sweep_enumeration_skips_on_holdout_unreachable(caplog):
    """Test 4: test_sweep_enumeration_skips_on_holdout_unreachable"""
    strategies = ["TestStrategy"]
    symbols = ["TEST.ETORO"]

    available_bars = {"TEST.ETORO": 1000}
    config = {
        "min_bars_per_param": 10,
        "min_oos_bars_per_fold": 10,
        "gate1_buffer_days": 10,
        "walk_forward": {
            "is_window_days": 90,
            "oos_window_days": 30,
            "splits": 2,
            "holdout_days": 30
        }
    }

    holdout_start_ns = START_NS
    newest_ns = holdout_start_ns - 5 * _DAY_NS

    latest_ts = {"TEST.ETORO": newest_ns}
    oos_start_ns = holdout_start_ns - 30 * _DAY_NS

    with pytest.MonkeyPatch.context() as m:
        m.setattr(sweep, "is_symbol_tunable", lambda *args, **kwargs: (True, "OK"))
        m.setattr(sweep, "n_params_for", lambda *args: 2)
        m.setattr(sweep, "data_reaches_oos_window", lambda newest, start, wf: (True, "OK", 0.0))
        m.setattr(sweep, "data_reaches_holdout_window", lambda newest, holdout_start: (False, "HOLDOUT_WINDOW_UNREACHABLE"))

        with caplog.at_level(logging.WARNING):
            pairs = sweep.enumerate_tunable_pairs(
                strategies=strategies,
                symbols=symbols,
                tier="all",
                available_bars=available_bars,
                config=config,
                latest_ts=latest_ts,
                start_ns=oos_start_ns,
                holdout_window_reach_target_ns=holdout_start_ns,
            )

    assert len(pairs) == 0
    assert "übersprungen (HOLDOUT_WINDOW_UNREACHABLE)" in caplog.text
    assert "5.0 Tage VOR der geforderten Holdout-Coverage-Grenze" in caplog.text
