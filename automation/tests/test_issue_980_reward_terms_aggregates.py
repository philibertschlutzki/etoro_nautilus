"""Issue #980 (Katalog C, P1, Pitfall #302 in AGENTS.md) — ``reward_terms_aggregates.terms`` war in
27 von 28 Studies des Referenzlaufs 46cf5070 leer (``{}``), obwohl ``check_reward_term_variance``
(invariants.py) für dieselben Studies konkrete Werte meldete. Root-Cause: die Aggregation in
``run_optimization._emit_study_summary`` filterte auf ``reward_terms.branch in ('eligible',
'per_symbol', 'pareto')`` — dieselbe 2.15%-Kohorte, die #979 als praktisch leer identifizierte
(``p_eligible == 0`` in 27/28 Studies) — statt auf ``oos_evaluated=True`` wie die Invariante
(Selection-on-the-dependent-variable, Pitfall #302).
"""
import time
from pathlib import Path

from automation.optimizer.run_optimization import _emit_study_summary


class _DummyTrial:
    def __init__(self, reward_terms, oos_eligible):
        self.value = -5.0
        self.user_attrs = {
            "oos_evaluated": True,
            "oos_eligible": oos_eligible,
            "backtest_ms": 10,
            "reward_terms": reward_terms,
        }


class _DummyStudy:
    study_name = "study_X"

    def __init__(self, trials):
        self.trials = trials
        self.best_value = max(t.value for t in trials)


def _terms(base, branch="failure"):
    return {"base": base, "branch": branch, "divergence": 0.0, "dd_penalty": 0.0,
            "param_pen": 0.0, "turnover": 0.0, "fold_dispersion": 0.0, "tie_breaker": 0.0}


def test_terms_are_populated_even_when_zero_trials_are_eligible(monkeypatch):
    """Reproduziert exakt den #980-Befund: ALLE Trials sind branch=='failure' (p_eligible == 0,
    wie in 27/28 Studies des Referenzlaufs) -- reward_terms_aggregates.terms darf trotzdem NICHT
    leer sein, weil die Population (oos_evaluated=True) nicht leer ist."""
    import automation.optimizer.run_optimization as ro
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    captured = []
    monkeypatch.setattr(ro, "emit_execution_event",
                        lambda logger, name, payload: captured.append((name, payload)))

    trials = [
        _DummyTrial(_terms(-4.0), False), _DummyTrial(_terms(-6.0), False),
        _DummyTrial(_terms(-5.0), False), _DummyTrial(_terms(-3.0), False),
        _DummyTrial(_terms(-7.0), False),
    ]
    _emit_study_summary(_DummyStudy(trials), "GSAT.ETORO", time.perf_counter())
    payload = captured[-1][1]
    assert payload["reward_terms_aggregates"]["terms"] != {}
    assert "base" in payload["reward_terms_aggregates"]["terms"]
    assert payload["reward_terms_aggregates"]["terms"]["base"]["std"] > 0.0


def test_still_populated_when_some_trials_are_eligible(monkeypatch):
    import automation.optimizer.run_optimization as ro
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    captured = []
    monkeypatch.setattr(ro, "emit_execution_event",
                        lambda logger, name, payload: captured.append((name, payload)))

    trials = [
        _DummyTrial(_terms(-4.0), False), _DummyTrial(_terms(2.0, "per_symbol"), True),
        _DummyTrial(_terms(1.5, "per_symbol"), True),
    ]
    _emit_study_summary(_DummyStudy(trials), "GSAT.ETORO", time.perf_counter())
    payload = captured[-1][1]
    assert payload["reward_terms_aggregates"]["terms"] != {}


def test_empty_when_no_trials_are_oos_evaluated(monkeypatch):
    """Bleibt korrekt leer, wenn die Population selbst leer ist (kein oos_evaluated Trial)."""
    import automation.optimizer.run_optimization as ro
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    captured = []
    monkeypatch.setattr(ro, "emit_execution_event",
                        lambda logger, name, payload: captured.append((name, payload)))

    class _UnevalTrial:
        value = -19.75
        user_attrs = {"oos_evaluated": False, "oos_eligible": False, "backtest_ms": 5}

    trials = [_UnevalTrial(), _UnevalTrial()]
    _emit_study_summary(_DummyStudy(trials), "GSAT.ETORO", time.perf_counter())
    payload = captured[-1][1]
    assert payload["reward_terms_aggregates"]["terms"] == {}
