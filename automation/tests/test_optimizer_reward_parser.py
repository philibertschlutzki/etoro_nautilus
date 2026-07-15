import json
import math
import statistics
import pytest
from pathlib import Path
from automation.optimizer import parsing, reward


def _base(cfg, sortino):
    """Issue #559 — weiche Sättigung (asinh), sonst Legacy-Hard-Clip."""
    ss = cfg.get("sortino_soft_scale")
    if ss:
        return float(ss) * math.asinh(sortino / float(ss))
    return max(-cfg["sortino_clip_abs"], min(cfg["sortino_clip_abs"], sortino))


def _apply_soft_scale_inline(value, scale):
    if scale is not None and scale > 0.0:
        return float(scale) * math.asinh(float(value) / float(scale))
    return float(value)


def _divergence(cfg, is_median, base):
    """Issue #565 / #575 — symmetrische Divergenz mit Skalenparität und Capping."""
    soft_scale = cfg.get("sortino_soft_scale")
    is_sortino_val = _apply_soft_scale_inline(is_median, soft_scale)

    if cfg.get("overfit_divergence_mode") == "symmetric":
        diff = is_sortino_val - base
        if diff >= 0.0:
            penalty = cfg["penalty_overfit_weight"] * diff
        else:
            penalty = cfg.get(
                "overfit_oos_luck_weight", cfg["penalty_overfit_weight"]
            ) * (-diff)
    else:
        penalty = cfg["penalty_overfit_weight"] * max(0.0, is_sortino_val - base)

    # Issue #591 — der relative Cap bindet an die positive Skalenkonstante soft_scale (Legacy-Fallback
    # sortino_clip_abs), NICHT an |base|.
    cap = cfg.get("penalty_relative_cap")
    if cap is not None:
        cap_scale = soft_scale if soft_scale else cfg["sortino_clip_abs"]
        penalty = min(penalty, float(cap) * float(cap_scale))

    return penalty


def _write_tournament(tmp_path, **agg):
    data = {"fully_eligible_pairs": 1, "aggregate_winner": agg}
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(data), "utf-8")
    return p


def test_parser_uses_pooled_sortino_not_fold_median(tmp_path):
    # Issue #589 — der kanonische OOS-Sortino ist der GEPOOLTE oos_metrics["sortino_ratio"] (kohärent
    # mit total_return), NICHT der Median der oos_fold_sortinos (der einen katastrophalen Fold
    # maskierte). Gate und Reward lesen damit exakt denselben Wert.
    p = _write_tournament(
        tmp_path,
        oos_evaluated=True,
        oos_eligible=True,
        win_count=3,
        median_is_sortino=2.0,
        oos_fold_sortinos=[1.0, 3.0, 2.0],
        oos_metrics={"sortino_ratio": 9.9, "max_drawdown": 0.1},
    )
    m = parsing.parse_tournament(p)
    assert m.oos_sortino == 9.9  # pooled, nicht median([1.0, 3.0, 2.0])==2.0


def test_reward_uses_config_weights(tmp_path):
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    cap = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))[
        "max_drawdown"
    ]

    # Issue #597 — realistischer OOS-Drawdown (dd_penalty normiert auf dd_reward_scale, nicht auf
    # den Gate-Cap; ein 40 %-DD würde den Reward katastrophal floorten).
    dd = 0.02
    p = _write_tournament(
        tmp_path,
        oos_evaluated=True,
        oos_eligible=True,
        win_count=5,
        median_is_sortino=3.0,
        oos_fold_sortinos=[1.0],
        oos_metrics={"sortino_ratio": 1.0, "max_drawdown": dd},
    )
    m = parsing.parse_tournament(p)

    base = _base(cfg, 1.0)
    # Ein einzelner Fold-Sortino ⇒ keine Dispersions-Strafe; oos_total_return=0 ⇒ kein Tie-Breaker.
    # Issue #597 — dd_penalty normiert auf dd_reward_scale.
    dd_scale = cfg.get("dd_reward_scale", cap)
    expected = (
        base
        - _divergence(cfg, 3.0, base)
        - cfg["penalty_dd_weight"] * ((dd / dd_scale) ** 2)
        + (5 / 100) * cfg["bonus_coverage_weight"]
    )

    assert reward.compute_reward(m, universe_size=100) == __import__("pytest").approx(
        expected, rel=1e-9
    )

