"""Issue #746 — zweistufige Retention: Reports langlebig, Rohlogs/JSONL-Sidecars 7 Tage.

``cleanup_old_logs`` (7 Tage, `logs/*.log*` + `logs/*.jsonl` seit #741) würde die #740-Pro-Lauf-
Logs UND das #741-JSONL-Sidecar erfassen — beide liegen unter ``logs/``. Die #742-Reports liegen
bewusst SEPARAT unter ``data/optimizer/reports/`` und sind von ``cleanup_old_logs`` strukturell
nie betroffen, weil diese Funktion ausschliesslich das ihr übergebene Verzeichnis durchsucht (nie
rekursiv in ``data/``) UND weil ein Report-Dateiname (``run_<run_id>.json``) auf keines der
Cleanup-Globmuster passt, selbst im hypothetischen Fall einer Fehlkonfiguration.
"""
import time
from fnmatch import fnmatch
from pathlib import Path

from automation.log_manager import cleanup_old_logs
from automation.optimizer.manifest import WORK
from automation.optimizer.report import REPORTS_DIR


def test_reports_dir_is_outside_logs_tree():
    """data/optimizer/reports liegt NICHT unter logs/ — zwei getrennte Verzeichnis-Bäume."""
    assert "logs" not in REPORTS_DIR.parts
    assert REPORTS_DIR == WORK / "reports"


def test_report_filename_never_matches_cleanup_glob_patterns():
    """Selbst falls ein Report versehentlich im logs/-Verzeichnis landen würde: sein Dateiname
    passt strukturell auf KEINES der beiden cleanup_old_logs-Globmuster (`*.log*`, `*.jsonl`)."""
    report_name = "run_db7bf76_20260719T120000.json"
    assert not fnmatch(report_name, "*.log*")
    assert not fnmatch(report_name, "*.jsonl")


def test_cleanup_old_logs_never_touches_a_real_report_file(tmp_path):
    """End-to-End: ein reales Report-Artefakt im logs/-Verzeichnis (worst-case Fehlkonfiguration)
    übersteht cleanup_old_logs unversehrt, waehrend gleich alte .log/.jsonl-Dateien geloescht werden."""
    old_ts = time.time() - 10 * 86400

    report_file = tmp_path / "run_old_but_precious.json"
    report_file.write_text("{}", encoding="utf-8")

    stale_log = tmp_path / "optimizer_old_run.log"
    stale_log.write_text("stale", encoding="utf-8")
    stale_jsonl = tmp_path / "optimizer_old_run.events.jsonl"
    stale_jsonl.write_text('{"event_type": "x"}\n', encoding="utf-8")

    import os
    for f in (report_file, stale_log, stale_jsonl):
        os.utime(f, (old_ts, old_ts))

    deleted = cleanup_old_logs(tmp_path, max_age_days=7)

    assert deleted == 2, "genau die .log- und .jsonl-Datei muessen als Rohdaten geraeumt werden"
    assert report_file.exists(), "ein Report-Artefakt darf NIE durch die Rohlog-Retention geloescht werden"
    assert not stale_log.exists()
    assert not stale_jsonl.exists()


def test_jsonl_sidecar_now_covered_by_the_same_retention_as_prose_logs(tmp_path):
    """Cohort-E-Annahme (#746-Ist-Zustand) explizit hergestellt: das #741-JSONL-Sidecar unterliegt
    DERSELBEN 7-Tage-Rohdaten-Retention wie die Prosa-Logs — cleanup_old_logs deckt seit #741 auch
    *.jsonl ab (nicht nur *.log*)."""
    old_ts = time.time() - 10 * 86400
    jsonl = tmp_path / "optimizer_abc123_20260101T000000.events.jsonl"
    jsonl.write_text('{"event_type": "sweep_completed"}\n', encoding="utf-8")
    import os
    os.utime(jsonl, (old_ts, old_ts))

    deleted = cleanup_old_logs(tmp_path, max_age_days=7)
    assert deleted == 1
    assert not jsonl.exists()
