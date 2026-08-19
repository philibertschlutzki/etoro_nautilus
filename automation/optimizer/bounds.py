"""Declarative numeric search-space bounds extractor (Ansatz 4 / A4.0).

The per-symbol reward regularisation (``param_pen``, A4.3) normalises each tuned
parameter against its search-space bounds onto ``[0, 1]``. Those bounds are only
implicitly encoded in the ``trial.suggest_*`` calls inside ``spaces.sample_params``.
Instead of duplicating them (DRY violation), this module extracts them *from*
``spaces.sample_params`` itself via introspection over a recording trial double —
a single source of truth, behaviour-neutral (``spaces.py`` is never modified).
"""
from typing import Any

from automation.optimizer import spaces


class _RecordingTrial:
    """Minimal trial double: records the ``(low, high)`` of every numeric suggest
    call and returns a deterministic value (``low``) so ``sample_params`` runs
    side-effect-free to the end. ``suggest_categorical`` is recorded but NOT kept
    as a numeric bound (categorical/boolean switches have no metric distance)."""

    def __init__(self) -> None:
        self.numeric: dict[str, tuple[float, float]] = {}
        self.categorical: dict[str, list[Any]] = {}

    def suggest_int(self, name: str, low: int, high: int, *a, **k) -> int:
        self.numeric[name] = (low, high)
        return low

    def suggest_float(self, name: str, low: float, high: float, *a, **k) -> float:
        self.numeric[name] = (low, high)
        return low

    def suggest_categorical(self, name: str, choices: list, *a, **k):
        self.categorical[name] = list(choices)
        return choices[0]


def extract_numeric_bounds(strategy: str) -> dict[str, tuple[float, float]]:
    """Return ``{param: (low, high)}`` for every numeric ``suggest_*`` parameter of
    ``strategy``. Derived parameters (e.g. ``macd_slow = fast + gap``) and categorical
    ones (e.g. ``require_vwap_confirmation``) are intentionally excluded: derived
    values have no own suggest bounds, and categorical/boolean switches have no
    metric distance. Raises ``ValueError`` for an unknown strategy (propagated from
    ``spaces.sample_params``)."""
    trial = _RecordingTrial()
    spaces.sample_params(strategy, trial)
    return dict(trial.numeric)


def normalized_param_distance(sampled: dict, reference: dict,
                              bounds: dict[str, tuple[float, float]]) -> float:
    """Mean squared, ``[0, 1]``-normalised deviation over all keys present in
    ``bounds`` AND ``sampled`` AND ``reference``.

    Per key: ``span = (hi - lo) or 1.0``; ``a = (sampled[k] - lo) / span``;
    ``b = (reference[k] - lo) / span``; contribution ``(a - b) ** 2``. Returns the
    mean over the common keys, or ``0.0`` if there are none."""
    total = 0.0
    count = 0
    for key, (lo, hi) in bounds.items():
        if key not in sampled or key not in reference:
            continue
        span = (hi - lo) or 1.0
        a = (sampled[key] - lo) / span
        b = (reference[key] - lo) / span
        total += (a - b) ** 2
        count += 1
    return total / count if count else 0.0


