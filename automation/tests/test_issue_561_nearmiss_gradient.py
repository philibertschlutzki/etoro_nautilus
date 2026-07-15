"""Issue #560 — Return-verankerter Near-Miss-Gradient für evaluiert-aber-ineligible Trials.

Der Failure-Reward war urspruenglich das Mittel von 6 Distanz-Termen, von denen mehrere kosten-
saturiert klebten ⇒ der einzige performance-tragende Term (Return) wurde verduennt und
corr(reward,return)≈0. Der ursprüngliche Fix (#560) fuehrte einen Opt-in-Modus
(``failure_reward_mode='return_anchored'``) ein, der den Failure-Reward direkt an
``oos_total_return`` verankerte.

Issue #629 — dieser Opt-in-Modus (inkl. ``_constraint_failure_reward``, ``failure_reward_mode``,
``failure_return_softplus_scale``, ``failure_return_penalty_weight``) ist ERSATZLOS ENTFALLEN: seit
#629 teilen sich eligible UND evaluated-aber-ineligible Trials DENSELBEN Qualitäts-Kern (base −
Divergenz − Drawdown − Turnover − Fold-Dispersion + Tie-Breaker), ineligible Trials erhalten
ZUSAETZLICH die bestehende, kontinuierliche Near-Miss-Distanzstrafe (``_constraint_distance_penalty``)
additiv obendrauf — kein separates Band, keine Ordnungsklammer mehr. Die #560-Akzeptanzkriterien
(Return-Korrelation, Monotonie unterhalb des Gates) muessen mit DIESER unified Formel weiterhin
gelten; sie werden hier gegen ``compute_reward`` direkt (ohne Modus-Schalter) verifiziert.
"""
import math
import statistics

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

WEIGHTS = {
    "penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25,
    "constraint_distance_penalty_weight": 0.25,
    "sortino_clip_abs": 5.0, "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
    "bonus_coverage_weight": 1.0,
}
CFG = {
    "oos_min_trades": 1, "oos_min_total_return": 0.005, "oos_min_expectancy": 0.001,
    "oos_min_win_rate": 0.25, "oos_min_sortino": 1.0, "oos_min_profit_factor": 1.1,
    "return_penalty_scale": 0.1, "expectancy_penalty_scale": 0.002,
    "distance_term_cap": 3.0, "max_drawdown": 0.3, "eligible_requires_any": [],
}


def _ineligible(oos_total_return):
    # Kosten-saturierte Nebendimensionen (Expectancy/Win-Rate ~ gepinnt), nur Return streut.
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
def test_unified_formula_recovers_return_correlation():
    returns = [-0.05 + i * (0.004 - (-0.05)) / 29 for i in range(30)]  # bis knapp unter Gate 0.005
    rewards = [_reward(_ineligible(r), WEIGHTS) for r in returns]

    mean_r, mean_ret = statistics.mean(rewards), statistics.mean(returns)
    cov = sum((a - mean_r) * (b - mean_ret) for a, b in zip(rewards, returns))
    denom = (math.sqrt(sum((a - mean_r) ** 2 for a in rewards))
             * math.sqrt(sum((b - mean_ret) ** 2 for b in returns)))
    corr = cov / denom
    assert corr > 0.3, f"corr(reward, return | ineligible) = {corr:.3f} muss > 0.3 sein."


# ── Monotonie: return_a > return_b ⇒ reward_a >= reward_b (strikt unter Gate) ─────────────────
def test_strictly_monotone_below_gate():
    r1 = _reward(_ineligible(-0.05), WEIGHTS)
    r2 = _reward(_ineligible(-0.02), WEIGHTS)
    r3 = _reward(_ineligible(0.004), WEIGHTS)  # knapp unter Gate 0.005
    assert r1 < r2 < r3, "Failure-Reward muss streng monoton in oos_total_return steigen."


def test_failure_reward_mode_machinery_is_gone():
    """Issue #629 — der Opt-in-Modus-Schalter (und die zugehoerigen Config-Keys) existiert nicht
    mehr; die unified Formel ist die EINZIGE Berechnung fuer evaluated-aber-ineligible Trials."""
    import automation.optimizer.reward as reward_mod

    assert not hasattr(reward_mod, "_constraint_failure_reward")
    assert not hasattr(reward_mod, "_evaluable_floor")

    import json
    from pathlib import Path

    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    for dead_key in ("failure_reward_mode", "failure_return_softplus_scale",
                     "failure_return_penalty_weight", "evaluable_reward_floor",
                     "evaluable_floor_epsilon"):
        assert dead_key not in cfg, f"{dead_key} haette mit #629 entfernt werden muessen"
