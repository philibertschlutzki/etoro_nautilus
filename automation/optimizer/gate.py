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


def data_reaches_oos_window(newest_ns: int | None,
                            oos_window_start_ns: int | None) -> tuple[bool, str]:
    """Issue #455 (Pitfall #82) — Gate-1-OOS-Erreichbarkeits-Vorprüfung (rein, I/O-frei).

    Ein (strategy, symbol)-Paar ist nur dann OOS-auswertbar, wenn der **jüngste** verfügbare Tick
    die **früheste** OOS-Sub-Fenster-Grenze (``start_ns + is_window_ns``, fold=0) erreicht. Liegt
    der jüngste Tick davor (dünner/staler H2-Katalog), erhält JEDES OOS-Sub-Fenster null Fills ⇒
    ``oos_total_trades=0`` strukturell, parameter-unabhängig, über alle Strategien. Solche Symbole
    sollen VOR dem Sweep übersprungen werden, statt 100 nutzlose Trials zu fahren.

    Returns ``(ok, reason)``:
      * ``(True, "OK")``                    — jüngster Tick erreicht die OOS-Grenze.
      * ``(False, "OOS_WINDOW_UNREACHABLE")`` — jüngster Tick liegt vor der OOS-Grenze.
      * ``(True, "OOS_PREFLIGHT_UNAVAILABLE")`` — **fail-open**: fehlt die Tick-Telemetrie
        (``newest_ns is None``) ODER die Geometrie (``oos_window_start_ns is None``), wird NICHT
        übersprungen — das Preflight bleibt aus und das Verhalten ist bit-identisch zum Ist-Zustand.
    """
    if newest_ns is None or oos_window_start_ns is None:
        return (True, "OOS_PREFLIGHT_UNAVAILABLE")
    if int(newest_ns) >= int(oos_window_start_ns):
        return (True, "OK")
    return (False, "OOS_WINDOW_UNREACHABLE")


def data_reaches_holdout_window(newest_ns: int | None,
                                holdout_start_ns: int | None) -> tuple[bool, str]:
    """Issue #462 — Gate-3-Holdout-Erreichbarkeits-Vorprüfung (rein, I/O-frei).

    Returns ``(ok, reason)``:
      * ``(True, "OK")``                    — jüngster Tick erreicht die Holdout-Grenze.
      * ``(False, "HOLDOUT_WINDOW_UNREACHABLE")`` — jüngster Tick liegt vor der Holdout-Grenze.
      * ``(True, "HOLDOUT_PREFLIGHT_UNAVAILABLE")`` — fail-open bei fehlender Geometrie/Telemetrie.
    """
    if newest_ns is None or holdout_start_ns is None:
        return (True, "HOLDOUT_PREFLIGHT_UNAVAILABLE")
    if int(newest_ns) >= int(holdout_start_ns):
        return (True, "OK")
    return (False, "HOLDOUT_WINDOW_UNREACHABLE")
