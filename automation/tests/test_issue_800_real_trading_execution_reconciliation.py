from automation.momentum_ls_allocator import validate_and_reconcile_live_execution


def test_issue_800_spread_guard_suppression():
    # current_spread = 0.50, atr_14 = 2.0 -> max_spread = 0.05 * 2.0 = 0.10 -> spread > max_spread -> 0.0
    size = validate_and_reconcile_live_execution(
        target_size=100.0,
        current_spread=0.50,
        atr_14=2.0,
        broker_position=0.0,
        strategy_position=0.0,
        alpha_spread_tolerance=0.05
    )
    assert size == 0.0


def test_issue_800_state_reconciliation_delta():
    # Discrepancy between strategy (10.0) and broker (7.0) -> returns delta = 3.0
    delta = validate_and_reconcile_live_execution(
        target_size=100.0,
        current_spread=0.02,
        atr_14=2.0,
        broker_position=7.0,
        strategy_position=10.0,
        alpha_spread_tolerance=0.05
    )
    assert delta == 3.0


def test_issue_800_slippage_dampened_sizing():
    # Normal execution with small spread
    size = validate_and_reconcile_live_execution(
        target_size=100.0,
        current_spread=0.02,
        atr_14=2.0,
        broker_position=5.0,
        strategy_position=5.0,
        alpha_spread_tolerance=0.05
    )
    # max_spread = 0.10, dampening = 1.0 - (0.02 / 0.10) = 0.80 -> 80.0
    assert abs(size - 80.0) < 1e-4
