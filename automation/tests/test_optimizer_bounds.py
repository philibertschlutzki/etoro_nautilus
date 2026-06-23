"""A4.0 — tests for the declarative numeric search-space bounds extractor.

End-to-end over the public API (real SmaCrossover / ComboTrendVwap search spaces),
no backtest / no I/O.
"""
import pytest

from automation.optimizer import bounds


def test_bounds_sma_exact():
    b = bounds.extract_numeric_bounds("SmaCrossoverStrategy")
    assert b["sma_period"] == (5, 60)
    assert b["cooldown_bars"] == (2, 36)


def test_bounds_combo_excludes_categorical_and_derived():
    b = bounds.extract_numeric_bounds("ComboTrendVwapStrategy")
    assert b["sma_period"] == (20, 100)
    assert b["bb_std_dev"] == (1.0, 2.5)
    assert "require_vwap_confirmation" not in b   # categorical
    assert "require_bb_touch" not in b            # categorical
    assert "macd_slow" not in b                   # derived (macd_fast + macd_gap)


def test_bounds_unknown_raises():
    with pytest.raises(ValueError):
        bounds.extract_numeric_bounds("DoesNotExist")


def test_distance_zero_when_equal():
    b = {"x": (0.0, 10.0)}
    assert bounds.normalized_param_distance({"x": 5}, {"x": 5}, b) == 0.0


def test_distance_normalized():
    b = {"x": (0.0, 10.0), "y": (0.0, 4.0)}
    # x: (8-2)/10 = 0.6 -> 0.36 ; y: (1-3)/4 = -0.5 -> 0.25 ; mean = 0.305
    d = bounds.normalized_param_distance({"x": 8, "y": 1}, {"x": 2, "y": 3}, b)
    assert d == pytest.approx(0.305, rel=1e-9)


def test_distance_no_common_keys_is_zero():
    b = {"x": (0.0, 10.0)}
    assert bounds.normalized_param_distance({"y": 5}, {"x": 5}, b) == 0.0


def test_distance_zero_span_does_not_divide_by_zero():
    # A degenerate bound (lo == hi) must not raise; span falls back to 1.0.
    b = {"x": (3.0, 3.0)}
    assert bounds.normalized_param_distance({"x": 5}, {"x": 4}, b) == pytest.approx(1.0)
