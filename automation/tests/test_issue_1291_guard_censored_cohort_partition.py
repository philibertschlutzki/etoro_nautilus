"""Issue #1291 (GH #1164, Katalog #1272-1297, P2) — Kohorten-Partition zaehlt guard-zensierte
Trials doppelt.

Symptom. ``check_ineligible_cohort_partition_identity`` FAILt 4/4, jeweils fuer SqueezeBreakout.
TSLA: ``n_trials=115``, ``n_evaluable=0``, ``n_eligible=0``, ``n_ineligible_measured=34``,
``n_ineligible_unmeasurable=0``, ``n_unevaluable=115`` ⇒ Summe 149, Differenz -34. NVDA: 180 vs.
318, Differenz -138.

Root-Cause. ``n_unevaluable := n_trials - n_evaluable`` umfasst bei ``n_evaluable=0`` ALLE Trials,
waehrend ``n_ineligible_measured`` (vormals aus ``is_rejection_detail_counts`` gezaehlt) dieselben
guard-zensierten Trials (SORTINO_GUARD_TRIPPED/SORTINO_INSUFFICIENT_DOWNSIDE,
``check_inference_diagnostics_concentration`` meldet 34/115 = 29,6%) ERNEUT erfasste — ein
guard-zensierter Trial ist gleichzeitig "unevaluable" UND traegt einen Rejection-Detail
(REJECT_OOS_DISCARDED_BY_IS_GATE), die Partition war per Konstruktion nicht disjunkt.

Fix.
1. ``n_ineligible_measured`` ausschliesslich ueber Trials mit ``oos_evaluated=True`` zaehlen.
2. Vierte Klasse ``n_guard_censored`` (gebrueckt aus run_optimization.py's UNBEDINGT gestempeltem
   User-Attr, dieselbe ``_censored_trial_share``-Zaehlung wie
   ``check_inference_diagnostics_concentration``).
3. Identitaet auf ``n_eligible + n_ineligible_measured + n_ineligible_unmeasurable +
   n_guard_censored + n_unevaluable_other == n_trials`` erweitert.
"""
import optuna

from automation.optimizer import invariants as inv
from automation.optimizer.report import _study_record


class _T:
    def __init__(self, oos_evaluated, oos_eligible, is_rejection_detail=None,
                state=optuna.trial.TrialState.COMPLETE):
        self.value = 1.0 if oos_evaluated else None
        self.params = {}
        self.state = state
        self.user_attrs = {
            "oos_evaluated": oos_evaluated, "oos_eligible": oos_eligible,
            "is_rejection_detail": is_rejection_detail,
        }


class _S:
    def __init__(self, trials, n_guard_censored=0):
        self.trials = trials
        self.best_value = 1.0
        self.user_attrs = {"n_guard_censored": n_guard_censored}


def _proposal():
    return {"symbol": "TSLA.ETORO", "strategy": "SqueezeBreakoutStrategy"}


# ---------------------------------------------------------------------------------------------
# invariants.check_ineligible_cohort_partition_identity — vier Klassen
# ---------------------------------------------------------------------------------------------

def test_reference_symptom_tsla_now_passes_with_n_guard_censored():
    result = inv.check_ineligible_cohort_partition_identity({
        "n_trials": 115, "n_evaluable": 0, "n_eligible": 0,
        "n_ineligible_measured": 0, "n_ineligible_unmeasurable": 0,
        "n_guard_censored": 34,
    })
    assert result.passed is True


def test_reference_symptom_nvda_now_passes_with_n_guard_censored():
    result = inv.check_ineligible_cohort_partition_identity({
        "n_trials": 318, "n_evaluable": 0, "n_eligible": 0,
        "n_ineligible_measured": 0, "n_ineligible_unmeasurable": 0,
        "n_guard_censored": 138,
    })
    assert result.passed is True


