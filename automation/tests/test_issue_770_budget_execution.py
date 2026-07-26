"""Issue #770 (P1) — Budget-Ausfuehrungsgrad ist nirgends telemetriert; die #768/#769-Luecke war
nur durch externe Log-Rekonstruktion sichtbar.

Fix: `run_optimization.compute_budget_execution` ist die EINE Quelle fuer
`n_trials_budgeted`/`n_trials_completed`/`budget_executed_fraction`/`stop_reason`/
`n_modelled_trials_completed`, konsumiert von `_emit_study_summary` (Live-Event) UND
`report._study_record` (persistierter Report). `invariants.check_budget_execution` ist der
sweep-weite FAIL-Waechter gegen eine strukturell zu niedrige Median-Ausfuehrung.
"""
from automation.optimizer import run_optimization as ro
from automation.optimizer import invariants as inv


# ── compute_budget_execution: reine Funktion ─────────────────────────────────────────────────────
def test_fraction_in_zero_one_and_stop_reason_never_empty():
    result = ro.compute_budget_execution(
        [object()] * 50, n_trials_budget=100, n_startup_trials=16, study_user_attrs={})
    assert 0.0 <= result["budget_executed_fraction"] <= 1.0
    assert result["stop_reason"] in ro._STOP_REASONS
    assert result["n_trials_budgeted"] == 100
    assert result["n_trials_completed"] == 50
    assert result["budget_executed_fraction"] == 0.5


def test_missing_budget_yields_none_fraction_not_a_silent_default():
    result = ro.compute_budget_execution([object()] * 10, n_trials_budget=None,
                                         n_startup_trials=16, study_user_attrs={})
    assert result["budget_executed_fraction"] is None
    assert result["stop_reason"] == "BUDGET_EXHAUSTED"


def test_stop_reason_structural_all_unevaluable():
    result = ro.compute_budget_execution(
        [object()] * 20, n_trials_budget=100, n_startup_trials=16,
        study_user_attrs={"floor_plateau_warned": True})
    assert result["stop_reason"] == "STRUCTURAL_ALL_UNEVALUABLE"


def test_stop_reason_structural_zero_eligible():
    result = ro.compute_budget_execution(
        [object()] * 64, n_trials_budget=100, n_startup_trials=16,
        study_user_attrs={"zero_eligible_plateau_warned": True})
    assert result["stop_reason"] == "STRUCTURAL_ZERO_ELIGIBLE"


def test_stop_reason_budget_exhausted_when_full_budget_completed():
    result = ro.compute_budget_execution(
        [object()] * 100, n_trials_budget=100, n_startup_trials=16, study_user_attrs={})
    assert result["stop_reason"] == "BUDGET_EXHAUSTED"
    assert result["budget_executed_fraction"] == 1.0


def test_stop_reason_exception_when_short_of_budget_without_plateau_flag():
    result = ro.compute_budget_execution(
        [object()] * 40, n_trials_budget=100, n_startup_trials=16, study_user_attrs={})
    assert result["stop_reason"] == "EXCEPTION"


# ── invariants.check_budget_execution: sweep-weite Median-Schwelle ──────────────────────────────
def test_median_below_threshold_fails():
    records = [{"budget_executed_fraction": f} for f in (0.13, 0.442, 0.45, 0.5)]
    result = inv.check_budget_execution(records, min_median=0.5)
    assert result.passed is False
    assert result.actual == 0.446


def test_median_above_threshold_passes():
    records = [{"budget_executed_fraction": f} for f in (0.7, 0.78, 0.9, 1.0)]
    result = inv.check_budget_execution(records, min_median=0.5)
    assert result.passed is True


def test_empty_cohort_is_not_applicable_and_passes():
    result = inv.check_budget_execution([], min_median=0.5)
    assert result.passed is True
    assert result.actual is None


def test_records_without_the_field_are_filtered_out():
    records = [{"budget_executed_fraction": None}, {}, {"budget_executed_fraction": 0.9}]
    result = inv.check_budget_execution(records, min_median=0.5)
    assert result.actual == 0.9
    assert result.passed is True


# ── report._study_record: Felder sind praesent und teilen die compute_budget_execution-Quelle ───
def test_study_record_carries_budget_execution_fields():
    from automation.optimizer.report import _study_record

    class _FakeTrial:
        def __init__(self, value, **attrs):
            self.value = value
            self.user_attrs = attrs

    class _FakeStudy:
        def __init__(self, trials, attrs):
            self.trials = trials
            self._attrs = attrs

        @property
        def user_attrs(self):
            return dict(self._attrs)

        @property
        def best_value(self):
            return max((t.value for t in self.trials), default=None)

    trials = [_FakeTrial(-9.8, oos_evaluated=False) for _ in range(20)]
    study = _FakeStudy(trials, {"n_trials_budget": 100, "n_startup_trials": 16})
    proposal = {"symbol": "TSLA.ETORO", "strategy": "SmaCrossoverStrategy", "status": "REJECTED_ON_HOLDOUT",
                "holdout_reject_detail": "REJECT_HOLDOUT_GATE"}
    record, _checks = _study_record(proposal, study)
    assert record["n_trials_budgeted"] == 100
    assert record["n_trials_completed"] == 20
    assert record["budget_executed_fraction"] == 0.2
    assert record["stop_reason"] == "EXCEPTION"
    assert "n_modelled_trials_completed" in record
