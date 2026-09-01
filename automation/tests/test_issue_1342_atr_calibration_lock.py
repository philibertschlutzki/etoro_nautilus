"""Issue #1342 (GH #1236, P1) — alle ATR-abgeleiteten Kalibrierungen sind gegen eine
Close-zu-Close-ATR gesetzt.

Symptom. ``atr_median_bps`` ist per #1339/GH#1233 eine Close-zu-Close-Grösse. Gegen sie sind
kalibriert: ``atr_floor_bps_by_asset_class``, ``k_min_bar_range_multiple``, die
``atr_trailing_multiplier``-Bänder in ``spaces.py``, die ``3 · c_rt``-Stopuntergrenze.

Root-Cause. Keine dieser Grössen ist falsch implementiert — sie sind gegen eine Achse kalibriert,
die #1330 verändert (Intrabar-Spanne einer liquiden Aktie in einer RTH-Stunde liegt regelmässig
über der Close-zu-Close-Bewegung derselben Stunde).

Fix.
1. Sperrvermerk: keine dieser Konstanten wird geändert, bevor ein echter Katalog-Rebuild
   stattgefunden hat UND ein Messlauf reale Intrabar-Spannen liefert.
2. ``run_measurement_pass``/``--measurement-run``: ein reiner Messlauf-Modus (keine Selektion),
   der ``intrabar_range_median_bps``/``atr_median_bps`` je Symbol protokolliert.
3. ``check_config_matches_calibration`` (bereits vorhanden, invariants.py) bleibt der Wächter für
   eine künftige Neukalibrierung.

Akzeptanzkriterien:
- ``check_config_matches_calibration`` PASSt nach der Neukalibrierung ohne Offender (hier: PASST
  bereits jetzt, da noch NICHTS rekalibriert wurde — der Sperrvermerk hält).
- Der Messlauf ist als eigener Modus reproduzierbar aufrufbar, nicht als Nebenprodukt eines Sweeps.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import sweep


# ── Sperrvermerk — die vier genannten Grössen bleiben unveraendert ────────────────────────────────

def test_atr_floor_bps_by_asset_class_equity_is_still_the_locked_2_0():
    cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    assert cfg["atr_floor_bps_by_asset_class"]["EQUITY"] == 2.0


def test_k_min_bar_range_multiple_is_still_the_locked_1_0():
    cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    assert cfg["k_min_bar_range_multiple"] == 1.0


def test_min_intrabar_range_median_bps_stays_the_noop_default_until_the_measurement_run():
    """Issue #1339 (GH #1233) Sperrvermerk-Default, unveraendert von #1236: erst NACH dem
    Messlauf darf dieser Wert gesetzt werden."""
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["bar_quality"]["min_intrabar_range_median_bps"] == 0.0


# ── check_config_matches_calibration — der Sperrvermerk haelt (keine Rekalibrierung erfolgt) ────

def test_check_config_matches_calibration_passes_on_the_current_uncalibrated_configs():
    """Akzeptanzkriterium 1 — solange KEIN _schema.calibrations-Eintrag fuer die vier gesperrten
    Groessen existiert (weil sie noch nicht rekalibriert wurden), ist der Check nicht auswertbar
    (INCONCLUSIVE), niemals FAIL."""
    backtest_cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    optimizer_cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    result = inv.check_config_matches_calibration(
        {"backtest.json": backtest_cfg, "optimizer.json": optimizer_cfg})
    assert result.passed is not False


# ── run_measurement_pass — der reproduzierbare Messlauf-Modus (Akzeptanzkriterium 3) ─────────────

def test_run_measurement_pass_is_a_standalone_function_not_a_sweep_byproduct():
    import inspect
    sig = inspect.signature(sweep.run_measurement_pass)
    assert "symbols" in sig.parameters
    assert "run_id" in sig.parameters


def test_run_measurement_pass_fails_open_for_a_symbol_without_a_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIMIZER_WORK_DIR", str(tmp_path))
    import importlib
    from automation.optimizer import manifest as _manifest
    importlib.reload(_manifest)
    importlib.reload(sweep)
    report = sweep.run_measurement_pass(symbols=["NO_SUCH_SYMBOL.ETORO"], run_id="test_lock_run")
    assert report["symbols_measured"] == 1
    entry = report["measurements"]["NO_SUCH_SYMBOL.ETORO"]
    assert entry["intrabar_range_median_bps"] is None
    assert entry["atr_median_bps"] is None
    assert entry["stop_distance_bps_measured"] is None
    assert "note" in entry


def test_run_measurement_pass_writes_the_report_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIMIZER_WORK_DIR", str(tmp_path))
    import importlib
    from automation.optimizer import manifest as _manifest
    importlib.reload(_manifest)
    importlib.reload(sweep)
    sweep.run_measurement_pass(symbols=["NO_SUCH_SYMBOL.ETORO"], run_id="test_lock_run_2")
    written_path = sweep._measurement_run_report_path(sweep.WORK, "test_lock_run_2")
    assert written_path.exists()
    on_disk = json.loads(written_path.read_text("utf-8"))
    assert on_disk["run_id"] == "test_lock_run_2"


def test_stop_distance_bps_measured_scope_cut_is_documented_in_the_docstring():
    import inspect
    src = inspect.getdoc(sweep.run_measurement_pass) or ""
    assert "stop_distance_bps_measured" in src
    assert "Scope-Cut" in src


def test_measurement_run_cli_flag_is_wired_into_main():
    import inspect
    src = inspect.getsource(sweep.main)
    assert "--measurement-run" in src
    assert "run_measurement_pass(" in src