def test_missing_n_guard_censored_defaults_to_zero_backward_compatible():
    """Legacy-Report vor #1291 (kein n_guard_censored-Feld) -- bit-identisches Pre-#1291-Verhalten."""
    result = inv.check_ineligible_cohort_partition_identity({
        "n_trials": 100, "n_evaluable": 81, "n_eligible": 20,
        "n_ineligible_measured": 51, "n_ineligible_unmeasurable": 10,
    })
    assert result.passed is True


def test_genuine_divergence_still_fails_with_n_guard_censored_present():
    result = inv.check_ineligible_cohort_partition_identity({
        "n_trials": 115, "n_evaluable": 0, "n_eligible": 0,
        "n_ineligible_measured": 10, "n_ineligible_unmeasurable": 0,  # falsch: sollte 0 sein
        "n_guard_censored": 34,
    })
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual["diff"] != 0
    assert result.actual["n_guard_censored"] == 34
    assert result.actual["n_unevaluable_other"] == 81


def test_not_applicable_when_a_core_counter_is_missing_even_with_guard_censored_present():
    result = inv.check_ineligible_cohort_partition_identity({
        "n_trials": 115, "n_guard_censored": 34,
    })
    assert result.passed is True
    assert result.actual is None


# ---------------------------------------------------------------------------------------------
# report._study_record — n_ineligible_measured excludes oos_evaluated=False guard-censored trials
# ---------------------------------------------------------------------------------------------

def test_guard_censored_trials_excluded_from_n_ineligible_measured():
    """34 guard-zensierte Trials (oos_evaluated=False, REJECT_OOS_DISCARDED_BY_IS_GATE) duerfen
    NICHT mehr in n_ineligible_measured zaehlen."""
    trials = (
        [_T(False, False, "REJECT_OOS_DISCARDED_BY_IS_GATE") for _ in range(34)]
        + [_T(False, False, "REJECT_OOS_WINDOW_UNREACHABLE") for _ in range(81)]
    )
    record, _checks = _study_record(_proposal(), _S(trials, n_guard_censored=34))
    assert record["n_evaluable"] == 0
    assert record["n_ineligible_measured"] == 0
    assert record["n_guard_censored"] == 34


def test_guard_censored_bridged_from_study_user_attrs():
    trials = [_T(True, True) for _ in range(5)]
    record, _checks = _study_record(_proposal(), _S(trials, n_guard_censored=7))
    assert record["n_guard_censored"] == 7


def test_missing_n_guard_censored_user_attr_defaults_to_zero():
    trials = [_T(True, True) for _ in range(5)]
    record, _checks = _study_record(_proposal(), _S(trials, n_guard_censored=0))
    assert record["n_guard_censored"] == 0


def test_the_partition_identity_check_within_study_record_passes_for_the_reference_symptom():
    """End-to-End: dieselbe Trial-Konstellation wie das TSLA-Symptom, ueber _study_record's
    tatsaechlichen check_ineligible_cohort_partition_identity-Aufruf gepasst."""
    trials = (
        [_T(False, False, "REJECT_OOS_DISCARDED_BY_IS_GATE") for _ in range(34)]
        + [_T(False, False, "REJECT_OOS_WINDOW_UNREACHABLE") for _ in range(81)]
    )
    _record, checks = _study_record(_proposal(), _S(trials, n_guard_censored=34))
    result = next(c for c in checks if c.name == "check_ineligible_cohort_partition_identity")
    assert result.passed is True


# ---------------------------------------------------------------------------------------------
# run_optimization: n_guard_censored ist UNBEDINGT gestempelt (nicht nur bei study_guard_dominated)
# ---------------------------------------------------------------------------------------------

def test_n_guard_censored_stamped_unconditionally():
    import inspect
    from automation.optimizer import run_optimization as ro
    source = inspect.getsource(ro)
    idx = source.index('study.set_user_attr("n_guard_censored"')
    idx_dominated_check = source.index("study_guard_dominated = bool(")
    # Der Stempel-Aufruf muss VOR der study_guard_dominated-Bedingung stehen (unbedingt, nicht nur
    # im if study_guard_dominated:-Zweig).
    assert idx < idx_dominated_check
