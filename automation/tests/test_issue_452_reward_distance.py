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
                win_count=0, fully_eligible_pairs=0, is_total_trades=0, hit_trade_cap=False,
                oos_total_return=0.0, oos_win_rate=0.0, oos_profit_factor=1.0)
    base.update(kw)
    return TournamentMetrics(**base)

def _reward(m):
    return compute_reward(m, universe_size=1, weights=W, risk_dd_cap=0.30)

def test_ineligible_near_miss_beats_severe_constraint_failure():
    # Make the near miss very close to avoid clamping
    near = _m(oos_total_trades=20, oos_total_return=0.0045, oos_win_rate=0.25,
              oos_sortino=0.3, oos_profit_factor=1.1, oos_max_drawdown=0.30)
    severe = _m(oos_total_trades=1, oos_total_return=-0.05, oos_win_rate=0.0,
                oos_sortino=-1.0, oos_profit_factor=0.2, oos_max_drawdown=0.90)

    assert _reward(near) > _reward(severe)
    assert _reward(near) != pytest.approx(_reward(severe))

def test_near_miss_can_outrank_a_catastrophic_eligible_trial():
    # Issue #629 — es gibt kein Floor-/Ceiling-Band mehr, das ineligible Trials pauschal unter jeden
    # eligiblen Trial zwingt. Ein knapper Near-Miss mit ordentlicher zugrundeliegender Qualität
    # rankt jetzt korrekt ÜBER einem eligiblen, aber katastrophal schlechten Trial (Sortino −5.0,
    # Drawdown 99 %) — genau das macht den Reward zu EINEM stetigen Qualitätsziel statt einer
    # künstlichen Stufenfunktion. Die Feasibility-RANGORDNUNG selbst übernimmt ausschliesslich der
    # #612-Sampler-Constraint (oos_constraint_violations), nicht compute_reward.
    near = _m(oos_total_trades=20, oos_total_return=0.0045, oos_win_rate=0.25,
              oos_sortino=0.3, oos_profit_factor=1.1, oos_max_drawdown=0.30)
    catastrophic_eligible = _m(oos_evaluated=True, oos_eligible=True, oos_total_trades=20,
                               oos_total_return=0.005, oos_win_rate=0.25, oos_sortino=-5.0,
                               oos_profit_factor=1.1, oos_max_drawdown=0.99)

    assert _reward(near) > _reward(catastrophic_eligible)


def test_ineligible_reward_equals_quality_core_minus_gate_distance_penalty():
    # Issue #629 — der ineligible Reward ist exakt der GEMEINSAME Qualitäts-Kern (dieselbe Formel wie
    # ein eligibler Trial mit identischen Kennzahlen erhielte) MINUS der kontinuierlichen Near-Miss-
    # Distanzstrafe — kein separates Band, keine künstliche Bodenklammer.
    from automation.optimizer.reward import _constraint_distance_penalty

    failed = _m(oos_total_trades=20, oos_total_return=0.0045, oos_win_rate=0.25,
                oos_sortino=0.3, oos_profit_factor=1.1, oos_max_drawdown=0.30)
    eligible_twin = _m(oos_evaluated=True, oos_eligible=True, oos_total_trades=20,
                       oos_total_return=0.0045, oos_win_rate=0.25, oos_sortino=0.3,
                       oos_profit_factor=1.1, oos_max_drawdown=0.30)

    # explizites (leeres) tournament_cfg fuer BEIDE Aufrufe: _any_condition_distance liest
    # eligible_requires_any ausschliesslich aus tournament_cfg (nie aus weights) — mit
    # tournament_cfg=None wuerde compute_reward intern die REALE tournament.json nachladen und
    # den manuellen Vergleich verfaelschen (W traegt alle oos_min_*-Schwellen direkt).
    tcfg = {"eligible_requires_any": []}
    gate_distance_penalty = _constraint_distance_penalty(failed, W, risk_dd_cap=0.30,
                                                          tournament_cfg=tcfg)
    assert gate_distance_penalty > 0.0
    r_failed = compute_reward(failed, universe_size=1, weights=W, risk_dd_cap=0.30, tournament_cfg=tcfg)
    r_eligible_twin = compute_reward(eligible_twin, universe_size=1, weights=W, risk_dd_cap=0.30,
                                     tournament_cfg=tcfg)
    assert r_failed == pytest.approx(r_eligible_twin - gate_distance_penalty, abs=1e-9)

def test_monotonic_gradient_across_trade_shortfall():
    w = W.copy()
    rewards = [
        compute_reward(_m(oos_total_trades=n, oos_total_return=0.005,
                   oos_win_rate=0.25, oos_sortino=0.3, oos_profit_factor=1.1), universe_size=1, weights=w, risk_dd_cap=0.30)
        for n in (19, 18, 17, 16)
    ]
    assert rewards == sorted(rewards, reverse=True)
    assert len(set(rewards)) == len(rewards)

def test_default_weight_falls_back_to_shaping_span():
    w = {k: v for k, v in W.items() if k != "constraint_distance_penalty_weight"}
    near = _m(oos_total_trades=19, oos_total_return=0.0049, oos_win_rate=0.24,
              oos_sortino=0.29, oos_profit_factor=1.09, oos_max_drawdown=0.30)
    severe = _m(oos_total_trades=1, oos_total_return=-0.05, oos_win_rate=0.0,
                oos_sortino=-1.0, oos_profit_factor=0.2, oos_max_drawdown=0.90)
    r_near = compute_reward(near, universe_size=1, weights=w, risk_dd_cap=0.30)
    r_severe = compute_reward(severe, universe_size=1, weights=w, risk_dd_cap=0.30)
    assert r_near > r_severe
