import json
from pathlib import Path
from automation.optimizer.parsing import TournamentMetrics

def compute_reward(m: TournamentMetrics, universe_size: int,
                   weights: dict | None = None, risk_dd_cap: float | None = None) -> float:
    """
    Berechnet den skalaren Reward (Fitness) der Strategie. Zero-Hardcoding!

    Defaults:
    weights=None  ⇒ liest aus automation/config/optimizer.json
                    (penalty_overfit_weight, penalty_dd_weight, bonus_coverage_weight,
                     penalty_unevaluable_oos, sortino_clip_abs).
    risk_dd_cap=None ⇒ liest max_drawdown aus automation/config/tournament.json.

    Logik:
    1. Falls `not m.oos_evaluated` oder `m.oos_sortino is None`:
       return penalty_unevaluable_oos.
    2. base = clip(m.oos_sortino, -sortino_clip_abs, +sortino_clip_abs)
    3. overfit_gap = max(0, m.is_sortino_median - base)
    4. dd_excess = max(0, m.oos_max_drawdown - risk_dd_cap)
    5. coverage = m.win_count / max(1, universe_size)

    return: base - (overfit_gap * penalty_overfit_weight)
                 - (dd_excess * penalty_dd_weight)
                 + (coverage * bonus_coverage_weight)
    """

    if weights is None:
        optimizer_cfg_path = Path("automation/config/optimizer.json")
        with open(optimizer_cfg_path, 'r', encoding='utf-8') as f:
            weights = json.load(f)

    if risk_dd_cap is None:
        tournament_cfg_path = Path("automation/config/tournament.json")
        with open(tournament_cfg_path, 'r', encoding='utf-8') as f:
            tournament_cfg = json.load(f)
            risk_dd_cap = tournament_cfg.get("max_drawdown", 0.0)

    penalty_unevaluable_oos = weights.get("penalty_unevaluable_oos", 0.0)
    sortino_clip_abs = weights.get("sortino_clip_abs", 0.0)
    penalty_overfit_weight = weights.get("penalty_overfit_weight", 0.0)
    penalty_dd_weight = weights.get("penalty_dd_weight", 0.0)
    bonus_coverage_weight = weights.get("bonus_coverage_weight", 0.0)

    if not m.oos_evaluated or m.oos_sortino is None:
        return penalty_unevaluable_oos

    base = max(-sortino_clip_abs, min(sortino_clip_abs, m.oos_sortino))
    overfit_gap = max(0.0, m.is_sortino_median - base)
    dd_excess = max(0.0, m.oos_max_drawdown - risk_dd_cap)
    coverage = m.win_count / max(1, universe_size)

    reward_score = (
        base
        - (overfit_gap * penalty_overfit_weight)
        - (dd_excess * penalty_dd_weight)
        + (coverage * bonus_coverage_weight)
    )

    return reward_score
