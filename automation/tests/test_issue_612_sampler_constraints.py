"""Issue #612 — Feasibility gehört in den Sampler, nicht in eine 12-Einheiten-Reward-Klippe.

Die Band-Invariante ``evaluable ≥ floor > failure > unevaluable`` erzwang eine Diskontinuität von
~12 Reward-Einheiten an der Eligibility-Grenze ⇒ der TPE optimierte faktisch eine binäre Stufen-
funktion (Klippe überschritten ja/nein). Fix: die normierten Gate-Verletzungen gehen als
``constraints`` in den Sampler; Optuna behandelt Feasibility nativ (feasible ≻ infeasible), der
Reward ist dann eine einzige stetige Grösse.
"""
import optuna

from automation.optimizer.run_optimization import (
    _compute_oos_constraints, _oos_constraints_func)


class _M:
    def __init__(self, eligible, deltas=None):
        self.oos_eligible = eligible
        self.oos_gate_deltas = deltas or {}


# ── Constraint-Vektor: konstante Länge, ≤ 0 = feasible ───────────────────────────────────────────
def test_feasible_trial_has_zero_constraint():
    assert _compute_oos_constraints(_M(True, {"oos_min_trades": -5.0})) == (0.0,)


def test_infeasible_sums_positive_violations():
    # delta = actual − threshold; Verletzung = max(0, −delta). {−2 ⇒ 2, +1 ⇒ 0, −0.5 ⇒ 0.5} = 2.5.
    c = _compute_oos_constraints(_M(False, {"a": -2.0, "b": 1.0, "c": -0.5}))
    assert c == (2.5,)
    assert c[0] > 0.0  # infeasible


def test_infeasible_without_deltas_gets_constant_violation():
    # nicht-evaluiert / Micro-Sizing ⇒ Deltas erfassen es nicht ⇒ konstante Verletzung 1.0 (> 0).
    assert _compute_oos_constraints(_M(False, {})) == (1.0,)


def test_constraint_vector_constant_length():
    # Optuna verlangt einen fixen Constraint-Vektor über alle Trials.
    assert len(_compute_oos_constraints(_M(True))) == len(_compute_oos_constraints(_M(False, {"x": -1.0})))


# ── Akzeptanz: infeasibel überholt NIE feasibel im Optuna-Ranking (study.best_trial) ─────────────
def test_infeasible_never_overtakes_feasible_in_best_trial():
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=1, constraints_func=_oos_constraints_func))
    # feasibler Trial mit NIEDRIGEM Wert (0.9) vs. infeasibler mit HOHEM Wert (5.0).
    for val, cv in [(0.9, (0.0,)), (5.0, (3.0,)), (0.7, (0.0,)), (9.9, (1.0,))]:
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_constraint_violations", cv)
        study.tell(t, val)
    # best_trial ist der beste FEASIBLE (0.9) — der infeasible 9.9/5.0 überholt ihn NICHT.
    assert study.best_trial.value == 0.9
    assert study.best_trial.user_attrs["oos_constraint_violations"] == (0.0,)


def test_constraints_func_reads_stamp_default_feasible():
    class _T:
        user_attrs = {}
    assert _oos_constraints_func(_T()) == (0.0,)   # fehlender Stempel ⇒ feasible
