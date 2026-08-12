"""Issue #770 (P1) — Budget-Ausfuehrungsgrad ist nirgends telemetriert; die #768/#769-Luecke war
nur durch externe Log-Rekonstruktion sichtbar.

Fix: `run_optimization.compute_budget_execution` ist die EINE Quelle fuer
`n_trials_budgeted`/`n_trials_completed`/`budget_executed_fraction`/`stop_reason`/
`n_modelled_trials_completed`, konsumiert von `_emit_study_summary` (Live-Event) UND
`report._study_record` (persistierter Report). `invariants.check_budget_execution` ist der
sweep-weite FAIL-Waechter gegen eine strukturell zu niedrige Median-Ausfuehrung.
"""
import pytest

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


def test_stop_reason_unknown_incomplete_when_short_of_budget_without_plateau_or_exception():
    """Issue #1026 (Katalog #866) — ohne einen tatsaechlich gezaehlten
    ``n_trials_exception``-Stempel behauptet die Funktion keine Absturzursache mehr; sie faellt auf
    die ehrliche Restkategorie ``UNKNOWN_INCOMPLETE`` zurueck (vorher faelschlich ``EXCEPTION``)."""
    result = ro.compute_budget_execution(
        [object()] * 40, n_trials_budget=100, n_startup_trials=16, study_user_attrs={})
    assert result["stop_reason"] == "UNKNOWN_INCOMPLETE"


def test_stop_reason_exception_only_when_exceptions_were_actually_counted():
    """Issue #1026 (Katalog #866) — ``EXCEPTION`` wird nur gemeldet, wenn
    ``study_user_attrs['n_trials_exception'] > 0`` (von ``_optimize_symbol_impl`` tatsaechlich
    gezaehlte, von ``study.optimize(..., catch=...)`` gefangene Exceptions)."""
    result = ro.compute_budget_execution(
        [object()] * 40, n_trials_budget=100, n_startup_trials=16,
        study_user_attrs={"n_trials_exception": 3, "exception_types": {"OSError": 3}})
    assert result["stop_reason"] == "EXCEPTION"
    assert result["n_trials_exception"] == 3
    assert result["exception_types"] == {"OSError": 3}


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
    assert record["n_trials_total_study"] == 20
    assert record["budget_executed_fraction"] == 0.2
    # Issue #1026 (Katalog #866) — kein n_trials_exception-Stempel ⇒ UNKNOWN_INCOMPLETE, nicht mehr
    # die unbelegte EXCEPTION-Restkategorie.
    assert record["stop_reason"] == "UNKNOWN_INCOMPLETE"
    assert "n_modelled_trials_completed" in record


def test_study_record_run_id_filters_budget_execution_to_this_run():
    """Issue #1015 (Katalog #858) — _study_record reicht run_id durch: eine Study mit Trials aus
    einem VORANGEGANGENEN Lauf zaehlt deren Budget nicht in diesem Lauf mit, aber
    n_trials_total_study zeigt weiterhin die volle SQLite-Historie."""
    from automation.optimizer.report import _study_record

    class _FakeTrial:
        def __init__(self, value, run_id, **attrs):
            self.value = value
            self.user_attrs = {"run_id": run_id, **attrs}

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

    trials = (
        [_FakeTrial(-9.8, "run_old", oos_evaluated=False) for _ in range(30)]
        + [_FakeTrial(-9.8, "run_new", oos_evaluated=False) for _ in range(5)]
    )
    study = _FakeStudy(trials, {"n_trials_budget": 100, "n_startup_trials": 16})
    proposal = {"symbol": "TSLA.ETORO", "strategy": "SmaCrossoverStrategy", "status": "REJECTED_ON_HOLDOUT",
                "holdout_reject_detail": "REJECT_HOLDOUT_GATE"}
    record, _checks = _study_record(proposal, study, run_id="run_new")
    assert record["n_trials_completed"] == 5
    assert record["n_trials_total_study"] == 35
    assert record["budget_executed_fraction"] == 0.05


