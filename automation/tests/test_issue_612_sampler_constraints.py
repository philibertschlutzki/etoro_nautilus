"""Issue #612 — Feasibility gehört in den Sampler, nicht in eine 12-Einheiten-Reward-Klippe.

Die Band-Invariante ``evaluable ≥ floor > failure > unevaluable`` erzwang eine Diskontinuität von
~12 Reward-Einheiten an der Eligibility-Grenze ⇒ der TPE optimierte faktisch eine binäre Stufen-
funktion (Klippe überschritten ja/nein). Fix: die normierten Gate-Verletzungen gehen als
``constraints`` in den Sampler; Optuna behandelt Feasibility nativ (feasible ≻ infeasible), der
Reward ist dann eine einzige stetige Grösse.

Issue #635 — die Verletzungen werden seither dimensionslos NORMIERT (nicht mehr rohe, un-normierte
Deltas summiert) — siehe test_issue_635_constraint_normalization.py für die dedizierten #635-Tests.
"""
import optuna

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.run_optimization import (
    _compute_oos_constraints, _oos_constraints_func)


class _M:
    """Legacy-Test-Double (nur oos_eligible/oos_evaluated) — für die reinen Feasibility-Routing-Tests,
    die keine echten Metrik-Werte brauchen."""
    def __init__(self, eligible, evaluated=True):
        self.oos_eligible = eligible
        self.oos_evaluated = evaluated


TCFG = {
    "oos_min_trades": 20, "oos_min_total_return": 0.005, "oos_min_expectancy": 0.001,
    "oos_min_win_rate": 0.15, "max_drawdown": 0.3, "return_penalty_scale": 0.1,
    "expectancy_penalty_scale": 0.002, "eligible_requires_any": [],
}


def _ineligible_metrics(**kw):
    base = dict(
        oos_evaluated=True, oos_eligible=False, is_sortino_median=0.0,
        oos_sortino=0.3, oos_max_drawdown=0.05, oos_total_trades=20, win_count=0,
        fully_eligible_pairs=0, is_total_trades=0, oos_total_return=0.003,
        oos_expectancy=0.0005, oos_win_rate=0.10, oos_profit_factor=0.9,
    )
    base.update(kw)
    return TournamentMetrics(**base)


# ── Constraint-Vektor: konstante Länge, ≤ 0 = feasible ───────────────────────────────────────────
def test_feasible_trial_has_zero_constraint():
    assert _compute_oos_constraints(_M(True)) == (0.0,)


def test_infeasible_without_tournament_cfg_gets_constant_violation():
    # Fehlt tournament_cfg (z. B. globaler make_objective-Pfad ohne Nachladen) ⇒ konstante
    # Verletzung 1.0 (> 0) — kein Crash, kein stiller Feasible-Fehlschluss.
    assert _compute_oos_constraints(_M(False), None) == (1.0,)


def test_infeasible_unevaluated_gets_constant_violation():
    # nicht-evaluiert (Micro-Sizing, kein OOS-Trade) ⇒ Distanzen erfassen es nicht ⇒ konstante
    # Verletzung 1.0 (> 0).
    assert _compute_oos_constraints(_M(False, evaluated=False), TCFG) == (1.0,)


def test_infeasible_uses_normalized_distances():
    m = _ineligible_metrics()  # Return 0.003 < Gate 0.005 ⇒ aktive Distanz
    c = _compute_oos_constraints(m, TCFG)
    assert c[0] > 0.0


def test_constraint_vector_constant_length():
    # Optuna verlangt einen fixen Constraint-Vektor über alle Trials.
    assert len(_compute_oos_constraints(_M(True))) == len(
        _compute_oos_constraints(_ineligible_metrics(), TCFG))


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
