import pytest
from automation.optimizer.reward import compute_reward
from dataclasses import dataclass

@dataclass
class DummyMetrics:
    oos_evaluated: bool = True
    oos_eligible: bool = False
    oos_total_trades: int = 0
    oos_total_return: float = 0.0
    oos_sortino: float = 0.0
    oos_max_drawdown: float = 0.0
    oos_win_rate: float = 0.0
    oos_profit_factor: float | None = 0.0

    is_total_trades: int = 0
    is_best_total_return: float = 0.0
    is_best_win_rate: float = 0.0
    is_sortino_median: float = 0.0
    win_count: int = 0

def get_weights():
    return {
        "penalty_unevaluable_oos": -10.0,
        "unevaluable_shaping_span": 0.25,
        "evaluable_floor_epsilon": 0.001,
        "constraint_distance_penalty_weight": 0.25,
        "shaping_trade_target": 50,
        "sortino_clip_abs": 5.0,
        "penalty_overfit_weight": 0.5,
        "penalty_dd_weight": 8.0,
        "bonus_coverage_weight": 1.0,
    }

def get_tournament_cfg():
    # Issue #467/#468 — strikte OOS-Isolation: die Constraint-Distanz-Penalty liest ausschliesslich
    # OOS-gekeyte Schwellen (oos_min_*), niemals den IS-Fallback (min_*). Die Fixture muss daher die
    # OOS-Keys setzen, exakt wie die ausgelieferte tournament.json.
    return {
        "oos_min_trades": 3,
        "oos_min_total_return": 0.05,
        "oos_min_expectancy": 0.0,
        "oos_min_win_rate": 0.0,
        "max_drawdown": 0.2,
    }

def test_reward_no_inversion_property():
    weights = get_weights()
    cfg = get_tournament_cfg()

    unevaluable_ceiling = weights["penalty_unevaluable_oos"] + weights["unevaluable_shaping_span"]
    evaluable_floor = unevaluable_ceiling + weights["evaluable_floor_epsilon"]

    # 1. 0 Trades (Unevaluable)
    m_0_trades = DummyMetrics(
        oos_evaluated=False,
        oos_eligible=False,
        oos_total_trades=0,
        is_total_trades=0
    )
    r_0_trades = compute_reward(m_0_trades, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    # 2. Many Trades, Near Miss (Evaluated, not eligible)
    m_near_miss = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=False,
        oos_total_trades=242,
        oos_total_return=0.04,  # misses 0.05
        oos_sortino=1.0,
        oos_max_drawdown=0.1,
        oos_win_rate=0.5,
        is_total_trades=500
    )
    r_near_miss = compute_reward(m_near_miss, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    # 3. Many Trades, Far Miss (Evaluated, not eligible)
    m_far_miss = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=False,
        oos_total_trades=242,
        oos_total_return=-0.50,  # huge miss
        oos_sortino=-1.0,
        oos_max_drawdown=0.5,
        oos_win_rate=0.2,
        is_total_trades=500
    )
    r_far_miss = compute_reward(m_far_miss, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    # 4. Eligible (Evaluated, eligible)
    m_eligible = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=True,
        oos_total_trades=242,
        oos_total_return=0.1,
        oos_sortino=1.0,
        oos_max_drawdown=0.1,
        oos_win_rate=0.5,
        is_total_trades=500
    )
    r_eligible = compute_reward(m_eligible, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    # Assert 1: MUSS >= unevaluable_ceiling (-9.75) sein
    assert r_near_miss >= unevaluable_ceiling, f"r_near_miss={r_near_miss} < {unevaluable_ceiling}"
    assert r_far_miss >= unevaluable_ceiling, f"r_far_miss={r_far_miss} < {unevaluable_ceiling}"

    # Assert 2: Weak monotonic invariant against 0 trades
    # Ein evaluierter Trial darf nie schlechter bewertet werden als ein komplett unevaluierter (oder nur epsilon schlechter)
    assert r_near_miss >= r_0_trades - 1e-6, f"r_near_miss={r_near_miss} < r_0_trades={r_0_trades}"
    assert r_far_miss >= r_0_trades - 1e-6, f"r_far_miss={r_far_miss} < r_0_trades={r_0_trades}"

    # Assert 3: Anti-Gate-Gaming
    # Eligible MUST be > any failure
    assert r_eligible >= evaluable_floor, f"r_eligible={r_eligible} < {evaluable_floor}"
    assert r_eligible > r_near_miss, f"r_eligible={r_eligible} <= r_near_miss={r_near_miss}"
    assert r_eligible > r_far_miss, f"r_eligible={r_eligible} <= r_far_miss={r_far_miss}"

    # Assert 4: Gradient preservation
    assert r_near_miss > r_far_miss, f"r_near_miss={r_near_miss} <= r_far_miss={r_far_miss}"


def test_reward_gradient_does_not_saturate():
    weights = get_weights()
    cfg = get_tournament_cfg()

    # We want to test that a raw penalty of ~20 vs ~100 still yields a difference.
    # oos_min_total_return = 0.05
    # distance = (gap / target)^2
    # if gap = 0.45, target = 0.05 -> 9^2 = 81 -> raw_distance = 81 -> penalty = 81 * 0.25 = 20.25
    m_penalty_20 = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=False,
        oos_total_trades=242,
        oos_total_return=-0.40,  # gap = 0.45
        oos_sortino=1.0,
        oos_max_drawdown=0.1,
        oos_win_rate=0.5,
        is_total_trades=500
    )
    r_penalty_20 = compute_reward(m_penalty_20, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    # if gap = 1.0, target = 0.05 -> 20^2 = 400 -> penalty = 400 * 0.25 = 100
    m_penalty_100 = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=False,
        oos_total_trades=242,
        oos_total_return=-0.95, # gap = 1.0
        oos_sortino=1.0,
        oos_max_drawdown=0.1,
        oos_win_rate=0.5,
        is_total_trades=500
    )
    r_penalty_100 = compute_reward(m_penalty_100, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    assert r_penalty_20 > r_penalty_100, f"Gradient saturated! r_penalty_20={r_penalty_20}, r_penalty_100={r_penalty_100}"
