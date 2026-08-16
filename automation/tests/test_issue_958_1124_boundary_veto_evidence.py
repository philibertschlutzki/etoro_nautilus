"""Issue #958/#1124 (Katalog #960) — Das Randlösungs-Veto feuert in 5 von 6 Fällen ohne
ausgewiesenen Grund.

Symptom. Sechs Studies bestehen IS-Gate, Confirm, Holdout, Deflation UND PBO und scheitern
ausschliesslich am Randlösungs-Veto. Nur EINE davon trägt einen dokumentierten Bruch
(``winner_outside_default_bounds``); die anderen fünf zeigen ``None``.

Root-Cause-Kandidat (b), bestätigt: ``winner_outside_default_bounds`` (report.py) verlangt eine
STRIKTE Verletzung der Default-Bounds (``value < lo or value > hi``), während das Veto selbst
(``run_optimization._boundary_hit_analysis``) bereits auf blosser NÄHE (<= 2 % vom Rand) feuert —
ein Gewinner bei ``norm=0.01`` (99 % innerhalb der Default-Bounds) löst das Veto aus, erscheint
aber NIE in ``winner_outside_default_bounds``.

Fix. Die Veto-Entscheidung exportiert ``boundary_veto_evidence`` (``{param: {sampled_value,
active_bounds, default_bounds, distance_to_edge}}``) direkt aus derselben Quelle
(``_boundary_hit_analysis``), die auch ``boundary_frac``/``boundary_directions`` speist — EINE
Quelle für alle drei Konsumenten. Neue Invariante ``check_boundary_veto_has_evidence`` (blocking):
ohne Evidenz kein Veto.

``OpeningRangeBreakout/TSLA`` (das eine dokumentierte Beispiel aus dem Issue) bestätigt den
#1066-Sperrvermerk empirisch — ``max_bars_in_trade = 7`` liegt unter der Default-Untergrenze 12 und
stammt aus dem geweiteten Suchraum; dieser Fall zeigt zusätzlich, wie ``active_bounds`` (die
TATSÄCHLICHE, ggf. geweiterte Sampling-Spanne) von ``default_bounds`` (der kuratierten Referenz)
abweichen kann.
"""
from automation.optimizer import invariants as inv
from automation.optimizer.run_optimization import (
    _boundary_hit_analysis, _boundary_hit_directions, _boundary_hit_fraction,
    _boundary_veto_evidence,
)


class _Distribution:
    def __init__(self, low, high):
        self.low = low
        self.high = high


class _Trial:
    def __init__(self, params, distributions=None):
        self.params = params
        self.distributions = distributions or {}


class _Study:
    def __init__(self, params, distributions=None):
        self.best_trial = _Trial(params, distributions)


# ── boundary_veto_evidence: the near-edge case that winner_outside_default_bounds would miss ────
def test_evidence_fires_on_near_edge_not_only_strict_violation():
    """SmaCrossoverStrategy: sma_period in [5, 60]. Ein Gewinner bei 5 (dem exakten Rand, norm=0.0)
    liegt INNERHALB der Default-Bounds (winner_outside_default_bounds waere leer), aber die Naehe
    (<=2%) loest das Veto trotzdem aus -- boundary_veto_evidence muss den Fall dennoch benennen."""
    study = _Study({"sma_period": 5, "cooldown_bars": 19})
    evidence = _boundary_veto_evidence(study, "SmaCrossoverStrategy")
    assert evidence is not None
    assert "sma_period" in evidence
    assert evidence["sma_period"]["sampled_value"] == 5
    assert evidence["sma_period"]["distance_to_edge"] == 0.0
    assert evidence["sma_period"]["default_bounds"] == [5, 60]


def test_evidence_and_directions_and_fraction_share_the_same_underlying_hits():
    """Konsistenz-Invariante: dieselbe Parametermenge in evidence/directions, dieselbe Fraktion."""
    study = _Study({"sma_period": 5, "cooldown_bars": 36})
    evidence = _boundary_veto_evidence(study, "SmaCrossoverStrategy")
    directions = _boundary_hit_directions(study, "SmaCrossoverStrategy")
    frac = _boundary_hit_fraction(study, "SmaCrossoverStrategy")
    assert set(evidence.keys()) == set(directions.keys())
    for param, d in directions.items():
        assert evidence[param]["direction"] == d
    assert len(evidence) == frac * 2  # 2 numerische Parameter insgesamt


