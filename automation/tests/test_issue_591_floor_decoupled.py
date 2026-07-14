"""Issue #591 — Reward-Floor an den Sortino-Clip gekoppelt ⇒ Plateau mit Gradient 0 im eligiblen Ast.

Der eligible Reward-Floor war ``−sortino_clip_abs`` (−5.0) und lag ÜBER dem, was der eligible Ast bei
realistischen Sortinos natürlich produziert ⇒ 6/8 SmaCrossover-Trials klemmten exakt auf −5.0 (TPE-
Plateau). Fix: ein EIGENER, entkoppelter ``evaluable_reward_floor`` (−12.0), tief genug, dass er im
eligiblen Ast praktisch nie bindet; seine Aufgabe ist die Ordnungsinvariante, nicht die Kompression.
Zusätzlich bindet ``penalty_relative_cap`` an eine POSITIVE Skalenkonstante statt an ``|base|``.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.reward import compute_reward, _evaluable_floor
from automation.optimizer.parsing import TournamentMetrics


def _weights(sortino_clip_abs, evaluable_reward_floor):
    return {
        "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
        "evaluable_floor_epsilon": 0.001, "evaluable_reward_floor": evaluable_reward_floor,
        "sortino_clip_abs": sortino_clip_abs, "sortino_soft_scale": 5.0,
        "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
        "failure_reward_mode": "legacy_mean", "constraint_distance_penalty_weight": 0.25,
        "w_ret": 0.0,
    }


_TCFG = {
    "oos_min_trades": 20, "oos_min_total_return": 0.05, "oos_min_expectancy": 0.01,
    "oos_min_win_rate": 0.4, "max_drawdown": 0.3, "return_penalty_scale": 0.1,
}


def _failed_metric():
    # evaluiert, aber NICHT eligible (schlechter Return) ⇒ Failure-Pfad.
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=False, is_sortino_median=0.0,
        oos_sortino=-2.0, oos_max_drawdown=0.02, oos_total_trades=5, win_count=0,
        fully_eligible_pairs=0, is_total_trades=10, oos_total_return=-0.2,
        oos_expectancy=-0.01, oos_win_rate=0.1,
    )


def test_floor_from_evaluable_reward_floor_independent_of_clip():
    """_evaluable_floor liest evaluable_reward_floor UNABHÄNGIG von sortino_clip_abs."""
    for clip in (3.0, 5.0, 20.0):
        for floor in (-8.0, -12.0, -30.0):
            assert _evaluable_floor(_weights(clip, floor)) == floor
    # Fehlt der Key ⇒ Legacy-Anker (−sortino_clip_abs).
    assert _evaluable_floor({"sortino_clip_abs": 5.0}) == -5.0


def test_order_invariant_failure_below_evaluable_for_all_combos():
    """Für ALLE (band-gültigen) Kombinationen (sortino_clip_abs, evaluable_reward_floor) gilt strikt:
    max(failure) < min(evaluable) == evaluable_reward_floor. Band-gültig heisst
    evaluable_reward_floor > unevaluable_ceiling (= penalty_unevaluable_oos + span = −19.75); ein
    evaluable-Floor UNTER dem Unevaluable-Band ist per Definition keine gültige Ordnung."""
    for clip in (3.0, 5.0, 20.0):
        for floor in (-8.0, -12.0, -18.0):   # alle > −19.75 (Unevaluable-Ceiling)
            w = _weights(clip, floor)
            failure = compute_reward(_failed_metric(), universe_size=1, weights=w,
                                     risk_dd_cap=0.3, tournament_cfg=_TCFG)
            assert failure < floor, f"failure {failure} muss < evaluable_floor {floor} sein (clip={clip})"


def test_relative_cap_same_absolute_height_for_negative_and_positive_base():
    """penalty_relative_cap produziert bei base = −5 und base = +5 dieselbe absolute Cap-Höhe."""
    ss = 5.0
    cap = 0.5
    import math
    sortino_for_base5 = ss * math.sinh(1.0)   # base = ss*asinh(sortino/ss) = 5
    w = {
        "evaluable_reward_floor": -100.0, "evaluable_floor_epsilon": 0.001,
        "penalty_unevaluable_oos": -200.0, "unevaluable_shaping_span": 0.25,
        "sortino_clip_abs": 5.0, "sortino_soft_scale": ss,
        "penalty_overfit_weight": 0.5, "penalty_dd_weight": 0.0, "bonus_coverage_weight": 0.0,
        "overfit_divergence_mode": "symmetric", "penalty_relative_cap": cap, "w_ret": 0.0,
    }

    def _div_penalty(oos_sortino):
        base = ss * math.asinh(oos_sortino / ss)
        m = TournamentMetrics(
            oos_evaluated=True, oos_eligible=True, is_sortino_median=200.0,  # riesige Divergenz ⇒ Cap greift
            oos_sortino=oos_sortino, oos_max_drawdown=0.0, oos_total_trades=30, win_count=1,
            fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.0)
        reward = compute_reward(m, universe_size=1, weights=w, risk_dd_cap=0.0,
                                tournament_cfg={"oos_min_total_return": 0.005, "max_drawdown": 0.3})
        return base - reward   # alle anderen Terme sind 0 ⇒ reward = base − divergence_penalty

    pen_pos = _div_penalty(+sortino_for_base5)   # base = +5
    pen_neg = _div_penalty(-sortino_for_base5)   # base = −5
    assert pen_pos == pytest.approx(cap * ss, abs=1e-9)  # Cap-Höhe = penalty_relative_cap · soft_scale
    assert pen_neg == pytest.approx(cap * ss, abs=1e-9)
    assert pen_pos == pytest.approx(pen_neg, abs=1e-9)   # vorzeichen-invariant (nicht |base|)


def test_reward_semantics_version_is_8():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["reward_semantics_version"] == 8
