"""Issue #559 — Weiche Sortino-Sättigung (asinh) + return-Tie-Breaker gegen Winner-Gradienten-Tod.

Der Hard-Clip base = clip(oos_sortino, ±5) ist eine stückweise konstante Funktion mit Gradient 0
oberhalb der Klemmgrenze — genau dort, wo die Winner leben (jeder evaluable Trial mit minimalem
Edge klemmte annualisiert auf +5.0). Der Fix ersetzt ihn durch base = c·asinh(sortino/c) (überall
streng monoton) plus einen kleinen return-Tie-Breaker (+ w_ret·oos_total_return).

Akzeptanzkriterien (Issue #559):
- pstdev(reward | eligible) > 1e-3 (Winner unterscheidbar).
- corr(reward, oos_total_return | eligible) > 0.5.
- Monotonie: sortino_a > sortino_b ⇒ reward_a > reward_b, auch ÜBER der alten Clip-Grenze.
- Ordnungsinvariante: min(reward | eligible) > max(reward | ineligible).
"""
import json
import math
import statistics
from pathlib import Path

import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

# Produktions-Weights (weiche Sättigung aktiv) + kanonische OOS-Schwellen.
WEIGHTS = {
    "penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001, "constraint_distance_penalty_weight": 0.25,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0, "w_ret": 2.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0, "bonus_coverage_weight": 1.0,
    "lambda_reg": 0.25,
}
CFG = {
    "oos_min_trades": 1, "oos_min_total_return": 0.005, "oos_min_expectancy": 0.001,
    "oos_min_win_rate": 0.25, "oos_min_sortino": 1.0, "oos_min_profit_factor": 1.1,
    "return_penalty_scale": 0.1, "max_drawdown": 0.3,
}


def _eligible(oos_sortino, oos_total_return, is_sortino_median=0.0):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=is_sortino_median,
        oos_sortino=oos_sortino, oos_max_drawdown=0.02, oos_total_trades=30,
        win_count=1, fully_eligible_pairs=1, is_total_trades=100,
        oos_total_return=oos_total_return, oos_expectancy=0.001, oos_win_rate=0.5,
        oos_profit_factor=1.5,
    )


def _reward(m):
    return compute_reward(m, universe_size=1, weights=WEIGHTS, risk_dd_cap=0.3, tournament_cfg=CFG)


# ── Monotonie ÜBER der alten Clip-Grenze ─────────────────────────────────────────────────────
def test_monotone_above_old_clip():
    """sortino 10 vs 20 (beide > alte Grenze 5): der Reward trägt WEITER Ordnung."""
    r_low = _reward(_eligible(10.0, 0.015))
    r_high = _reward(_eligible(20.0, 0.015))
    assert r_high > r_low, "asinh muss oberhalb der alten Clip-Grenze streng monoton bleiben."


def test_soft_saturation_compresses_but_preserves_order():
    """base = c·asinh(sortino/c): Ordnung 50 > 10 > 5 bleibt, Werte komprimiert."""
    def base(s):
        return 5.0 * math.asinh(s / 5.0)
    assert base(50.0) > base(10.0) > base(5.0)
    assert base(50.0) < 50.0  # komprimiert (kein linearer Durchlauf)


# ── Winner unterscheidbar (pstdev > 1e-3) + corr(reward, return) > 0.5 ────────────────────────
def test_eligible_rewards_have_spread_and_correlate_with_return():
    # 18 eligible Trials wie im Log: identischer (geklemmt-hoher) Sortino, aber streuender Return.
    returns = [0.0118 + i * (0.0177 - 0.0118) / 17 for i in range(18)]
    rewards = [_reward(_eligible(oos_sortino=6.0, oos_total_return=r)) for r in returns]

    assert statistics.pstdev(rewards) > 1e-3, "Eligible-Rewards müssen unterscheidbar sein (Plateau tot)."

    mean_r, mean_ret = statistics.mean(rewards), statistics.mean(returns)
    cov = sum((a - mean_r) * (b - mean_ret) for a, b in zip(rewards, returns))
    denom = (math.sqrt(sum((a - mean_r) ** 2 for a in rewards))
             * math.sqrt(sum((b - mean_ret) ** 2 for b in returns)))
    corr = cov / denom
    assert corr > 0.5, f"corr(reward, return | eligible) = {corr:.3f} muss > 0.5 sein."


# ── Ordnungsinvariante: eligible strikt über ineligible ──────────────────────────────────────
def test_order_invariant_eligible_above_ineligible():
    elig_rewards = [_reward(_eligible(6.0, r)) for r in (0.0118, 0.015, 0.0177)]

    def _ineligible(ret):
        return TournamentMetrics(
            oos_evaluated=True, oos_eligible=False, is_sortino_median=0.0,
            oos_sortino=0.01, oos_max_drawdown=0.05, oos_total_trades=30,
            win_count=0, fully_eligible_pairs=0, is_total_trades=100,
            oos_total_return=ret, oos_expectancy=-0.0005, oos_win_rate=0.05,
            oos_profit_factor=0.5,
        )
    # legacy_mean-Failure-Pfad (kein failure_reward_mode gesetzt) ⇒ strikt unter dem Feasible-Floor.
    inelig_rewards = [_reward(_ineligible(r)) for r in (-0.05, 0.0, 0.004)]
    assert min(elig_rewards) > max(inelig_rewards)


# ── Legacy-Migrationssicherheit: fehlt sortino_soft_scale ⇒ Hard-Clip (bit-identisch) ─────────
def test_missing_soft_scale_is_legacy_hard_clip():
    legacy = dict(WEIGHTS); legacy.pop("sortino_soft_scale"); legacy.pop("w_ret")
    # Setzen von max_drawdown auf 0, um die in Issue #578 eingeführte Drawdown-Soft-Penalty
    # zu vermeiden. Vorher war dd_excess immer 0, jetzt gibt es eine quadratic penalty.
    m = _eligible(10.0, 0.015)
    m.oos_max_drawdown = 0.0
    r = compute_reward(m, universe_size=1, weights=legacy, risk_dd_cap=0.3, tournament_cfg=CFG)
    # Hard-Clip: base == 5.0 (geklemmt), keine return-Komponente. overfit_gap=max(0,0-5)=0, dd-penalty=0.
    assert r == pytest.approx(5.0)


# ── Produktions-Config hat die Soft-Sättigung scharfgeschaltet ───────────────────────────────
def test_production_config_enables_soft_saturation():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg.get("sortino_soft_scale", 0) > 0
    assert cfg.get("w_ret", 0) > 0
