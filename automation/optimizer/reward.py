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
        from automation.optimizer.trial_config import config_dir
        cfg_path = config_dir() / "optimizer.json"
        with open(cfg_path, 'r', encoding='utf-8') as f:
            weights = json.load(f)

    if risk_dd_cap is None:
        from automation.optimizer.trial_config import config_dir
        cfg_path = config_dir() / "tournament.json"
        with open(cfg_path, 'r', encoding='utf-8') as f:
            tournament_cfg = json.load(f)
            risk_dd_cap = tournament_cfg["max_drawdown"]

    penalty_unevaluable_oos = weights["penalty_unevaluable_oos"]

    if not m.oos_evaluated or m.oos_sortino is None:
        return penalty_unevaluable_oos

    sortino_clip_abs = weights["sortino_clip_abs"]
    base = max(-sortino_clip_abs, min(sortino_clip_abs, m.oos_sortino))

    penalty_overfit_weight = weights["penalty_overfit_weight"]
    penalty_dd_weight = weights["penalty_dd_weight"]
    bonus_coverage_weight = weights["bonus_coverage_weight"]

    overfit_gap = max(0.0, m.is_sortino_median - base)
    dd_excess = max(0.0, m.oos_max_drawdown - risk_dd_cap)

    coverage = m.win_count / max(1, universe_size)

    reward = (base
              - overfit_gap * penalty_overfit_weight
              - dd_excess * penalty_dd_weight
              + coverage * bonus_coverage_weight)

    return reward
