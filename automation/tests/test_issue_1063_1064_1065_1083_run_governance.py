"""Issue #1063/#1064/#1065/#1083 (Katalog #866-2, Kohorte A, "Lauf-Governance") — der Fail-Fast-
Mechanismus, das Coverage-Ledger und die deutschsprachige Zusammenfassung wiesen widersprüchliche
oder falsche Verdikte über denselben Lauf aus.

#1063 — ``check_holding_time_cap`` stempelte seinen Magnituden-Ast (#1036) nicht in ``actual``;
``sweep._offending_pairs_for_fail_fast_check`` konnte die Offender dann nicht parsen und fiel auf
den globalen Konservativ-Abbruch zurück, OHNE die #1016-Breitenschwelle auszuwerten. Fix: ``actual``
trägt jetzt beide Äste; der Parser versucht zusätzlich ``provenance['magnitude_offenders']`` als
Fallback; eine neue Meta-Invariante (``check_fail_fast_actual_convention``) macht eine künftige
Vertragsverletzung sichtbar statt sie still zu tolerieren.

#1064 — ``has_prior_reports`` zählte den eigenen Report DIESES Laufs mit, sobald er zwischen zwei
Auswertungswellen bereits in ``REPORTS_DIR`` lag (Selbstreferenz). Fix: Ausschluss über ``run_id``
UND eine ``coverage_bootstrap_phase``-Ausnahme (dieselbe Bootstrap-Semantik wie
``check_symbol_coverage``).

#1065 — "dieser Lauf ist NICHT vollständig" wurde allein aus ``run_status != 'complete'``
abgeleitet, auch wenn 100 % der geplanten Arbeit tatsächlich fertig gerechnet war
(``run_status == 'aborted_invariant'`` nach einem Fail-Fast-Abbruch, der erst NACH dem letzten
Symbol greift). Fix: Vollständigkeit (Abdeckung) und Gültigkeit (blockierende Invarianten) werden
als getrennte Aussagen behandelt; ``sweep.py`` unterscheidet jetzt ``completed_invalid`` (voll
abgedeckt, aber ungültig) von ``aborted_invariant`` (echter Abbruch mit unvollständiger Arbeit).
"""
from pathlib import Path

from automation.optimizer import invariants as inv
from automation.optimizer import sweep
from automation.optimizer import summary_de as sd


# ── #1063: check_holding_time_cap stamps BOTH branches into actual ──────────────────────────────
def test_holding_time_cap_actual_includes_magnitude_only_offenders():
    records = [{
        "strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO",
        "max_holding_time_s": 3991 * 3600.0,
    }]
    result = inv.check_holding_time_cap(records)
    assert result.passed is False
    assert result.actual is not None
    assert "SqueezeBreakoutStrategy/TSLA.ETORO" in result.actual


def test_holding_time_cap_actual_prefers_fraction_value_when_both_branches_fire():
    records = [{
        "strategy": "S", "symbol": "X",
        "timebox_violating_trades_denominator": 500,
        "timebox_violating_trades_numerator": 150,
        "timebox_violating_trades_frac": 0.30,
        "max_holding_time_s": 4.0 * 27 * 3600.0,
    }]
    result = inv.check_holding_time_cap(records, study_tolerance=0.25, hard_multiple=3.0)
    assert result.actual["S/X"] == 0.30


# ── #1063: sweep._offending_pairs_for_fail_fast_check falls back to provenance ───────────────────
def test_offending_pairs_falls_back_to_provenance_magnitude_offenders():
    checks = [{
        "name": "check_holding_time_cap", "passed": False,
        "actual": None,
        "provenance": {"magnitude_offenders": {"SqueezeBreakoutStrategy/TSLA.ETORO": 36.66}},
    }]
    pairs, symbols = sweep._offending_pairs_for_fail_fast_check(checks, "check_holding_time_cap")
    assert "TSLA.ETORO" in symbols
    assert len(pairs) == 1


def test_offending_pairs_still_falls_back_to_conservative_branch_when_nothing_parseable():
    checks = [{"name": "check_holding_time_cap", "passed": False, "actual": None}]
    pairs, symbols = sweep._offending_pairs_for_fail_fast_check(checks, "check_holding_time_cap")
    assert pairs == {}
    assert symbols == set()


