"""Issue #1269 (GH #1139) — Fail-Fast-Probe auf Study-Ebene statt Symbol-Ebene.

Symptom. Die In-Prozess-Fail-Fast-Probe (``sweep.py``) wertet erst aus, sobald
``_fail_fast_min_symbols`` Symbole VOLLSTÄNDIG abgeschlossen sind (alle Strategien je Symbol) — bei
einem Single-Symbol-Lauf ist das strukturell erst am ENDE des Laufs der Fall. 3/3 betroffene Läufe
verbrauchten ihr gesamtes Wallclock-Budget, BEVOR die Probe überhaupt zum ersten Mal auswertete.

Scope-Entscheidung (bewusst dokumentiert). Fix Punkt 1 aus GH #1139 ("Probe nach jeder
abgeschlossenen STUDY statt nach jedem abgeschlossenen SYMBOL auswerten") wurde NICHT
implementiert: die Familien-Statistik-Maschine, die eine Probe dafür wiederverwenden müsste
(``sweep._run_confirm_and_export`` / ``deflation_n_family_frozen``), stempelt ihr Ergebnis als
EINMALIGEN, NIE NEU BERECHNETEN Wert auf die echten Optuna-Study-Objekte (siehe dortiger
Docstring: "EIN Stempelzeitpunkt, stabil unabhängig vom späteren Lesezeitpunkt"). Eine Probe, die
diese Maschine auf einer UNVOLLSTÄNDIGEN Familie (nur ein Teil der geplanten Studies) anstößt,
würde die Produktions-Familiengröße PERMANENT mit einem zu kleinen Wert verfälschen — ein
Korrektheitsrisiko, das der Fix Punkt 1 selbst nicht adressiert. Implementiert ist stattdessen Fix
Punkt 3 (das beobachtbare Telemetrie-/Invarianten-Paar unten), das den Effekt SICHTBAR macht, ohne
den riskanten Mechanismus selbst zu ändern.

Fix (Punkt 3).
1. ``sweep.sweep_fail_fast_probe_triggered_at_wallclock_s`` — Wallclock-Sekunden seit Sweep-Start,
   zu denen die In-Prozess-Probe zuletzt auswertete (unabhängig davon, ob sie feuerte).
2. Durchgereicht über ``generate_sweep_report``/``generate_report_for_run`` in
   ``report._build_report`` als ``probe_triggered_at_wallclock_s``; dort zu
   ``blocking_invariant_probe_triggered_at_wallclock_fraction = probe_triggered_at_wallclock_s / wallclock_s``
   verrechnet (``None``, wenn eine der beiden Größen unbekannt ist) und im Report-Dict ausgewiesen.
3. ``invariants.check_fail_fast_probe_timeliness`` (severity 'medium'): FAIL, wenn eine tatsächlich
   gefeuerte Probe erst bei > 60 % der Gesamt-Wallclock auswertete.
"""
import inspect

from automation.optimizer import invariants as inv, report as rpt, sweep


# ---------------------------------------------------------------------------------------------
# invariants.check_fail_fast_probe_timeliness
# ---------------------------------------------------------------------------------------------

def test_none_fraction_passes_not_inconclusive():
    r = inv.check_fail_fast_probe_timeliness(None)
    assert r.passed is True
    assert r.inconclusive is False
    assert r.severity == "medium"


def test_fraction_at_or_below_threshold_passes():
    # Issue #1287 (GH #1160) — Schwelle von 60 % auf 50 % gesenkt.
    assert inv.check_fail_fast_probe_timeliness(0.0).passed is True
    assert inv.check_fail_fast_probe_timeliness(0.50).passed is True
    assert inv.check_fail_fast_probe_timeliness(0.30).passed is True


def test_fraction_above_threshold_fails():
    r = inv.check_fail_fast_probe_timeliness(0.51)
    assert r.passed is False
    assert r.severity == "medium"
    assert r.actual == 0.51


def test_fraction_at_full_wallclock_fails():
    r = inv.check_fail_fast_probe_timeliness(1.0)
    assert r.passed is False


