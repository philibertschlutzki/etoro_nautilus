"""Issue #1350 (GH #1244, P1) — Stop-Trigger und Stop-Fill brauchen nach #1330 eine deklarierte
Trennung.

Symptom. Alle vier Stop-Mechanik-Invarianten meldeten SUPPRESSED_UPSTREAM_BAR_AXIS, unabhängig
davon, ob ein Katalog-Rebuild (#1330) tatsächlich stattgefunden hatte — die Suppression prüfte nur
die ALTE bar_range_population_n/zero_range_bar_fraction-Population, die eine echte Intrabar-Achse
nicht von einer zufällig-gesunden high!=low-Verteilung unterscheiden kann.

Fix.
1. Trigger gegen Intrabar-Extremwerte, Fill an der nächsten Preisbeobachtung danach — SPERRVERMERK
   (siehe manuals/strategie_optimierung.md §Stop-Trigger-/-Fill-Trennung): bleibt 'bar_close_only'
   bis zum echten Post-Rebuild-Messlauf.
2. stop_exit_fill_lag_ticks ergänzt stop_exit_fill_lag_bars.
3. stop_trigger_axis wird um stop_fill_axis ergänzt; beide erscheinen in jeder Study-Telemetrie.
4. Die Aufhebung von SUPPRESSED_UPSTREAM_BAR_AXIS prüft zusätzlich intrabar_range_median_bps > 0
   UND intrabar_path gestempelt (api_backfiller.read_intrabar_path, sweep._load_symbol_bar_
   quality_sample).
5. Jede Stop-Kennzahl trägt intrabar_path als Begleitfeld (symbol_bar_quality).
"""
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


# ── api_backfiller.read_intrabar_path ─────────────────────────────────────────────────────────

def _write_parquet_with_metadata(path: Path, *, intrabar_path: str | None) -> None:
    table = pa.table({"ts_event": pa.array([1, 2, 3], type=pa.int64())})
    if intrabar_path is not None:
        table = table.replace_schema_metadata({b"intrabar_path": intrabar_path.encode()})
    pq.write_table(table, str(path))


def test_read_intrabar_path_returns_the_stamped_value(tmp_path):
    from automation.api_backfiller import read_intrabar_path, INTRABAR_PATH_SYNTHETIC
    p = tmp_path / "part-0.parquet"
    _write_parquet_with_metadata(p, intrabar_path=INTRABAR_PATH_SYNTHETIC)
    assert read_intrabar_path(p) == "synthetic_ohlc_adverse_first"


def test_read_intrabar_path_returns_none_for_a_legacy_catalog_without_the_field(tmp_path):
    from automation.api_backfiller import read_intrabar_path
    p = tmp_path / "part-0.parquet"
    _write_parquet_with_metadata(p, intrabar_path=None)
    assert read_intrabar_path(p) is None


def test_read_intrabar_path_returns_none_for_a_missing_file(tmp_path):
    from automation.api_backfiller import read_intrabar_path
    assert read_intrabar_path(tmp_path / "does_not_exist.parquet") is None


def test_read_intrabar_path_recognizes_the_observed_constant(tmp_path):
    from automation.api_backfiller import read_intrabar_path, INTRABAR_PATH_OBSERVED
    p = tmp_path / "part-0.parquet"
    _write_parquet_with_metadata(p, intrabar_path=INTRABAR_PATH_OBSERVED)
    assert read_intrabar_path(p) == "observed"


# ── invariants._bar_axis_supports_stop_verdict — siehe test_issue_1274 fuer die volle Matrix ────
# (dediziert dort erweitert, um Duplikation zu vermeiden — hier nur die Verdrahtungs-/Konfig-Tests.)


# ── report._study_record — stop_fill_axis / stop_exit_fill_lag_ticks Verdrahtung ────────────────

def test_study_record_stamps_stop_fill_axis_from_config():
    import inspect
    from automation.optimizer import report
    src = inspect.getsource(report)
    assert '"stop_fill_axis": optimizer_cfg.get("stop_fill_axis")' in src


def test_study_record_stamps_stop_exit_fill_lag_ticks_scaled_from_bars():
    import inspect
    from automation.optimizer import report
    src = inspect.getsource(report)
    assert "stop_exit_fill_lag_ticks" in src
    assert "_SYNTHETIC_TICKS_PER_BAR" in src


def test_synthetic_ticks_per_bar_matches_the_ohlc_expansion_count():
    """4 Ticks je Kerze (Open/Low/High/Close), dieselbe Zahl wie api_backfiller._candles_to_
    arrow_table (#1330/GH#1224)."""
    from automation.optimizer.report import _SYNTHETIC_TICKS_PER_BAR
    assert _SYNTHETIC_TICKS_PER_BAR == 4


# ── sweep._load_symbol_bar_quality_sample — intrabar_path/intrabar_range_median_bps Passthrough ─

def test_load_symbol_bar_quality_sample_wiring_reads_intrabar_path():
    import inspect
    from automation.optimizer import sweep
    src = inspect.getsource(sweep._load_symbol_bar_quality_sample)
    assert "read_intrabar_path" in src
    assert '"intrabar_path"' in src


def test_preflight_quality_by_symbol_carries_the_new_fields():
    import inspect
    from automation.optimizer import sweep
    src = inspect.getsource(sweep.run_per_symbol_sweep)
    assert '"intrabar_range_median_bps": _quality.get("intrabar_range_median_bps")' in src
    assert '"intrabar_path": _sample.get("intrabar_path")' in src


# ── optimizer.json — stop_fill_axis Konfiguration + Sperrvermerk ────────────────────────────────

def test_optimizer_json_declares_stop_fill_axis_locked_to_bar_close_only():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["stop_fill_axis"] == "bar_close_only"
    doc = cfg["_schema"]["fields"]["stop_fill_axis"]
    assert "1350" in doc
    assert "SPERRVERMERK" in doc.upper() or "Sperrvermerk" in doc
