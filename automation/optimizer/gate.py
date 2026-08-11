"""Gate 1 — data sufficiency (Ansatz 4 / A4.4).

The most important structural brake against "memorising the chart": a
(strategy, symbol) study may only start if the available history covers the whole
walk-forward window (IS + folds*OOS + holdout + buffer) AND a parameter-to-data
heuristic holds. Pure, I/O-free, fully injectable — the bar count is supplied by
the caller (the sweep adapter), never read from disk here.
"""
import math


class InsufficientGeometryError(Exception):
    """Issue #531 — die REAL vorhandene Bar-Spanne deckt die Walk-Forward-Geometrie NICHT ab.

    Fehlercode ``REJECT_DATA_INSUFFICIENT_GEOMETRY`` (Error-Taxonomy, AGENTS.md §14). Wird
    geworfen, sobald ``actual_span_days < is_window + splits*oos_window + holdout``. Ersetzt das
    stille ``.loc``-Klemmen des letzten OOS-Folds/Holdouts (No-Clamping-Policy, Fail-Loud-Paradigma):
    ein verkürzter/leerer Fold verzerrt die Walk-Forward-Aggregation und darf NIE stillschweigend
    entstehen. Trägt die Diskrepanz (``actual``/``required``/``delta``) explizit, damit sie im
    Log/Telemetry sichtbar wird (``emit_gate1_rejection``)."""

    code = "REJECT_DATA_INSUFFICIENT_GEOMETRY"

    def __init__(self, *, actual: float, required: float, symbol: str | None = None,
                 code: str = "REJECT_DATA_INSUFFICIENT_GEOMETRY") -> None:
        self.actual = round(float(actual), 2)
        self.required = round(float(required), 2)
        self.delta = round(self.required - self.actual, 2)
        self.symbol = symbol
        self.code = code
        sym = f"{symbol}: " if symbol else ""
        super().__init__(
            f"{sym}{code} — verfügbare Bar-Spanne {self.actual:.1f} Tage < erforderliche "
            f"Walk-Forward-Geometrie {self.required:.1f} Tage (Defizit {self.delta:.1f} Tage). "
            f"Silent .loc-Klemmung des letzten OOS-Folds/Holdouts ist verboten (No-Clamping-Policy). "
            f"Reduziere die Geometrie ODER beschaffe mehr Katalog-Historie (Backfill)."
        )


def required_span_days(walk_forward_dict: dict) -> int:
    """Issue #531/#596 — die physisch erforderliche Kalendertag-Spanne der ROHDATEN für die volle
    Walk-Forward-Geometrie: ``is_window_days + embargo_period_days + splits*oos_window_days +
    holdout_days``.

    Rein, I/O-frei. Single Source of Truth der ``required_span``-Formel, NUMERISCH deckungsgleich mit
    ``compute_walk_forward_window`` + Holdout: ``(end − start).days + holdout_days`` = ``is + embargo +
    splits·oos + holdout`` (= 180 + 21 + 4·45 + 45 = 426 mit der Produktions-Geometrie).

    Issue #596 — das Embargo GEHÖRT in die Spanne. Der frühere Docstring behauptete das Gegenteil
    ("das Embargo verschiebt die OOS-Grenzen INNERHALB des Fensters"), das war sachlich falsch und
    widersprach Issue #548: ``compute_fold_boundaries`` startet OOS-Fold 0 bei ``is_end + embargo`` und
    ``compute_walk_forward_window`` reserviert das Embargo im Außen-Span. Ohne das Embargo prüfte
    ``assert_walk_forward_geometry`` gegen eine um exakt ``embargo_period_days`` zu KLEINE Zahl — ein
    Symbol mit 405–425 d Historie passierte den Guard, obwohl ``start`` vor den Datenanfang fiel und
    das IS-Fenster still verkürzt wurde (genau die No-Clamping-Verletzung, die #531 ausschliessen
    sollte). Bewusst OHNE ``gate1_buffer_days`` (der Puffer ist die Backfill-Schwelle, nicht der
    Fail-Loud-Floor)."""
    wf = walk_forward_dict or {}
    return int(
        wf.get("is_window_days", 0)
        + wf.get("embargo_period_days", 0)
        + wf.get("splits", 0) * wf.get("oos_window_days", 0)
        + wf.get("holdout_days", 0)
    )


def assert_walk_forward_geometry(*, actual_span_days: float, walk_forward_dict: dict,
                                 symbol: str | None = None) -> float:
    """Issue #531 — Fail-Loud-Gate gegen die REAL vorhandene Bar-Spanne (nicht gegen den
    Config-Wert ``data_history_days``).

    Wirft ``InsufficientGeometryError`` (Code ``REJECT_DATA_INSUFFICIENT_GEOMETRY``), sobald
    ``actual_span_days < required_span_days(walk_forward_dict)``. Rein, I/O-frei — der Aufrufer
    injiziert die real gemessene Spanne (``(df.index.max() - df.index.min()).days`` bzw.
    ``available_bars / bars_per_day``). Gibt ``required_span_days`` zurück, wenn die Geometrie passt.

    Das ist der einzige zulässige Ersatz für die frühere stille ``.loc``-Klemmung: greift die
    Geometrie über den Datenrand, MUSS hier ein deterministischer Abbruch erfolgen (No-Clamping)."""
    required = required_span_days(walk_forward_dict)
    if float(actual_span_days) < float(required):
        raise InsufficientGeometryError(actual=actual_span_days, required=required, symbol=symbol)
    return float(required)


