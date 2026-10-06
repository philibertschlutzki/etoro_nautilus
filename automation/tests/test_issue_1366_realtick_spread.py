"""Issue #1366 (GH #1263, P2) — Echt-Ticks aus ``catalog_service`` waren seit #1331 verwaist; ihr gemessener
Spread floss nicht ins Kostenmodell (EQUITY 3,0 bps konfiguriert, obwohl gemessene eToro-Bid/Ask anfallen).

Fix: Ziel ``quote_tick/<symbol>/RealTick/`` (#1354), ``calibration.calibrate_spread_from_realtick`` (Median/P75
von ``(ask − bid) / mid`` in bps, nur in der Session, ab ``spread_calibration_n_min`` Ticks, Cache
``calibrated_spread.json`` mit Quelle ``realtick``), ``resolve_spread_bps = max(Config, gemessener Median)``,
promotions-blockierende Invariante ``check_modeled_spread_not_below_measured``, Telemetrie
``spread_bps_applied``/``spread_bps_measured_p50``/``spread_bps_measured_p75``/``spread_source``.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from automation import api_backfiller as ab
from automation import session_windows as sw
from automation.catalog_paths import EngineCatalogViewError, engine_catalog_view
from automation.optimizer import calibration as cal
from automation.optimizer import invariants as inv

_SYM = "XOM.ETORO"
_H = 3_600_000_000_000


def _write_realtick(root: Path, spreads_bps: list[float], *, start: datetime, step=timedelta(minutes=5),
                    mid: float = 100.0) -> list[int]:
    d = root / "data" / "quote_tick" / _SYM / "RealTick"
    d.mkdir(parents=True, exist_ok=True)
    ts, bids, asks = [], [], []
    t = start
    for bps in spreads_bps:
        half = mid * bps / 10_000.0 / 2.0
        ts.append(int(t.timestamp()) * 1_000_000_000)
        bids.append(ab._encode_fsb16(mid - half, 6))
        asks.append(ab._encode_fsb16(mid + half, 6))
        t += step
    n = len(ts)
    pq.write_table(pa.table({
        "bid_price": pa.array(bids, type=pa.binary(16)), "ask_price": pa.array(asks, type=pa.binary(16)),
        "bid_size": pa.array([ab._encode_qty_fsb16(1.0, 2)] * n, type=pa.binary(16)),
        "ask_size": pa.array([ab._encode_qty_fsb16(1.0, 2)] * n, type=pa.binary(16)),
        "ts_event": pa.array(ts, type=pa.uint64()), "ts_init": pa.array(ts, type=pa.uint64()),
        "bar_interval_ns": pa.array([0] * n, type=pa.uint64()),
    }), str(d / "data.parquet"))
    return ts


def test_six_bps_median_realtick_spread_is_applied_over_the_config_three(tmp_path):
    # 300 Echt-Ticks, Median 6 bps (Werktag in der Session: 14:30-20:30 UTC am Mo 2026-10-05, EDT).
    spreads = [5.0] * 100 + [6.0] * 101 + [8.0] * 99
    _write_realtick(tmp_path, spreads, start=datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc),
                    step=timedelta(minutes=1))
    res = cal.calibrate_spread_from_realtick(_SYM, tmp_path, n_min=200)
    assert res is not None and res["source"] == "realtick"
    assert res["p50"] == pytest.approx(6.0, abs=1e-6) and res["p75"] == pytest.approx(8.0, abs=1e-6)

    from automation.backtest_runner import _measured_spread_p50, resolve_spread_bps
    cache = {_SYM: res}
    applied = resolve_spread_bps(_SYM, {"EQUITY": 3.0}, {}, "EQUITY",
                                 measured_spread_bps=_measured_spread_p50(_SYM, cache))
    assert applied == pytest.approx(6.0)
    # Config über der Messung bleibt (max), ohne Messung bit-identisch.
    assert resolve_spread_bps(_SYM, {"EQUITY": 9.0}, {}, "EQUITY", measured_spread_bps=6.0) == 9.0
    assert resolve_spread_bps(_SYM, {"EQUITY": 3.0}, {}, "EQUITY") == 3.0
    assert resolve_spread_bps(_SYM, {"EQUITY": 3.0}, {_SYM: 2.0}, "EQUITY", measured_spread_bps=6.0) == 6.0


def test_calibration_uses_session_ticks_only_and_needs_n_min(tmp_path):
    ny = sw.parse_session_window({"tz": "America/New_York", "open": "09:30", "close": "16:00"})
    # 120 Ticks nachts (02:00 UTC, 30 bps) + 250 Ticks in der Session (2 bps).
    night = _write_realtick(tmp_path, [30.0] * 120, start=datetime(2026, 10, 5, 2, 0, tzinfo=timezone.utc),
                            step=timedelta(seconds=30))
    assert len(night) == 120
    d = tmp_path / "data" / "quote_tick" / _SYM / "RealTick" / "data.parquet"
    night_table = pq.read_table(str(d))
    _write_realtick(tmp_path, [2.0] * 250, start=datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc),
                    step=timedelta(minutes=1))
    pq.write_table(pa.concat_tables([night_table, pq.read_table(str(d))]), str(d))
    with_session = cal.calibrate_spread_from_realtick(_SYM, tmp_path, n_min=200, session_window=ny)
    assert with_session["p50"] == pytest.approx(2.0) and with_session["n_ticks"] == 250
    assert cal.calibrate_spread_from_realtick(_SYM, tmp_path, n_min=300, session_window=ny) is None
    assert cal.calibrate_spread_from_realtick("NOPE.ETORO", tmp_path) is None


def test_calibrated_spread_cache_round_trip(tmp_path):
    cal.write_calibrated_spread_cache(tmp_path, {_SYM: {"p50": 6.0, "p75": 8.0, "source": "realtick"}})
    assert cal.read_calibrated_spread_cache(tmp_path)[_SYM]["p50"] == 6.0
    assert cal.read_calibrated_spread_cache(tmp_path / "missing") == {}


def test_engine_never_reads_realtick(tmp_path):
    """Die Engine-Sicht (#1354) verlinkt ausschliesslich die OneHour-Datei; ein Symbol NUR mit Echt-Ticks hat
    keine Engine-Sicht (die Echt-Ticks verfälschen den Backtest nie)."""
    _write_realtick(tmp_path, [5.0] * 10, start=datetime(2026, 10, 5, 14, tzinfo=timezone.utc))
    with pytest.raises(EngineCatalogViewError):
        engine_catalog_view(tmp_path, _SYM)
    hour_dir = tmp_path / "data" / "quote_tick" / _SYM / "OneHour"
    hour_dir.mkdir(parents=True)
    pq.write_table(pa.table({"ts_event": pa.array([1], type=pa.uint64())}), str(hour_dir / "data.parquet"))
    with engine_catalog_view(tmp_path, _SYM) as view:
        assert view.source.parent.name == "OneHour"


def test_promotion_blocking_invariant():
    ok = {"strategy": "S", "symbol": _SYM, "promotion_outcome": "READY_FOR_PR",
          "spread_bps_applied": 6.0, "spread_bps_measured_p50": 6.0, "spread_source": "realtick"}
    bad = {**ok, "symbol": "Y.ETORO", "spread_bps_applied": 3.0}
    not_promoted = {**bad, "promotion_outcome": "REJECTED_ON_HOLDOUT"}
    assert inv.check_modeled_spread_not_below_measured([ok, not_promoted]).passed is True
    res = inv.check_modeled_spread_not_below_measured([ok, bad])
    assert res.passed is False and res.severity == "blocking" and "S/Y.ETORO" in res.actual
    empty = inv.check_modeled_spread_not_below_measured([not_promoted])
    assert empty.passed is True and empty.inconclusive is True
    assert getattr(inv.check_modeled_spread_not_below_measured, "_invariant_scope") == "promotion"


def test_spread_telemetry_reaches_the_study_record():
    from automation.backtest_runner import run_single_backtest_worker
    from automation.optimizer import confirm, parsing, report, run_optimization

    fields = set(parsing.TournamentMetrics.__dataclass_fields__)
    for f in ("oos_spread_bps_applied", "oos_spread_bps_measured_p50", "oos_spread_bps_measured_p75",
              "oos_spread_source"):
        assert f in fields
        assert f in run_optimization._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS
        assert f'"{f}"' in inspect.getsource(confirm)
        assert f'holdout_metrics.get("{f}")' in inspect.getsource(report._study_record)
    src = inspect.getsource(run_single_backtest_worker)
    assert '"spread_source": _spread_source' in src and "measured_spread_bps=_measured_spread_p50(" in src
    assert "_inv.check_modeled_spread_not_below_measured(studies_out)" in inspect.getsource(report)