# ── Issue #1015 (Katalog #858, Fix Punkt 1): run_id-Filter gegen ungepurgte Studies ─────────────

class _FakeTrialForRunId:
    def __init__(self, run_id=None):
        self.user_attrs = {} if run_id is None else {"run_id": run_id}


def test_run_id_none_counts_all_trials_bit_identical_to_legacy():
    """Kein run_id uebergeben (Default) -- bit-identisches Alt-Verhalten: ALLE Trials zaehlen,
    unabhaengig von einem etwaigen run_id-Stempel."""
    trials = [_FakeTrialForRunId("run_a")] * 3 + [_FakeTrialForRunId("run_b")] * 4
    result = ro.compute_budget_execution(
        trials, n_trials_budget=100, n_startup_trials=16, study_user_attrs={})
    assert result["n_trials_completed"] == 7
    assert result["n_trials_total_study"] == 7


def test_run_id_filters_out_trials_from_other_runs():
    """Der Kern-Fix aus #1015: eine Study mit Trials aus einem VORANGEGANGENEN Lauf (run_a, 853
    "fremde" Trials im Issue-Beispiel) darf deren Budget nicht in run_b's Ausfuehrungsgrad zaehlen."""
    trials = [_FakeTrialForRunId("run_a")] * 853 + [_FakeTrialForRunId("run_b")] * 245
    result = ro.compute_budget_execution(
        trials, n_trials_budget=280, n_startup_trials=16, study_user_attrs={}, run_id="run_b")
    assert result["n_trials_completed"] == 245
    assert result["n_trials_total_study"] == 1098  # die volle (irrefuehrende) SQLite-Historie
    assert result["budget_executed_fraction"] == pytest.approx(245 / 280)


def test_run_id_with_unstamped_legacy_trials_excludes_them():
    """Trials OHNE jeden run_id-Stempel (vor #1015 erzeugt) zaehlen NICHT als "dieser Lauf" mit,
    sobald ein run_id uebergeben wird -- kein Vermischen von Alt- und Neu-Semantik."""
    trials = [_FakeTrialForRunId(None)] * 10 + [_FakeTrialForRunId("run_c")] * 5
    result = ro.compute_budget_execution(
        trials, n_trials_budget=50, n_startup_trials=16, study_user_attrs={}, run_id="run_c")
    assert result["n_trials_completed"] == 5
    assert result["n_trials_total_study"] == 15


def test_n_trials_total_study_always_present_regardless_of_run_id():
    result = ro.compute_budget_execution(
        [object()] * 12, n_trials_budget=100, n_startup_trials=16, study_user_attrs={})
    assert result["n_trials_total_study"] == 12


def test_make_symbol_objective_stamps_run_id_on_every_trial():
    """Der Erzeugungspunkt des Stempels: jeder Trial traegt user_attrs['run_id'], BEVOR
    irgendeine andere Logik ihn ablehnen/pruning kann."""
    import optuna
    from automation.optimizer import run_optimization as _ro

    def _fake_run_backtest(trial_dir, manifest_path, **_kwargs):
        raise RuntimeError("stop before any real backtest work — only the pre-stamp matters")

    objective = _ro.make_symbol_objective(
        "SmaCrossoverStrategy", "AAA.ETORO", {}, run_backtest=_fake_run_backtest,
        run_id="run_xyz789")
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=1, catch=(Exception,))
    assert len(study.trials) == 1
    assert study.trials[0].user_attrs.get("run_id") == "run_xyz789"


def test_make_symbol_objective_without_run_id_does_not_stamp_it():
    """run_id=None (Default) -- kein Stempel, bit-identisch zum Pre-#1015-Verhalten."""
    import optuna
    from automation.optimizer import run_optimization as _ro

    def _fake_run_backtest(trial_dir, manifest_path, **_kwargs):
        raise RuntimeError("stop before any real backtest work")

    objective = _ro.make_symbol_objective(
        "SmaCrossoverStrategy", "AAA.ETORO", {}, run_backtest=_fake_run_backtest)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=1, catch=(Exception,))
    assert "run_id" not in study.trials[0].user_attrs
