"""Issue #1066/#1216 (P2, Katalog #1196-1221) — `check_report_artifact_written` fehlt in 14/14 im
eigenen Strom.

Symptom: `check_invariant_coverage` meldet in allen 14 Läufen dieselbe eine Funktion als fehlend:
`check_report_artifact_written`.

Root-Cause: Selbstreferenz — die Prüfung, ob das Report-Artefakt geschrieben wurde, kann im Report
nicht stehen, weil sie erst nach dem Schreiben läuft.

Fix: `check_report_artifact_written` auf die `_DELIBERATELY_UNWIRED`-Allowlist gesetzt, mit
Begründung im Code, und ihr Ergebnis stattdessen in `run.json` (`report_artifact.written`, `path`,
`bytes`, `sha256`) gestempelt.

Akzeptanzkriterien: `check_invariant_coverage` passiert in 14/14; `run.json` enthält
`report_artifact`.
"""
import hashlib
import json

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import sweep as sw


def test_check_report_artifact_written_is_on_the_allowlist():
    assert "check_report_artifact_written" in rpt._DELIBERATELY_UNWIRED_INVARIANT_CHECKS


def test_check_invariant_coverage_no_longer_reports_it_as_missing():
    """Reproduziert das genaue #1216-Symptom: DEFINED enthaelt check_report_artifact_written,
    STREAM enthaelt es (strukturell) nie -- vor dem Fix war es deshalb IMMER 'missing'."""
    defined = ["check_report_artifact_written", "check_bar_quality"]
    stream = ["check_bar_quality"]  # check_report_artifact_written erscheint NIE im eigenen Strom
    result = inv.check_invariant_coverage(
        defined, stream, allowlisted_check_names=list(rpt._DELIBERATELY_UNWIRED_INVARIANT_CHECKS))
    assert result.passed is True


def test_check_invariant_coverage_still_fails_for_a_genuinely_unwired_check():
    """Die Allowlist darf nicht zu einem generellen Blankoscheck werden -- eine ECHTE Luecke muss
    weiterhin auffallen."""
    defined = ["check_report_artifact_written", "check_totally_new_and_unwired"]
    stream = []
    result = inv.check_invariant_coverage(
        defined, stream, allowlisted_check_names=list(rpt._DELIBERATELY_UNWIRED_INVARIANT_CHECKS))
    assert result.passed is False
    assert "check_totally_new_and_unwired" in result.actual["missing"]
    assert "check_report_artifact_written" not in result.actual["missing"]


# --- sweep._stamp_report_artifact_metadata: Read-Back-Patch, dieselbe Technik wie #1016 -----------

def test_stamp_report_artifact_metadata_adds_written_path_bytes_sha256(tmp_path):
    report_path = tmp_path / "run_test123.json"
    payload = {"run_id": "test123", "run_status": "complete", "studies": []}
    report_path.write_text(json.dumps(payload), "utf-8")
    # sha256/bytes werden auf dem Inhalt VOR dem Patch gebildet (siehe Docstring) -- vorab
    # unabhaengig nachrechnen.
    _pre_patch_bytes = report_path.read_bytes()
    expected_sha256 = hashlib.sha256(_pre_patch_bytes).hexdigest()
    expected_len = len(_pre_patch_bytes)

    result = sw._stamp_report_artifact_metadata(report_path)

    assert result == {
        "written": True, "path": str(report_path),
        "bytes": expected_len, "sha256": expected_sha256,
    }
    patched = json.loads(report_path.read_text("utf-8"))
    assert patched["report_artifact"] == result
    # der urspruengliche Inhalt bleibt erhalten (kein zweiter, abweichender Schreibpfad).
    assert patched["run_id"] == "test123"


def test_stamp_report_artifact_metadata_is_non_fatal_on_missing_file(tmp_path):
    result = sw._stamp_report_artifact_metadata(tmp_path / "does_not_exist.json")
    assert result is None
