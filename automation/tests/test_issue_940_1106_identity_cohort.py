"""Issue #940/#1106 (Katalog #960, HEADLINE) — ``check_report_cohort_coherence`` bleibt fuer
zeitlich enthaltene Nachbarlaeufe blind.

Root-Cause: alle drei ehemaligen Klauseln von ``check_report_cohort_coherence`` waren
ENTHALTUNGSTESTS (pruefen, ob eine Study zeitlich AUSSERHALB des Laufsfensters liegt). Ein
Nachbarlauf, der NACH dem Referenzlauf startet und VOR ihm endet, liegt vollstaendig INNERHALB und
ist damit strukturell nicht detektierbar — unabhaengig von jeder Schwellenwahl (B-3:
earliest_offset_s=-4.0s, latest_overrun_s=-195.6s, Spannweite 199.9s gegen Schwelle 3141.0s, alle
drei PASS trotz 51.7% fremder Studies).

Fix: die Invariante urteilt jetzt ueber IDENTITAET (``record['run_id'] == run_id``) statt Zeit; die
alten Zeitklauseln leben als reine Diagnose in ``check_cohort_clock_drift`` (severity ``low``,
siehe test_issue_1087_cohort_coherence_offset.py). Eine echte, unabhaengige zweite Linie
(``check_report_cohort_event_stream_coherence``) prueft gegen eine ANDERE Evidenzachse (den
``optimizer_study_completed``-Ereignisstrom statt Trial-``user_attrs``).

Deckt ab:
  - Akzeptanzkriterium #940: ein Regressionstest mit der B-3-Zeitgeometrie (Nachbar startet +185s,
    endet -195s vor dem Laufende — vollstaendig zeitlich enthalten) FAILt ueber Identitaet, obwohl
    die Zeitgeometrie selbst unauffaellig waere.
  - ``record["run_id"] is None`` erzeugt einen blockierenden Befund, keinen stillen Durchlass.
  - ``check_report_cohort_event_stream_coherence`` erkennt Divergenz in BEIDE Richtungen und
    ignoriert Ereignisse fremder run_id im selben Sidecar.
"""
from automation.optimizer import invariants as inv


def test_identity_check_passes_when_every_record_matches_run_id():
    records = [
        {"strategy": "TestStrat", "symbol": "TSLA.ETORO", "run_id": "run_a"},
        {"strategy": "TestStrat", "symbol": "NVDA.ETORO", "run_id": "run_a"},
    ]
    result = inv.check_report_cohort_coherence(records, run_id="run_a")
    assert result.passed is True
    assert result.severity == "blocking"


def test_identity_check_catches_b3_time_contained_neighbor_run():
    """Akzeptanzkriterium #940: die B-3-Geometrie (Nachbar startet +185s NACH Laufbeginn, endet
    -195s VOR dem erwarteten Laufende — vollstaendig zeitlich innerhalb) FAILt ueber Identitaet.
    Zum Beleg, dass die Zeitklauseln diese Geometrie NICHT erkennen wuerden: check_cohort_clock_drift
    PASSt auf denselben Records."""
    records = [
        {"strategy": "TestStrat", "symbol": "TSLA.ETORO", "run_id": "run_a",
         "study_started_at_utc": "2026-08-14T19:30:00.000+00:00",
         "study_ended_at_utc": "2026-08-14T20:34:00.000+00:00"},
        # Nachbarlauf: startet 185s NACH dem Referenzlauf, endet 195s VOR dessen erwartetem Ende —
        # zeitlich vollstaendig enthalten, aber eine ANDERE run_id.
        {"strategy": "OtherStrat", "symbol": "PLTR.ETORO", "run_id": "run_neighbor",
         "study_started_at_utc": "2026-08-14T19:33:05.000+00:00",
         "study_ended_at_utc": "2026-08-14T20:30:45.000+00:00"},
    ]
    identity_result = inv.check_report_cohort_coherence(records, run_id="run_a")
    assert identity_result.passed is False
    assert identity_result.severity == "blocking"
    assert "OtherStrat/PLTR.ETORO" in identity_result.provenance["violating_studies"]

    # Die Zeitgeometrie selbst waere unauffaellig -- exakt die #1106-Root-Cause.
    clock_drift_result = inv.check_cohort_clock_drift(
        records, wallclock_s=3841.0, run_started_at_utc="2026-08-14T19:30:00.000+00:00")
    assert clock_drift_result.passed is True, (
        "Die Zeitklauseln haetten den enthaltenen Nachbarlauf NICHT erkannt -- das ist die "
        "#1106-Root-Cause, kein Testfehler.")


