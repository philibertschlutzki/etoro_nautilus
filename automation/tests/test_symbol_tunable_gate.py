"""A4.4 — Gate 1 (data sufficiency) tests.

Pure / I/O-free. Thresholds are read from backtest.json + optimizer.json (HI-6):
no duplicated magic literals in the asserts — the test derives the boundary from
the same config the production code uses.
"""
import json
from pathlib import Path

from automation.optimizer import gate


def _cfg():
    bt = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    opt = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    return {"walk_forward": bt["walk_forward"],
            **{k: opt[k] for k in ("gate1_buffer_days", "min_bars_per_param", "min_oos_bars_per_fold")}}


def _need(cfg, bars_per_day=24):
    wf = cfg["walk_forward"]
    return gate.required_bars(
        is_window_days=wf["is_window_days"], oos_window_days=wf["oos_window_days"],
        splits=wf["splits"], holdout_days=wf["holdout_days"],
        buffer_days=cfg["gate1_buffer_days"], bars_per_day=bars_per_day)


def test_required_bars_formula():
    assert gate.required_bars(is_window_days=180, oos_window_days=45, splits=4,
                              holdout_days=45, buffer_days=30) == (180 + 4 * 45 + 45 + 30) * 24
    # bars_per_day is configurable: (10 + 2*5 + 5 + 0) * 1 == 25
    assert gate.required_bars(is_window_days=10, oos_window_days=5, splits=2,
                              holdout_days=5, buffer_days=0, bars_per_day=1) == 25


def test_sufficient_passes():
    cfg = _cfg()
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=6, available_bars=_need(cfg) + 1000, config=cfg)
    assert ok and why == "OK"


# --- Gate 1a: history --------------------------------------------------------
def test_insufficient_history():
    cfg = _cfg()
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=6, available_bars=_need(cfg) - 1, config=cfg)
    assert not ok and why == "INSUFFICIENT_HISTORY"


def test_history_boundary_exact_is_inclusive():
    """available_bars == required_bars passes the history gate (>=), boundary for gate1_buffer_days."""
    cfg = _cfg()
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=1, available_bars=_need(cfg), config=cfg)
    assert why != "INSUFFICIENT_HISTORY"


def test_buffer_days_shifts_history_threshold():
    """Raising gate1_buffer_days raises required_bars — the same bar count now fails (boundary test)."""
    cfg = _cfg()
    avail = _need(cfg)
    cfg2 = {**cfg, "gate1_buffer_days": cfg["gate1_buffer_days"] + 1}
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=1, available_bars=avail, config=cfg2)
    assert not ok and why == "INSUFFICIENT_HISTORY"


# --- Gate 1b: bars-per-param ratio ------------------------------------------
def test_param_data_ratio():
    cfg = _cfg()
    cfg["min_bars_per_param"] = 100_000  # force the ratio to fail
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=8, available_bars=_need(cfg) + 1000, config=cfg)
    assert not ok and why == "PARAM_DATA_RATIO_TOO_LOW"


def test_param_ratio_boundary():
    """available_bars / n_params == min_bars_per_param passes; one more param fails (boundary)."""
    cfg = _cfg()
    mbp = cfg["min_bars_per_param"]
    # exact multiple of mbp that also clears the history gate
    avail = mbp * (_need(cfg) // mbp + 5)
    n_ok = avail // mbp                       # avail / n_ok == mbp exactly -> passes (>=)
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=n_ok, available_bars=avail, config=cfg)
    assert why != "PARAM_DATA_RATIO_TOO_LOW"
    ok2, why2 = gate.is_symbol_tunable("A.ETORO", n_params=n_ok + 1, available_bars=avail, config=cfg)
    assert not ok2 and why2 == "PARAM_DATA_RATIO_TOO_LOW"


# --- Gate 1c: OOS fold length -----------------------------------------------
def test_oos_fold_too_short():
    cfg = _cfg()
    cfg["min_oos_bars_per_fold"] = 10 ** 9  # force the OOS-fold check to fail
    # huge history so (a) and (b) pass, isolating the OOS-fold rejection
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=1, available_bars=10 ** 9, config=cfg)
    assert not ok and why == "OOS_FOLD_TOO_SHORT"
