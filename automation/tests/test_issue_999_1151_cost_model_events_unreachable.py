"""Issue #999/#1151 (Katalog #1170) — ``COST_MODEL_RESOLVED``-Events erreichen den Report nie.

Symptom: ``check_cost_model_floor``/``check_cost_model_resolution`` meldeten in jedem Lauf "Keine
COST_MODEL_RESOLVED-Events — nicht auswertbar" bei ``passed=True``.

Root-Cause: ``COST_MODEL_RESOLVED`` wird im ISOLIERTEN Worker-Prozess unter dem Logger-Namen
``"backtest_worker"`` emittiert (``backtest_runner.py``, ``ProcessPoolExecutor``) — ein frischer
Interpreter-Prozess hat keine ``_JSONL_SIDECAR_PATHS``-Registrierung fuer diesen Logger-Namen
(Prozess-lokaler Modul-Zustand). ``report._build_report`` liest ausserdem den Sidecar des
``"optimizer"``-Loggers (Hauptprozess) — zwei disjunkte Transportwege. Beide Checks sahen dadurch
STRUKTURELL IMMER 0 Events, "emittiert, nie konsumiert" (zweite Instanz von #1138).

Fix-Entscheidung (beide im Issue genannten Optionen sind zulaessig): der transportseitige Fix
(Worker-Rueckgabewert -> Reemission im Hauptprozess, analog
``run_optimization._reemit_inference_diagnostics``) aendert den heissen Worker-Pfad an einer in
dieser Sandbox (kein ``nautilus_trader``) nicht end-to-end verifizierbaren Stelle. Diese Session
waehlt daher die Alternative: beide Checks werden aus dem Report-Invariantenstrom entfernt und in
``_DELIBERATELY_UNWIRED_INVARIANT_CHECKS`` dokumentiert.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


def test_both_cost_model_checks_are_on_the_deliberately_unwired_allowlist():
    assert "check_cost_model_resolution" in rpt._DELIBERATELY_UNWIRED_INVARIANT_CHECKS
    assert "check_cost_model_floor" in rpt._DELIBERATELY_UNWIRED_INVARIANT_CHECKS


def test_invariant_registry_wiring_check_still_passes():
    """Regressionswaechter (#984/#1138): die Introspektion darf nach der Entfernung der beiden
    Aufrufstellen NICHT rot werden, weil die Allowlist sie jetzt explizit dokumentiert."""
    result = rpt._invariant_registry_wiring_check()
    assert result.passed is True


def test_both_check_functions_still_exist_and_are_independently_unit_testable():
    """Die Funktionen selbst bleiben unveraendert (kein Code geloescht) — nur die Aufrufstelle in
    report.py._build_report ist entfernt; ein kuenftiger Transport-Fix braucht nur die
    all_checks.append(...)-Zeilen zurueckzusetzen."""
    result = inv.check_cost_model_resolution([])
    assert result.passed is True
    assert result.detail == "Keine COST_MODEL_RESOLVED-Events — nicht auswertbar."

    result = inv.check_cost_model_floor([])
    assert result.passed is True


def test_removed_call_sites_are_really_gone_from_build_report_source():
    """Textuelle Regressionssicherung: keine ``_inv.check_cost_model_resolution(``/
    ``_inv.check_cost_model_floor(``-Aufrufstelle mehr in report.py (die Funktionen selbst duerfen
    im Modul weiterhin in Kommentaren/Docstrings vorkommen)."""
    source = __import__("pathlib").Path(rpt.__file__).read_text("utf-8")
    assert "_inv.check_cost_model_resolution(" not in source
    assert "_inv.check_cost_model_floor(" not in source
