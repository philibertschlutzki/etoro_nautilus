"""Issue #1347 (GH #1241, P3) — ``check_report_artifact_written`` prüft ausgerechnet bei
``completed_invalid`` (und jedem anderen terminalen Status ausser ``complete``) nichts.

Symptom. ``run_status='completed_invalid'``, ``report_written=true`` gemeldet — der Check trug
trotzdem ``passed=true``/``actual=null``/``detail="... != 'complete' — nicht anwendbar."``.

Root-Cause. Die Vorbedingung war an ``run_status == 'complete'`` gebunden statt an "der Lauf hat
einen terminalen Status erreicht".

Fix. Die Invariante gilt für JEDEN terminalen ``run_status`` (``complete``, ``completed_invalid``,
``aborted_*``, ...): ein Lauf, der ``report_written=true`` meldet, muss eine lesbare Report-Datei
hinterlassen haben. Nur ein Lauf OHNE terminalen Status (``'in_progress'``) ist "nicht anwendbar" —
trägt dann ``passed=None``, nicht ``passed=True`` (#1307-Tri-State).

Akzeptanzkriterien:
- Der Check prüft die Datei tatsächlich und PASSt mit ``actual={"path": …}``.
- Ein Lauf mit ``report_written=true`` ohne Datei FAILt blockierend.
- "Nicht anwendbar" trägt nie ``passed=true``.
"""
from automation.optimizer import invariants as inv


# ── terminale vs. nicht-terminale Status ──────────────────────────────────────────────────────────

def test_in_progress_is_not_applicable_and_is_tri_state_not_pass():
    result = inv.check_report_artifact_written(run_status="in_progress", report_written=False)
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


def test_none_run_status_is_also_not_applicable_and_tri_state():
    result = inv.check_report_artifact_written(run_status=None, report_written=False)
    assert result.passed is None
    assert result.inconclusive is True


def test_completed_invalid_is_terminal_and_is_evaluated():
    """Das #1347-Referenzsymptom: completed_invalid mit report_written=true PASSt jetzt ECHT
    (nicht mehr 'nicht anwendbar')."""
    result = inv.check_report_artifact_written(
        run_status="completed_invalid", report_written=True)
    assert result.passed is True
    assert result.inconclusive is False


def test_every_documented_terminal_status_is_evaluated_not_skipped():
    terminal_statuses = [
        "complete", "completed_invalid", "completed_with_quarantine",
        "completed_with_failures", "resumed_complete", "aborted_invariant",
        "aborted_wallclock", "aborted_disk", "aborted_signal", "aborted_error",
        "aborted_no_report",
    ]
    for status in terminal_statuses:
        result = inv.check_report_artifact_written(run_status=status, report_written=False)
        assert result.passed is False, f"{status}: erwartet FAIL (report_written=False), war {result.passed}"
        assert result.inconclusive is False, f"{status}: sollte NICHT 'nicht anwendbar' sein"


# ── report_written=false auf einem terminalen Lauf ────────────────────────────────────────────────

def test_report_written_false_on_a_terminal_run_fails_blocking():
    result = inv.check_report_artifact_written(run_status="aborted_error", report_written=False)
    assert result.passed is False
    assert result.severity == "blocking"


# ── report_path — tatsaechliche Dateipruefung statt blindem Vertrauen ────────────────────────────

def test_report_written_true_without_a_readable_file_fails_blocking(tmp_path):
    """Akzeptanzkriterium 2 — report_written=true behauptet, aber die Datei existiert nicht."""
    missing_path = str(tmp_path / "run_does_not_exist.json")
    result = inv.check_report_artifact_written(
        run_status="complete", report_written=True, report_path=missing_path)
    assert result.passed is False
    assert result.severity == "blocking"
    assert missing_path in result.detail


def test_report_written_true_with_an_empty_file_fails_blocking(tmp_path):
    empty_path = tmp_path / "run_empty.json"
    empty_path.write_text("", encoding="utf-8")
    result = inv.check_report_artifact_written(
        run_status="complete", report_written=True, report_path=str(empty_path))
    assert result.passed is False
    assert result.severity == "blocking"


def test_report_written_true_with_a_real_file_passes_and_carries_the_path(tmp_path):
    """Akzeptanzkriterium 1 — der vorliegende Lauf prueft die Datei tatsaechlich und PASSt mit
    actual={'path': ...}."""
    real_path = tmp_path / "run_abc123.json"
    real_path.write_text('{"run_status": "complete"}', encoding="utf-8")
    result = inv.check_report_artifact_written(
        run_status="complete", report_written=True, report_path=str(real_path))
    assert result.passed is True
    assert result.actual["path"] == str(real_path)


def test_report_path_omitted_falls_back_to_trusting_the_boolean_flag():
    """Rueckwaertskompatibilitaet: kein report_path -> reine report_written-Behauptung (Aufrufer/
    Tests ohne Pfad-Kontext bleiben unveraendert funktionsfaehig)."""
    result = inv.check_report_artifact_written(run_status="complete", report_written=True)
    assert result.passed is True


# ── Verdrahtung: sweep.py uebergibt report_path ────────────────────────────────────────────────────

def test_sweep_py_passes_report_path_to_the_check():
    import inspect
    from automation.optimizer import sweep as sweep_mod
    src = inspect.getsource(sweep_mod)
    assert "check_report_artifact_written(" in src
    assert "report_path=report_path if _report_written else None" in src
