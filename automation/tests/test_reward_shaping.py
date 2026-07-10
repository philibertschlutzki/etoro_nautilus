import pytest
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

W = {  # Test-Weights (DI)
    "penalty_unevaluable_oos": -4.0,
    "sortino_clip_abs": 3.0,
    "penalty_overfit_weight": 1.0,
    "penalty_dd_weight": 1.0,
    "bonus_coverage_weight": 0.5,
    "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 1e-3,
    "oos_min_trades": 20,  # falls Helper Weights-Override unterstützt; sonst tournament.json mocken
}


def _m(**kw):
    base = dict(
        oos_evaluated=False,
        oos_eligible=False,
        is_sortino_median=0.0,
        oos_sortino=None,
        oos_max_drawdown=0.0,
        oos_total_trades=0,
        win_count=0,
        fully_eligible_pairs=0,
        is_total_trades=0,
        hit_trade_cap=False,
    )
    base.update(kw)
    return TournamentMetrics(**base)


def test_unevaluable_monotonic_in_trades():
    low = compute_reward(
        _m(oos_total_trades=2), universe_size=70, weights=W, risk_dd_cap=0.3
    )
    high = compute_reward(
        _m(oos_total_trades=18), universe_size=70, weights=W, risk_dd_cap=0.3
    )
    assert high > low


def test_zero_trades_equals_floor_penalty():
    r = compute_reward(
        _m(oos_total_trades=0), universe_size=70, weights=W, risk_dd_cap=0.3
    )
    assert r == pytest.approx(W["penalty_unevaluable_oos"])


def test_unevaluable_capped_at_min_trades():
    a = compute_reward(
        _m(oos_total_trades=20), universe_size=70, weights=W, risk_dd_cap=0.3
    )
    b = compute_reward(
        _m(oos_total_trades=999), universe_size=70, weights=W, risk_dd_cap=0.3
    )
    assert a == pytest.approx(b)  # trade_progress clippt bei 1.0


def test_evaluable_ALWAYS_beats_unevaluable():
    worst_eval = _m(
        oos_evaluated=True,
        oos_eligible=True,
        oos_sortino=-W["sortino_clip_abs"],
        is_sortino_median=W["sortino_clip_abs"],
        oos_max_drawdown=0.99,
        oos_total_trades=20,
        win_count=0,
    )
    best_uneval = _m(oos_total_trades=10_000)  # maximal geshaped
    r_eval = compute_reward(worst_eval, universe_size=70, weights=W, risk_dd_cap=0.30)
    r_uneval = compute_reward(
        best_uneval, universe_size=70, weights=W, risk_dd_cap=0.30
    )
    assert r_eval > r_uneval  # HARTE Invariante


def test_holdout_not_referenced(monkeypatch):
    # compute_reward darf keine Holdout-Pfade/Dateien öffnen — reine Funktion auf TournamentMetrics + weights
    import builtins

    monkeypatch.setattr(
        builtins,
        "open",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein File-IO erlaubt")),
    )
    compute_reward(
        _m(oos_total_trades=5), universe_size=70, weights=W, risk_dd_cap=0.3
    )  # darf nicht werfen


def test_drawdown_soft_penalty_eligible_trials():
    """
    Issue #578: Drawdown-Penalty (Soft Penalty) must strictly enforce that for two
    eligible trials with equal return and sortino, the one with higher drawdown
    (but still below the max_drawdown cap) gets a lower reward.
    """
    weights = W.copy()
    # Give a noticeable weight to see the effect clearly
    weights["penalty_dd_weight"] = 1.0

    # Two identical trials, eligible, evaluated. The only difference is drawdown.
    # We set return and sortino explicitly so that base and tie breakers are identical.
    common_metrics = dict(
        oos_evaluated=True,
        oos_eligible=True,
        is_sortino_median=2.0,
        oos_sortino=2.0,
        oos_total_trades=50,
        win_count=20,
        oos_total_return=0.1,
    )

    cap = 0.3

    # Trial A has a smaller drawdown (0.1)
    trial_a = _m(**common_metrics, oos_max_drawdown=0.1)

    # Trial B has a higher drawdown (0.2), still below cap (0.3)
    trial_b = _m(**common_metrics, oos_max_drawdown=0.2)

    reward_a = compute_reward(
        trial_a, universe_size=70, weights=weights, risk_dd_cap=cap
    )
    reward_b = compute_reward(
        trial_b, universe_size=70, weights=weights, risk_dd_cap=cap
    )

    # 1. Assert order constraint
    assert (
        reward_a > reward_b
    ), "Höherer Drawdown muss zu einem geringeren Reward führen."

    # 2. Assert exact math
    penalty_a = weights["penalty_dd_weight"] * ((0.1 / cap) ** 2)
    penalty_b = weights["penalty_dd_weight"] * ((0.2 / cap) ** 2)
    expected_diff = penalty_b - penalty_a

    actual_diff = reward_a - reward_b

    import pytest

    assert actual_diff == pytest.approx(expected_diff)
