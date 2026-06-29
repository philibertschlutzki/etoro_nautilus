import pytest
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

W = {
    "penalty_unevaluable_oos": -10.0,
    "sortino_clip_abs": 5.0,
    "penalty_overfit_weight": 0.5,
    "penalty_dd_weight": 8.0,
    "bonus_coverage_weight": 1.0,
    "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001,
    "shaping_trade_target": 50,
    "per_symbol_shaping_trade_target": 400,
    "shaping_return_target": 0.5,
    "shaping_winrate_target": 0.5,
    "oos_min_trades": 3,
    "lambda_reg": 0.25,
}

def _m(**kw):
    base = dict(oos_evaluated=False, oos_eligible=False, is_sortino_median=0.0,
                oos_sortino=None, oos_max_drawdown=0.0, oos_total_trades=0,
                win_count=0, fully_eligible_pairs=0, is_total_trades=0, is_max_trades=0,
                oos_total_return=0.0, is_best_total_return=0.0, is_best_win_rate=0.0)
    base.update(kw)
    return TournamentMetrics(**base)

def test_issue_472_shaping_not_overtrading_monotone():
    trial_low = _m(is_total_trades=50, is_best_total_return=0.0, is_best_win_rate=0.0)
    trial_high = _m(is_total_trades=200, is_best_total_return=0.0, is_best_win_rate=0.0)
    r_low = compute_reward(trial_low, universe_size=1, weights=W, risk_dd_cap=0.3)
    r_high = compute_reward(trial_high, universe_size=1, weights=W, risk_dd_cap=0.3)

    assert not (r_high > r_low), f"r_high={r_high}, r_low={r_low} (r_high must not be strictly greater)"

if __name__ == "__main__":
    test_issue_472_shaping_not_overtrading_monotone()
