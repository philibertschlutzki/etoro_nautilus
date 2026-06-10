import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.optimizer.parsing import TournamentMetrics

def compute_reward(m: "TournamentMetrics", universe_size: int,
                   weights: dict | None = None, risk_dd_cap: float | None = None) -> float:
    """weights=None  ⇒ aus optimizer.json (penalty_overfit_weight, penalty_dd_weight,
                        bonus_coverage_weight, penalty_unevaluable_oos, sortino_clip_abs).
       risk_dd_cap=None ⇒ aus tournament.json (max_drawdown).
       Falls not m.oos_evaluated oder m.oos_sortino is None: return penalty_unevaluable_oos.
       base = clip(oos_sortino, -sortino_clip_abs, +sortino_clip_abs)
       overfit_gap = max(0, is_sortino_median - base); dd_excess = max(0, oos_max_drawdown - risk_dd_cap)
       coverage = win_count / max(1, universe_size)
       return base - overfit_gap*penalty_overfit_weight - dd_excess*penalty_dd_weight
              + coverage*bonus_coverage_weight"""

    if weights is None:
        cfg_path = Path("automation/config/optimizer.json")
        with open(cfg_path, 'r', encoding='utf-8') as f:
            weights = json.load(f)

    if risk_dd_cap is None:
        cfg_path = Path("automation/config/tournament.json")
        with open(cfg_path, 'r', encoding='utf-8') as f:
            tournament_cfg = json.load(f)
            risk_dd_cap = tournament_cfg.get("max_drawdown", 0.3)

    penalty_unevaluable_oos = weights.get("penalty_unevaluable_oos", -10.0)

    if not m.oos_evaluated or m.oos_sortino is None:
        return penalty_unevaluable_oos

    sortino_clip_abs = weights.get("sortino_clip_abs", 5.0)
    base = max(-sortino_clip_abs, min(sortino_clip_abs, m.oos_sortino))

    penalty_overfit_weight = weights.get("penalty_overfit_weight", 0.5)
    penalty_dd_weight = weights.get("penalty_dd_weight", 8.0)
    bonus_coverage_weight = weights.get("bonus_coverage_weight", 1.0)

    overfit_gap = max(0.0, m.is_sortino_median - base)
    dd_excess = max(0.0, m.oos_max_drawdown - risk_dd_cap)

    coverage = m.win_count / max(1, universe_size)

    reward = (base
              - overfit_gap * penalty_overfit_weight
              - dd_excess * penalty_dd_weight
              + coverage * bonus_coverage_weight)

    return reward