def test_evidence_reports_active_bounds_from_the_trial_distribution_when_widened():
    """OpeningRangeBreakout/TSLA-Analogon aus dem Issue: max_bars_in_trade=7 stammt aus einem
    geweiterten Suchraum (active_bounds=[7,24]), waehrend default_bounds die kuratierte Referenz
    ([12,24]) bleibt -- die beiden koennen divergieren (Root-Cause-Kandidat (a))."""
    study = _Study(
        {"cooldown_bars": 20, "max_bars_in_trade": 7},
        distributions={"max_bars_in_trade": _Distribution(7, 24)},
    )
    evidence = _boundary_veto_evidence(study, "TrendPullbackStrategy")
    assert "max_bars_in_trade" in evidence
    assert evidence["max_bars_in_trade"]["active_bounds"] == [7, 24]
    assert evidence["max_bars_in_trade"]["default_bounds"] != [7, 24]


def test_evidence_falls_back_to_default_bounds_without_distribution_info():
    """Ein Trial-Double ohne .distributions (Legacy-/Testkonstrukt) darf nicht crashen -- die
    aktive Spanne faellt auf die kuratierte Referenz zurueck."""
    study = _Study({"sma_period": 5, "cooldown_bars": 19})
    evidence = _boundary_veto_evidence(study, "SmaCrossoverStrategy")
    assert evidence["sma_period"]["active_bounds"] == evidence["sma_period"]["default_bounds"]


def test_evidence_none_for_interior_winner():
    study = _Study({"sma_period": 30, "cooldown_bars": 19})
    assert _boundary_veto_evidence(study, "SmaCrossoverStrategy") is None


def test_evidence_none_for_unknown_strategy_or_missing_winner():
    study = _Study({"sma_period": 5})
    assert _boundary_veto_evidence(study, "NoSuchStrategy") is None
    assert _boundary_veto_evidence(study, None) is None


def test_boundary_hit_analysis_return_type_carries_full_evidence_dicts():
    """Regressionswaechter fuer den Refactor: _boundary_hit_analysis liefert jetzt
    {param: {direction, sampled_value, active_bounds, default_bounds, distance_to_edge}}, nicht
    mehr nur {param: direction}."""
    study = _Study({"sma_period": 5, "cooldown_bars": 36})
    result, total = _boundary_hit_analysis(study, "SmaCrossoverStrategy")
    assert total == 2
    for param, entry in result.items():
        assert set(entry.keys()) == {
            "direction", "sampled_value", "active_bounds", "default_bounds", "distance_to_edge"}


# ── invariants.check_boundary_veto_has_evidence ──────────────────────────────────────────────────
def _proposal(status, boundary_veto_evidence=None):
    return {
        "status": status,
        "holdout": {"symbol": {"boundary_veto_evidence": boundary_veto_evidence}},
    }


def test_check_not_applicable_for_non_boundary_outcomes():
    result = inv.check_boundary_veto_has_evidence(_proposal("READY_FOR_PR"))
    assert result.passed is True
    assert result.severity == "blocking"


def test_check_passes_when_evidence_is_present():
    result = inv.check_boundary_veto_has_evidence(_proposal(
        "REJECTED_BOUNDARY_SOLUTION",
        boundary_veto_evidence={"cooldown_bars": {
            "sampled_value": 36, "active_bounds": [2, 36], "default_bounds": [2, 36],
            "distance_to_edge": 0.0}}))
    assert result.passed is True


def test_check_fails_blocking_when_evidence_is_missing_for_rejected_boundary_solution():
    result = inv.check_boundary_veto_has_evidence(_proposal("REJECTED_BOUNDARY_SOLUTION"))
    assert result.passed is False
    assert result.severity == "blocking"


def test_check_fails_blocking_when_evidence_is_missing_for_hold_boundary_unresolved():
    result = inv.check_boundary_veto_has_evidence(_proposal("HOLD_BOUNDARY_UNRESOLVED"))
    assert result.passed is False
    assert result.severity == "blocking"


def test_check_fails_on_empty_evidence_dict():
    result = inv.check_boundary_veto_has_evidence(
        _proposal("REJECTED_BOUNDARY_SOLUTION", boundary_veto_evidence={}))
    assert result.passed is False
