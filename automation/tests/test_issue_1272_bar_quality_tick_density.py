"""Issue #1272 (GH #1145, Katalog #1272-1297, P0) — check_bar_quality verdrahten und Tick-Dichte
je Bar messen.

Symptom. ``check_bar_quality`` war in 4/4 realen Läufen ``passed=None`` mit Detail "Kein Symbol
tatsächlich geprüft" — obwohl die Läufe tatsächlich mit Tick-Daten liefen (1742+ Trials). Die
entscheidende Kennzahl fehlte zudem komplett: ``frac_zero_true_range`` misst die AGGREGIERTEN
Bars, nicht die TICK-DICHTE, aus der sie entstehen (ein Tick je Stunde macht ``high == low``
mechanisch unvermeidlich, unabhängig von der Marktvolatilität).

Fix.
1. Neue Kennzahlen ``ticks_per_bar_median``/``ticks_per_bar_p05``/``frac_bars_single_tick``
   (``sweep._load_symbol_bar_quality_sample``, ``sweep._percentile``).
2. ``ticks_per_bar_median <= 1`` bzw. ``frac_bars_single_tick > 0.5`` ⇒ FAIL severity 'blocking'
   mit Code ``BAR_AXIS_NO_INTRABAR_INFORMATION`` (``sweep_diagnostics.check_bar_quality``).
3. ``passed=None`` ist nicht mehr zulässig, sobald das Katalog-Wurzelverzeichnis existiert (
   ``run_per_symbol_sweep``s Fallback) — nur ein fehlender Katalog rechtfertigt INCONCLUSIVE.
4. ``run.json`` trägt ``cross_study.symbol_bar_quality.<symbol>.ticks_per_bar_median``.
"""
import json
import logging

import pytest

from automation.optimizer import sweep
from automation.optimizer.sweep_diagnostics import check_bar_quality


# ---------------------------------------------------------------------------------------------
# sweep_diagnostics.check_bar_quality — neue Tick-Dichte-Kriterien
# ---------------------------------------------------------------------------------------------

def _healthy_bars(n=40):
    # genug Streuung, um alle UEBRIGEN Degenerations-Kriterien zu bestehen.
    highs = [100.0 + 0.5 * (i % 5) for i in range(n)]
    lows = [99.0 + 0.5 * (i % 5) for i in range(n)]
    closes = [99.5 + 0.5 * (i % 7) for i in range(n)]
    return highs, lows, closes


def test_ticks_per_bar_median_at_or_below_one_fails_blocking_with_named_code():
    highs, lows, closes = _healthy_bars()
    result = check_bar_quality(highs, lows, closes, ticks_per_bar_median=1.0,
                               frac_bars_single_tick=1.0)
    assert result["passed"] is False
    assert result["severity"] == "blocking"
    assert "BAR_AXIS_NO_INTRABAR_INFORMATION" in result["reason"]


def test_ticks_per_bar_median_above_one_does_not_trigger_the_blocking_reason():
    highs, lows, closes = _healthy_bars()
    result = check_bar_quality(highs, lows, closes, ticks_per_bar_median=5.0,
                               frac_bars_single_tick=0.1)
    assert "BAR_AXIS_NO_INTRABAR_INFORMATION" not in (result["reason"] or "")
    assert result["severity"] == "high"


def test_frac_bars_single_tick_above_threshold_fails_blocking():
    highs, lows, closes = _healthy_bars()
    result = check_bar_quality(highs, lows, closes, ticks_per_bar_median=3.0,
                               frac_bars_single_tick=0.6)
    assert result["passed"] is False
    assert result["severity"] == "blocking"
    assert "frac_bars_single_tick" in result["reason"]


def test_frac_bars_single_tick_at_threshold_passes():
    highs, lows, closes = _healthy_bars()
    result = check_bar_quality(highs, lows, closes, ticks_per_bar_median=3.0,
                               frac_bars_single_tick=0.5)
    assert result["severity"] == "high"