def required_bars(*, is_window_days: int, oos_window_days: int, splits: int,
                  holdout_days: int, buffer_days: int, bars_per_day: int = 24,
                  embargo_period_days: int = 0) -> int:
    """Minimum bar count for the entire window on 1h bars:
    ``(is + embargo + splits*oos + holdout + buffer) * bars_per_day``.

    Issue #596 — konsistent zu ``required_span_days`` um ``embargo_period_days`` erweitert (der
    Embargo/Purge-Gap gehört in die geforderte Spanne; vgl. ``compute_walk_forward_window``/#548).
    Fehlt der Parameter (Default 0) ⇒ bit-identisch zum Alt-Verhalten."""
    return int((is_window_days + embargo_period_days + splits * oos_window_days
                + holdout_days + buffer_days) * bars_per_day)


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
    # Issue #596 — inkl. embargo_period_days (konsistent zu required_span_days / #548-Geometrie).
    need = required_bars(
        is_window_days=wf["is_window_days"],
        oos_window_days=wf["oos_window_days"],
        splits=wf["splits"],
        holdout_days=wf["holdout_days"],
        buffer_days=config["gate1_buffer_days"],
        bars_per_day=bars_per_day,
        embargo_period_days=wf.get("embargo_period_days", 0),
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
                            start_ns: int | None, walk_forward_dict: dict | None) -> tuple[bool, str, float | None]:
    """Issue #455 (Pitfall #82) — Gate-1-OOS-Erreichbarkeits-Vorprüfung (rein, I/O-frei).

    Ein (strategy, symbol)-Paar ist nur dann OOS-auswertbar, wenn der **jüngste** verfügbare Tick
    die **früheste** OOS-Sub-Fenster-Grenze (``start_ns + is_window_ns``, fold=0) erreicht. Liegt
    der jüngste Tick davor (dünner/staler H2-Katalog), erhält JEDES OOS-Sub-Fenster null Fills ⇒
    ``oos_total_trades=0`` strukturell, parameter-unabhängig, über alle Strategien. Solche Symbole
    sollen VOR dem Sweep übersprungen werden, statt 100 nutzlose Trials zu fahren.

    Returns ``(ok, reason, gap_days)``:
      * ``(True, "OK", 0.0)``                    — jüngster Tick erreicht die OOS-Grenze.
      * ``(False, "OOS_WINDOW_UNREACHABLE", gap)`` — jüngster Tick liegt vor der OOS-Grenze.
      * ``(True, "OOS_PREFLIGHT_UNAVAILABLE", None)`` — **fail-open**: fehlt die Tick-Telemetrie
        (``newest_ns is None``) ODER die Geometrie (``oos_window_start_ns is None``), wird NICHT
        übersprungen — das Preflight bleibt aus und das Verhalten ist bit-identisch zum Ist-Zustand.
    """
    if newest_ns is None or start_ns is None or walk_forward_dict is None:
        return (True, "OOS_PREFLIGHT_UNAVAILABLE", None)

    from automation.backtest_runner import compute_fold_boundaries
    fold_boundaries = compute_fold_boundaries(start_ns, walk_forward_dict)
    if not fold_boundaries:
        return (True, "OOS_PREFLIGHT_UNAVAILABLE", None)

    oos_window_start_ns = fold_boundaries[0][1]

    if int(newest_ns) >= int(oos_window_start_ns):
        return (True, "OK", 0.0)
    gap_days = round((oos_window_start_ns - int(newest_ns)) / (86400 * 1_000_000_000), 1)
    return (False, "OOS_WINDOW_UNREACHABLE", gap_days)


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


def evaluate_continuous_feasibility_distance(gate_deltas: dict, max_penalty: float = 50.0) -> float:
    """Issue #980 (#804) — Continuous Feasibility Distance Gradient (D_feas)."""
    if not gate_deltas:
        return 0.0
    sq_sum = 0.0
    for delta in gate_deltas.values():
        if delta is not None and float(delta) < 0.0:
            sq_sum += float(delta) ** 2
    dist = math.sqrt(sq_sum)
    return -min(float(max_penalty), dist)


def evaluate_soft_guard_penalty(
    shortfall: float = 0.0,
    scale: float = 1.0,
    n_trades: int | None = None,
    min_trades: int = 30,
    max_penalty: float = 50.0,
) -> float:
    """Issue #799 — Continuous soft penalty gradient for non-eligible trials."""
    if n_trades is not None:
        if n_trades >= min_trades:
            return 0.0
        diff = float(min_trades - n_trades)
        penalty = -float(max_penalty) * ((diff / float(min_trades)) ** 2)
        return float(max(-max_penalty, penalty))
    return float(max(0.0, shortfall)) * float(scale)




