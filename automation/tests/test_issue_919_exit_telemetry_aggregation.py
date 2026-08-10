"""Issue #919 — Exit-Telemetrie (Order-Tags, #899) liegt bereits PRO TRIAL vor, wurde aber nie zu
einem Study-Aggregat zusammengefasst — invariants.check_effective_stop_distance liegt seit #897
vor, aber der zweite Teil (exit_reason_histogram je Study, Coverage-Invariante) fehlte.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


def test_sum_exit_reason_histograms_aggregates_across_trials():
    trial_attrs = [
        {"oos_exit_reason_histogram": {"TRAILING_STOP": 3, "TIME_BOX": 1}},
        {"oos_exit_reason_histogram": {"TRAILING_STOP": 2, "PROFIT_TARGET": 4}},
        {"oos_exit_reason_histogram": None},
    ]
    result = rpt._sum_exit_reason_histograms(trial_attrs)
    assert result == {"TRAILING_STOP": 5, "TIME_BOX": 1, "PROFIT_TARGET": 4}


def test_time_box_exit_fraction():
    trial_attrs = [{"oos_exit_reason_histogram": {"TRAILING_STOP": 3, "TIME_BOX": 1}}]
    assert rpt._time_box_exit_fraction(trial_attrs) == 0.25


def test_time_box_exit_fraction_none_without_telemetry():
    assert rpt._time_box_exit_fraction([{"oos_exit_reason_histogram": None}]) is None
    assert rpt._time_box_exit_fraction([]) is None


def test_check_exit_reason_coverage_passes_on_matching_sums():
    records = [{
        "strategy": "TrendPullbackStrategy", "symbol": "XOM.ETORO",
        "exit_reason_histogram": {"TRAILING_STOP": 5, "TIME_BOX": 3},
        "oos_total_trades_with_exit_telemetry": 8,
    }]
    result = inv.check_exit_reason_coverage(records)
    assert result.passed is True


def test_check_exit_reason_coverage_fails_on_a_gap():
    records = [{
        "strategy": "TrendPullbackStrategy", "symbol": "XOM.ETORO",
        "exit_reason_histogram": {"TRAILING_STOP": 5, "TIME_BOX": 3},
        "oos_total_trades_with_exit_telemetry": 10,  # 2 round-trips with no attribution
    }]
    result = inv.check_exit_reason_coverage(records)
    assert result.passed is False
    assert "TrendPullbackStrategy/XOM.ETORO" in result.actual


def test_check_exit_reason_coverage_not_applicable_without_telemetry():
    records = [{
        "strategy": "TrendPullbackStrategy", "symbol": "XOM.ETORO",
        "exit_reason_histogram": {}, "oos_total_trades_with_exit_telemetry": 0,
    }]
    result = inv.check_exit_reason_coverage(records)
    assert result.passed is True
