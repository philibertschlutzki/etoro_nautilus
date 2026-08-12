"""Issue #1037 (Katalog #866) — eine Position, die am Ende der verfuegbaren Backtest-Daten noch
offen ist, wird von ``backtest_runner._finalize_round_trip`` zwangsweise zu EINEM Round-Trip
finalisiert (keine echte Handelsentscheidung). Root-Cause: dieser Round-Trip wurde zuvor ueber
``order_exit_meta`` aufgeloest, dessen ``closing_order_id`` in Wahrheit die ENTRY-Order war (kein
EXIT_REASON-Tag) — er zaehlte als UNKNOWN, ununterscheidbar vom Rest der UNKNOWN-Population
(#1034/B-9 des Katalogs)."""
from automation.backtest_runner import _aggregate_exit_telemetry
from automation.optimizer import invariants as inv


def test_data_end_round_trip_is_tagged_not_unknown():
    meta_list = [
        {"exit_reason": "TRAILING_STOP", "atr_median_bps": 10.0, "atr_min_bps": 5.0, "pnl_bps": 20.0},
        {"exit_reason": "DATA_END", "atr_median_bps": None, "atr_min_bps": None, "pnl_bps": -5.0},
    ]
    result = _aggregate_exit_telemetry(meta_list)
    assert result["exit_reason_histogram"] == {"TRAILING_STOP": 1, "DATA_END": 1}
    assert result["n_round_trips_data_end"] == 1


def test_no_data_end_round_trips_yields_zero():
    meta_list = [{"exit_reason": "TIME_BOX", "atr_median_bps": None, "atr_min_bps": None, "pnl_bps": 1.0}]
    result = _aggregate_exit_telemetry(meta_list)
    assert result["n_round_trips_data_end"] == 0
    assert "DATA_END" not in result["exit_reason_histogram"]


# ── invariants.check_open_position_at_data_end ──────────────────────────────────────────────────
def test_open_position_at_data_end_passes_below_threshold():
    records = [{
        "strategy": "TrendPullbackStrategy", "symbol": "XOM.ETORO",
        "n_round_trips_data_end": 1, "oos_total_trades_with_exit_telemetry": 200,
    }]
    result = inv.check_open_position_at_data_end(records)
    assert result.passed is True


def test_open_position_at_data_end_fails_above_threshold():
    """Beobachtete Symptomatik: eine Study mit bimodaler Haltedauerverteilung (kleiner Median,
    riesiges Maximum) — ein signifikanter Anteil DATA_END-Round-Trips ist die Signatur."""
    records = [{
        "strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO",
        "n_round_trips_data_end": 10, "oos_total_trades_with_exit_telemetry": 100,
    }]
    result = inv.check_open_position_at_data_end(records)
    assert result.passed is False
    assert result.severity == "high"
    assert "SqueezeBreakoutStrategy/TSLA.ETORO" in result.actual


def test_open_position_at_data_end_not_applicable_without_telemetry():
    result = inv.check_open_position_at_data_end([{"strategy": "S", "symbol": "A.ETORO"}])
    assert result.passed is True
    assert result.actual is None
