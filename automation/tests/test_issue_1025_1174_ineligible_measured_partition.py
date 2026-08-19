"""Issue #1025/#1174 (P0) — ``n_ineligible_measured`` wird per ``max(0, …)`` geklemmt und verbirgt
einen Kohortenbruch.

Symptom (SqueezeBreakout): ``n_evaluable = 71``, ``n_eligible = 20``, ``n_ineligible_unmeasurable =
78`` ⇒ ``71 − 20 − 78 = −27``, ausgewiesen als ``0``. Korrekt wären 51 (39 REJECT_OOS_MIN_TRADES +
12 REJECT_OOS_MIN_PSR aus ``is_rejection_detail_counts``). Root-Cause: ``n_evaluable`` (Zeile 846)
schliesst PRUNED-Trials aus (#1079), ``n_ineligible_unmeasurable`` tat das VOR diesem Fix nicht —
zwei Zähler über verschiedene Grundgesamtheiten, deren Differenz ``max(0, …)`` stillschweigend auf 0
klemmte.
"""
import optuna

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
    def __init__(self, trials):
        self.trials = trials
        self.best_value = 1.0
        self.user_attrs = {}


def _proposal():
    return {"symbol": "TSLA.ETORO", "strategy": "SqueezeBreakoutStrategy"}


def test_n_ineligible_measured_is_counted_directly_not_subtracted():
    trials = (
        [_T(True, True) for _ in range(20)]
        + [_T(True, False, "REJECT_OOS_MIN_TRADES") for _ in range(39)]
        + [_T(True, False, "REJECT_OOS_MIN_PSR") for _ in range(12)]
        + [_T(True, False, "REJECT_OOS_STATISTIC_UNAVAILABLE") for _ in range(10)]
    )
    record, _checks = _study_record(_proposal(), _S(trials))
    assert record["n_evaluable"] == 81
    assert record["n_eligible"] == 20
    assert record["n_ineligible_unmeasurable"] == 10
    assert record["n_ineligible_measured"] == 51
    assert record["n_ineligible_cohort_residual"] == 0


def test_pruned_trials_with_stale_unmeasurable_attrs_are_excluded_from_both_counters():
    """Root-Cause-Reproduktion: PRUNED-Trials mit veralteten user_attrs (aus der Zeit VOR dem
    Pruning) duerfen weder n_evaluable NOCH n_ineligible_unmeasurable aufblaehen — vor diesem Fix
    zaehlte NUR n_evaluable sie korrekt heraus, n_ineligible_unmeasurable nicht (das war exakt die
    Kohorten-Divergenz, die max(0, …) verbarg)."""
    trials = (
        [_T(True, True) for _ in range(20)]
        + [_T(True, False, "REJECT_OOS_MIN_TRADES") for _ in range(39)]
        + [_T(True, False, "REJECT_OOS_MIN_PSR") for _ in range(12)]
        + [_T(True, False, "REJECT_OOS_STATISTIC_UNAVAILABLE") for _ in range(10)]
        # 78 PRUNED-Trials mit stale oos_evaluated=True/REJECT_OOS_STATISTIC_UNAVAILABLE-Attrs.
        + [_T(True, False, "REJECT_OOS_STATISTIC_UNAVAILABLE", state=optuna.trial.TrialState.PRUNED)
           for _ in range(78)]
    )
    record, _checks = _study_record(_proposal(), _S(trials))
    assert record["n_evaluable"] == 81  # PRUNED korrekt ausgeschlossen
    assert record["n_ineligible_unmeasurable"] == 10  # PRUNED jetzt AUCH hier ausgeschlossen
    assert record["n_ineligible_measured"] == 51  # unveraendert, direkt gezaehlt
    assert record["n_ineligible_cohort_residual"] == 0  # kein stiller Kohortenbruch mehr


def test_check_ineligible_cohort_partition_identity_fails_on_a_genuine_divergence():
    from automation.optimizer import invariants as inv
    result = inv.check_ineligible_cohort_partition_identity({
        "n_trials": 100, "n_evaluable": 71, "n_eligible": 20,
        "n_ineligible_measured": 51, "n_ineligible_unmeasurable": 78,
    })
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual["diff"] != 0


def test_check_ineligible_cohort_partition_identity_passes_on_a_consistent_partition():
    from automation.optimizer import invariants as inv
    result = inv.check_ineligible_cohort_partition_identity({
        "n_trials": 100, "n_evaluable": 81, "n_eligible": 20,
        "n_ineligible_measured": 51, "n_ineligible_unmeasurable": 10,
    })
    assert result.passed is True


def test_check_ineligible_cohort_partition_identity_not_applicable_when_a_counter_is_missing():
    from automation.optimizer import invariants as inv
    result = inv.check_ineligible_cohort_partition_identity({"n_trials": 100})
    assert result.passed is True
    assert result.actual is None
