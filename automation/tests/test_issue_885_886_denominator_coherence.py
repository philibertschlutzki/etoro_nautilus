"""Issue #885/#886 (P1) — Geprunte Trials in Nennern, Konzentration statt Abwesenheit.

#885: ``STUDY_GUARD_DOMINATED`` (#823) zählte ``evaluable`` (``oos_evaluated=True``) als Nenner —
ein unter ``inference_failure_policy='prune'`` (#864) geprunter Trial behält sein
``oos_evaluated=True``-User-Attr, sein ``TrialState`` ist aber ``PRUNED``: er liefert keine
informative Beobachtung mehr, wurde aber trotzdem mitgezählt (FlashCrash/VLO: 25/160 statt der
korrekten 25/135 = 18,5 %). ``n_trials_informative`` (``TrialState.COMPLETE`` UND
``oos_evaluated=True``) ist jetzt der EINE Nenner; ``check_denominator_coherence`` verifiziert,
dass die vier Trial-Kategorien die Gesamtzahl disjunkt/vollständig zerlegen.

#886: ``check_inference_diagnostics_absent`` meldete ``SORTINO_GUARD_TRIPPED``/
``SORTINO_INSUFFICIENT_DOWNSIDE`` als Defekt, obwohl beide seit #863/#864 reguläre dritte Ausgänge
sind (Pitfall #276) — ``check_inference_diagnostics_concentration`` ersetzt die blosse Anwesenheit
durch dieselbe Konzentrations-Schwelle wie ``STUDY_GUARD_DOMINATED``.
"""
from automation.optimizer import invariants as inv


# ── check_denominator_coherence ──────────────────────────────────────────────────────────────────
def test_denominator_coherence_passes_when_categories_sum_to_total():
    counts = {"n_trials_total": 160, "n_trials_informative": 135, "n_trials_pruned": 25,
              "n_trials_unevaluable": 0, "n_trials_failed": 0}
    result = inv.check_denominator_coherence(counts)
    assert result.passed is True


def test_denominator_coherence_fails_on_a_gap():
    counts = {"n_trials_total": 160, "n_trials_informative": 100, "n_trials_pruned": 25,
              "n_trials_unevaluable": 0, "n_trials_failed": 0}
    result = inv.check_denominator_coherence(counts)
    assert result.passed is False
    assert result.actual["n_trials_total"] == 160


def test_denominator_coherence_not_applicable_pre_885_report():
    result = inv.check_denominator_coherence({})
    assert result.passed is True
    assert result.actual is None


# ── check_inference_diagnostics_concentration ────────────────────────────────────────────────────
def _trials(n_regular_outcome, n_clean):
    out = [{"inference_diagnostics": [{"code": "SORTINO_GUARD_TRIPPED"}]}] * n_regular_outcome
    out += [{"inference_diagnostics": []}] * n_clean
    return out


def test_concentration_check_passes_below_threshold():
    trials = _trials(1, 9)  # 1/10 = 10% == threshold, nicht > threshold
    result = inv.check_inference_diagnostics_concentration(
        trials, n_trials_informative=10, guard_dominance_threshold=0.10)
    assert result.passed is True


def test_concentration_check_fails_above_threshold():
    trials = _trials(5, 5)  # 50% > 10%
    result = inv.check_inference_diagnostics_concentration(
        trials, n_trials_informative=10, guard_dominance_threshold=0.10)
    assert result.passed is False
    assert result.actual == 0.5


def test_concentration_check_matches_the_flashcrash_vlo_example():
    """Issue #885/#886 — der konkrete Referenzbeleg: 25 SORTINO_GUARD_TRIPPED-Trials gegen 135
    informative (nicht 160 rohe) Trials ergibt 18,5 %, nicht 15,6 %."""
    trials = _trials(25, 110)  # 135 total, 25 betroffen
    result = inv.check_inference_diagnostics_concentration(
        trials, n_trials_informative=135, guard_dominance_threshold=0.10)
    assert result.passed is False
    assert round(result.actual, 3) == round(25 / 135, 3)


def test_concentration_check_not_applicable_without_informative_count():
    result = inv.check_inference_diagnostics_concentration(
        _trials(5, 5), n_trials_informative=None)
    assert result.passed is True


def test_concentration_check_counts_each_trial_once_even_with_both_codes():
    trials = [{"inference_diagnostics": [
        {"code": "SORTINO_GUARD_TRIPPED"}, {"code": "SORTINO_INSUFFICIENT_DOWNSIDE"}]}]
    result = inv.check_inference_diagnostics_concentration(
        trials, n_trials_informative=1, guard_dominance_threshold=0.10)
    assert result.actual == 1.0


# ── check_inference_diagnostics_absent no longer flags the regular third outcomes ───────────────
def test_absent_check_ignores_regular_third_outcome_codes():
    trials = [{"inference_diagnostics": [{"code": "SORTINO_GUARD_TRIPPED"}]},
             {"inference_diagnostics": [{"code": "SORTINO_INSUFFICIENT_DOWNSIDE"}]}]
    result = inv.check_inference_diagnostics_absent(trials)
    assert result.passed is True


def test_absent_check_still_flags_equity_nonpositive():
    trials = [{"inference_diagnostics": [{"code": "EQUITY_NONPOSITIVE"}]}]
    result = inv.check_inference_diagnostics_absent(trials)
    assert result.passed is False
