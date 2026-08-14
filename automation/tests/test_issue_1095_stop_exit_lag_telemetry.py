"""Issue #1095 (Katalog #928) — Bars zwischen Trailing-Stop-Signal und tatsaechlichem Markt-Close-
Fill werden jetzt gemessen (``STOP_EXIT_LAG_BARS``-Order-Tag,
``hourly_strategy_base._execute_market_close``), bis zur Study-Ebene (``oos_stop_exit_lag_bars``)
durchgereicht.
"""
from automation.backtest_runner import _parse_exit_order_tags, _aggregate_exit_telemetry
from automation.optimizer import report as rpt


def test_parse_exit_order_tags_reads_stop_exit_lag_bars():
    meta = _parse_exit_order_tags(["EXIT_REASON:TRAILING_STOP", "STOP_EXIT_LAG_BARS:2"])
    assert meta == {"exit_reason": "TRAILING_STOP", "stop_exit_lag_bars": 2}


def test_parse_exit_order_tags_ignores_malformed_lag_value():
    meta = _parse_exit_order_tags(["STOP_EXIT_LAG_BARS:not_a_number"])
    assert "stop_exit_lag_bars" not in meta


def test_aggregate_exit_telemetry_medians_lag_over_trailing_stop_exits_only():
    meta_list = [
        {"exit_reason": "TRAILING_STOP", "pnl_bps": -50.0, "stop_exit_lag_bars": 0},
        {"exit_reason": "TRAILING_STOP", "pnl_bps": -30.0, "stop_exit_lag_bars": 2},
        {"exit_reason": "TIME_BOX", "pnl_bps": -10.0, "stop_exit_lag_bars": 5},  # nicht gezaehlt
    ]
    result = _aggregate_exit_telemetry(meta_list)
    assert result["stop_exit_lag_bars_median"] == 1.0
    assert result["n_trailing_stop_exits_with_lag_telemetry"] == 2


def test_aggregate_exit_telemetry_none_without_any_lag_tag():
    result = _aggregate_exit_telemetry([{"exit_reason": "TIME_BOX", "pnl_bps": -10.0}])
    assert result["stop_exit_lag_bars_median"] is None
    assert result["n_trailing_stop_exits_with_lag_telemetry"] == 0


def test_study_record_exports_oos_stop_exit_lag_bars_as_trial_median():
    trial_attrs = [
        {"oos_stop_exit_lag_bars_median": 1.0},
        {"oos_stop_exit_lag_bars_median": 3.0},
    ]
    value = rpt._median_of_trial_field(trial_attrs, "oos_stop_exit_lag_bars_median")
    assert value == 2.0