# ---------------------------------------------------------------------------------------------
# report._build_report — blocking_invariant_probe_triggered_at_wallclock_fraction computation
# ---------------------------------------------------------------------------------------------

def test_build_report_computes_fraction_from_probe_and_wallclock(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1269-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=100.0, cli_args={}, probe_triggered_at_wallclock_s=85.0,
        reports_dir=tmp_path,
    )
    assert report["blocking_invariant_probe_triggered_at_wallclock_fraction"] == 0.85


def test_build_report_fraction_none_when_no_probe_triggered(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1269-b", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=100.0, cli_args={}, probe_triggered_at_wallclock_s=None,
        reports_dir=tmp_path,
    )
    assert report["blocking_invariant_probe_triggered_at_wallclock_fraction"] is None


def test_build_report_fraction_none_when_wallclock_unknown(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1269-c", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=None, cli_args={}, probe_triggered_at_wallclock_s=42.0,
        reports_dir=tmp_path,
    )
    assert report["blocking_invariant_probe_triggered_at_wallclock_fraction"] is None


def test_build_report_late_probe_surfaces_as_failing_check(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1269-d", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=100.0, cli_args={}, probe_triggered_at_wallclock_s=95.0,
        reports_dir=tmp_path,
    )
    checks = [c for c in report["invariant_checks"] if c.get("check") == "check_fail_fast_probe_timeliness"]
    assert len(checks) == 1
    assert checks[0]["passed"] is False


def test_report_level_field_name_avoids_the_banned_fail_fast_substring(tmp_path):
    # Akzeptanzkriterium #1037/2 (siehe report._build_report-Docstring) — kein Feldname im
    # Report-Dict darf "fail_fast" enthalten, das ohne echten Abbruch gesetzt sein koennte. Die
    # invariants.py-Check-FUNKTION selbst (check_fail_fast_probe_timeliness) ist davon NICHT
    # betroffen (das Verbot gilt nur fuer report.keys(), siehe test_issue_1037_1186s eigener Test).
    report = rpt._build_report(
        [], run_id="run-1269-f", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=100.0, cli_args={}, probe_triggered_at_wallclock_s=50.0,
        reports_dir=tmp_path,
    )
    assert not any("fail_fast" in k for k in report.keys())


def test_build_report_wires_check_before_coverage_snapshot(tmp_path):
    # Regressionsschutz gegen die in dieser Session bereits einmal gefundene Positionierungs-Falle
    # (vgl. check_run_is_not_duplicate): eine NACH invariant_coverage_check platzierte
    # all_checks.append(...)-Stelle macht den neuen Check fuer die Coverage-Meta-Pruefung
    # unsichtbar, obwohl er im finalen invariant_checks-Strom erscheint.
    report = rpt._build_report(
        [], run_id="run-1269-e", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=100.0, cli_args={}, probe_triggered_at_wallclock_s=50.0,
        reports_dir=tmp_path,
    )
    coverage = next(
        c for c in report["invariant_checks"] if c.get("check") == "check_invariant_coverage")
    assert "check_fail_fast_probe_timeliness" not in (coverage.get("actual") or {}).get("missing", [])


# ---------------------------------------------------------------------------------------------
# sweep.py wiring — module-level telemetry global + CLI/report threading
# ---------------------------------------------------------------------------------------------

def test_sweep_module_declares_triggered_at_wallclock_global():
    assert hasattr(sweep, "sweep_fail_fast_probe_triggered_at_wallclock_s")


def test_sweep_main_threads_probe_timing_into_both_report_paths():
    src = inspect.getsource(sweep.main)
    assert "probe_triggered_at_wallclock_s=_probe_triggered_at_wallclock_s" in src
    assert src.count("probe_triggered_at_wallclock_s=_probe_triggered_at_wallclock_s") == 2


def test_run_per_symbol_sweep_resets_and_stamps_probe_timing():
    src = inspect.getsource(sweep.run_per_symbol_sweep)
    assert "sweep_fail_fast_probe_triggered_at_wallclock_s = None" in src
    assert "sweep_fail_fast_probe_triggered_at_wallclock_s = time.perf_counter() - sweep_t0" in src
