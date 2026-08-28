"""Issue #1308 (GH #1185, P1) — ``check_fail_fast_invariants_wired`` beurteilt eine Teilmenge des
Stroms.

Symptom. Der Wächter meldet ``check_bar_quality`` als "ohne jedes Ergebnis in diesem Lauf",
während derselbe Check im selben Artefakt mit ``passed=false`` steht (B-8).

Root-Cause. Die drei fail_fast_invariants-Meta-Wächter (``check_fail_fast_invariants_wired``/
``_actual_convention``/``_are_blocking``) lasen ausschliesslich ``[c.name for _label, c in
all_checks]``/``[c.to_dict() for _label, c in all_checks]`` — nur die report-seitig BERECHNETEN
Checks. Sweep-seitige ``INVARIANT_STREAM_RESULT``-Einträge (``_read_external_invariant_results``)
und Preflight-Ergebnisse werden erst SPÄTER, beim eigentlichen Aufbau von ``invariant_checks``,
gemergt.

Fix.
1. ``report._final_invariant_snapshot(all_checks, preflight_invariant_checks)`` vereint dieselben
   drei Quellen, aus denen ``invariant_checks`` weiter unten gebaut wird.
2. Alle drei Meta-Wächter beziehen ihre Eingabe über je eine eigene Call-Site dieser Funktion.
"""
import inspect

from automation.optimizer import invariants as inv
from automation.optimizer import report


class _FakeInvariantResult:
    """Minimaler Ersatz fuer InvariantResult — nur ``name``/``to_dict()`` werden von
    ``_final_invariant_snapshot`` konsumiert."""

    def __init__(self, name, passed=True, actual=None, severity="medium"):
        self.name = name
        self._d = {"name": name, "passed": passed, "actual": actual, "severity": severity}

    def to_dict(self):
        return dict(self._d)


# ── report._final_invariant_snapshot: vereint alle drei Quellen ─────────────────────────────────

def test_snapshot_includes_report_side_checks(monkeypatch):
    monkeypatch.setattr(report, "_read_external_invariant_results", lambda: [])
    all_checks = [("global", _FakeInvariantResult("check_holding_time_cap"))]
    snapshot = report._final_invariant_snapshot(all_checks, None)
    assert [d["name"] for d in snapshot] == ["check_holding_time_cap"]


def test_snapshot_includes_preflight_checks(monkeypatch):
    monkeypatch.setattr(report, "_read_external_invariant_results", lambda: [])
    preflight = [{"name": "check_required_config_keys", "passed": True}]
    snapshot = report._final_invariant_snapshot([], preflight)
    assert [d["name"] for d in snapshot] == ["check_required_config_keys"]


def test_snapshot_includes_sweep_side_stream_events():
    """Kern-Akzeptanzkriterium: ein NUR sweep-seitig gemeldeter Check (check_bar_quality) muss im
    Snapshot erscheinen, obwohl ``all_checks`` ihn nicht enthaelt (er lief ausserhalb von
    ``_build_report``, siehe ``_read_external_invariant_results``-Docstring)."""
    import automation.optimizer.report as report_mod
    original = report_mod._read_external_invariant_results
    try:
        report_mod._read_external_invariant_results = lambda: [
            {"name": "check_bar_quality", "check": "check_bar_quality", "passed": False,
             "actual": {"S/SYM": 1.0}, "severity": "blocking", "scope": "sweep"},
        ]
        snapshot = report_mod._final_invariant_snapshot([], None)
    finally:
        report_mod._read_external_invariant_results = original
    names = {d.get("name") for d in snapshot}
    assert "check_bar_quality" in names


def test_snapshot_merges_all_three_sources_together(monkeypatch):
    monkeypatch.setattr(report, "_read_external_invariant_results", lambda: [
        {"name": "check_bar_quality", "passed": False, "actual": {"S/SYM": 1.0}}])
    all_checks = [("global", _FakeInvariantResult("check_holding_time_cap"))]
    preflight = [{"name": "check_required_config_keys", "passed": True}]
    snapshot = report._final_invariant_snapshot(all_checks, preflight)
    names = {d.get("name") for d in snapshot}
    assert names == {"check_holding_time_cap", "check_required_config_keys", "check_bar_quality"}


# ── Akzeptanzkriterium 1 — check_fail_fast_invariants_wired PASSt fuer einen reinen Strom-Check ──

def test_fail_fast_invariants_wired_passes_for_a_stream_only_check(monkeypatch):
    """Vor dem Fix: ``check_fail_fast_invariants_wired`` sah nur ``all_checks`` (hier leer) und
    meldete ``check_bar_quality`` faelschlich als nicht verdrahtet, obwohl der Strom ein Ergebnis
    dafuer traegt."""
    monkeypatch.setattr(report, "_read_external_invariant_results", lambda: [
        {"name": "check_bar_quality", "passed": False, "actual": {"S/SYM": 1.0}}])
    snapshot = report._final_invariant_snapshot([], None)
    names = [d.get("name") or d.get("check") for d in snapshot if d.get("name") or d.get("check")]
    result = inv.check_fail_fast_invariants_wired(
        names, fail_fast_invariants=["check_bar_quality"])
    assert result.passed is True


def test_fail_fast_invariants_wired_still_fails_for_a_truly_unwired_check(monkeypatch):
    """Regressionsschutz: ein Name, der WEDER report- noch sweep-seitig je auftaucht, bleibt ein
    Offender."""
    monkeypatch.setattr(report, "_read_external_invariant_results", lambda: [])
    snapshot = report._final_invariant_snapshot([], None)
    names = [d.get("name") or d.get("check") for d in snapshot if d.get("name") or d.get("check")]
    result = inv.check_fail_fast_invariants_wired(
        names, fail_fast_invariants=["check_never_ran"])
    assert result.passed is False
    assert "check_never_ran" in result.actual


# ── Akzeptanzkriterium 3 — alle Meta-Wächter benutzen dieselbe Hilfsfunktion, je eine ───────────
# eigene Call-Site. Issue #1310 (GH #1187, P1) erweitert die Familie um einen vierten Wächter
# (``check_fail_fast_inconclusive_budget``), der dieselbe Konvention fortsetzt.

def test_all_four_meta_guards_call_the_shared_snapshot_helper_exactly_once_each():
    source = inspect.getsource(report._build_report)
    n_calls = source.count("_final_invariant_snapshot(all_checks, preflight_invariant_checks)")
    assert n_calls == 4, (
        f"erwartet: eine Call-Site je der vier fail_fast_invariants-Meta-Waechter (4 insgesamt), "
        f"gefunden: {n_calls}"
    )
    for guard_call in (
        "_inv.check_fail_fast_invariants_wired(",
        "_inv.check_fail_fast_actual_convention(",
        "_inv.check_fail_fast_invariants_are_blocking(",
        "_inv.check_fail_fast_inconclusive_budget(",
    ):
        idx = source.index(guard_call)
        # Der Snapshot-Aufruf muss innerhalb der naechsten paar Zeilen NACH dem Wächter-Aufruf
        # selbst auftauchen (dieselbe Anweisung fuettert ihn direkt oder ueber eine unmittelbar
        # zuvor gebundene Variable).
        window = source[max(0, idx - 400):idx + 200]
        assert "_final_invariant_snapshot(" in window, (
            f"{guard_call} bezieht seine Eingabe offenbar nicht aus _final_invariant_snapshot()"
        )