def theoretical_max_oos_trades(strategy: str, *, walk_forward: dict,
                               bars_per_day: int = 24) -> int | None:
    """Issue #762 — obere Schranke der ueber die GESAMTE gepoolte OOS-Spanne (alle Folds)
    erreichbaren Round-Trips, gerechnet an der SCHNELLSTEN im Suchraum zulaessigen
    Parameterkombination (``cooldown_bars``- UND ``max_bars_in_trade``-UNTERGRENZE): jede
    zusaetzliche Handelsfrequenz-Bremse (Signalrate, vorzeitiger ATR-Trailing-Exit vor
    ``max_bars_in_trade``) kann die REALE Trade-Zahl nur SENKEN, nie erhoehen. Liegt schon diese
    obere Schranke unter ``oos_min_trades``, ist das mechanisch belegt ein Bounds-Bug (der
    Suchraum laesst gar keinen ausreichend schnellen Zyklus zu) — kein Signalqualitaets-Befund.

    Pure, I/O-frei (``walk_forward`` wird injiziert, analog ``gate.required_bars``). Gibt ``None``
    zurueck, wenn die Strategie kein ``cooldown_bars``/``max_bars_in_trade``-Zyklusmodell hat (die
    Schranke ist dafuer nicht definierbar — keine falsch-positive Warnung)."""
    bounds_map = extract_numeric_bounds(strategy)
    if "cooldown_bars" not in bounds_map or "max_bars_in_trade" not in bounds_map:
        return None
    cooldown_lo, _ = bounds_map["cooldown_bars"]
    max_bars_lo, _ = bounds_map["max_bars_in_trade"]
    cycle_bars = max(1.0, float(cooldown_lo) + float(max_bars_lo))
    total_oos_bars = (float(walk_forward["splits"]) * float(walk_forward["oos_window_days"])
                      * float(bars_per_day))
    return int(total_oos_bars // cycle_bars)


def active_bounds_overrides() -> list[dict[str, Any]]:
    """Issue #1040/#1189 (Katalog #1189) — Inventar ALLER aktiven Suchraum-Overrides (kuratiert
    UND automatisch vorgeschlagen), je (Strategie, Symbol, Parameter): ``{strategy, symbol,
    parameter, active_bounds, default_bounds, source, set_in_run_id, rationale}``.

    Symptom #1040: ``boundary_veto_evidence`` belegt aktive Overrides bereits indirekt
    (``active_bounds`` vs. ``default_bounds`` je klemmendem Parameter EINES Gewinner-Trials), aber
    KEINE Report-Sektion listet, WELCHE (Strategie, Symbol, Parameter)-Kombinationen ueberhaupt
    unter einem geweiteten Suchraum liefen — ein Leser kann nicht erkennen, dass z. B.
    TrendPullback.ema_period ueber [5, 25] statt der kuratierten Default-Bounds [50, 300] gesucht
    wurde, ohne den Diagnose-Cache/``search_space_overrides.json`` von Hand zu lesen.

    Zwei Quellen (dieselben, die ``spaces._bounds_for`` in dieser Prioritaet anwendet):
    1. ``search_space_overrides.json`` (``spaces._load_search_space_overrides``) — eine
       KURATIERTE, menschliche PR-Entscheidung. ``rationale`` ist die Datei-weite
       ``_schema.description`` (keine feinere Pro-Parameter-Begruendung existiert in diesem
       Format); ``set_in_run_id`` bleibt ``None`` (ein statischer Config-Eintrag ist an keinen
       einzelnen Lauf gebunden).
    2. Der #761-Diagnose-Cache (``sweep_diagnostics.load_diagnosed_pairs_cache``) — ein
       AUTOMATISCH vorgeschlagener Override (``binding_cause == 'boundary_solution'``,
       ``action == 'search_space_override'``). ``rationale`` benennt die Ursache
       (``binding_cause``); ``set_in_run_id`` ist der Cache-Eintrag ``first_seen_run_id``
       (Akzeptanzkriterium #1040/2 — die Herkunft ist je Override auf Datei+Lauf aufloesbar).

    Ein Parameter kann in BEIDEN Quellen erscheinen (die kuratierte gewinnt laut ``spaces.
    _bounds_for``-Prioritaet zur Laufzeit) — beide Eintraege bleiben hier sichtbar, damit ein
    Operator den tatsaechlich wirksamen von einem toten (durch die kuratierte Regel bereits
    ueberdeckten) automatischen Vorschlag unterscheiden kann. Leer, wenn keine Quelle etwas
    beitraegt (bit-identisch zum Vorher, kein erfundener Eintrag)."""
    out: list[dict[str, Any]] = []
    try:
        curated = spaces._load_search_space_overrides()
    except Exception:
        curated = {}
    curated_rationale = None
    try:
        from automation.optimizer.trial_config import config_dir
        path = config_dir() / "search_space_overrides.json"
        if path.exists():
            import json as _json
            curated_rationale = (
                (_json.loads(path.read_text("utf-8")) or {}).get("_schema") or {}
            ).get("description")
    except Exception:
        curated_rationale = None
    for strategy, symbols in (curated or {}).items():
        try:
            default_bounds = extract_numeric_bounds(strategy)
        except Exception:
            default_bounds = {}
        for symbol, params in (symbols or {}).items():
            for param, bound in (params or {}).items():
                if not bound or len(bound) != 2:
                    continue
                default = default_bounds.get(param)
                out.append({
                    "strategy": strategy, "symbol": symbol, "parameter": param,
                    "active_bounds": [bound[0], bound[1]],
                    "default_bounds": [default[0], default[1]] if default else None,
                    "source": "curated",
                    "set_in_run_id": None,
                    "rationale": curated_rationale,
                })
    try:
        from automation.optimizer.sweep_diagnostics import load_diagnosed_pairs_cache
        auto_cache = load_diagnosed_pairs_cache()
    except Exception:
        auto_cache = {}
    for (strategy, symbol), entry in (auto_cache or {}).items():
        proposed = entry.get("proposed_bounds") or {}
        if not proposed:
            continue
        try:
            default_bounds = extract_numeric_bounds(strategy)
        except Exception:
            default_bounds = {}
        for param, bound in proposed.items():
            if not bound or len(bound) != 2:
                continue
            default = default_bounds.get(param)
            out.append({
                "strategy": strategy, "symbol": symbol, "parameter": param,
                "active_bounds": [bound[0], bound[1]],
                "default_bounds": [default[0], default[1]] if default else None,
                "source": "auto_proposed",
                "set_in_run_id": entry.get("first_seen_run_id"),
                "rationale": entry.get("binding_cause"),
            })
    return out
