"""A4.3 — per-symbol reward path (reward_mode, param_pen, coverage drop).

Zero-hardcoding: every weight/margin is read from optimizer.json/tournament.json,
never a duplicated literal in the assert (HI-6). Pure arithmetic, no backtest.

Issue #559/#565: die Reward-Base ist weich gesättigt (base = c·asinh(sortino/c)) und die
Overfit-Strafe symmetrisch (|IS − OOS|); die Spec-Ableitungen unten spiegeln das (Living Spec).
"""

import json
import math
import pytest
from pathlib import Path

from automation.optimizer import parsing, reward, bounds


def _tournament(tmp_path, **agg):
    p = tmp_path / "t.json"
    p.write_text(
        json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": agg}), "utf-8"
    )
    return p


def _cfg():
    return json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))


def _base(cfg, sortino):
    """Issue #559 — weiche Sättigung, wenn sortino_soft_scale gesetzt, sonst Legacy-Hard-Clip."""
    ss = cfg.get("sortino_soft_scale")
    if ss:
        return float(ss) * math.asinh(sortino / float(ss))
    return max(-cfg["sortino_clip_abs"], min(cfg["sortino_clip_abs"], sortino))


def _apply_soft_scale_inline(value, scale):
    if scale is not None and scale > 0.0:
        return float(scale) * math.asinh(float(value) / float(scale))
    return float(value)


def _divergence(cfg, is_median, base):
    """Issue #565 / #575 — symmetrische Divergenz mit Skalenparität."""
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

    cap = cfg.get("penalty_relative_cap")
    if cap is not None:
        penalty = min(penalty, float(cap) * abs(base))

    return penalty


def _cap():
    return json.loads(Path("automation/config/tournament.json").read_text("utf-8"))[
        "max_drawdown"
    ]


def test_per_symbol_drops_coverage_and_applies_param_pen(tmp_path):
    cfg = _cfg()
    cap = _cap()
    p = _tournament(
        tmp_path,
        oos_evaluated=True,
        oos_eligible=True,
        win_count=1,
        median_is_sortino=2.0,
        oos_fold_sortinos=[1.0],
        # Issue #597 — realistischer OOS-Drawdown (nicht der 30 %-Gate-Cap), damit der auf
        # dd_reward_scale (0.03) normierte dd_penalty nicht katastrophal wird und den Reward floort.
        oos_metrics={"sortino_ratio": 1.0, "max_drawdown": 0.01},
    )
    m = parsing.parse_tournament(p)
    sampled = {"sma_period": 60, "cooldown_bars": 36}
    glob = {"sma_period": 20, "cooldown_bars": 12}
    b = bounds.extract_numeric_bounds("SmaCrossoverStrategy")
    base = _base(cfg, 1.0)
    pen = cfg["lambda_reg"] * bounds.normalized_param_distance(sampled, glob, b)
    # Ein einzelner Fold-Return ⇒ keine Dispersions-Strafe; oos_total_return=0 ⇒ kein Tie-Breaker.
    # Issue #597 — dd_penalty normiert auf dd_reward_scale (nicht mehr auf den Gate-Cap).
    dd = 0.01
    dd_scale = cfg.get("dd_reward_scale", cap)
    dd_penalty = (
        cfg["penalty_dd_weight"] * ((dd / dd_scale) ** 2) if dd_scale and dd_scale > 0 else 0.0
    )
    expected = base - _divergence(cfg, 2.0, base) - dd_penalty - pen
    # Issue #591 — der eligible Reward-Floor ist der ENTKOPPELTE evaluable_reward_floor.
    floor = cfg["evaluable_reward_floor"]
    got = reward.compute_reward(
        m,
        universe_size=1,
        sampled=sampled,
        global_params=glob,
        strategy="SmaCrossoverStrategy",
    )
    assert got == pytest.approx(max(expected, floor), rel=1e-9)
    assert pen > 0.0  # divergent params actually incur a shrinkage penalty


def test_per_symbol_no_param_pen_when_missing_inputs(tmp_path):
    p = _tournament(
        tmp_path,
        oos_evaluated=True,
        oos_eligible=True,
        win_count=1,
        median_is_sortino=1.0,
        oos_fold_sortinos=[1.0],
        oos_metrics={"sortino_ratio": 1.0, "max_drawdown": 0.0},
    )
    m = parsing.parse_tournament(p)
    # without sampled/global -> param_pen=0; reward equals base (minus 0)
    r = reward.compute_reward(m, universe_size=1)
    assert r > 0


def test_per_symbol_unevaluable_matches_global(tmp_path):
    p = _tournament(tmp_path, oos_evaluated=False, win_count=0)
    m = parsing.parse_tournament(p)
    # unevaluable path is shared and universe-independent
    assert reward.compute_reward(m, universe_size=1) == reward.compute_reward(
        m, universe_size=70
    )


def test_universe_gt_1_is_unchanged_coverage_path(tmp_path):
    cfg = _cfg()
    p = _tournament(
        tmp_path,
        oos_evaluated=True,
        oos_eligible=True,
        win_count=5,
        median_is_sortino=1.0,
        oos_fold_sortinos=[1.0],
        oos_metrics={"sortino_ratio": 1.0, "max_drawdown": 0.0},
    )
    m = parsing.parse_tournament(p)
    r = reward.compute_reward(m, universe_size=100)
    assert r >= 1.0 / 100 * cfg["bonus_coverage_weight"]  # coverage still contributes


def test_param_pen_ignored_in_coverage_path(tmp_path):
    """At universe_size>1 (reward_mode 'auto') sampled/global are ignored — coverage path stays."""
    p = _tournament(
        tmp_path,
        oos_evaluated=True,
        oos_eligible=True,
        win_count=5,
        median_is_sortino=1.0,
        oos_fold_sortinos=[1.0],
        oos_metrics={"sortino_ratio": 1.0, "max_drawdown": 0.0},
    )
    m = parsing.parse_tournament(p)
    r_plain = reward.compute_reward(m, universe_size=100)
    r_with_inputs = reward.compute_reward(
        m,
        universe_size=100,
        sampled={"sma_period": 60},
        global_params={"sma_period": 20},
        strategy="SmaCrossoverStrategy",
    )
    assert r_plain == r_with_inputs  # param_pen never applies on the coverage path


def test_reward_mode_per_symbol_forces_path_above_universe_1(tmp_path):
    """reward_mode='per_symbol' drops coverage even at universe_size>1 (answers the issue's Rückfrage)."""
    cfg = _cfg()
    weights = {**cfg, "reward_mode": "per_symbol"}
    p = _tournament(
        tmp_path,
        oos_evaluated=True,
        oos_eligible=True,
        win_count=5,
        median_is_sortino=1.0,
        oos_fold_sortinos=[1.0],
        oos_metrics={"sortino_ratio": 1.0, "max_drawdown": 0.0},
    )
    m = parsing.parse_tournament(p)
    cap = _cap()
    r = reward.compute_reward(m, universe_size=100, weights=weights, risk_dd_cap=cap)
    base = _base(cfg, 1.0)
    # no coverage term, no param_pen (no sampled/global); IS-median==1.0 ⇒ kleine symmetrische
    # Divergenz-Strafe (base < 1.0 durch Soft-Sättigung); ein Fold ⇒ keine Dispersion; return=0.
    expected = base - _divergence(cfg, 1.0, base)
    assert r == pytest.approx(expected, rel=1e-9)
