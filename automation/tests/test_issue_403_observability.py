"""Issue #403 — Backtest-Zeitfenster und Konfigurationsquellen fehlten in den Logs.

Part 1: der Worker loggt jetzt das *tatsaechliche* Daten-Zeitfenster (Start, Ende, Dauer) —
        verifiziert ueber den reinen Formatter `_format_backtest_window`.
Part 2/3: ein strukturierter Startup-Header (`log_active_config`) legt die Config-Quellen
        (Dateipfade) und Kern-Schwellen offen, bevor der Lauf in die (bei capture_output=True
        stummen) iterativen Trials uebergeht — fuer Sweep (`sweep.main`) und globale
        Optimierung (`run`) gemeinsam genutzt.
"""
from pathlib import Path

import pandas as pd

from automation.backtest_runner import _format_backtest_window
from automation.optimizer import run_optimization as ro
from automation.optimizer import sweep


def test_format_backtest_window_includes_end_and_span():
    start_ns = int(pd.Timestamp("2024-01-01", tz="UTC").value)
    end_ns = int(pd.Timestamp("2024-04-10", tz="UTC").value)  # exakt 100 Tage spaeter
    msg = _format_backtest_window(start_ns, end_ns)
    assert "2024-01-01" in msg
    assert "2024-04-10" in msg
    assert "100.0 Tage" in msg


def test_log_active_config_exposes_sources_and_thresholds(capsys):
    cfg_dir = Path(__file__).resolve().parent.parent / "config"
    ro.log_active_config("unit-test", base_cfg=cfg_dir, extra={"n_jobs": 6})
    out = capsys.readouterr().out
    # Config-Quellen (Dateipfade) sichtbar
    assert "optimizer.json" in out
    assert "tournament.json" in out
    assert str(cfg_dir) in out
    # Kern-Schwellen sichtbar (inkl. der in #401 eingefuehrten Keys)
    assert "sortino_min_trades" in out
    assert "oos_sortino_fallback" in out
    assert "max_drawdown" in out
    # Kontext + extra
    assert "unit-test" in out
    assert "n_jobs" in out


def test_log_active_config_missing_dir_is_defensive(capsys, tmp_path):
    ro.log_active_config("missing", base_cfg=tmp_path)  # leeres Verzeichnis ⇒ kein Crash
    out = capsys.readouterr().out
    assert "(FEHLT)" in out


def test_sweep_main_prints_config_header(monkeypatch, capsys):
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: [])
    sweep.main(["--strategies", "SmaCrossoverStrategy", "--symbols", "A.ETORO",
                "--tier", "all", "--n-jobs", "4"])
    out = capsys.readouterr().out
    assert "Aktive Konfiguration" in out
    assert "per-symbol sweep" in out
    assert "n_jobs" in out
