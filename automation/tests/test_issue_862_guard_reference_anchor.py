"""Issue #862 (P0, Katalog #862-#865, GitHub-Issue #759) — `sortino_numeric_guard_min_periods` =
1600 ist an die Pre-#823-Definition von `n_periods` verankert.

Root-Cause: `sortino_numeric_guard_min_periods` (1600) wurde gegen die VOLLE 24/7-Bar-Achse
kalibriert (≈4315 Perioden). #823 hat `n_periods` auf die INFORMATIVE Teilmenge umdefiniert (Bars
mit tatsächlicher Rendite ≠ 0) — der beobachtete Median in einem Referenzlauf fiel auf 319 (Faktor
13,5 kleiner). Der Referenzwert ist seither systematisch zu gross, der effektive Guard um
√(1600/319) ≈ 2,24 zu streng, für JEDEN Trial.

Fix:
1. `guard_reference_value`/`guard_reference_source` als Telemetrie auf jedem
   `SORTINO_GUARD_TRIPPED`-Diagnose-Eintrag (`backtest_runner._effective_sortino_numeric_guard`).
2. `invariants.check_guard_reference_coherence` — Report-Invariante, die FAILt, wenn der
   konfigurierte Referenzwert um mehr als Faktor 2 vom über den Lauf beobachteten
   Median(n_periods) abweicht.
3. `sortino_numeric_guard_min_periods` in `_required_keys.json` trägt jetzt
   `depends_on: ["n_periods_definition"]`.
4. `sortino_numeric_guard_reference` (`absolute`/`family_median`) als deklarierter, aber (Scope-
   Entscheidung, siehe Docstring von `_effective_sortino_numeric_guard`) nicht aktivierter Modus —
   `absolute` bleibt bit-identisch zum Pre-#862-Verhalten.
"""
import json

import pytest

from automation.backtest_runner import _calculate_stats, _effective_sortino_numeric_guard
from automation.optimizer import invariants as inv
from automation.optimizer.report import _study_record


