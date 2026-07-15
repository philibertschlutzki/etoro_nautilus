"""Issue #591 (historisch) — Reward-Floor an den Sortino-Clip gekoppelt ⇒ Plateau mit Gradient 0 im
eligiblen Ast. Der eligible Reward-Floor war ``−sortino_clip_abs`` (−5.0) und lag ÜBER dem, was der
eligible Ast bei realistischen Sortinos natürlich produziert ⇒ 6/8 SmaCrossover-Trials klemmten
exakt auf −5.0 (TPE-Plateau). #591 entkoppelte den Floor (``evaluable_reward_floor``, −12.0) von
``sortino_clip_abs``, behielt aber weiterhin einen Floor/eine Bandordnung.

Issue #629 — der GESAMTE Floor/Band-Mechanismus (``evaluable_reward_floor``,
``evaluable_floor_epsilon``, ``_evaluable_floor``, ``failure_ceiling``, ``unevaluable_ceiling``) ist
seither ERSATZLOS ENTFALLEN: Feasibility wird ausschliesslich vom #612-Sampler-Constraint
entschieden, compute_reward liefert ein einziges, ungeklemmtes Qualitätsziel. Dieses Modul verifiziert
das (die eigentlichen Floor-Tests sind #629-obsolet und wurden ersetzt) und behält die weiterhin
gültige ``penalty_relative_cap``-Vorzeichen-Invarianz aus #591/#613.
"""
import math

import pytest

from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics


def test_evaluable_reward_floor_machinery_is_gone():
    """Issue #629 — der Floor/die zugehörige Helper-Funktion existieren nicht mehr."""
    import automation.optimizer.reward as reward_mod

    assert not hasattr(reward_mod, "_evaluable_floor")


def test_eligible_reward_is_unclamped_for_arbitrarily_bad_metrics():
    """Issue #629 — ein eligibler Trial mit beliebig schlechten Kennzahlen wird NICHT mehr auf einen
    Floor geklemmt; der Reward faellt monoton mit der Qualitaet weiter (kein Plateau bei −12.0)."""
    w = {
        "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
        "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
        "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
        "constraint_distance_penalty_weight": 0.25, "w_ret": 0.0,
    }
    tcfg = {"oos_min_trades": 20, "oos_min_total_return": 0.05, "oos_min_expectancy": 0.01,
            "oos_min_win_rate": 0.4, "max_drawdown": 0.3, "return_penalty_scale": 0.1}

    def _reward(dd):
        m = TournamentMetrics(
            oos_evaluated=True, oos_eligible=True, is_sortino_median=0.0,
            oos_sortino=-2.0, oos_max_drawdown=dd, oos_total_trades=20, win_count=0,
            fully_eligible_pairs=0, is_total_trades=10, oos_total_return=0.0,
        )
        return compute_reward(m, universe_size=1, weights=w, risk_dd_cap=0.3, tournament_cfg=tcfg)

    # Immer groesserer Drawdown ⇒ immer schlechterer, NIE geklemmter Reward.
    rewards = [_reward(dd) for dd in (0.02, 0.5, 2.0, 10.0)]
    assert rewards == sorted(rewards, reverse=True)
    assert len(set(rewards)) == len(rewards)  # kein Plateau
    assert all(math.isfinite(r) for r in rewards)


def test_relative_cap_same_absolute_height_for_negative_and_positive_base():
    """penalty_relative_cap produziert bei base = −5 und base = +5 dieselbe absolute Cap-Höhe."""
    ss = 5.0
    cap = 0.5
    sortino_for_base5 = ss * math.sinh(1.0)   # base = ss*asinh(sortino/ss) = 5
    w = {
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
