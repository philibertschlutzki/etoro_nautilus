"""Issue #534 — Reward-Konditionierung: Near-Miss-Gradient konsistent über AKTIVE Dimensionen.

Vor dem Fix summierte ``_constraint_distance_penalty`` nur die aktiven Distanzen, teilte aber durch
die feste Gesamtzahl der Dimensionen (``len(distances) == 6``). Dadurch hing die effektive Strafe
pro aktiver Dimension von der Anzahl inaktiver Gates ab (Gradientenrauschen). Der Fix normalisiert
strikt über die aktiven Dimensionen (``sum(active_dists) / len(active_dists)``). Die Distanzen
bleiben linear (Issue #505) und der Floor bleibt invariant (Issue #452/#461).
"""
import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import _constraint_distance_penalty, compute_reward

# tournament_cfg mit genau einem scharfen Gate (Return); alle anderen Schwellen ⇒ trivial erfüllbar,
# damit der Return der einzige aktive Freiheitsgrad ist und der Test die Normalisierung isoliert.
CFG = {
    "oos_min_trades": 10,
    "oos_min_total_return": 0.05,
    "oos_min_expectancy": 0.0,
    "oos_min_win_rate": 0.0,
    "return_penalty_scale": 0.1,
    "max_drawdown": 0.2,
}

WEIGHTS = {
    "penalty_unevaluable_oos": -10.0,
    "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001,
    "constraint_distance_penalty_weight": 0.25,
    "sortino_clip_abs": 5.0,
    "penalty_overfit_weight": 0.5,
    "penalty_dd_weight": 8.0,
    "bonus_coverage_weight": 1.0,
}


def _m(**kw):
    base = dict(
        oos_evaluated=True, oos_eligible=False, is_sortino_median=0.0,
        oos_sortino=1.0, oos_max_drawdown=0.05, oos_total_trades=100,
        win_count=0, fully_eligible_pairs=0, is_total_trades=0,
        oos_total_return=0.04, oos_expectancy=0.01, oos_win_rate=0.5,
        oos_profit_factor=2.0,
    )
    base.update(kw)
    return TournamentMetrics(**base)


def test_penalty_normalizes_over_active_dimensions():
    # Genau eine aktive Dimension (Return): dist = (0.05-0.04)/0.1 = 0.1.
    # Fix (#534): mean = 0.1/1 = 0.1 ⇒ penalty = 0.1 * weight(1.0) = 0.1.
    # Bug (vorher): 0.1/6 = 0.01667 durch Division über die feste Gesamtzahl der Dimensionen.
    w = dict(WEIGHTS, constraint_distance_penalty_weight=1.0)
    penalty = _constraint_distance_penalty(_m(), w, risk_dd_cap=CFG["max_drawdown"], tournament_cfg=CFG)
    assert penalty == pytest.approx(0.1, abs=1e-9)
    assert penalty > 0.1 / 6 + 1e-3  # eindeutig NICHT die alte /6-Normalisierung


def test_identical_return_shortfall_same_penalty_regardless_of_satisfied_gates():
    # Akzeptanzkriterium 1: identischer Return-Shortfall ⇒ identische Teilstrafe, unabhängig davon,
    # wie viele Nebengates (mit welcher Marge) zufällig erfüllt sind.
    a = _m(oos_total_trades=100, oos_win_rate=0.90, oos_expectancy=0.50)
    b = _m(oos_total_trades=11, oos_win_rate=0.001, oos_expectancy=0.0001)
    pa = _constraint_distance_penalty(a, WEIGHTS, risk_dd_cap=CFG["max_drawdown"], tournament_cfg=CFG)
    pb = _constraint_distance_penalty(b, WEIGHTS, risk_dd_cap=CFG["max_drawdown"], tournament_cfg=CFG)
    assert pa == pytest.approx(pb, abs=1e-12)


def test_reward_monotonic_with_oos_total_return_in_sub_gate_region():
    # Akzeptanzkriterium 2: Reward korreliert monoton mit oos_total_return unterhalb des Gates.
    returns = [0.00, 0.01, 0.02, 0.03, 0.04]  # alle < oos_min_total_return (0.05)
    rewards = [
        compute_reward(_m(oos_total_return=r), universe_size=1, weights=WEIGHTS,
                       risk_dd_cap=CFG["max_drawdown"], tournament_cfg=CFG)
        for r in returns
    ]
    assert rewards == sorted(rewards)          # streng steigend mit dem Return
    assert len(set(rewards)) == len(rewards)   # kein Plateau (echter Gradient)


def test_failed_trial_stays_below_evaluable_floor():
    # Invariante (Issue #452/#461/#534): failed Trials bleiben strikt unter dem Evaluable-Floor.
    evaluable_floor = -float(WEIGHTS["sortino_clip_abs"])
    unevaluable_ceiling = WEIGHTS["penalty_unevaluable_oos"] + WEIGHTS["unevaluable_shaping_span"]
    r = compute_reward(_m(oos_total_return=0.0), universe_size=1, weights=WEIGHTS,
                       risk_dd_cap=CFG["max_drawdown"], tournament_cfg=CFG)
    assert r < evaluable_floor
    assert r >= unevaluable_ceiling