# ── _effective_sortino_numeric_guard: Telemetrie-Rückgabe ───────────────────────────────────────
def test_effective_guard_returns_reference_value_and_source_absolute_mode(monkeypatch, tmp_path):
    import automation.backtest_runner as br
    (tmp_path / "tournament.json").write_text(
        json.dumps({"sortino_numeric_guard_min_periods": 500}), encoding="utf-8")
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cache", None)
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cached", False)
    monkeypatch.setattr(br, "_sortino_numeric_guard_reference_mode_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)

    guard, ref_value, ref_source = _effective_sortino_numeric_guard(25.0, 137)
    assert ref_value == 500
    assert ref_source == "absolute"
    assert guard < 25.0


def test_effective_guard_reference_absent_when_min_periods_unset(monkeypatch, tmp_path):
    import automation.backtest_runner as br
    (tmp_path / "tournament.json").write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cache", None)
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cached", False)
    monkeypatch.setattr(br, "_sortino_numeric_guard_reference_mode_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)

    guard, ref_value, ref_source = _effective_sortino_numeric_guard(25.0, 137)
    assert guard == 25.0
    assert ref_value is None


def test_unknown_reference_mode_is_fail_loud(monkeypatch, tmp_path):
    import automation.backtest_runner as br
    (tmp_path / "tournament.json").write_text(
        json.dumps({"sortino_numeric_guard_reference": "bogus"}), encoding="utf-8")
    monkeypatch.setattr(br, "_sortino_numeric_guard_reference_mode_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match="bogus"):
        br._read_sortino_numeric_guard_reference_mode()


def test_family_median_mode_without_provided_value_is_honest_not_silent(monkeypatch, tmp_path):
    """Issue #901 Fix 1 (Root-Cause) — VOR dem Fix fiel 'family_median' ohne bereitgestelltes
    family_median_n_periods still auf den absoluten Anker zurück UND stempelte
    guard_reference_source='absolute' (eine Config, die family_median verlangt, log das Gegenteil).
    Jetzt: (None, None, 'family_median_unavailable') — der Aufrufer muss den Trial prunen."""
    import automation.backtest_runner as br
    (tmp_path / "tournament.json").write_text(
        json.dumps({"sortino_numeric_guard_reference": "family_median",
                    "sortino_numeric_guard_min_periods": 1600}), encoding="utf-8")
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cache", None)
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cached", False)
    monkeypatch.setattr(br, "_sortino_numeric_guard_reference_mode_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)

    guard, ref_value, ref_source = _effective_sortino_numeric_guard(25.0, 137)
    assert guard is None
    assert ref_value is None
    assert ref_source == "family_median_unavailable"
    assert ref_source != "absolute"  # die #901-Root-Cause-Behauptung darf nie wieder auftauchen


def test_family_median_mode_with_provided_value_computes_correctly(monkeypatch, tmp_path):
    """Der (kuenftige) echte Injektionspunkt bleibt funktionsfaehig, sobald ein Aufrufer
    family_median_n_periods tatsaechlich befuellt."""
    import automation.backtest_runner as br
    (tmp_path / "tournament.json").write_text(
        json.dumps({"sortino_numeric_guard_reference": "family_median"}), encoding="utf-8")
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cache", None)
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cached", False)
    monkeypatch.setattr(br, "_sortino_numeric_guard_reference_mode_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)

    guard, ref_value, ref_source = _effective_sortino_numeric_guard(
        25.0, 126, family_median_n_periods=130.0)
    assert ref_value == 130.0
    assert ref_source == "family_median"
    import math
    assert guard == pytest.approx(25.0 * math.sqrt(min(1.0, 126 / 130.0)), abs=1e-9)


def test_absolute_mode_is_bit_identical_default(monkeypatch, tmp_path):
    import automation.backtest_runner as br
    (tmp_path / "tournament.json").write_text(
        json.dumps({"sortino_numeric_guard_min_periods": 1600}), encoding="utf-8")
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cache", None)
    monkeypatch.setattr(br, "_sortino_numeric_guard_min_periods_cached", False)
    monkeypatch.setattr(br, "_sortino_numeric_guard_reference_mode_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)

    guard, ref_value, ref_source = _effective_sortino_numeric_guard(25.0, 319)
    assert ref_source == "absolute"
    assert ref_value == 1600
    import math
    assert guard == pytest.approx(25.0 * math.sqrt(319 / 1600), abs=1e-9)


# ── invariants.check_guard_reference_coherence ───────────────────────────────────────────────────
def test_coherence_check_fails_on_the_reference_run_ratio():
    """Akzeptanzkriterium #862 — Referenz 1600 vs. beobachteter Median 319 (Faktor 5,0) FAILt."""
    result = inv.check_guard_reference_coherence(1600, [319.0])
    assert result.passed is False
    assert "5" in result.detail or "5.0" in result.detail


def test_coherence_check_passes_when_within_factor_two():
    result = inv.check_guard_reference_coherence(1600, [900.0])
    assert result.passed is True


def test_coherence_check_not_applicable_without_configured_reference():
    result = inv.check_guard_reference_coherence(None, [319.0])
    assert result.passed is True
    assert result.actual is None


def test_coherence_check_not_applicable_without_observed_data():
    result = inv.check_guard_reference_coherence(1600, [])
    assert result.passed is True


def test_coherence_check_uses_the_median_across_studies():
    # Median([100, 300, 1000]) = 300 -> ratio 1600/300 ≈ 5.33 -> FAIL
    result = inv.check_guard_reference_coherence(1600, [100.0, 300.0, 1000.0])
    assert result.passed is False


def test_coherence_check_passes_under_family_median_reference_without_absolute_evidence():
    """Issue #901 — sortino_numeric_guard_reference='family_median' PASSt, solange KEIN Event
    guard_reference_source=='absolute' meldet (der absolute Ratio-Vergleich unten ist unter diesem
    Modus nicht die massgebliche Frage mehr — anders als #882 Fix Punkt 3 urspruenglich annahm)."""
    result = inv.check_guard_reference_coherence(1600, [319.0], reference_mode="family_median")
    assert result.passed is True
    assert result.severity == "blocking"


def test_coherence_check_fails_when_family_median_mode_observes_absolute_source():
    """Issue #901 (siebte Wiederkehr Pitfall #267) — Root-Cause-Regressionswächter: die #882-
    Version dieses Checks liess reference_mode=='family_median' UNBEDINGT passieren (Pitfall #288,
    ein Wächter, der die Konfiguration nur mit sich selbst vergleicht). Reproduziert den
    archivierten `ea4c409d`-Lauf: Config verlangt 'family_median', aber alle 5
    SORTINO_GUARD_TRIPPED-Events melden guard_reference_source=='absolute' (Anker 1600 gegen
    Median n_periods ≈ 130, Faktor 12,3) — ein Widerspruch, den der Wächter jetzt fängt."""
    result = inv.check_guard_reference_coherence(
        1600, [130.0], reference_mode="family_median",
        observed_guard_reference_sources=["absolute"] * 5)
    assert result.passed is False
    assert result.severity == "blocking"
    assert "absolute" in result.actual


def test_coherence_check_passes_under_family_median_with_honest_unavailable_source():
    """Der ehrliche dritte Zustand ('family_median_unavailable', Issue #901 Fix 1) ist KEIN
    Widerspruch (er behauptet nicht faelschlich, den absoluten Anker verwendet zu haben) und darf
    den Wächter nicht FAILen lassen."""
    result = inv.check_guard_reference_coherence(
        1600, [130.0], reference_mode="family_median",
        observed_guard_reference_sources=["family_median_unavailable"] * 5)
    assert result.passed is True


def test_coherence_check_severity_is_blocking():
    """Issue #882 Fix Punkt 4 — vorher severity='high' ohne Entscheidungspflicht (Pitfall #280)."""
    result = inv.check_guard_reference_coherence(1600, [319.0])
    assert result.severity == "blocking"


# ── report._study_record: oos_n_periods_median ───────────────────────────────────────────────────
class _T:
    def __init__(self, n_periods, evaluated=True):
        self.value = 1.0
        self.user_attrs = {"oos_evaluated": evaluated, "oos_eligible": True, "oos_n_periods": n_periods}


class _S:
    def __init__(self, trials):
        self.trials = trials
        self.best_value = 1.0
        self.user_attrs = {}


def test_study_record_computes_n_periods_median():
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TestStrategy"}
    study = _S([_T(100), _T(300), _T(1000)])
    record, _checks = _study_record(proposal, study, {})
    assert record["oos_n_periods_median"] == 300


def test_study_record_n_periods_median_ignores_unevaluated_trials():
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TestStrategy"}
    study = _S([_T(100), _T(9999, evaluated=False)])
    record, _checks = _study_record(proposal, study, {})
    assert record["oos_n_periods_median"] == 100


def test_study_record_n_periods_median_is_none_without_data():
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TestStrategy"}
    study = _S([])
    record, _checks = _study_record(proposal, study, {})
    assert record["oos_n_periods_median"] is None
