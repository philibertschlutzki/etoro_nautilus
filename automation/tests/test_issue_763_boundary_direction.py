"""Issue #763 (P2) — Randlösungen: [#597] feuert in 29 Studies mit bis zu 50 % Grenz-Parametern.

`_boundary_hit_fraction` (run_optimization.py) misst nur DASS eine Randlösung vorliegt, nicht
WELCHER Parameter in WELCHE Richtung drückt — das Warning nennt beide möglichen Root-Causes (zu
enge Bounds ODER Reward-Konditionierung), unterscheidet sie aber nicht. Fix: `_boundary_hit_
directions` liefert `{param: "low"|"high"}` je Grenz-Parameter; `sweep_diagnostics.propose_bounds_
from_boundary_hits` leitet daraus einen konkreten, ausgeweiteten Bounds-Vorschlag ab (analog
`propose_bounds_widening`, #761); ein Winner mit `boundary_hit_fraction >= 0.5` erzeugt in
`confirm.py` `HOLD_BOUNDARY_UNRESOLVED` statt `REJECTED_BOUNDARY_SOLUTION` UND schreibt den
Vorschlag in den #761-Diagnose-Cache (siehe test_issue_654_rejection_detail_attribution.py für den
konfirm-seitigen Integrationstest).
"""
from automation.optimizer.run_optimization import (
    _boundary_hit_fraction, _boundary_hit_directions,
)
from automation.optimizer.sweep_diagnostics import propose_bounds_from_boundary_hits


class _Trial:
    def __init__(self, params):
        self.params = params


class _Study:
    def __init__(self, params):
        self.best_trial = _Trial(params)


# ── _boundary_hit_directions: WELCHER Parameter an WELCHER Grenze ──────────────────────────────────
def test_boundary_hit_directions_names_param_and_side():
    # SmaCrossoverStrategy: sma_period ∈ [5, 60], cooldown_bars ∈ [2, 36].
    study = _Study({"sma_period": 5, "cooldown_bars": 36})
    directions = _boundary_hit_directions(study, "SmaCrossoverStrategy")
    assert directions == {"sma_period": "low", "cooldown_bars": "high"}


def test_boundary_hit_directions_omits_interior_params():
    study = _Study({"sma_period": 5, "cooldown_bars": 19})   # cooldown_bars innen
    directions = _boundary_hit_directions(study, "SmaCrossoverStrategy")
    assert directions == {"sma_period": "low"}


def test_boundary_hit_directions_none_for_unknown_strategy_or_missing_winner():
    study = _Study({"sma_period": 5})
    assert _boundary_hit_directions(study, "NoSuchStrategy") is None
    assert _boundary_hit_directions(study, None) is None


def test_boundary_hit_fraction_and_directions_agree_on_hit_count():
    """Konsistenz-Invariante: len(directions) == fraction * total_numeric_params."""
    study = _Study({"sma_period": 5, "cooldown_bars": 36})
    frac = _boundary_hit_fraction(study, "SmaCrossoverStrategy")
    directions = _boundary_hit_directions(study, "SmaCrossoverStrategy")
    assert frac == 1.0
    assert len(directions) == 2


# ── propose_bounds_from_boundary_hits: konkreter Vorschlag aus der Richtungsinformation ────────────
def test_propose_bounds_widens_lower_bound_for_low_direction():
    # Issue #1066 — die rohe Arithmetik (2.0 - 0.3*34.0 = -8.2) liegt ausserhalb des
    # cooldown_bars-Domänenregisters (Untergrenze 1) und wird seither auf den Domänen-Randwert
    # geklammert, statt negativ zu bleiben.
    proposals = propose_bounds_from_boundary_hits(
        {"cooldown_bars": "low"}, "TrendPullbackStrategy",
        current_bounds={"cooldown_bars": (2.0, 36.0)}, widen_fraction=0.3)
    assert proposals == {"cooldown_bars": [1, 36.0]}


def test_propose_bounds_low_direction_unclamped_stays_exact_when_within_domain():
    """Ein Vorschlag, der INNERHALB des Domänenregisters bleibt, ist bit-identisch zur
    rohen Weitungs-Arithmetik (die Klammer greift nur bei einer tatsaechlichen Verletzung)."""
    proposals = propose_bounds_from_boundary_hits(
        {"cooldown_bars": "low"}, "TrendPullbackStrategy",
        current_bounds={"cooldown_bars": (10.0, 20.0)}, widen_fraction=0.3)
    assert proposals == {"cooldown_bars": [round(10.0 - 0.3 * 10.0, 6), 20.0]}


def test_propose_bounds_widens_upper_bound_for_high_direction():
    proposals = propose_bounds_from_boundary_hits(
        {"ema_period": "high"}, "TrendPullbackStrategy",
        current_bounds={"ema_period": (50.0, 300.0)}, widen_fraction=0.3)
    assert proposals == {"ema_period": [50.0, round(300.0 + 0.3 * 250.0, 6)]}


def test_propose_bounds_handles_multiple_params_independently():
    proposals = propose_bounds_from_boundary_hits(
        {"cooldown_bars": "low", "max_bars_in_trade": "high"}, "TrendPullbackStrategy",
        current_bounds={"cooldown_bars": (2.0, 36.0), "max_bars_in_trade": (12.0, 24.0)})
    assert set(proposals.keys()) == {"cooldown_bars", "max_bars_in_trade"}
    assert proposals["cooldown_bars"][1] == 36.0            # Obergrenze unveraendert
    assert proposals["max_bars_in_trade"][0] == 12.0         # Untergrenze unveraendert


def test_propose_bounds_skips_params_without_known_bounds():
    proposals = propose_bounds_from_boundary_hits(
        {"unrelated_param": "low"}, "TrendPullbackStrategy", current_bounds={})
    assert proposals == {}


def test_propose_bounds_defaults_current_bounds_from_strategy():
    """Ohne current_bounds wird bounds.extract_numeric_bounds(strategy) verwendet."""
    proposals = propose_bounds_from_boundary_hits({"cooldown_bars": "low"}, "TrendPullbackStrategy")
    assert "cooldown_bars" in proposals
    assert proposals["cooldown_bars"][0] < 2.0    # Default-Untergrenze von TrendPullback ist 2.0
