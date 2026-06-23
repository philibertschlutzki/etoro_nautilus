"""Gate 1 — data sufficiency (Ansatz 4 / A4.4).

The most important structural brake against "memorising the chart": a
(strategy, symbol) study may only start if the available history covers the whole
walk-forward window (IS + folds*OOS + holdout + buffer) AND a parameter-to-data
heuristic holds. Pure, I/O-free, fully injectable — the bar count is supplied by
the caller (the sweep adapter), never read from disk here.
"""


def required_bars(*, is_window_days: int, oos_window_days: int, splits: int,
                  holdout_days: int, buffer_days: int, bars_per_day: int = 24) -> int:
    """Minimum bar count for the entire window on 1h bars:
    ``(is + splits*oos + holdout + buffer) * bars_per_day``."""
    return int((is_window_days + splits * oos_window_days + holdout_days + buffer_days) * bars_per_day)


def is_symbol_tunable(symbol: str, n_params: int, *, available_bars: int,
                      config: dict, bars_per_day: int = 24) -> tuple[bool, str]:
    """Decide whether ``symbol`` has enough data to be safely tuned.

    Returns ``(ok, reason)`` where ``ok`` is True only if ALL of:
      (a) ``available_bars >= required_bars(... config['walk_forward'] + config['gate1_buffer_days'])``
      (b) ``available_bars / max(1, n_params) >= config['min_bars_per_param']``
      (c) ``oos_window_days * bars_per_day >= config['min_oos_bars_per_fold']``

    ``reason`` ∈ {'OK', 'INSUFFICIENT_HISTORY', 'PARAM_DATA_RATIO_TOO_LOW',
    'OOS_FOLD_TOO_SHORT'}. Thresholds come from ``config`` (zero-hardcoding, HI-6).
    ``available_bars`` is injected by the caller — this function performs NO I/O.
    """
    wf = config["walk_forward"]

    # (a) absolute history coverage of the full walk-forward corridor + buffer
    need = required_bars(
        is_window_days=wf["is_window_days"],
        oos_window_days=wf["oos_window_days"],
        splits=wf["splits"],
        holdout_days=wf["holdout_days"],
        buffer_days=config["gate1_buffer_days"],
        bars_per_day=bars_per_day,
    )
    if available_bars < need:
        return (False, "INSUFFICIENT_HISTORY")

    # (b) enough data per tuned parameter (anti-overfit ratio)
    if available_bars / max(1, n_params) < config["min_bars_per_param"]:
        return (False, "PARAM_DATA_RATIO_TOO_LOW")

    # (c) each OOS fold must itself be statistically meaningful
    if wf["oos_window_days"] * bars_per_day < config["min_oos_bars_per_fold"]:
        return (False, "OOS_FOLD_TOO_SHORT")

    return (True, "OK")
