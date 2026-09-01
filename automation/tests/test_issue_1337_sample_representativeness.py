"""Issue #1337 (GH #1231) — der Bar-Qualitäts-Preflight urteilt nicht mehr blockierend über eine
Stichprobe, die die geforderte Walk-Forward-Spanne nicht abdeckt: ``sample_covers_required_span``/
``sample_span_days`` machen die Repräsentativität explizit, und ein zu kurzes Fenster liefert
INCONCLUSIVE (``passed=None``, Tri-State #1307), nicht FAIL.
"""
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from automation.optimizer import sweep
from automation.optimizer.sweep_diagnostics import check_bar_quality
from automation._serde import encode_price_fsb16


def test_check_bar_quality_is_inconclusive_when_sample_span_too_short():
    result = check_bar_quality(
        [101.0] * 20, [99.0] * 20, [100.0] * 20, min_distinct_closes=1,
        sample_covers_required_span=False, sample_span_days=70.0)
    assert result["passed"] is None
    assert result["severity"] == "blocking"
    assert "INCONCLUSIVE" in result["reason"]


def test_check_bar_quality_never_reports_inconclusive_as_pass_or_fail():
    result = check_bar_quality(
        [101.0] * 20, [99.0] * 20, [100.0] * 20, min_distinct_closes=1,
        sample_covers_required_span=False, sample_span_days=70.0)
    assert result["passed"] is not True
    assert result["passed"] is not False
    assert result["passed"] is None


def test_check_bar_quality_default_sample_covers_required_span_is_backward_compatible():
    """``sample_covers_required_span`` Default True ⇒ bit-identisches Alt-Verhalten fuer Aufrufer,
    die das neue Feld nicht kennen."""
    closes = [100.0 + i * 0.3 for i in range(30)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    result = check_bar_quality(highs, lows, closes, min_distinct_closes=5)
    assert result["passed"] is True


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


def test_sample_covers_required_span_false_for_a_70_day_window_against_426_required(tmp_path):
    start = datetime(2026, 6, 22, tzinfo=timezone.utc)
    ts_list = [int((start + timedelta(hours=i)).timestamp() * 1e9) for i in range(24 * 70)]
    _write_quote_tick_parquet(tmp_path, "NVDA.ETORO", ts_list)

    sample = sweep._load_symbol_bar_quality_sample(
        "NVDA.ETORO", catalog_path=tmp_path, required_span_days=426)
    assert sample is not None
    assert sample["sample_covers_required_span"] is False
    assert sample["sample_span_days"] < 426


def test_sample_covers_required_span_true_when_no_requirement_given(tmp_path):
    start = datetime(2026, 6, 22, tzinfo=timezone.utc)
    ts_list = [int((start + timedelta(hours=i)).timestamp() * 1e9) for i in range(24 * 70)]
    _write_quote_tick_parquet(tmp_path, "NVDA.ETORO", ts_list)

    sample = sweep._load_symbol_bar_quality_sample("NVDA.ETORO", catalog_path=tmp_path)
    assert sample is not None
    assert sample["sample_covers_required_span"] is True
