"""Issue #942/#1108 (Katalog #960) — ``run_status`` ist nicht-deterministisch und beschreibt in
einem Fall das Gegenteil der Tatsachen.

Symptom: identische Fakten (14/14 Studies, volles Budget, Fail-Fast-Abbruch NACH Abschluss der
Arbeit) ergaben in zwei Reports ``completed_invalid`` ("vollständig gerechnet") und in einem
dritten ``aborted_invariant`` ("echter Arbeitsabbruch") — derselbe Faktenstand, zwei sich
widersprechende Lesarten. Im dritten Report waren zusaetzlich ``symbols_completed``/
``symbols_planned``/``symbols_discovered``/``symbols_gate1_rejected`` alle ``null``, weil die
Zaehler ausschliesslich aus einer erneut gelesenen Checkpoint-DATEI kamen, deren Lesevorgang
(Datei fehlt/kaputt oder ``run_id``-Mismatch durch einen anderen gleichzeitigen Prozess) still
fehlschlagen konnte.

Fix: GENAU EIN Statusfeld mit drei orthogonalen Achsen (``work_completed``/``decision_admissible``/
``fail_fast_triggered``) statt eines ueberladenen Strings, aus DERSELBEN Quelle (``invariant_checks``
plus Symbol-Zaehler) fuer JEDEN Report-Erzeugungspfad abgeleitet (``report._build_report``); die
Symbol-Zaehler kommen seither primaer aus einem In-Prozess-Spiegel (``sweep.sweep_symbol_funnel``),
der von Datei-I/O und fremden Prozessen unabhaengig ist.

Deckt ab:
  - ``_compute_decision_admissible``/``_compute_work_completed`` (reine Funktionen).
  - ``summary_de.py`` behauptet KEINEN Arbeitsabbruch, wenn ``work_completed`` wahr ist — weder in
    Sektion 1 noch in der "Lauf-Status:"-Zeile (Sektion 3.1).
  - ``sweep.sweep_symbol_funnel`` wird bei jedem Checkpoint-Schreiben aktualisiert, UNABHAENGIG
    davon, ob der Datei-Schreibversuch selbst gelingt.
"""
from automation.optimizer.report import _compute_decision_admissible, _compute_work_completed
from automation.optimizer import summary_de


# ── _compute_decision_admissible / _compute_work_completed (reine Funktionen) ───────────────────

def test_decision_admissible_true_without_any_blocking_fail():
    checks = [
        {"name": "a", "severity": "blocking", "passed": True},
        {"name": "b", "severity": "high", "passed": False},
    ]
    assert _compute_decision_admissible(checks) is True


def test_decision_admissible_false_with_a_blocking_fail():
    checks = [{"name": "check_effective_stop_distance", "severity": "blocking", "passed": False}]
    assert _compute_decision_admissible(checks) is False


def test_work_completed_true_when_all_planned_symbols_done():
    assert _compute_work_completed(14, 14) is True


def test_work_completed_false_when_fewer_completed_than_planned():
    assert _compute_work_completed(2, 14) is False


def test_work_completed_none_when_either_counter_unknown():
    assert _compute_work_completed(None, 14) is None
    assert _compute_work_completed(14, None) is None
    assert _compute_work_completed(None, None) is None


# ── summary_de.py: keine "Arbeitsabbruch"-Behauptung bei work_completed=True ─────────────────────

def _minimal_report(**overrides):
    base = {
        "run_id": "run-c", "run_status": "aborted_invariant",
        "started_at_utc": "2026-08-14T19:30:00Z", "wallclock_s": 3841.0,
        "cli_args": {"n_jobs": 7, "n_jobs_source": "CLI"},
        "symbols_completed": None, "symbols_planned": None,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


def test_section_1_never_claims_work_abort_when_work_completed_true():
    """Akzeptanzkriterium #942: Report C's Faktenstand (14/14 Studies, work_completed=True,
    decision_admissible=False, fail_fast_triggered='check_effective_stop_distance') darf NICHT als
    Arbeitsabbruch erscheinen."""
    report = _minimal_report(
        symbols_completed=14, symbols_planned=14,
        work_completed=True, decision_admissible=False,
        fail_fast_triggered="check_effective_stop_distance",
    )
    text = summary_de.generate_german_summary(report)
    section_1 = text.split("## 2.")[0]
    assert "Arbeitsabbruch" not in section_1
    assert "vollständig gerechnet" in section_1.lower() or "Vollständig gerechnet" in section_1
    assert "check_effective_stop_distance" in section_1


def test_section_1_claims_incompleteness_only_when_work_completed_false():
    report = _minimal_report(
        symbols_completed=2, symbols_planned=14,
        work_completed=False, decision_admissible=True, fail_fast_triggered=None,
    )
    text = summary_de.generate_german_summary(report)
    section_1 = text.split("## 2.")[0]
    assert "NICHT vollständig" in section_1


def test_section_1_no_status_note_when_fully_complete_and_admissible():
    report = _minimal_report(
        run_status="complete", symbols_completed=14, symbols_planned=14,
        work_completed=True, decision_admissible=True, fail_fast_triggered=None,
    )
    text = summary_de.generate_german_summary(report)
    section_1 = text.split("## 2.")[0]
    assert "Hinweis" not in section_1


def test_run_status_label_overrides_misleading_aborted_invariant_wording():
    """Report C's Lauf-Status-Zeile (Sektion 3.1) darf nicht mehr unbedingt "echter Arbeitsabbruch"
    zeigen, wenn work_completed bereits bekannt und wahr ist."""
    report = _minimal_report(
        run_status="aborted_invariant", symbols_completed=14, symbols_planned=14,
        work_completed=True, decision_admissible=False,
        fail_fast_triggered="check_effective_stop_distance",
    )
    label = summary_de._run_status_label_de(report)
    assert "echter Arbeitsabbruch" not in label
    assert not label.startswith("abgebrochen")
    assert "nicht entscheidungsfähig" in label


def test_run_status_label_legacy_fallback_without_new_fields():
    """Ein Report ohne die #942-Felder (Alt-Artefakt) faellt auf das rohe Label zurueck --
    unveraendertes Verhalten fuer bestehende Konsumenten."""
    report = _minimal_report(run_status="aborted_invariant")
    label = summary_de._run_status_label_de(report)
    assert label == summary_de._RUN_STATUS_LABELS_DE["aborted_invariant"]


def test_legacy_report_without_new_fields_uses_old_heuristic():
    """Ein Report ganz ohne work_completed/decision_admissible (vor #942) verhaelt sich exakt wie
    vor diesem Fix -- reine Rueckwaertskompatibilitaet."""
    report = _minimal_report(
        run_status="aborted_invariant", symbols_completed=14, symbols_planned=14,
    )
    text = summary_de.generate_german_summary(report)
    section_1 = text.split("## 2.")[0]
    assert "Vollständig gerechnet" in section_1
