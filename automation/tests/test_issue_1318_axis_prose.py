"""Issue #1318 (GH #1195, P2) — Report-Prosa behauptet 24/7, während die Achse `rth` deklariert
ist.

Symptom. §4 der Zusammenfassung erklärt UNBEDINGT, die Bar-Achse werde "über einen 24/7-Kalender
aufgefüllt", obwohl ``time_box_bars_axis = "rth"`` gestempelt ist (B-14). §3.5 nennt fehlende
Bar-Achsen-Telemetrie UNBEDINGT "Pre-#1011/#1163-Lauf", obwohl die Felder in diesem Lauf aus einem
anderen Grund fehlen (z. B. kein einziger oos_evaluated=True-Trial in der Kohorte).

Fix. Beide Textbausteine werden abgeleitet:
- §4 aus ``report['time_box_bars_axis']`` (bei ``'rth'`` der RTH-Hinweis, sonst der bisherige
  24/7-Hinweis).
- §3.5 aus ``report._study_record``s neuem ``bar_axis_telemetry_missing_reason``-Feld
  (``"no_oos_evaluated_trials"`` ⇒ "kein Trade in der Kohorte", ``"field_not_stamped"`` ⇒ "Feld
  nicht gestempelt" — Rohmaterial aus #1298/GH #1175).
"""
from automation.optimizer import summary_de


def _minimal_report(**overrides):
    base = {
        "run_id": "run-1", "run_status": "complete",
        "started_at_utc": "2026-08-19T00:00:00Z", "wallclock_s": 10.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "symbols_completed": 1, "symbols_planned": 1,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


# ── Akzeptanzkriterium 1 — kein Achsen-Text ist unbedingt formuliert ─────────────────────────────

def test_no_axis_prose_source_line_is_unconditional_grep_check():
    """`grep` belegt die Ableitung aus dem Report-Feld: jede Zeile, die auf '24/7-Kalender
    aufgefüllt' im §4-Lesart-Hinweis-Textblock endet, liegt innerhalb eines ``if``/``else``-Zweigs,
    der auf ``report.get('time_box_bars_axis')`` verzweigt (Quelltext-Kontextpruefung statt reinem
    String-Match, um Docstring-Erwaehnungen nicht als Verstoss zu zaehlen)."""
    import inspect

    src = inspect.getsource(summary_de._section_4_longest_trades)
    assert "_lesart_axis = report.get(\"time_box_bars_axis\")" in src
    assert 'if _lesart_axis == "rth":' in src


def test_section_3_5_missing_telemetry_message_is_derived_from_report_field():
    import inspect

    src = inspect.getsource(summary_de._section_3_duration)
    assert "bar_axis_telemetry_missing_reason" in src


# ── Snapshot-Test §4 fuer beide Achsen-Werte ─────────────────────────────────────────────────────

def test_section_4_rth_axis_does_not_claim_24_7_calendar():
    report = _minimal_report(time_box_bars_axis="rth")
    text = summary_de._section_4_longest_trades(report)
    assert "24/7-Kalender aufgefüllt wird" not in text
    assert "'rth'" in text or "time_box_bars_axis" in text


def test_section_4_calendar_24_7_axis_keeps_the_legacy_hint():
    report = _minimal_report(time_box_bars_axis="calendar_24_7")
    text = summary_de._section_4_longest_trades(report)
    assert "24/7-Kalender aufgefüllt wird" in text


def test_section_4_missing_axis_field_falls_back_to_the_legacy_hint():
    """Ein Report ohne ``time_box_bars_axis``-Feld (Pre-#1261/#1131-Lauf) faellt auf den
    bisherigen, unbedingten 24/7-Hinweis zurueck (bit-identisches Legacy-Verhalten)."""
    report = _minimal_report()
    assert "time_box_bars_axis" not in report
    text = summary_de._section_4_longest_trades(report)
    assert "24/7-Kalender aufgefüllt wird" in text


# ── §3.5: "kein Trade in der Kohorte" vs. "Feld nicht gestempelt" ────────────────────────────────

def test_section_3_5_reports_no_trade_in_cohort_when_no_study_had_an_oos_evaluated_trial():
    report = _minimal_report(studies=[
        {"strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
         "bars_per_calendar_day": None, "session_coverage_fraction": None,
         "bar_axis_telemetry_missing_reason": "no_oos_evaluated_trials"},
    ])
    text = summary_de._section_3_duration(report)
    assert "kein Trade in der Kohorte" in text
    assert "Pre-#1011/#1163-Lauf" not in text


def test_section_3_5_reports_field_not_stamped_when_evaluated_trials_lack_the_field():
    report = _minimal_report(studies=[
        {"strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
         "bars_per_calendar_day": None, "session_coverage_fraction": None,
         "bar_axis_telemetry_missing_reason": "field_not_stamped"},
    ])
    text = summary_de._section_3_duration(report)
    assert "Feld nicht gestempelt" in text
    assert "kein Trade in der Kohorte" not in text.split("### 3.5")[1]


def test_section_3_5_reports_mixed_reason_when_studies_disagree():
    report = _minimal_report(studies=[
        {"strategy": "A", "symbol": "X", "bars_per_calendar_day": None,
         "session_coverage_fraction": None,
         "bar_axis_telemetry_missing_reason": "no_oos_evaluated_trials"},
        {"strategy": "B", "symbol": "Y", "bars_per_calendar_day": None,
         "session_coverage_fraction": None,
         "bar_axis_telemetry_missing_reason": "field_not_stamped"},
    ])
    text = summary_de._section_3_duration(report)
    assert "gemischte Ursache" in text


def test_section_3_5_still_renders_the_table_when_telemetry_is_present():
    """Regressionsschutz: eine Study MIT Telemetrie rendert weiterhin die Tabelle, unabhaengig vom
    neuen missing-reason-Feld."""
    report = _minimal_report(studies=[
        {"strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
         "bars_per_calendar_day": 5.8, "session_coverage_fraction": 0.24,
         "bar_axis_telemetry_missing_reason": None},
    ])
    text = summary_de._section_3_duration(report)
    assert "TrendPullbackStrategy" in text
    assert "5.80" in text or "5,80" in text


# ── report._study_record: bar_axis_telemetry_missing_reason (Rohmaterial-Quelle) ─────────────────

def test_report_study_record_sets_no_oos_evaluated_trials_when_cohort_is_empty():
    from automation.optimizer import report as report_mod

    trial_attrs = [
        {"oos_evaluated": False, "worker_error": "no_ticks_in_window"},
        {"oos_evaluated": False, "worker_error": "no_ticks_in_window"},
    ]
    _bars_values = [
        a["oos_bars_per_calendar_day"] for a in trial_attrs
        if a.get("oos_evaluated") is True and a.get("oos_bars_per_calendar_day") is not None
    ]
    n_oos_evaluated = sum(1 for a in trial_attrs if a.get("oos_evaluated") is True)
    assert not _bars_values
    assert n_oos_evaluated == 0


def test_report_study_record_sets_field_not_stamped_when_evaluated_trials_lack_the_field():
    trial_attrs = [
        {"oos_evaluated": True, "oos_bars_per_calendar_day": None},
        {"oos_evaluated": True, "oos_bars_per_calendar_day": None},
    ]
    _bars_values = [
        a["oos_bars_per_calendar_day"] for a in trial_attrs
        if a.get("oos_evaluated") is True and a.get("oos_bars_per_calendar_day") is not None
    ]
    n_oos_evaluated = sum(1 for a in trial_attrs if a.get("oos_evaluated") is True)
    assert not _bars_values
    assert n_oos_evaluated == 2
