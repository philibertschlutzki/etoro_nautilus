"""Issue #560 — Return-verankerter Near-Miss-Gradient für evaluiert-aber-ineligible Trials.

Der Failure-Reward war das Mittel von 6 Distanz-Termen, von denen 3 kosten-saturiert bei ~1.0
kleben ⇒ der einzige performance-tragende Term (Return) wurde verdünnt und corr(reward,return)≈0.
Der TPE hatte auf 97,5 % der Landschaft keine Richtung Richtung Eligibility. Der Fix verankert
den Failure-Reward an oos_total_return via softplus (überall streng monoton, C∞).

Akzeptanzkriterien (Issue #560):
- corr(reward, oos_total_return | ineligible) > 0.3.
- Monotonie: return_a > return_b ⇒ reward_a ≥ reward_b (strikt unterhalb des Return-Gates).
- Ordnungsinvariante: max(failure_reward) < −sortino_clip_abs.
- legacy_mean bleibt Default (bit-identisch).
"""
import json
import math
import statistics
from pathlib import Path

import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward, _constraint_failure_reward

WEIGHTS_ANCHORED = {
    "penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001, "constraint_distance_penalty_weight": 0.25,
    "sortino_clip_abs": 5.0, "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
    "bonus_coverage_weight": 1.0,
    "failure_reward_mode": "return_anchored",
    "failure_return_softplus_scale": 0.02, "failure_return_penalty_weight": 2.0,
}
CFG = {
    "oos_min_trades": 1, "oos_min_total_return": 0.005, "oos_min_expectancy": 0.001,
    "oos_min_win_rate": 0.25, "oos_min_sortino": 1.0, "oos_min_profit_factor": 1.1,
    "return_penalty_scale": 0.1, "expectancy_penalty_scale": 0.002,
    "distance_term_cap": 3.0, "max_drawdown": 0.3,
}


def _ineligible(oos_total_return):
    # Kosten-saturierte Nebendimensionen (Expectancy/Win-Rate/ANY ~ gepinnt), nur Return streut.
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=False, is_sortino_median=0.0,
        oos_sortino=0.01, oos_max_drawdown=0.05, oos_total_trades=100,
        win_count=0, fully_eligible_pairs=0, is_total_trades=0,
        oos_total_return=oos_total_return, oos_expectancy=-0.001, oos_win_rate=0.05,
        oos_profit_factor=0.05,
    )


def _reward(m, weights):
    return compute_reward(m, universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=CFG)


# ── corr(reward, return | ineligible) > 0.3 ──────────────────────────────────────────────────
def test_return_anchored_recovers_return_correlation():
    returns = [-0.05 + i * (0.004 - (-0.05)) / 29 for i in range(30)]  # bis knapp unter Gate 0.005
    rewards = [_reward(_ineligible(r), WEIGHTS_ANCHORED) for r in returns]

    mean_r, mean_ret = statistics.mean(rewards), statistics.mean(returns)
    cov = sum((a - mean_r) * (b - mean_ret) for a, b in zip(rewards, returns))
    denom = (math.sqrt(sum((a - mean_r) ** 2 for a in rewards))
             * math.sqrt(sum((b - mean_ret) ** 2 for b in returns)))
    corr = cov / denom
    assert corr > 0.3, f"corr(reward, return | ineligible) = {corr:.3f} muss > 0.3 sein."


def test_legacy_mean_has_no_return_gradient():
    """Kontrast: legacy_mean (Default) zeigt praktisch keine Return-Korrelation (der Ausgangsdefekt)."""
    weights_legacy = dict(WEIGHTS_ANCHORED); weights_legacy.pop("failure_reward_mode")
    returns = [-0.05 + i * (0.004 - (-0.05)) / 29 for i in range(30)]
    rewards = [_reward(_ineligible(r), weights_legacy) for r in returns]
    # Alle Nebendimensionen saturiert + Return-Distanz gedeckelt ⇒ deutlich flacher als anchored.
    assert statistics.pstdev(rewards) < 0.05


# ── Monotonie: return_a > return_b ⇒ reward_a >= reward_b (strikt unter Gate) ─────────────────
def test_strictly_monotone_below_gate():
    r1 = _reward(_ineligible(-0.05), WEIGHTS_ANCHORED)
    r2 = _reward(_ineligible(-0.02), WEIGHTS_ANCHORED)
    r3 = _reward(_ineligible(0.004), WEIGHTS_ANCHORED)  # knapp unter Gate 0.005
    assert r1 < r2 < r3, "Failure-Reward muss streng monoton in oos_total_return steigen."


# ── Ordnungsinvariante: max(failure) < −sortino_clip_abs ─────────────────────────────────────
def test_failure_reward_strictly_below_feasible_floor():
    # Selbst ein ineligibler Trial mit hohem Return (der ein anderes Gate verfehlt) bleibt < −5.
    for ret in (-0.05, 0.0, 0.004, 0.10):
        r = _constraint_failure_reward(_ineligible(ret), WEIGHTS_ANCHORED, 0.3, CFG)
        assert r < -WEIGHTS_ANCHORED["sortino_clip_abs"], (
            f"Failure-Reward {r} muss strikt < −{WEIGHTS_ANCHORED['sortino_clip_abs']} bleiben.")


# ── Default-Modus bleibt legacy_mean (bit-identisch) ─────────────────────────────────────────
def test_default_mode_is_legacy_mean():
    weights_no_mode = dict(WEIGHTS_ANCHORED); weights_no_mode.pop("failure_reward_mode")
    m = _ineligible(-0.02)
    r_default = _constraint_failure_reward(m, weights_no_mode, 0.3, CFG)
    weights_legacy = dict(weights_no_mode, failure_reward_mode="legacy_mean")
    r_explicit = _constraint_failure_reward(m, weights_legacy, 0.3, CFG)
    assert r_default == pytest.approx(r_explicit)


def test_production_config_enables_return_anchored():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg.get("failure_reward_mode") == "return_anchored"