# ── #1063: new meta-invariant ────────────────────────────────────────────────────────────────────
def test_fail_fast_actual_convention_fails_on_actual_none():
    checks = [{"name": "check_holding_time_cap", "passed": False, "actual": None}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_holding_time_cap"])
    assert result.passed is False
    assert "check_holding_time_cap" in result.actual


def test_fail_fast_actual_convention_passes_on_conforming_actual():
    checks = [{"name": "check_holding_time_cap", "passed": False,
               "actual": {"S/X": 36.66}}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_holding_time_cap"])
    assert result.passed is True


def test_fail_fast_actual_convention_ignores_passing_checks():
    checks = [{"name": "check_holding_time_cap", "passed": True, "actual": None}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_holding_time_cap"])
    assert result.passed is True


# ── #1307 (GH #1184, P1): passed=None (Tri-State, inkonklusiv) ist KEIN Konventionsverstoss ─────
def test_fail_fast_actual_convention_ignores_inconclusive_none_passed():
    """Root-Cause: ``chk.get('passed', True)`` behandelte ``None`` (falsy) wie ein fehlendes Feld
    und liess den ``continue`` nicht greifen — ``check_effective_stop_distance`` bei leerer Kohorte
    (``passed=None``, ``actual=None``) wurde faelschlich als Offender gemeldet."""
    checks = [{"name": "check_effective_stop_distance", "passed": None, "actual": None}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_effective_stop_distance"])
    assert result.passed is True
    assert "check_effective_stop_distance" not in (result.actual or {})


def test_fail_fast_actual_convention_still_flags_explicit_fail_with_none_actual():
    """Regressionsschutz: eine EXPLIZIT FAILende Auswertung (``passed=False``) bleibt ein Offender,
    unveraendert durch die Tri-State-Praezisierung."""
    checks = [{"name": "check_holding_time_cap", "passed": False, "actual": None}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_holding_time_cap"])
    assert result.passed is False
    assert "check_holding_time_cap" in result.actual


def test_fail_fast_actual_convention_still_passes_explicit_fail_with_conforming_actual():
    """Regressionsschutz: eine FAILende Auswertung mit konformem ``actual`` bleibt kein Offender,
    unveraendert durch die Tri-State-Praezisierung."""
    checks = [{"name": "check_holding_time_cap", "passed": False,
               "actual": {"S/SYM": 1.0}}]
    result = inv.check_fail_fast_actual_convention(
        checks, fail_fast_invariants=["check_holding_time_cap"])
    assert result.passed is True


# ── #1064: check_coverage_ledger_continuity bootstrap exemption ─────────────────────────────────
def test_coverage_ledger_continuity_fails_without_bootstrap():
    result = inv.check_coverage_ledger_continuity(1, True)
    assert result.passed is False


def test_coverage_ledger_continuity_passes_during_bootstrap():
    result = inv.check_coverage_ledger_continuity(1, True, coverage_bootstrap_phase=True)
    assert result.passed is True


# ── #1064: report._coverage_ledger_continuity_check excludes own run_id ─────────────────────────
def test_coverage_ledger_continuity_check_excludes_own_report(tmp_path, monkeypatch):
    from automation.optimizer import report as rpt
    monkeypatch.setattr(rpt, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(rpt, "RUN_FINGERPRINT_INDEX_PATH", Path(tmp_path) / "run_fingerprints.jsonl")
    (tmp_path / "run_abc123.json").write_text("{}", "utf-8")

    class _FakeCoverage:
        @staticmethod
        def load_coverage():
            return {"total_runs_started": 1}

    monkeypatch.setattr(rpt, "_symbol_coverage", _FakeCoverage)
    # Der eigene Report (run_id='abc123') liegt bereits auf der Platte — OHNE Ausschluss würde
    # has_prior_reports faelschlich True werden, obwohl es der EIGENE Report ist.
    result = rpt._coverage_ledger_continuity_check("abc123")
    assert result.passed is True


def test_coverage_ledger_continuity_check_still_detects_a_genuinely_different_prior_report(
        tmp_path, monkeypatch):
    from automation.optimizer import report as rpt
    monkeypatch.setattr(rpt, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(rpt, "RUN_FINGERPRINT_INDEX_PATH", Path(tmp_path) / "run_fingerprints.jsonl")
    (tmp_path / "run_some_other_run.json").write_text("{}", "utf-8")

    class _FakeCoverage:
        @staticmethod
        def load_coverage():
            return {"total_runs_started": 1}

    monkeypatch.setattr(rpt, "_symbol_coverage", _FakeCoverage)
    result = rpt._coverage_ledger_continuity_check("abc123")
    assert result.passed is False


# ── #1065: coverage vs validity in the summary ───────────────────────────────────────────────────
def _minimal_report(**overrides):
    report = {"run_status": "complete", "studies": [], "cross_study": {}}
    report.update(overrides)
    return report


def test_section1_still_flags_genuine_incompleteness():
    report = _minimal_report(
        run_status="aborted_wallclock", symbols_completed=40, symbols_planned=122)
    text = sd._section_1_result_in_one_sentence(report)
    assert "NICHT vollständig" in text


def test_section1_does_not_claim_incompleteness_when_coverage_is_complete():
    report = _minimal_report(
        run_status="aborted_invariant", symbols_completed=1, symbols_planned=1,
        invariant_checks=[{"name": "check_holding_time_cap", "passed": False,
                           "severity": "blocking", "scope": "global"}])
    text = sd._section_1_result_in_one_sentence(report)
    assert "NICHT vollständig" not in text
    assert "Vollständig gerechnet" in text
    assert "nicht entscheidungsfähig" in text


def test_section1_completed_invalid_status_also_gets_the_validity_note():
    report = _minimal_report(run_status="completed_invalid", symbols_completed=1, symbols_planned=1)
    text = sd._section_1_result_in_one_sentence(report)
    assert "NICHT vollständig" not in text
    assert "Vollständig gerechnet" in text


# ── #1065: sweep.py downgrades aborted_invariant to completed_invalid on full coverage ──────────
def test_completed_invalid_status_value_is_distinct_from_aborted_invariant():
    # Reine Konstanten-/Wortlaut-Pruefung: die beiden Status duerfen nicht identisch sein, sonst
    # kann summary_de.py sie nicht unterscheiden.
    assert "aborted_invariant" in sd._RUN_STATUS_LABELS_DE
    assert "completed_invalid" in sd._RUN_STATUS_LABELS_DE
    assert sd._RUN_STATUS_LABELS_DE["aborted_invariant"] != sd._RUN_STATUS_LABELS_DE["completed_invalid"]
