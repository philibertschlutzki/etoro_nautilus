import pytest
import math
from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics

def get_base_metrics(oos_sortino, is_sortino_median=0.0, folds=None):
    if folds is None:
        folds = [oos_sortino] * 4

    m = TournamentMetrics(
        oos_evaluated=True,
        oos_eligible=True,
        is_sortino_median=is_sortino_median,
        oos_sortino=oos_sortino,
        oos_max_drawdown=0.1,
        oos_total_trades=50,
        win_count=1,
        fully_eligible_pairs=1,
        is_total_trades=200
    )
    m.oos_total_return = 0.05
    m.oos_fold_sortinos = folds
    m.oos_expectancy = 0.01
    m.oos_win_rate = 0.55
    m.oos_profit_factor = 1.2
    m.is_best_total_return = 0.1
    m.is_best_win_rate = 0.6
    m.hit_trade_cap = False
    return m

def get_base_weights():
    return {
        "penalty_overfit_weight": 0.5,
        "penalty_dd_weight": 8.0,
        "bonus_coverage_weight": 1.0,
        "penalty_unevaluable_oos": -10.0,
        "sortino_clip_abs": 5.0,
        "sortino_soft_scale": 5.0,  # scale c
        "w_ret": 0.0,
        "fold_dispersion_weight": 1.0,
        "penalty_relative_cap": 0.5,
        "overfit_divergence_mode": "symmetric",
        "evaluable_floor_epsilon": 0.001,
        "unevaluable_shaping_span": 0.25,
        "shaping_trade_target": 50,
        "shaping_return_target": 0.5,
        "shaping_winrate_target": 0.5
    }

def test_monotonicity_unit_test():
    """Isoliere die Fold-Dispersion. Simuliere einen konstanten Dispersionswert. Assert, dass bei steigendem Signal (base) der reward strikt monoton steigt."""
    weights = get_base_weights()
    weights["penalty_relative_cap"] = None # Disable cap to test purely monotonicity of dispersion

    folds_low = [2.0, 4.0]
    m_low = get_base_metrics(oos_sortino=3.0, is_sortino_median=3.0, folds=folds_low)
    reward_low = compute_reward(m_low, universe_size=2, weights=weights, risk_dd_cap=0.30)

    # Increase the base signal but keep the exact same absolute dispersion in raw sortino
    # Let's say we increase by 10
    folds_high = [12.0, 14.0]
    m_high = get_base_metrics(oos_sortino=13.0, is_sortino_median=13.0, folds=folds_high)
    reward_high = compute_reward(m_high, universe_size=2, weights=weights, risk_dd_cap=0.30)

    # Due to asinh scale parity, dispersion penalty is smaller for higher base, OR simply the base increase dominates.
    # The requirement is that reward strictly increases with the base signal.
    assert reward_high > reward_low

def test_cap_unit_test():
    """Assert, dass penalty <= penalty_relative_cap * abs(base) unter Extremwerten (+- 50 Raw Sortino) nicht gebrochen wird."""
    weights = get_base_weights()
    cap = 0.5
    weights["penalty_relative_cap"] = cap
    weights["fold_dispersion_weight"] = 1000.0 # Force extreme dispersion
    weights["penalty_overfit_weight"] = 1000.0 # Force extreme divergence

    folds_extreme = [0.0, 50.0]
    m = get_base_metrics(oos_sortino=25.0, is_sortino_median=50.0, folds=folds_extreme)

    # Run compute_reward
    reward = compute_reward(m, universe_size=2, weights=weights, risk_dd_cap=0.30)

    # Reconstruct the expected base
    c = weights["sortino_soft_scale"]
    base = c * math.asinh(m.oos_sortino / c)

    # Reconstruct what the theoretical penalty cap should be
    max_penalty_allowed = cap * abs(base)

    # Check what the actual penalities were (we can infer them if we know base, dd_excess, coverage)
    # reward = base - div_pen - dd_excess*w + cov*w - turnover_pen - fold_disp_pen + ret_tie
    # dd_excess = 0, turnover = 0, ret = 0
    # reward = base - div_pen + 0.5 - fold_disp_pen
    # Both div_pen and fold_disp_pen should have been capped at max_penalty_allowed
    # So max possible penalty is 2 * max_penalty_allowed

    expected_reward_if_capped = base - max_penalty_allowed + 0.5 - max_penalty_allowed

    # Allow small float tolerances
    assert reward == pytest.approx(expected_reward_if_capped, abs=1e-5), f"Reward {reward} not equal to {expected_reward_if_capped}"

def test_floor_guard_test():
    """Assert, dass eligible Trials nicht ausschließlich durch Dispersionsstrafen in den negativen Bereich gezwungen werden, wenn der base Edge ausreichend positiv ist."""
    weights = get_base_weights()
    weights["penalty_relative_cap"] = 0.9 # Even with a high cap
    weights["fold_dispersion_weight"] = 100.0

    m = get_base_metrics(oos_sortino=10.0, is_sortino_median=10.0, folds=[-50.0, 50.0])

    reward = compute_reward(m, universe_size=2, weights=weights, risk_dd_cap=0.30)

    # Eligible trial with base edge should not be negative
    # base is around 5 * asinh(2) = 7.21
    # 90% cap is around 6.49
    # reward = 7.21 - 6.49 + 0.5 > 0
    assert reward > 0.0
