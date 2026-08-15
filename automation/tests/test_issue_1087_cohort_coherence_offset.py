"""Issue #1087 (Katalog #920) — die Zeit-basierten Klauseln messen den OFFSET zum Laufbeginn/-ende,
nicht nur die Spannweite der Studies-Vereinigung.

Root-Cause: die Alt-Fassung bildete ausschliesslich ``max(study_started) - min(study_started)``
gegen ``wallclock_s + tolerance_s``. Bei ueberlappenden Laeufen bleibt diese Spannweite strukturell
UNTER dem Budget, obwohl die frueheste Study klar VOR dem Laufbeginn dieses Reports gestartet
wurde — die Groesse ist blind fuer eine Vorlaufkohorte. Diese Tests reproduzieren genau das
Muster aus den drei kontaminierten Referenzreports (#1086-Katalog): eine fremde, frueh gestartete
Study bleibt UNTER der Spannweiten-Schranke, wird aber durch die Offset-Klausel erkannt.

Update #940/#1106 (Katalog #960): diese drei Zeitklauseln leben seit #1106 in
``check_cohort_clock_drift`` (severity ``low``, reine Uhr-Drift-Diagnose) statt in
``check_report_cohort_coherence`` (das seither ausschliesslich ueber Kohorten-IDENTITAET urteilt,
siehe ``test_issue_940_1106_identity_cohort.py``) — Root-Cause: B-3 zeigte, dass genau diese drei
Zeitklauseln fuer einen zeitlich vollstaendig ENTHALTENEN Nachbarlauf strukturell blind sind
(Enthaltungstests koennen keine Identitaet entscheiden). Die Tests hier bleiben inhaltlich
unveraendert (dieselbe Zeitgeometrie, dieselben Offsets), nur Funktionsname und Severity wurden
auf die neue Aufteilung angepasst.
"""
from automation.optimizer import invariants as inv


def test_span_clause_alone_would_miss_a_leading_foreign_study():
    """Sanity-Beleg fuer die Root-Cause: die reine Spannweiten-Bedingung (Klausel 3) besteht,
    obwohl die frueheste Study 2757s vor dem Laufbeginn gestartet wurde — die Vereinigung bleibt
    unter wallclock_s + tolerance_s, weil die spaetere (eigene) Study die Spannweite nicht
    verlaengert."""
    run_started = "2026-08-13T15:45:00.000+00:00"
    wallclock_s = 3200.0
    records = [
        # Fremde, VOR dem Laufbeginn gestartete Study (die eigentliche Kontamination).
        {"strategy": "Combo", "symbol": "ASML.ETORO",
         "study_started_at_utc": "2026-08-13T14:59:03.000+00:00",
         "study_ended_at_utc": "2026-08-13T15:44:50.000+00:00"},
        # Eigene Study, kurz nach Laufbeginn gestartet.
        {"strategy": "TestStrat", "symbol": "TSLA.ETORO",
         "study_started_at_utc": "2026-08-13T15:46:40.000+00:00",
         "study_ended_at_utc": "2026-08-13T16:38:00.000+00:00"},
    ]
    # Klausel 3 isoliert (kein run_started_at_utc uebergeben ⇒ nur die Alt-Bedingung aktiv):
    span_only = inv.check_cohort_clock_drift(records, wallclock_s=wallclock_s)
    assert span_only.passed is True, (
        "Spannweite allein haette die fremde Study NICHT erkannt — genau die #1087-Root-Cause.")

    # Mit run_started_at_utc (die Offset-Klauseln) erkennt derselbe Report die Kontamination —
    # aber seit #1106 nur noch als NIEDRIG-schwerer Uhr-Drift-Hinweis, kein Beleg mehr gegen
    # Kohortenmischung (das leistet jetzt check_report_cohort_coherence ueber Identitaet).
    result = inv.check_cohort_clock_drift(
        records, wallclock_s=wallclock_s, run_started_at_utc=run_started)
    assert result.passed is False
    assert result.severity == "low"
    assert result.actual["earliest_offset_s"] == 2757.0
    assert "Combo/ASML.ETORO" in result.provenance["violating_studies"]


def test_reference_reports_fail_with_expected_earliest_offsets():
    """Akzeptanzkriterium #1087: die drei kontaminierten Referenzreports melden FAIL mit
    earliest_offset_s = 2469.1 / 2644.1 / 2757.0."""
    run_started = "2026-08-13T15:45:00.000+00:00"
    for expected_offset in (2469.1, 2644.1, 2757.0):
        earliest_dt_s = 55 * 60 - expected_offset  # Sekunden seit Mitternacht-Offset, beliebig
        records = [
            {"strategy": "AdxAtrMomentum", "symbol": "ASML.ETORO",
             "study_started_at_utc": "2026-08-13T15:45:00.000+00:00"},
        ]
        # Frueheste Study exakt `expected_offset` Sekunden vor Laufbeginn.
        import datetime as _dt
        run_dt = _dt.datetime.fromisoformat(run_started)
        earliest_dt = run_dt - _dt.timedelta(seconds=expected_offset)
        records[0]["study_started_at_utc"] = earliest_dt.isoformat()

        result = inv.check_cohort_clock_drift(
            records, wallclock_s=3200.0, run_started_at_utc=run_started)
        assert result.passed is False
        assert result.severity == "low"
        assert result.actual["earliest_offset_s"] == round(expected_offset, 1)


def test_clean_reference_report_passes_with_negative_offset():
    """Akzeptanzkriterium #1087: der saubere 3910e12b-Referenzlauf meldet PASS mit
    earliest_offset_s = -4.3 (Study startete NACH dem Laufbeginn, wie erwartet)."""
    run_started = "2026-08-13T15:04:41.000+00:00"
    records = [
        {"strategy": "TestStrat", "symbol": "A.ETORO",
         "study_started_at_utc": "2026-08-13T15:04:45.300+00:00",
         "study_ended_at_utc": "2026-08-13T15:11:41.000+00:00"},
    ]
    result = inv.check_cohort_clock_drift(
        records, wallclock_s=500.0, run_started_at_utc=run_started)
    assert result.passed is True
    assert result.actual["earliest_offset_s"] == -4.3


def test_end_after_expected_run_end_fails_independently_of_start_clause():
    run_started = "2026-08-13T15:45:00.000+00:00"
    wallclock_s = 600.0
    records = [
        {"strategy": "TestStrat", "symbol": "A.ETORO",
         "study_started_at_utc": "2026-08-13T15:45:10.000+00:00",
         "study_ended_at_utc": "2026-08-13T16:10:00.000+00:00"},  # weit nach 15:55 erwartetem Ende
    ]
    result = inv.check_cohort_clock_drift(
        records, wallclock_s=wallclock_s, run_started_at_utc=run_started)
    assert result.passed is False
    assert result.actual["latest_overrun_s"] > 60.0
