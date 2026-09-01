"""Issue #1336 (GH #1230) — ``bar_coverage_ratio``: der Nenner ist die erwartete Zahl RTH-Bins im
Fenster, nicht mehr die rohe 24/7-Kalenderstundendifferenz.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from automation.optimizer import sweep
from automation._serde import encode_price_fsb16

_NS_PER_HOUR = 3_600_000_000_000
_EQUITY_SESSION = {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}


def test_expected_session_bins_per_day_is_seven_for_equity_1330_2000():
    assert sweep._expected_session_bins_per_day("13:30", "20:00") == 7


def test_expected_session_bins_per_day_is_six_for_aligned_1400_2000():
    assert sweep._expected_session_bins_per_day("14:00", "20:00") == 6


def test_bar_coverage_expected_bins_counts_only_weekdays():
    # 2026-06-22 ist ein Montag; ein Fenster ueber genau eine volle Handelswoche (Mo-Fr).
    start = pd.Timestamp("2026-06-22T14:00:00Z")
    end = pd.Timestamp("2026-06-26T19:00:00Z")
    expected = sweep._bar_coverage_expected_bins(start, end, "13:30", "20:00")
    assert expected == 7 * 5  # 5 Handelstage x 7 Bins


def _write_quote_tick_parquet(tmp_path, symbol, ts_ns_list, price=100.0):
    d = tmp_path / "data" / "quote_tick" / symbol
    d.mkdir(parents=True, exist_ok=True)
    n = len(ts_ns_list)
    _FSB16 = pa.binary(16)
    table = pa.table({
        "bid_price": pa.array([encode_price_fsb16(price, 2)] * n, type=_FSB16),
        "ask_price": pa.array([encode_price_fsb16(price + 0.02, 2)] * n, type=_FSB16),
        "ts_event": pa.array(ts_ns_list, type=pa.int64()),
    })
    pq.write_table(table, str(d / "data.parquet"))


def _rth_ticks_for_weekdays(n_weeks: int, start: datetime, hours=(13, 14, 15, 16, 17, 18, 19)):
    """Ein Tick je Session-Stunde (0..6=13:00..19:00-Kerzen) an jedem Handelstag."""
    ts = []
    day = start
    weeks_done = 0
    d = 0
    while weeks_done < n_weeks:
        if day.weekday() < 5:
            for h in hours:
                ts.append(int((day.replace(hour=h, minute=0, second=0, microsecond=0)).timestamp() * 1e9))
        day = day + timedelta(days=1)
        d += 1
        if day.weekday() == 0:
            weeks_done += 1
    return ts


def test_full_rth_coverage_yields_ratio_near_one_not_0_177(tmp_path):
    """Reproduziert das #1246-Symptom: ein auf der RTH-Achse LUECKENLOSES Raster darf nicht mehr
    als ueberwiegend luckenhaft (0.177) verworfen werden."""
    monday = datetime(2026, 6, 22, tzinfo=timezone.utc)
    ts_list = _rth_ticks_for_weekdays(10, monday)
    _write_quote_tick_parquet(tmp_path, "NVDA.ETORO", ts_list)

    sample = sweep._load_symbol_bar_quality_sample(
        "NVDA.ETORO", catalog_path=tmp_path,
        session_hours_by_asset_class=_EQUITY_SESSION, asset_class_key="EQUITY")

    assert sample is not None
    assert sample["bar_coverage_ratio"] >= 0.95
    assert sample["bar_coverage_expected_bins"] > 0


def test_half_missing_session_hours_yields_ratio_near_half_and_still_fails():
    from automation.optimizer.sweep_diagnostics import check_bar_quality
    result = check_bar_quality(
        [100.0] * 20, [99.0] * 20, [99.5] * 20,
        min_distinct_closes=1, bar_coverage_ratio=0.5, min_bar_coverage_ratio=0.6)
    assert result["passed"] is False
    assert "bar_coverage_ratio" in result["reason"]


def test_crypto_without_session_window_keeps_calendar_denominator(tmp_path):
    """CRYPTO (kein Session-Fenster konfiguriert) bleibt beim 24/7-Kalendernenner."""
    start = datetime(2026, 6, 22, tzinfo=timezone.utc)
    ts_list = [int((start + timedelta(hours=i)).timestamp() * 1e9) for i in range(200)]
    _write_quote_tick_parquet(tmp_path, "BTC.ETORO", ts_list)

    sample_no_window = sweep._load_symbol_bar_quality_sample(
        "BTC.ETORO", catalog_path=tmp_path,
        session_hours_by_asset_class=_EQUITY_SESSION, asset_class_key=None)
    assert sample_no_window is not None
    # 200 aufeinanderfolgende Stunden-Ticks -> voll besetztes Kalenderraster -> ratio nahe 1.0
    assert sample_no_window["bar_coverage_ratio"] > 0.95


def test_min_bar_coverage_ratio_unchanged_at_0_6():
    import json
    from pathlib import Path
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["bar_quality"]["min_bar_coverage_ratio"] == 0.6
