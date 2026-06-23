import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.optimizer.parsing import TournamentMetrics




_oos_min_trades_cache: int | None = None

def _read_oos_min_trades() -> int:
    global _oos_min_trades_cache
    if _oos_min_trades_cache is not None:
        return _oos_min_trades_cache

    try:
        from automation.optimizer.trial_config import config_dir
        cfg_path = config_dir() / "tournament.json"
        if not cfg_path.exists():
            _oos_min_trades_cache = 3
            return 3
        with open(cfg_path, 'r', encoding='utf-8') as f:
            import json
            tournament_cfg = json.load(f)
            _oos_min_trades_cache = tournament_cfg.get("oos_min_trades", 3)
            return _oos_min_trades_cache
    except OSError:
        _oos_min_trades_cache = 3
        return 3
    except ValueError:
        _oos_min_trades_cache = 3
        return 3
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
        # Avoid IO if possible:
        oos_min_trades = None
        if weights is not None and "oos_min_trades" in weights:
            val = weights["oos_min_trades"]
            if val is not None:
                oos_min_trades = int(val)

        if oos_min_trades is None:
            oos_min_trades = _read_oos_min_trades()

        # OOS trade progress (existing signal).
        trade_progress = min(1.0, m.oos_total_trades / max(1, oos_min_trades))

        # ISSUE-OPT-375: while no symbol is IS-eligible, oos_total_trades is flat 0, so the
        # penalty is a flat plateau and TPE has no gradient toward the eligibility threshold.
        # Couple the shaping to IS activity (sum of IS trades across the universe) as well, so
        # "almost eligible" becomes distinguishable from "never eligible". shaping_trade_target
        # lives in optimizer.json (zero-hardcoding); if absent, behaviour is the legacy OOS-only path.
        progress = trade_progress
        shaping_trade_target = weights.get("shaping_trade_target")
        if shaping_trade_target:
            activity = min(1.0, m.is_total_trades / max(1, int(shaping_trade_target)))
            progress = max(progress, activity)

        # Floor invariant: progress ∈ [0, 1] ⇒ shaping ≤ unevaluable_shaping_span, hence every
        # unevaluable trial stays ≤ penalty + span, strictly below the evaluable floor below.
        shaping = weights["unevaluable_shaping_span"] * progress
        return penalty_unevaluable_oos + shaping

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

    floor = penalty_unevaluable_oos + weights["unevaluable_shaping_span"] + weights["evaluable_floor_epsilon"]
    return max(reward, floor)
