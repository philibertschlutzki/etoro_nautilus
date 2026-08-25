"""Issue #932 (Pitfall #305) — pipeline_depth existierte, war dokumentiert, in der Registry
gefuehrt und NULL ausfuehrende Referenzen (derselbe #913-Zustand, eine Konfigurationsdatei weiter).
Entfernt; Longest-Processing-Time-Dispatch implementiert stattdessen: die Studies eines Symbols
werden absteigend nach dem letzten Report-Erfahrungswert dispatcht.
"""
import json
from pathlib import Path

from automation.optimizer import sweep


def test_pipeline_depth_removed_from_shipped_config():
    data = json.loads(open("automation/config/optimizer.json", encoding="utf-8").read())
    assert "pipeline_depth" not in data
    assert "pipeline_depth" not in (data.get("_schema") or {}).get("fields", {})


def test_read_last_study_wallclock_by_strategy_empty_without_a_report(tmp_path, monkeypatch):
    import automation.optimizer.report as report_mod
    monkeypatch.setattr(report_mod, "REPORTS_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(report_mod, "RUN_FINGERPRINT_INDEX_PATH", Path(tmp_path / "nonexistent") / "run_fingerprints.jsonl")
    assert sweep._read_last_study_wallclock_by_strategy() == {}


def test_read_last_study_wallclock_by_strategy_reads_latest_report(tmp_path, monkeypatch):
    import automation.optimizer.report as report_mod
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    older = reports_dir / "report_1.json"
    older.write_text(json.dumps({"studies": [
        {"strategy": "ComboTrendVwapStrategy", "wallclock_s": 100},
    ]}), encoding="utf-8")
    newer = reports_dir / "report_2.json"
    newer.write_text(json.dumps({"studies": [
        {"strategy": "ComboTrendVwapStrategy", "wallclock_s": 2858},
        {"strategy": "ComboTrendVwapStrategy", "wallclock_s": 2900},
        {"strategy": "VolatilityBreakoutPumpStrategy", "wallclock_s": 849},
    ]}), encoding="utf-8")
    import os
    import time
    os.utime(older, (time.time() - 100, time.time() - 100))
    monkeypatch.setattr(report_mod, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(report_mod, "RUN_FINGERPRINT_INDEX_PATH", Path(reports_dir) / "run_fingerprints.jsonl")

    result = sweep._read_last_study_wallclock_by_strategy()
    assert result["ComboTrendVwapStrategy"] == 2879.0  # median(2858, 2900), the NEWER report only
    assert result["VolatilityBreakoutPumpStrategy"] == 849.0


def test_symbol_pairs_sort_descending_by_expected_wallclock():
    """Reine Sortierlogik-Verifikation (dieselbe key-Funktion wie im Dispatch-Loop)."""
    wallclock_by_strategy = {
        "ComboTrendVwapStrategy": 2858.0,
        "SqueezeBreakoutStrategy": 1500.0,
        "VolatilityBreakoutPumpStrategy": 849.0,
    }
    symbol_pairs = [
        ("VolatilityBreakoutPumpStrategy", "XOM.ETORO", "OK"),
        ("ComboTrendVwapStrategy", "XOM.ETORO", "OK"),
        ("UnknownNewStrategy", "XOM.ETORO", "OK"),
        ("SqueezeBreakoutStrategy", "XOM.ETORO", "OK"),
    ]
    sorted_pairs = sorted(
        symbol_pairs, key=lambda p: wallclock_by_strategy.get(p[0], 0.0), reverse=True)
    assert [p[0] for p in sorted_pairs] == [
        "ComboTrendVwapStrategy", "SqueezeBreakoutStrategy",
        "VolatilityBreakoutPumpStrategy", "UnknownNewStrategy",
    ]
