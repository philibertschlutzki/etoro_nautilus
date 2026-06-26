"""Issue #452 — Constraint-Verletzungen duerfen nicht auf Flat-Penalties kollabieren.

Der TPE-Sampler braucht eine kontinuierliche Fitness-Landschaft. Werden evaluierte, aber durchs
OOS-Eligibility-Gate gefallene Trials alle auf denselben Floor geclampt, zerstoert das den
Suchgradienten (Reward-Cluster −9.75/−9.89/−9.9, Random-Search-Degeneration). Diese Tests fixieren:
  * near-miss > katastrophal verfehlt (Gradient wieder sichtbar),
  * failed Constraint-Trials bleiben strikt unter dem Evaluable-Floor (Anti-Gate-Gaming),
  * auch ohne Distanzmetriken kein Kollaps auf die Evaluable-Hoehe.
"""

import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward


W = {
    "penalty_unevaluable_oos": -10.0,
    "sortino_clip_abs": 5.0,
    "penalty_overfit_weight": 0.5,
    "penalty_dd_weight": 8.0,
    "bonus_coverage_weight": 1.0,
    "unevaluable_shaping_span": 0.25,
    "constraint_distance_penalty_weight": 0.25,
    "evaluable_floor_epsilon": 0.001,
    "lambda_reg": 0.25,
    "oos_min_trades": 20,
    "oos_min_total_return": 0.005,
    "oos_min_expectancy": 0.00005,
    "oos_min_win_rate": 0.25,
    "oos_min_sortino": 0.3,
    "oos_min_profit_factor": 1.1,
}


def _m(**kw):
    base = dict(oos_evaluated=True, oos_eligible=False, is_sortino_median=0.0,
                oos_sortino=0.2, oos_max_drawdown=0.10, oos_total_trades=0,
                win_count=0, fully_eligible_pairs=0, is_total_trades=0, is_max_trades=0,
                oos_total_return=0.0, oos_win_rate=0.0, oos_profit_factor=1.0)
    base.update(kw)
    return TournamentMetrics(**base)


def _reward(m):
    return compute_reward(m, universe_size=1, weights=W, risk_dd_cap=0.30)


def test_ineligible_near_miss_beats_severe_constraint_failure():
    """TPE sieht wieder einen Gradienten: knapp verfehlt > massiv verfehlt."""
    near = _m(oos_total_trades=19, oos_total_return=0.0049, oos_win_rate=0.24,
              oos_sortino=0.29, oos_profit_factor=1.09, oos_max_drawdown=0.30)
    severe = _m(oos_total_trades=1, oos_total_return=-0.05, oos_win_rate=0.0,
                oos_sortino=-1.0, oos_profit_factor=0.2, oos_max_drawdown=0.90)

    assert _reward(near) > _reward(severe)
    assert _reward(near) != pytest.approx(_reward(severe))


def test_failed_constraint_trials_stay_below_evaluable_floor():
    near = _m(oos_total_trades=19, oos_total_return=0.0049, oos_win_rate=0.24,
              oos_sortino=0.29, oos_profit_factor=1.09, oos_max_drawdown=0.30)
    eligible = _m(oos_evaluated=True, oos_eligible=True, oos_total_trades=20,
                  oos_total_return=0.005, oos_win_rate=0.25, oos_sortino=-5.0,
                  oos_profit_factor=1.1, oos_max_drawdown=0.99)

    floor = W["penalty_unevaluable_oos"] + W["unevaluable_shaping_span"] + W["evaluable_floor_epsilon"]
    assert _reward(near) < floor
    assert _reward(eligible) == pytest.approx(floor)
    assert _reward(eligible) > _reward(near)


def test_missing_distance_metrics_still_do_not_flatten_to_evaluable_floor():
    failed = _m(oos_total_trades=20, oos_total_return=0.005, oos_win_rate=0.25,
                oos_sortino=0.3, oos_profit_factor=1.1, oos_max_drawdown=0.30)
    ceiling = W["penalty_unevaluable_oos"] + W["unevaluable_shaping_span"]

    assert _reward(failed) < ceiling


def test_monotonic_gradient_across_trade_shortfall():
    """Sukzessiv groesserer Trade-Shortfall ⇒ strikt fallender Reward (kein Plateau)."""
    rewards = [
        _reward(_m(oos_total_trades=n, oos_total_return=0.0,
                   oos_win_rate=0.10, oos_sortino=0.0, oos_profit_factor=0.5))
        for n in (18, 12, 6, 1)
    ]
    assert rewards == sorted(rewards, reverse=True)
    assert len(set(rewards)) == len(rewards)  # keine zwei identischen Floor-Cluster


def test_default_weight_falls_back_to_shaping_span():
    """Fehlt constraint_distance_penalty_weight, faellt das Gewicht auf unevaluable_shaping_span
    zurueck (kein KeyError, weiterhin Gradient)."""
    w = {k: v for k, v in W.items() if k != "constraint_distance_penalty_weight"}
    near = _m(oos_total_trades=19, oos_total_return=0.0049, oos_win_rate=0.24,
              oos_sortino=0.29, oos_profit_factor=1.09, oos_max_drawdown=0.30)
    severe = _m(oos_total_trades=1, oos_total_return=-0.05, oos_win_rate=0.0,
                oos_sortino=-1.0, oos_profit_factor=0.2, oos_max_drawdown=0.90)
    r_near = compute_reward(near, universe_size=1, weights=w, risk_dd_cap=0.30)
    r_severe = compute_reward(severe, universe_size=1, weights=w, risk_dd_cap=0.30)
    assert r_near > r_severe
