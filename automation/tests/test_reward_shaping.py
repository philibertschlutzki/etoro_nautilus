import pytest
from dataclasses import replace
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

W = {  # Test-Weights (DI)
    "penalty_unevaluable_oos": -2.0,
    "sortino_clip_abs": 3.0,
    "penalty_overfit_weight": 1.0,
    "penalty_dd_weight": 1.0,
    "bonus_coverage_weight": 0.5,
    "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 1e-3,
    "oos_min_trades": 20,   # falls Helper Weights-Override unterstützt; sonst tournament.json mocken
}

def _m(**kw):
    base = dict(oos_evaluated=False, oos_eligible=False, is_sortino_median=0.0,
                oos_sortino=None, oos_max_drawdown=0.0, oos_total_trades=0,
                win_count=0, fully_eligible_pairs=0, is_total_trades=0, is_max_trades=0)
    base.update(kw)
    return TournamentMetrics(**base)

def test_unevaluable_monotonic_in_trades():
    low  = compute_reward(_m(oos_total_trades=2),  universe_size=70, weights=W, risk_dd_cap=0.3)
    high = compute_reward(_m(oos_total_trades=18), universe_size=70, weights=W, risk_dd_cap=0.3)
    assert high > low

def test_zero_trades_equals_floor_penalty():
    r = compute_reward(_m(oos_total_trades=0), universe_size=70, weights=W, risk_dd_cap=0.3)
    assert r == pytest.approx(W["penalty_unevaluable_oos"])

def test_unevaluable_capped_at_min_trades():
    a = compute_reward(_m(oos_total_trades=20),  universe_size=70, weights=W, risk_dd_cap=0.3)
    b = compute_reward(_m(oos_total_trades=999), universe_size=70, weights=W, risk_dd_cap=0.3)
    assert a == pytest.approx(b)  # trade_progress clippt bei 1.0

def test_evaluable_ALWAYS_beats_unevaluable():
    worst_eval = _m(oos_evaluated=True, oos_eligible=True, oos_sortino=-W["sortino_clip_abs"],
                    is_sortino_median=W["sortino_clip_abs"], oos_max_drawdown=0.99,
                    oos_total_trades=20, win_count=0)
    best_uneval = _m(oos_total_trades=10_000)  # maximal geshaped
    r_eval   = compute_reward(worst_eval,  universe_size=70, weights=W, risk_dd_cap=0.30)
    r_uneval = compute_reward(best_uneval, universe_size=70, weights=W, risk_dd_cap=0.30)
    assert r_eval > r_uneval   # HARTE Invariante

def test_holdout_not_referenced(monkeypatch):
    # compute_reward darf keine Holdout-Pfade/Dateien öffnen — reine Funktion auf TournamentMetrics + weights
    import builtins, automation.optimizer.reward as rw
    monkeypatch.setattr(builtins, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein File-IO erlaubt")))
    compute_reward(_m(oos_total_trades=5), universe_size=70, weights=W, risk_dd_cap=0.3)  # darf nicht werfen
