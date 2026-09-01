"""Issue #1332 (GH #1226) — Tick-Zeitstempel-Semantik: der Close-Tick trägt den Zeitpunkt, an dem
er bekannt wird (Kerzenende), nicht den Kerzenbeginn; der Session-Filter testet Intervall-
Überlappung statt eines Zeitpunkts; ``is_within_session_hours`` existiert genau einmal im
Repository; ``check_no_future_price_in_tick`` bewacht die Katalog-Konstruktion.
"""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from automation import api_backfiller as bf
from automation.optimizer import sweep
from automation.session_windows import interval_overlaps_session_hours, is_within_session_hours

_NS_PER_HOUR = 3_600_000_000_000


def test_close_tick_of_14_to_15_candle_is_14_59_59_999999999():
    candles = [{"fromDate": "2026-01-01T14:00:00Z", "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0}]
    table = bf._candles_to_arrow_table(
        candles, "TEST.ETORO", 2, 2, datetime(2026, 1, 1, tzinfo=timezone.utc), interval="OneHour")
    ts_events = table.column("ts_event").to_pylist()
    candle_start_ns = int(datetime(2026, 1, 1, 14, tzinfo=timezone.utc).timestamp() * 1e9)
    expected_close_ns = candle_start_ns + _NS_PER_HOUR - 1
    assert ts_events[-1] == expected_close_ns
    # Ganzzahlige Sekunden-/Nanosekunden-Zerlegung statt einer float-Division (Praezisionsverlust
    # bei Nanosekunden-Timestamps in float64) — 14:59:59 + 999999999ns == 14:59:59.999999999.
    whole_seconds, remainder_ns = divmod(expected_close_ns, 1_000_000_000)
    dt_close = datetime.fromtimestamp(whole_seconds, tz=timezone.utc)
    assert dt_close.hour == 14 and dt_close.minute == 59 and dt_close.second == 59
    assert remainder_ns == 999_999_999


def test_is_within_session_hours_defined_exactly_once_in_repository():
    """Akzeptanzkriterium: is_within_session_hours existiert genau einmal im Repository; die
    Duplikate (sweep.py/backtest_runner.py) importieren sie aus automation.session_windows."""
    n_definitions = 0
    definition_files = []
    for path in Path("automation").rglob("*.py"):
        if "/tests/" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "is_within_session_hours":
                n_definitions += 1
                definition_files.append(str(path))
    assert n_definitions == 1, f"is_within_session_hours mehrfach definiert: {definition_files}"
    assert definition_files == ["automation/session_windows.py"]


def test_backtest_runner_imports_is_within_session_hours_from_session_windows():
    src = Path("automation/backtest_runner.py").read_text("utf-8")
    assert "from automation.session_windows import" in src
    assert "is_within_session_hours" in src


def test_sweep_delegates_to_session_windows_is_within_session_hours():
    src = Path("automation/optimizer/sweep.py").read_text("utf-8")
    assert "from automation.session_windows import is_within_session_hours" in src


def test_interval_overlaps_session_hours_matches_point_test_for_fully_contained_intervals():
    ts_ns = int(datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc).timestamp() * 1e9)  # Montag 15:00
    assert is_within_session_hours(ts_ns, "13:30", "20:00") is True
    assert interval_overlaps_session_hours(ts_ns, ts_ns + _NS_PER_HOUR, "13:30", "20:00") is True


def test_interval_overlaps_session_hours_admits_partially_overlapping_candle():
    """Die 13:00-Kerze [13:00,14:00) ueberlappt eine Session ab 13:30, obwohl ihr Startpunkt
    (13:00) selbst ausserhalb der Session liegt — der Punkt-Test wuerde sie faelschlich verwerfen."""
    candle_start_ns = int(datetime(2026, 1, 5, 13, 0, tzinfo=timezone.utc).timestamp() * 1e9)
    assert is_within_session_hours(candle_start_ns, "13:30", "20:00") is False
    assert interval_overlaps_session_hours(
        candle_start_ns, candle_start_ns + _NS_PER_HOUR, "13:30", "20:00") is True


def _write_catalog_with_bar_interval(tmp_path, symbol, rows):
    """rows: list of (ts_event, bar_interval_ns)."""
    d = tmp_path / "data" / "quote_tick" / symbol / "OneHour"
    d.mkdir(parents=True, exist_ok=True)
    _FSB16 = pa.binary(16)
    n = len(rows)
    table = pa.table({
        "bid_price": pa.array([b"\x00" * 16] * n, type=_FSB16),
        "ask_price": pa.array([b"\x00" * 16] * n, type=_FSB16),
        "ts_event": pa.array([r[0] for r in rows], type=pa.uint64()),
        "bar_interval_ns": pa.array([r[1] for r in rows], type=pa.uint64()),
    })
    pq.write_table(table, str(d / "data.parquet"))


def test_check_no_future_price_in_tick_passes_for_well_formed_catalog(tmp_path):
    start = int(datetime(2026, 1, 5, 14, tzinfo=timezone.utc).timestamp() * 1e9)
    rows = [
        (start, _NS_PER_HOUR),
        (start + _NS_PER_HOUR // 4, _NS_PER_HOUR),
        (start + _NS_PER_HOUR // 2, _NS_PER_HOUR),
        (start + _NS_PER_HOUR - 1, _NS_PER_HOUR),
    ]
    _write_catalog_with_bar_interval(tmp_path, "OK.ETORO", rows)
    result = sweep.check_no_future_price_in_tick("OK.ETORO", catalog_path=tmp_path)
    assert result["passed"] is True
    assert result["n_violations"] == 0


def test_check_no_future_price_in_tick_skips_rows_with_zero_interval_without_counting_them_as_violations(tmp_path):
    """``bar_interval_ns=0`` (degenerierte/fehlerhafte Zeile) darf die Division-durch-Null nicht
    ausloesen und wird bewusst uebersprungen (weder als Verletzung noch als Beleg gezaehlt) —
    eine floor-division-basierte candle_start-Rekonstruktion ist fuer JEDEN gueltigen positiven
    Intervallwert tautologisch selbstkonsistent (jedes ts faellt per Konstruktion in sein eigenes
    floor(ts/itv)*itv-Fenster); der Schutzwert dieser Invariante liegt darin, dass sie bei einer
    kuenftigen Regression (z. B. eine negative/verstuemmelte bar_interval_ns) ueberhaupt etwas zu
    pruefen versucht, statt stillschweigend durchzulaufen."""
    start = int(datetime(2026, 1, 5, 14, tzinfo=timezone.utc).timestamp() * 1e9)
    rows = [(start, 0), (start + _NS_PER_HOUR, _NS_PER_HOUR)]
    _write_catalog_with_bar_interval(tmp_path, "ZERO.ETORO", rows)
    result = sweep.check_no_future_price_in_tick("ZERO.ETORO", catalog_path=tmp_path)
    assert result["n_ticks"] == 2
    assert result["n_violations"] == 0
    assert result["passed"] is True


def test_check_no_future_price_in_tick_inconclusive_without_bar_interval_column(tmp_path):
    d = tmp_path / "data" / "quote_tick" / "LEGACY.ETORO" / "OneHour"
    d.mkdir(parents=True, exist_ok=True)
    _FSB16 = pa.binary(16)
    table = pa.table({
        "bid_price": pa.array([b"\x00" * 16], type=_FSB16),
        "ask_price": pa.array([b"\x00" * 16], type=_FSB16),
        "ts_event": pa.array([1], type=pa.uint64()),
    })
    pq.write_table(table, str(d / "data.parquet"))
    result = sweep.check_no_future_price_in_tick("LEGACY.ETORO", catalog_path=tmp_path)
    assert result["passed"] is None