def test_none_tick_density_fields_are_not_evaluated():
    """Kein Aufrufer liefert die Tick-Zahlen (Legacy-Pfad) -> kein Fail allein deswegen."""
    highs, lows, closes = _healthy_bars()
    result = check_bar_quality(highs, lows, closes)
    assert "BAR_AXIS_NO_INTRABAR_INFORMATION" not in (result["reason"] or "")
    assert result["ticks_per_bar_median"] is None


def test_reason_lists_blocking_code_before_derived_symptoms():
    """BAR_AXIS_NO_INTRABAR_INFORMATION ist die URSACHE, sie muss vor den abgeleiteten
    frac_zero_true_range/frac_high_eq_low-Symptomen in der Grunde-Liste erscheinen."""
    n = 30
    highs = [100.0] * n
    lows = [100.0] * n  # high==low in jeder Bar -> triggert auch frac_high_eq_low/frac_zero_true_range
    closes = [100.0] * n
    result = check_bar_quality(highs, lows, closes, ticks_per_bar_median=1.0,
                               frac_bars_single_tick=1.0)
    assert result["passed"] is False
    first_reason = result["reason"].split(";")[0].strip()
    assert first_reason.startswith("BAR_AXIS_NO_INTRABAR_INFORMATION")


def test_severity_field_present_on_empty_input():
    result = check_bar_quality([], [], [])
    assert result["passed"] is False
    assert result["severity"] == "high"
    assert result["ticks_per_bar_median"] is None


# ---------------------------------------------------------------------------------------------
# sweep._percentile
# ---------------------------------------------------------------------------------------------

def test_percentile_empty_returns_none():
    assert sweep._percentile([], 0.05) is None


def test_percentile_single_value():
    assert sweep._percentile([7.0], 0.05) == 7.0


def test_percentile_matches_known_linear_interpolation():
    # 0,1,2,...,9 -> p50 (Median) muss 4.5 sein (lineare Interpolation, numpy-Default-Konvention).
    values = [float(i) for i in range(10)]
    assert sweep._percentile(values, 0.5) == pytest.approx(4.5)
    assert sweep._percentile(values, 0.0) == 0.0
    assert sweep._percentile(values, 1.0) == 9.0


# ---------------------------------------------------------------------------------------------
# _load_symbol_bar_quality_sample — end-to-end gegen eine echte, kleine Parquet-Stichprobe
# ---------------------------------------------------------------------------------------------

def _write_quote_tick_parquet(tmp_path, symbol, ts_ns_list, price=100.0):
    import pyarrow as pa
    import pyarrow.parquet as pq
    d = tmp_path / "data" / "quote_tick" / symbol
    d.mkdir(parents=True, exist_ok=True)
    n = len(ts_ns_list)
    table = pa.table({
        "bid_price": pa.array([price] * n, type=pa.float64()),
        "ask_price": pa.array([price + 0.02] * n, type=pa.float64()),
        "ts_event": pa.array(ts_ns_list, type=pa.int64()),
    })
    pq.write_table(table, str(d / "data.parquet"))


def test_one_tick_per_hour_yields_median_one_and_full_single_tick_fraction(tmp_path):
    _NS_PER_HOUR = 3_600_000_000_000
    ts_list = [i * _NS_PER_HOUR for i in range(48)]  # exakt 1 Tick je Stunde, 48 Bars
    _write_quote_tick_parquet(tmp_path, "TSLA.ETORO", ts_list)
    sample = sweep._load_symbol_bar_quality_sample("TSLA.ETORO", catalog_path=tmp_path)
    assert sample is not None
    assert sample["ticks_per_bar_median"] == 1.0
    assert sample["frac_bars_single_tick"] == 1.0
    # 1 Tick/Bar -> high==low mechanisch -> auch die abgeleiteten Symptome degenerieren.
    assert all(h == l for h, l in zip(sample["highs"], sample["lows"]))


def test_dense_ticks_per_hour_yields_healthy_density(tmp_path):
    _NS_PER_HOUR = 3_600_000_000_000
    ts_list = []
    for hour in range(24):
        for k in range(20):  # 20 Ticks je Stunde, leicht bewegter Preis
            ts_list.append(hour * _NS_PER_HOUR + k * 100_000_000_000)
    _write_quote_tick_parquet(tmp_path, "NVDA.ETORO", ts_list)
    sample = sweep._load_symbol_bar_quality_sample("NVDA.ETORO", catalog_path=tmp_path)
    assert sample is not None
    assert sample["ticks_per_bar_median"] == 20.0
    assert sample["frac_bars_single_tick"] == 0.0