def test_none_run_id_on_a_record_is_blocking_not_fail_open():
    """Akzeptanzkriterium #940: record['run_id'] is None erzeugt einen blockierenden Befund."""
    records = [
        {"strategy": "TestStrat", "symbol": "TSLA.ETORO", "run_id": "run_a"},
        # Legacy-Fallback-Study ohne jede run_id-Evidenz auf ihren Trials (report._build_report
        # stempelt in diesem Fall record["run_id"] = None statt der aufrufenden run_id).
        {"strategy": "LegacyStrat", "symbol": "OLD.ETORO", "run_id": None},
    ]
    result = inv.check_report_cohort_coherence(records, run_id="run_a")
    assert result.passed is False
    assert "LegacyStrat/OLD.ETORO" in result.provenance["violating_studies"]


def test_missing_run_id_key_is_also_blocking():
    records = [{"strategy": "TestStrat", "symbol": "TSLA.ETORO"}]  # kein "run_id"-Schluessel
    result = inv.check_report_cohort_coherence(records, run_id="run_a")
    assert result.passed is False


def test_no_run_id_context_is_not_applicable():
    records = [{"strategy": "TestStrat", "symbol": "TSLA.ETORO", "run_id": None}]
    result = inv.check_report_cohort_coherence(records, run_id=None)
    assert result.passed is True
    assert result.actual is None


def test_empty_cohort_passes_trivially():
    assert inv.check_report_cohort_coherence([], run_id="run_a").passed is True


# ── check_report_cohort_event_stream_coherence ───────────────────────────────────────────────────

def test_event_stream_coherence_passes_when_pairs_match():
    records = [{"strategy": "TestStrat", "symbol": "TSLA.ETORO"}]
    events = [
        {"event_type": "optimizer_study_completed", "run_id": "run_a",
         "strategy": "TestStrat", "symbol": "TSLA.ETORO"},
    ]
    result = inv.check_report_cohort_event_stream_coherence(
        records, run_id="run_a", study_completed_events=events)
    assert result.passed is True
    assert result.severity == "blocking"


def test_event_stream_coherence_catches_study_in_report_without_matching_event():
    """Der Fehlermodus, den die Trial-user_attrs-basierte Identitaetspruefung NICHT unabhaengig
    entdecken kann (dieselbe Evidenzquelle) -- der Ereignisstrom ist eine ANDERE Achse."""
    records = [{"strategy": "TestStrat", "symbol": "TSLA.ETORO"},
               {"strategy": "OtherStrat", "symbol": "PLTR.ETORO"}]
    events = [
        {"event_type": "optimizer_study_completed", "run_id": "run_a",
         "strategy": "TestStrat", "symbol": "TSLA.ETORO"},
    ]
    result = inv.check_report_cohort_event_stream_coherence(
        records, run_id="run_a", study_completed_events=events)
    assert result.passed is False
    assert result.actual["missing_in_events"] == ["OtherStrat/PLTR.ETORO"]
    assert result.actual["missing_in_report"] == []


def test_event_stream_coherence_catches_event_without_matching_report_study():
    records = [{"strategy": "TestStrat", "symbol": "TSLA.ETORO"}]
    events = [
        {"event_type": "optimizer_study_completed", "run_id": "run_a",
         "strategy": "TestStrat", "symbol": "TSLA.ETORO"},
        {"event_type": "optimizer_study_completed", "run_id": "run_a",
         "strategy": "OtherStrat", "symbol": "PLTR.ETORO"},
    ]
    result = inv.check_report_cohort_event_stream_coherence(
        records, run_id="run_a", study_completed_events=events)
    assert result.passed is False
    assert result.actual["missing_in_report"] == ["OtherStrat/PLTR.ETORO"]


def test_event_stream_coherence_ignores_foreign_run_id_events_in_same_sidecar():
    """B-2: der Ereignisstrom ist lauf-sauber, aber die Funktion filtert defensiv trotzdem selbst
    auf run_id -- ein Ereignis einer FREMDEN run_id im selben Sidecar darf weder verdecken noch
    faelschlich als Uebereinstimmung zaehlen."""
    records = [{"strategy": "TestStrat", "symbol": "TSLA.ETORO"}]
    events = [
        {"event_type": "optimizer_study_completed", "run_id": "run_a",
         "strategy": "TestStrat", "symbol": "TSLA.ETORO"},
        {"event_type": "optimizer_study_completed", "run_id": "run_foreign",
         "strategy": "TestStrat", "symbol": "TSLA.ETORO"},
        {"event_type": "optimizer_study_completed", "run_id": "run_foreign",
         "strategy": "ForeignStrat", "symbol": "FOREIGN.ETORO"},
    ]
    result = inv.check_report_cohort_event_stream_coherence(
        records, run_id="run_a", study_completed_events=events)
    assert result.passed is True
    assert result.actual["n_events"] == 1


def test_event_stream_coherence_not_applicable_without_context():
    result = inv.check_report_cohort_event_stream_coherence(
        [{"strategy": "TestStrat", "symbol": "TSLA.ETORO"}],
        run_id=None, study_completed_events=None)
    assert result.passed is True
