"""Issue #1033 (Katalog #866) — Ereignisse im Zaehler, Entitaeten im Nenner sind nicht
kommensurabel: ``check_inference_diagnostics_concentration``/``check_adaptive_diagnostic_rate``
zaehlen distinkte TRIALS je Code (nicht Diagnose-Ereignisse, von denen ein Trial mehrere tragen
kann, z. B. je Fold) und koennen dadurch strukturell nicht > 1.0 werden; ein Regressionswaechter
meldet ``rate > 1.0`` als eigenen FAIL statt einer unplausiblen Prozentzahl (Pitfall #356).
"""
from automation.optimizer import invariants as inv


def test_adaptive_rate_never_exceeds_one_even_with_multiple_events_per_trial():
    """Jeder Trial traegt MEHRERE SORTINO_DOWNSIDE_SHRUNK-Ereignisse (z. B. je Fold) — die Rate
    zaehlt den Trial trotzdem nur einmal."""
    trials = [
        {"inference_diagnostics": [
            {"code": "SORTINO_DOWNSIDE_SHRUNK"}, {"code": "SORTINO_DOWNSIDE_SHRUNK"},
            {"code": "SORTINO_DOWNSIDE_SHRUNK"},
        ]}
        for _ in range(5)
    ]
    result = inv.check_adaptive_diagnostic_rate(trials, n_trials_informative=5, max_rate=0.3)
    assert result.actual <= 1.0
    assert result.actual == 1.0
    assert result.passed is False  # 100% > 30%-Schwelle, aber ein GUELTIGER, kommensurabler Wert


def test_adaptive_rate_reports_dedicated_fail_when_denominator_is_wrong():
    """Ein bewusst inkommensurabler Nenner (kleiner als die Zahl der tatsaechlich betroffenen
    Trials) muss als eigene FAIL-Kategorie erkennbar sein, nicht als plausibel wirkende Prozentzahl
    > 100%."""
    trials = [{"inference_diagnostics": [{"code": "SORTINO_DOWNSIDE_SHRUNK"}]} for _ in range(5)]
    result = inv.check_adaptive_diagnostic_rate(trials, n_trials_informative=3, max_rate=0.3)
    assert result.passed is False
    assert result.actual > 1.0
    assert "nicht kommensurabel" in result.detail


def test_inference_diagnostics_concentration_never_exceeds_one_even_with_multiple_events_per_trial():
    trials = [
        {"inference_diagnostics": [
            {"code": "SORTINO_GUARD_TRIPPED"}, {"code": "SORTINO_GUARD_TRIPPED"},
        ]}
        for _ in range(4)
    ]
    result = inv.check_inference_diagnostics_concentration(trials, n_trials_informative=4)
    assert result.actual <= 1.0
    assert result.actual == 1.0


def test_inference_diagnostics_concentration_reports_dedicated_fail_when_denominator_is_wrong():
    trials = [{"inference_diagnostics": [{"code": "SORTINO_GUARD_TRIPPED"}]} for _ in range(4)]
    result = inv.check_inference_diagnostics_concentration(trials, n_trials_informative=2)
    assert result.passed is False
    assert result.actual > 1.0
    assert "nicht kommensurabel" in result.detail
