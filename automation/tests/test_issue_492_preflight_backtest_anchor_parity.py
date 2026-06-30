import datetime as dt
from automation.optimizer.sweep import compute_oos_window_start_ns
from automation.optimizer.trial_config import compute_walk_forward_window


_GATE_CFG = {
    "walk_forward": {
        "is_window_days": 365,
        "oos_window_days": 30,
        "splits": 12,
        "holdout_days": 60,
        "walk_forward_active": True,
    }
}


def test_preflight_backtest_boundary_parity_stale_catalog():
    """
    Test condition: Catalog is stale (catalog_newest_ns < current time).
    Assertion: Preflight OOS boundary matches trial generation OOS boundary.
    """
    now = dt.datetime(2026, 6, 25, 12, 0, tzinfo=dt.timezone.utc)
    # Stale catalog: 1 year in the past
    stale_catalog_dt = dt.datetime(2025, 6, 25, 12, 0, tzinfo=dt.timezone.utc)
    stale_catalog_ns = int(stale_catalog_dt.timestamp() * 1_000_000_000)

    # Preflight start_ns
    preflight_oos_start = compute_oos_window_start_ns(
        _GATE_CFG,
        now=now,
        catalog_newest_ns=stale_catalog_ns
    )

    # Trial generation start_ns
    trial_start, trial_end = compute_walk_forward_window(
        now=now,
        holdout_days=_GATE_CFG["walk_forward"]["holdout_days"],
        is_window_days=_GATE_CFG["walk_forward"]["is_window_days"],
        oos_window_days=_GATE_CFG["walk_forward"]["oos_window_days"],
        n_folds=_GATE_CFG["walk_forward"]["splits"],
        catalog_newest_ns=stale_catalog_ns
    )

    trial_start_ns = int(trial_start.timestamp() * 1_000_000_000)

    assert preflight_oos_start == trial_start_ns, (
        f"Mismatch: preflight {preflight_oos_start} != trial {trial_start_ns}"
    )


def test_preflight_backtest_boundary_parity_fresh_catalog():
    """
    Test condition: Catalog is fresh (now >= catalog_newest_ns).
    Assertion: Unaltered baseline behavior, identical boundaries.
    """
    now = dt.datetime(2026, 6, 25, 12, 0, tzinfo=dt.timezone.utc)
    # Fresh catalog: Same as now
    fresh_catalog_dt = now
    fresh_catalog_ns = int(fresh_catalog_dt.timestamp() * 1_000_000_000)

    # Preflight start_ns
    preflight_oos_start = compute_oos_window_start_ns(
        _GATE_CFG,
        now=now,
        catalog_newest_ns=fresh_catalog_ns
    )

    # Trial generation start_ns
    trial_start, trial_end = compute_walk_forward_window(
        now=now,
        holdout_days=_GATE_CFG["walk_forward"]["holdout_days"],
        is_window_days=_GATE_CFG["walk_forward"]["is_window_days"],
        oos_window_days=_GATE_CFG["walk_forward"]["oos_window_days"],
        n_folds=_GATE_CFG["walk_forward"]["splits"],
        catalog_newest_ns=fresh_catalog_ns
    )

    trial_start_ns = int(trial_start.timestamp() * 1_000_000_000)

    assert preflight_oos_start == trial_start_ns, (
        f"Mismatch: preflight {preflight_oos_start} != trial {trial_start_ns}"
    )
