"""Issue #638 — w_ret = 2.0 überstimmt die (flache) Base — Tie-Breaker nach De-Saturierung reskaliert.

Root-Cause: ``return_tie_breaker = w_ret·oos_total_return`` mit ``w_ret=2.0`` war gegen die alte,
breiter gestreute asinh-Sortino-Base kalibriert. Seit #614/#630 ist die Base ``psr_z`` viel enger
gestreut ⇒ derselbe w_ret machte den "Tie-Breaker" zum PRIMÄRterm (Return-Chasing durch die
Hintertür — genau das, was die PSR-Base ersetzen sollte).

Fix: w_ret auf 0.05 rekalibriert (Faktor 40 kleiner, im Issue-Korridor 20–50×) — der Return bleibt
nur der letzte Entscheider bei tatsächlich (nahezu) gleicher Base.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))

W = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "constraint_distance_penalty_weight": 0.25,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
    "penalty_turnover_weight": 0.0, "penalty_scale_vs_base": 1.0,
}
TCFG = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}


def _trial(psr_z, ret):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=0.0,
        oos_sortino=psr_z, oos_max_drawdown=0.01, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=ret,
        oos_win_rate=0.4, oos_profit_factor=1.3, oos_psr_z=psr_z,
    )


def _reward(psr_z, ret, w_ret):
    w = dict(W, w_ret=w_ret)
    return compute_reward(_trial(psr_z, ret), universe_size=1, weights=w,
                          risk_dd_cap=0.3, tournament_cfg=TCFG)


def test_production_config_rescaled_down_from_legacy_2_0():
    assert CFG["w_ret"] == pytest.approx(0.05)
    assert CFG["w_ret"] < 2.0 / 20.0  # mindestens Faktor 20 kleiner als der alte Wert


def test_tie_breaker_no_longer_overturns_meaningful_base_gap():
    """Akzeptanzkriterium (#638): der Tie-Breaker darf das Ranking NUR bei |Δbase| ≈ 0 ändern.
    A hat deutlich höheres psr_z (Δbase=0.05) aber niedrigeren Return; B hat niedrigeres psr_z aber
    einen viel höheren Return. Mit dem ALTEN w_ret=2.0 kippt B vor A (Return-Chasing); mit dem NEUEN,
    rekalibrierten w_ret bleibt die Base-Ordnung (A vor B) erhalten."""
    trial_a = (0.90, 0.01)   # höheres psr_z, niedrigerer Return
    trial_b = (0.85, 0.05)   # niedrigeres psr_z (Δbase=0.05), aber viel höherer Return

    r_a_old = _reward(*trial_a, w_ret=2.0)
    r_b_old = _reward(*trial_b, w_ret=2.0)
    assert r_b_old > r_a_old, "Sanity: der ALTE w_ret=2.0 zeigt tatsächlich Return-Chasing (Defekt)"

    r_a_new = _reward(*trial_a, w_ret=CFG["w_ret"])
    r_b_new = _reward(*trial_b, w_ret=CFG["w_ret"])
    assert r_a_new > r_b_new, "Der rekalibrierte w_ret darf die Base-Ordnung nicht mehr überstimmen"


def test_tie_breaker_still_decides_true_ties():
    """Bei WIRKLICH identischer Base (Δbase=0) bleibt der Tie-Breaker der Entscheider — er wird nicht
    komplett bedeutungslos, nur klein genug, um die Base-Ordnung nicht zu überstimmen."""
    same_z = 0.90
    r_low_ret = _reward(same_z, 0.01, w_ret=CFG["w_ret"])
    r_high_ret = _reward(same_z, 0.05, w_ret=CFG["w_ret"])
    assert r_high_ret > r_low_ret
    assert r_high_ret != pytest.approx(r_low_ret)


def test_return_correlation_below_base_correlation_on_eligible_cohort():
    """Akzeptanzkriterium (#638): corr(reward, oos_total_return) < corr(reward, base) auf der
    eligiblen Kohorte — der Return ist nicht mehr der Primärtreiber."""
    import math
    import random
    import statistics as st

    rng = random.Random(11)
    z_floor = 0.6744897501960817
    cohort = [(z_floor + rng.uniform(0.0, 1.0), 0.005 + 0.05 * rng.random()) for _ in range(40)]

    def corr(a, b):
        ma, mb = st.mean(a), st.mean(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = math.sqrt(sum((x - ma) ** 2 for x in a))
        db = math.sqrt(sum((y - mb) ** 2 for y in b))
        return cov / (da * db) if da > 0 and db > 0 else 0.0

    rewards = [_reward(z, ret, w_ret=CFG["w_ret"]) for z, ret in cohort]
    bases = [z for z, _ in cohort]
    rets = [ret for _, ret in cohort]
    assert abs(corr(rewards, rets)) < abs(corr(rewards, bases))
