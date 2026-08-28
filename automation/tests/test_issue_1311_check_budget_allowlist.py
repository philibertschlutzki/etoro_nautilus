"""Issue #1311 (GH #1188, P2) — ``check_budget`` erscheint in keinem Artefakt.

Symptom. ``check_invariant_coverage`` FAILt: 143 definiert, 135 im Strom, 7 allowlistet,
``missing = ["check_budget"]`` (B-14).

Root-Cause. ``disk_guard.check_budget`` IST verdrahtet — ``run_optimization.disk_budget_callback``
ruft es auf und emittiert symmetrisch (PASS/PRESSURE/EXCEEDED) ein ``INVARIANT_STREAM_RESULT``-
Event (#1015/#1167) — aber NUR alle ``disk_check_interval_trials`` (Default 200) abgeschlossene
Trials EINER Study. Jeder Lauf mit < 200 Trials je Study (die uebergrosse Mehrheit kurzer Läufe,
beide committeten Referenzläufe eingeschlossen) durchläuft den Callback-Körper nie — ohne
Allowlist-Eintrag FAILt ``check_invariant_coverage`` in JEDEM solchen Lauf, obwohl die Prüfung
nachweislich existiert.

Fix. ``check_budget`` in ``report._DELIBERATELY_UNWIRED_INVARIANT_CHECKS`` mit Begründung
aufgenommen (Option 2 aus dem Issue-Text — der Check ist NICHT tot/abgelöst, nur strukturell
selten feuernd; ``check_budget_execution`` ist ein unabhängiger, anderer Check über die SUCH-
budget-Ausschöpfung, keine Ablösung von ``disk_guard.check_budget``s Disk-Budget-Prüfung).
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report


def test_check_budget_is_discovered_as_a_defined_check():
    """disk_guard.py ist eine der von _all_defined_check_names gescannten Dateien — check_budget
    muss als definiert auftauchen, sonst waere die Allowlist-Begruendung selbst hinfaellig."""
    assert "check_budget" in report._all_defined_check_names()


def test_check_budget_is_now_on_the_deliberately_unwired_allowlist():
    assert "check_budget" in report._DELIBERATELY_UNWIRED_INVARIANT_CHECKS


def test_check_invariant_coverage_passes_when_check_budget_is_defined_but_absent_from_stream():
    """Akzeptanzkriterium: check_invariant_coverage PASSt (n_defined - n_in_stream - n_allowlisted
    == 0), auch wenn check_budget (wie in jedem Lauf < 200 Trials/Study) nicht im Strom auftaucht."""
    defined = ["check_budget", "check_holding_time_cap"]
    stream = ["check_holding_time_cap"]  # check_budget fehlt, wie in einem kurzen Testlauf.
    result = inv.check_invariant_coverage(
        defined, stream, allowlisted_check_names=list(report._DELIBERATELY_UNWIRED_INVARIANT_CHECKS))
    assert result.passed is True


def test_check_invariant_coverage_still_fails_for_a_genuinely_unwired_new_check():
    """Regressionsschutz: die Allowlist deckt NUR die dort benannten Checks ab — ein voellig
    neuer, unbenannter check_*-Name FAILt weiterhin (die Allowlist ist kein Freifahrtschein)."""
    defined = ["check_budget", "check_some_brand_new_unwired_check"]
    stream = []
    result = inv.check_invariant_coverage(
        defined, stream, allowlisted_check_names=list(report._DELIBERATELY_UNWIRED_INVARIANT_CHECKS))
    assert result.passed is False
    assert "check_some_brand_new_unwired_check" in result.actual["missing"]
    assert "check_budget" not in result.actual["missing"]


def test_allowlist_entry_has_a_justification_comment_in_the_source():
    """Akzeptanzkriterium: 'Falls Allowlist: der Eintrag trägt eine Begründung in demselben Format
    wie die bestehenden sieben.' — geprüft als Quelltext-Nachbarschaft, dieselbe Konvention wie die
    bestehenden Eintraege (Issue-Nummer + Fliesstext direkt vor dem Namen)."""
    import inspect
    source = inspect.getsource(report)
    idx = source.index('"check_budget",')
    preceding = source[max(0, idx - 1400):idx]
    assert "#1311" in preceding or "#1188" in preceding
    assert "disk_check_interval_trials" in preceding
