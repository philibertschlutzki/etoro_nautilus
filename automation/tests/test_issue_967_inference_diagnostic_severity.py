"""Issue #967 (Katalog A, P0) — 91,4 % der Inferenzausfälle emittierten keine Diagnose (behoben in
backtest_runner.py, siehe test_issue_804/test_issue_918); die Invariante bestrafte gleichzeitig den
einzigen funktionierenden Fix: ``check_inference_diagnostics_absent`` FAILte bei GENAU EINEM
``SORTINO_DOWNSIDE_SHRUNK``-Ereignis, obwohl dieser Code (#944) ein erwünschtes, adaptives Verhalten
markiert (der Trial bleibt mit einer korrigierten Statistik im Suchraum, wird nicht verworfen).

Fix: Codes werden in ``CENSORING`` (Trial verschwindet aus der Zielverteilung) und ``ADAPTIVE``
(Trial bleibt, korrigierte Statistik) klassifiziert. ``check_inference_diagnostics_absent`` zählt
NUR CENSORING; ``check_adaptive_diagnostic_rate`` überwacht ADAPTIVE getrennt (Anteil <= 0.3).
"""
from automation.optimizer import invariants as inv


def _trial(codes):
    return {"inference_diagnostics": [{"code": c} for c in codes]}


# ── check_inference_diagnostics_absent: ADAPTIVE allein darf nicht FAILen ────────────────────────
def test_absent_check_passes_with_only_adaptive_diagnoses():
    trials = [_trial(["SORTINO_DOWNSIDE_SHRUNK"]), _trial([]), _trial([])]
    result = inv.check_inference_diagnostics_absent(trials)
    assert result.passed is True


def test_absent_check_still_fails_on_a_real_defect_code():
    trials = [_trial(["EQUITY_NONPOSITIVE"]), _trial([])]
    result = inv.check_inference_diagnostics_absent(trials)
    assert result.passed is False
    assert result.actual == 1


def test_absent_check_passes_with_only_censoring_codes():
    """CENSORING-Codes (Guard/Ausfall) waren bereits vor #967 ausgenommen — bleibt unveraendert."""
    trials = [_trial(["SORTINO_GUARD_TRIPPED"]), _trial(["SORTINO_INSUFFICIENT_TRADES"])]
    result = inv.check_inference_diagnostics_absent(trials)
    assert result.passed is True


def test_absent_check_passes_with_mixed_censoring_and_adaptive():
    trials = [_trial(["SORTINO_GUARD_TRIPPED", "SORTINO_DOWNSIDE_SHRUNK"])]
    result = inv.check_inference_diagnostics_absent(trials)
    assert result.passed is True


# ── check_adaptive_diagnostic_rate ────────────────────────────────────────────────────────────────
def test_adaptive_rate_passes_under_threshold():
    trials = [_trial(["SORTINO_DOWNSIDE_SHRUNK"])] + [_trial([])] * 9
    result = inv.check_adaptive_diagnostic_rate(trials, n_trials_informative=10, max_rate=0.3)
    assert result.passed is True
    assert result.actual == 0.1


def test_adaptive_rate_fails_over_threshold():
    trials = [_trial(["SORTINO_DOWNSIDE_SHRUNK"])] * 5 + [_trial([])] * 5
    result = inv.check_adaptive_diagnostic_rate(trials, n_trials_informative=10, max_rate=0.3)
    assert result.passed is False
    assert result.actual == 0.5


def test_adaptive_rate_ignores_censoring_codes():
    """Nur ADAPTIVE-Codes zaehlen fuer diese Rate — ein Trial mit ausschliesslich CENSORING-Codes
    (der Trial ist ohnehin schon aus der Zielverteilung verschwunden) traegt nicht bei."""
    trials = [_trial(["SORTINO_GUARD_TRIPPED"])] * 8 + [_trial([])] * 2
    result = inv.check_adaptive_diagnostic_rate(trials, n_trials_informative=10, max_rate=0.3)
    assert result.passed is True
    assert result.actual == 0.0


def test_adaptive_rate_noop_without_denominator():
    result = inv.check_adaptive_diagnostic_rate([_trial(["SORTINO_DOWNSIDE_SHRUNK"])],
                                                  n_trials_informative=None)
    assert result.passed is True
    assert result.actual is None