def test_missing_catalog_file_returns_none(tmp_path):
    assert sweep._load_symbol_bar_quality_sample("GHOST.ETORO", catalog_path=tmp_path) is None


# ---------------------------------------------------------------------------------------------
# run_per_symbol_sweep fallback: passed=None ist nur bei fehlendem Katalog-Wurzelverzeichnis
# zulaessig (Fix Punkt 4)
# ---------------------------------------------------------------------------------------------

def test_fallback_escalates_to_blocking_fail_when_catalog_root_exists(tmp_path, monkeypatch):
    # config_dir() liegt PRODUKTIV zwei Ebenen unter PROJECT_ROOT (automation/config) -- der
    # Katalogpfad wird relativ dazu aufgeloest (``_cfg_base.parent.parent / raw_catalog``,
    # siehe sweep._load_symbol_bar_quality_sample). Dieselbe Struktur hier nachgebaut, statt
    # config_dir() auf tmp_path direkt zu binden (das wuerde zwei Ebenen zu weit hochlaufen).
    fake_cfg_dir = tmp_path / "automation" / "config"
    fake_cfg_dir.mkdir(parents=True)
    catalog_root = tmp_path / "data" / "nautilus"
    catalog_root.mkdir(parents=True)
    monkeypatch.setattr(sweep, "config_dir", lambda: fake_cfg_dir)

    events = []

    def _capture(logger, event_type, payload, level=logging.INFO):
        events.append((event_type, payload, level))

    monkeypatch.setattr(sweep, "emit_execution_event", _capture)
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda: ["TSLA.ETORO"])
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {"walk_forward": {}})
    monkeypatch.setattr(sweep, "count_available_bars", lambda syms, **kw: {})

    def _fake_optimize(pair):
        return None

    def _fake_confirm(*a, **kw):
        return None

    try:
        sweep.run_per_symbol_sweep(
            ["NonexistentStrategy"], ["TSLA.ETORO"],
            optimize_symbol=_fake_optimize, confirm=_fake_confirm,
            bar_quality_fn=lambda symbol: None,  # jede Stichprobe schlaegt fehl
            run_id="test-1272-fallback-escalate",
        )
    except Exception:
        pass  # ein HI-7-Fake ohne echten Suchraum darf hier scheitern -- der Preflight lief davor.

    bar_quality_events = [
        p for (etype, p, _lvl) in events
        if etype == "INVARIANT_STREAM_RESULT" and p.get("name") == "check_bar_quality"
    ]
    assert bar_quality_events, "kein check_bar_quality-Ereignis emittiert"
    result = bar_quality_events[-1]
    assert result["passed"] is False
    assert result["severity"] == "blocking"
    assert "CATALOG_SAMPLE_UNAVAILABLE_DESPITE_CATALOG" in result["detail"]


# ---------------------------------------------------------------------------------------------
# report.py — cross_study.symbol_bar_quality
# ---------------------------------------------------------------------------------------------

def test_cross_study_symbol_bar_quality_exposes_ticks_per_bar_median(tmp_path, monkeypatch):
    from automation.optimizer import report as rpt

    cache = {"TSLA.ETORO": {"frac_zero_true_range": 1.0, "atr_median_bps": 0.0,
                            "bar_coverage_ratio": 1.0, "median_delta_t_s": 3600.0,
                            "ticks_per_bar_median": 1.0, "ticks_per_bar_p05": 1.0,
                            "frac_bars_single_tick": 1.0, "passed": False}}
    monkeypatch.setattr(rpt, "read_symbol_bar_quality_cache", lambda root: cache)
    monkeypatch.setattr(rpt, "symbol_bar_quality_cache_status",
                        lambda root: {"cache_found": True, "cache_path": str(tmp_path)})

    report = rpt._build_report(
        [], run_id="run-1272-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    assert report["cross_study"]["symbol_bar_quality"]["TSLA.ETORO"]["ticks_per_bar_median"] == 1.0
