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
