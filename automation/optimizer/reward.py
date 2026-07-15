import json
import math
import statistics
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.optimizer.parsing import TournamentMetrics

def _softplus(z: float) -> float:
    """``ln(1 + e^z)`` — überall streng monoton, C∞, numerisch stabil (kein Overflow für großes z)."""
    if z > 30.0:
        return z
    if z < -30.0:
        return math.exp(z)
    return math.log1p(math.exp(z))

def _apply_soft_scale(value: float, scale: float | None) -> float:
    """Wendelt die weiche asinh-Kompression an, wenn eine Scale vorliegt."""
    if scale is not None and scale > 0.0:
        c = float(scale)
        return c * math.asinh(float(value) / c)
    return float(value)

def _dd_penalty(m: "TournamentMetrics", weights: dict, risk_dd_cap: float | None) -> float:
    """Issue #578/#597 — progressive Drawdown-Strafe ``penalty_dd_weight·(oos_max_drawdown/scale)^2``."""
    scale = weights.get("dd_reward_scale")
    if scale is None:
        scale = risk_dd_cap
    if scale and float(scale) > 0.0:
        return float(weights["penalty_dd_weight"]) * ((m.oos_max_drawdown / float(scale)) ** 2)
    return 0.0

_oos_min_trades_cache: int | None = None
_tournament_cfg_cache: dict | None = None

def _read_tournament_cfg() -> dict:
    global _tournament_cfg_cache
    if _tournament_cfg_cache is not None:
        return _tournament_cfg_cache
    try:
        from automation.optimizer.trial_config import config_dir
        cfg_path = config_dir() / "tournament.json"
        if not cfg_path.exists():
            _tournament_cfg_cache = {}
            return {}
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            _tournament_cfg_cache = data
            return data
    except OSError:
        _tournament_cfg_cache = {}
        return {}
    except ValueError:
        _tournament_cfg_cache = {}
        return {}

def _read_oos_min_trades() -> int:
    global _oos_min_trades_cache
    if _oos_min_trades_cache is not None:
        return _oos_min_trades_cache
    tournament_cfg = _read_tournament_cfg()
    _oos_min_trades_cache = tournament_cfg.get("oos_min_trades", 3)
    return _oos_min_trades_cache

def _cfg_value(weights: dict, tournament_cfg: dict | None, key: str, default=None):
    if key in weights:
        return weights[key]
    if tournament_cfg and key in tournament_cfg:
        return tournament_cfg[key]
    return default

