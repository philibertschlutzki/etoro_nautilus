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


def data_reaches_oos_window(
    *,
    newest_ns: int | None,
    start_ns: int,
    is_window_days: int,
    recency_grace_days: float = 0.0,
) -> tuple[bool, float]:
    """Issue #449 (Pitfall #82) — reine OOS-Erreichbarkeits-Prüfung für das Sweep-Gate-1-Preflight.

    Gate 1 (a)-(c) prüft die Bar-ANZAHL, aber NICHT die AKTUALITÄT: ein Symbol kann ≥ required_bars
    besitzen und trotzdem ausschließlich Historie in der ersten Fensterhälfte haben — dann bleibt
    das OOS-Sub-Fenster leer und JEDER Trial kollabiert strukturell auf den Unevaluable-Floor
    (oos_total_trades=0), unabhängig von den Parametern. Diese Funktion prüft die notwendige
    Bedingung „der jüngste Tick erreicht die früheste OOS-Grenze".

    Die früheste OOS-Grenze ist ``start_ns + is_window_days`` (= split_oos_start bei fold=0, siehe
    extract_metrics). ``newest_ns`` muss diese Grenze (minus optionaler Karenz) erreichen.

    Gibt ``(ok, gap_days)`` zurück; ``gap_days`` > 0 = Abstand des jüngsten Ticks VOR der OOS-Grenze
    (nur aussagekräftig, wenn ok=False). ``newest_ns=None`` (unbekannt/gemockt) ⇒ fail-open (True),
    damit das Preflight nie strenger ist als die vorhandene Information erlaubt.
    """
    if newest_ns is None:
        return (True, 0.0)
    day_ns = 86400 * 1_000_000_000
    oos_window_start_ns = start_ns + int(is_window_days * day_ns)
    grace_ns = int(recency_grace_days * day_ns)
    gap_days = (oos_window_start_ns - newest_ns) / day_ns
    ok = newest_ns >= (oos_window_start_ns - grace_ns)
    return (ok, gap_days)
