"""Issue #1079 (Katalog #866-2, Pitfall #377) — der blockierende Selektions-Check rechnet gegen
geprunte Trials.

``report._study_record``s ``n_evaluable`` zählte bislang JEDEN Trial mit ``oos_evaluated=True`` im
``user_attrs``-Dict, unabhängig vom tatsächlichen Optuna-``TrialState`` — ein Trial, der SPÄTER
(nach dem Stempeln von ``oos_evaluated``) via ``inference_failure_policy='prune'`` (#864) auf
``TrialState.PRUNED`` gesetzt wurde, kann per Konstruktion keine Selektionsstatistik tragen, wurde
aber trotzdem mitgezählt (Beweis B-13 im #866-Katalog: Squeeze, 130 "evaluable" Trials, obwohl nur
56 informativ waren — 74 der 130 waren tatsächlich PRUNED, ``130 + n_trials_pruned(78) = 208 >
n_trials(180)``). ``check_selection_statistic_availability`` FAILte dadurch blocking auf einer
strukturell falschen Grundgesamtheit.

Fix: ``n_evaluable`` schliesst PRUNED-state Trials aus; ``check_denominator_coherence`` erhält eine
zweite, unabhängige Identität (``n_evaluable + n_trials_pruned + n_trials_unevaluable +
n_trials_failed == n_trials_total``) als Regressionswächter.
"""
import optuna

from automation.optimizer import invariants as inv
from automation.optimizer.report import _study_record


class _T:
    def __init__(self, oos_evaluated, oos_psr, state=optuna.trial.TrialState.COMPLETE,
                oos_eligible=False):
        self.value = 1.0 if oos_evaluated else None
        self.params = {}
        self.state = state
        self.user_attrs = {"oos_evaluated": oos_evaluated, "oos_psr": oos_psr,
                           "oos_eligible": oos_eligible}


class _S:
    def __init__(self, trials):
        self.trials = trials
        self.best_value = 1.0
        self.user_attrs = {}


# ── report._study_record: n_evaluable excludes PRUNED-state trials ─────────────────────────────
def test_n_evaluable_excludes_pruned_trials_with_stale_oos_evaluated_true():
    # 56 echte informative Trials + 74 PRUNED-Trials, deren user_attrs noch oos_evaluated=True
    # aus der Zeit VOR dem Pruning tragen (Beweis B-13: Squeeze 56 informativ + 74 PRUNED).
    trials = (
        [_T(True, 0.5, state=optuna.trial.TrialState.COMPLETE) for _ in range(56)]
        + [_T(True, None, state=optuna.trial.TrialState.PRUNED) for _ in range(74)]
    )
    proposal = {"symbol": "TSLA.ETORO", "strategy": "SqueezeBreakoutStrategy"}
    record, _checks = _study_record(proposal, _S(trials))
    assert record["n_evaluable"] == 56


def test_n_evaluable_unaffected_when_no_trials_are_pruned():
    trials = [_T(True, 0.5, state=optuna.trial.TrialState.COMPLETE) for _ in range(10)]
    proposal = {"symbol": "TSLA.ETORO", "strategy": "SqueezeBreakoutStrategy"}
    record, _checks = _study_record(proposal, _S(trials))
    assert record["n_evaluable"] == 10


# ── check_selection_statistic_availability: honest availability on the corrected n_evaluable ───
def test_selection_statistic_availability_passes_on_corrected_n_evaluable():
    # Vor #1079: 56/130 = 0.4308 < 0.80 ⇒ FAIL. Nach #1079: n_evaluable=56 ⇒ 56/56 = 1.0 ⇒ PASS.
    records = [{
        "strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO",
        "n_evaluable": 56, "n_selection_statistic_available": 56,
    }]
    result = inv.check_selection_statistic_availability(records)
    assert result.passed is True


def test_selection_statistic_availability_still_fails_on_a_genuine_shortfall():
    records = [{
        "strategy": "S", "symbol": "X", "n_evaluable": 100, "n_selection_statistic_available": 10,
    }]
    result = inv.check_selection_statistic_availability(records)
    assert result.passed is False


# ── check_denominator_coherence: second identity ─────────────────────────────────────────────────
def test_denominator_coherence_second_identity_fails_on_the_b13_reference_case():
    counts = {
        "n_trials_total": 180, "n_trials_informative": 56, "n_trials_pruned": 78,
        "n_trials_unevaluable": 46, "n_trials_failed": 0,
        # Der (VORHER unkorrigierte) n_evaluable-Wert aus B-13 — beweist, dass die zweite Identität
        # genau diesen Fall FAILt, waehrend die erste (n_trials_informative-basierte) Identität hier
        # bereits PASST (56+78+46+0=180).
        "n_evaluable": 130,
    }
    result = inv.check_denominator_coherence(counts)
    assert result.passed is False
    assert "1079" in result.detail


def test_denominator_coherence_second_identity_passes_after_the_source_fix():
    counts = {
        "n_trials_total": 180, "n_trials_informative": 56, "n_trials_pruned": 78,
        "n_trials_unevaluable": 46, "n_trials_failed": 0,
        "n_evaluable": 56,
    }
    result = inv.check_denominator_coherence(counts)
    assert result.passed is True


def test_denominator_coherence_ignores_n_evaluable_when_absent():
    """Rueckwaertskompatibilitaet: Pre-#1079-Reports ohne n_evaluable pruefen nur die erste
    Identitaet (bit-identisch zum Pre-#1079-Verhalten)."""
    counts = {"n_trials_total": 160, "n_trials_informative": 135, "n_trials_pruned": 25,
              "n_trials_unevaluable": 0, "n_trials_failed": 0}
    result = inv.check_denominator_coherence(counts)
    assert result.passed is True