def compute_reward(
    m: "TournamentMetrics",
    universe_size: int,
    weights: dict | None = None,
    risk_dd_cap: float | None = None,
    *,
    sampled: dict | None = None,
    global_params: dict | None = None,
    strategy: str | None = None,
    tournament_cfg: dict | None = None,
    holdout: bool = False,
    return_terms: bool = False,
) -> float | tuple:
    """Berechnet ein stetiges Qualitätsziel über ALLE Trials (Issue #629)."""
    loaded_tournament_cfg = tournament_cfg

    if weights is None:
        from automation.optimizer.trial_config import config_dir
        cfg_path = config_dir() / "optimizer.json"
        with open(cfg_path, "r", encoding="utf-8") as f:
            weights = json.load(f)
        loaded_tournament_cfg = _read_tournament_cfg()

    if risk_dd_cap is None:
        if loaded_tournament_cfg is None:
            loaded_tournament_cfg = _read_tournament_cfg()
        risk_dd_cap = loaded_tournament_cfg["max_drawdown"]

    reward_mode_config = weights.get("reward_mode", "auto") if weights else "auto"
    
    # Pareto path
    if reward_mode_config == "pareto":
        res = (
            float(m.oos_total_return),
            float(m.oos_expectancy),
            float(m.oos_win_rate),
            float(m.oos_sortino if m.oos_sortino is not None else 0.0),
            float(m.oos_max_drawdown),
            float(m.oos_total_trades),
        )
        if return_terms:
            return res, {
                "branch": "pareto",
                "base": 0.0,
                "divergence": 0.0,
                "divergence_at_cap": False,
                "dd_penalty": 0.0,
                "param_pen": 0.0,
                "turnover": 0.0,
                "fold_dispersion": 0.0,
                "tie_breaker": 0.0,
                "floor_clamped": False
            }
        return res

    # Branch Tracking for Telemetry
    base_source = getattr(m, "oos_sortino", None)
    if (
        getattr(m, "oos_evaluated", False)
        and getattr(m, "oos_eligible", False)
        and base_source is None
        and weights.get("oos_sortino_fallback") == "total_return"
    ):
        base_source = getattr(m, "oos_total_return", 0.0)

    if not getattr(m, "oos_evaluated", False) or base_source is None:
        branch = "unevaluable"
    elif not getattr(m, "oos_eligible", False):
        branch = "failure"
    elif universe_size == 1 or weights.get("reward_mode", "auto") == "per_symbol":
        branch = "per_symbol"
    else:
        branch = "eligible"

    # Base Calculation
    soft_scale = None
    soft_scale_val = weights.get("sortino_soft_scale")
    if soft_scale_val is not None and float(soft_scale_val) > 0.0:
        soft_scale = float(soft_scale_val)

    psr_base_active = getattr(m, "oos_psr", None) is not None
    if psr_base_active:
        base = float(m.oos_psr)
    else:
        if base_source is None:
            base_source_val = 0.0
        else:
            base_source_val = float(base_source)
            
        if soft_scale is not None:
            base = _apply_soft_scale(base_source_val, soft_scale)
        else:
            sortino_clip_abs = float(weights.get("sortino_clip_abs", 5.0))
            base = max(-sortino_clip_abs, min(sortino_clip_abs, base_source_val))

    penalty_overfit_weight = float(weights.get("penalty_overfit_weight", 0.0))
    penalty_dd_weight = float(weights.get("penalty_dd_weight", 0.0))
    bonus_coverage_weight = float(weights.get("bonus_coverage_weight", 0.0))
    penalty_turnover_weight = float(weights.get("penalty_turnover_weight", 0.0))
    penalty_relative_cap = weights.get("penalty_relative_cap")

    cap_scale = soft_scale if soft_scale is not None else float(weights.get("sortino_clip_abs", 5.0))

    divergence_at_cap = False
    if holdout or psr_base_active:
        divergence_penalty = 0.0
    else:
        is_source = (getattr(m, "is_sortino_pooled", None) if getattr(m, "is_sortino_pooled", None) is not None
                     else getattr(m, "is_sortino_median", None))
        if is_source is None:
            raise ValueError(
                "compute_reward: is_sortino_pooled/is_sortino_median is None ohne holdout=True — ein "
                "Platzhalter darf nie in einen Reward-Ausdruck fliessen (#594/#613). Holdout-Rewards "
                "mit holdout=True aufrufen."
            )
        is_sortino_val = float(is_source)
        if soft_scale is not None:
            is_sortino_val = _apply_soft_scale(is_sortino_val, soft_scale)

        overfit_gap = max(0.0, is_sortino_val - base)
        divergence_mode = weights.get("overfit_divergence_mode")
        if divergence_mode == "symmetric":
            diff = is_sortino_val - base
            if diff >= 0.0:
                divergence_penalty = penalty_overfit_weight * diff
            else:
                oos_luck_w = float(
                    weights.get("overfit_oos_luck_weight", penalty_overfit_weight)
                )
                divergence_penalty = oos_luck_w * (-diff)
        else:
            divergence_penalty = penalty_overfit_weight * overfit_gap

        if penalty_relative_cap is not None:
            cap_val = float(penalty_relative_cap) * cap_scale
            if divergence_penalty >= cap_val:
                divergence_at_cap = True
            divergence_penalty = min(
                divergence_penalty, cap_val
            )

    dd_penalty = _dd_penalty(m, weights, risk_dd_cap)

    oos_total_trades = getattr(m, "oos_total_trades", 0) or 0
    turnover_penalty = float(oos_total_trades) * penalty_turnover_weight

    fold_dispersion_penalty = 0.0
    w_disp = weights.get("fold_dispersion_weight")
    fold_returns = list(getattr(m, "oos_fold_returns", None) or [])
    n_total = int(getattr(m, "oos_folds_total", 0) or 0)
    if w_disp and not holdout and n_total >= 2:
        n_valid = len(fold_returns)
        base_disp = statistics.pstdev(fold_returns) if n_valid >= 2 else 0.0
        _fds = weights.get("fold_dispersion_scale")
        fold_dispersion_scale = float(_fds) if _fds and float(_fds) > 0.0 else None
        norm_disp = (base_disp / fold_dispersion_scale) if fold_dispersion_scale else base_disp
        if n_valid < n_total:
            miss_scale = float(weights.get("missing_fold_penalty_scale", 0.0))
            frac_missing = (n_total - n_valid) / n_total
            fold_dispersion_penalty = float(w_disp) * (norm_disp + miss_scale * frac_missing)
        else:
            fold_dispersion_penalty = float(w_disp) * norm_disp
        if penalty_relative_cap is not None:
            fold_dispersion_penalty = min(
                fold_dispersion_penalty, float(penalty_relative_cap) * cap_scale
            )

    w_ret = float(weights.get("w_ret", 0.0))
    oos_total_return = getattr(m, "oos_total_return", 0.0) or 0.0
    return_tie_breaker = w_ret * float(oos_total_return)

    if branch == "per_symbol":
        param_pen = 0.0
        if sampled and global_params and strategy:
            from automation.optimizer import bounds
            b = bounds.extract_numeric_bounds(strategy)
            param_pen = float(weights.get("lambda_reg", 0.0)) * bounds.normalized_param_distance(
                sampled, global_params, b
            )
        reward = (
            base
            - divergence_penalty
            - dd_penalty
            - param_pen
            - turnover_penalty
            - fold_dispersion_penalty
            + return_tie_breaker
        )
        if return_terms:
            return reward, {
                "branch": branch,
                "base": base,
                "divergence": divergence_penalty,
                "divergence_at_cap": divergence_at_cap,
                "dd_penalty": dd_penalty,
                "param_pen": param_pen,
                "turnover": turnover_penalty,
                "fold_dispersion": fold_dispersion_penalty,
                "tie_breaker": return_tie_breaker,
                "floor_clamped": False
            }
        return reward

    # Coverage path (universe_size > 1)
    win_count = getattr(m, "win_count", 0) or 0
    coverage = float(win_count) / max(1, universe_size)
    coverage_bonus = coverage * bonus_coverage_weight
    reward = (
        base
        - divergence_penalty
        - dd_penalty
        + coverage_bonus
        - turnover_penalty
        - fold_dispersion_penalty
        + return_tie_breaker
    )
    if return_terms:
        return reward, {
            "branch": branch,
            "base": base,
            "divergence": divergence_penalty,
            "divergence_at_cap": divergence_at_cap,
            "dd_penalty": dd_penalty,
            "param_pen": 0.0,
            "turnover": turnover_penalty,
            "fold_dispersion": fold_dispersion_penalty,
            "tie_breaker": return_tie_breaker,
            "floor_clamped": False
        }
    return reward
