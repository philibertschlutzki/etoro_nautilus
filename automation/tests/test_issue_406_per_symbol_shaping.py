"""Issue #406 (P1) — `shaping_trade_target` kontextsensitiv (Pitfall #75, Defekt 2).

Das IS-Aktivitaets-Shaping (ISSUE-OPT-375) nutzt ``activity = min(1, is_total_trades /
shaping_trade_target)``. ``shaping_trade_target=50`` ist fuer die UNIVERSE-weite IS-Trade-Summe
(~70 Symbole) kalibriert. Im Per-Symbol-Pfad ist ``is_total_trades`` die Fold-Summe EINES Symbols
(≫ 50) ⇒ ``activity`` saettigt sofort auf 1.0 ⇒ konstantes Shaping (Zero-Gradient genau im
Bedarfsfall).

Fix: bei ``universe_size == 1`` ODER ``reward_mode == 'per_symbol'`` wird der dedizierte,
groessere ``per_symbol_shaping_trade_target`` (400) verwendet. Reine Arithmetik via DI-Weights.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

W = {
    "penalty_unevaluable_oos": -10.0,
    "sortino_clip_abs": 5.0,
    "penalty_overfit_weight": 0.5,
    "penalty_dd_weight": 8.0,
    "bonus_coverage_weight": 1.0,
    "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001,
    "shaping_trade_target": 50,
    "per_symbol_shaping_trade_target": 400,
    "oos_min_trades": 3,
    "lambda_reg": 0.25,
}


def _m(**kw):
    base = dict(oos_evaluated=False, oos_eligible=False, is_sortino_median=0.0,
                oos_sortino=None, oos_max_drawdown=0.0, oos_total_trades=0,
                win_count=0, fully_eligible_pairs=0, is_total_trades=0, hit_trade_cap=False,
                oos_total_return=0.0)
    base.update(kw)
    return TournamentMetrics(**base)


def test_per_symbol_target_avoids_immediate_saturation():
    """universe_size==1, is_total_trades=200 ⇒ activity = 200/400 = 0.5 (NICHT 1.0)."""
    r = compute_reward(_m(is_total_trades=200), universe_size=1, weights=W, risk_dd_cap=0.3)
    assert r == pytest.approx(-10.0 + 0.25 * 0.5)


def test_per_symbol_gradient_preserved():
    """Zwei unevaluable Per-Symbol-Trials mit verschiedener IS-Aktivitaet sind unterscheidbar."""
    low = compute_reward(_m(is_total_trades=120), universe_size=1, weights=W, risk_dd_cap=0.3)
    high = compute_reward(_m(is_total_trades=360), universe_size=1, weights=W, risk_dd_cap=0.3)
    assert high > low  # 120/400 = 0.3 < 360/400 = 0.9


def test_per_symbol_target_fixes_saturation_vs_legacy():
    """Der Kern des Bugs: ohne den dedizierten Target saettigt dasselbe Symbol sofort (Floor-Plateau)."""
    m = _m(is_total_trades=200)
    legacy_weights = {k: v for k, v in W.items() if k != "per_symbol_shaping_trade_target"}
    r_legacy = compute_reward(m, universe_size=1, weights=legacy_weights, risk_dd_cap=0.3)
    r_fixed = compute_reward(m, universe_size=1, weights=W, risk_dd_cap=0.3)
    assert r_legacy == pytest.approx(-10.0 + 0.25 * 1.0)   # 200/50 -> saturiert
    assert r_fixed == pytest.approx(-10.0 + 0.25 * 0.5)    # 200/400 -> Gradient erhalten
    assert r_fixed < r_legacy


def test_universe_path_unchanged_uses_legacy_target():
    """Multi-Symbol-Pfad (reward_mode 'auto', universe>1) nutzt unveraendert shaping_trade_target=50."""
    r = compute_reward(_m(is_total_trades=40), universe_size=70, weights=W, risk_dd_cap=0.3)
    assert r == pytest.approx(-10.0 + 0.25 * (40 / 50))


def test_reward_mode_per_symbol_uses_per_symbol_target_above_universe_1():
    """reward_mode='per_symbol' erzwingt den dedizierten Target auch bei universe_size>1."""
    weights = {**W, "reward_mode": "per_symbol"}
    r = compute_reward(_m(is_total_trades=200), universe_size=70, weights=weights, risk_dd_cap=0.3)
    assert r == pytest.approx(-10.0 + 0.25 * (200 / 400))


def test_floor_invariant_holds_per_symbol():
    """Shaping bleibt hart durch unevaluable_shaping_span gedeckelt (progress clippt bei 1.0)."""
    r = compute_reward(_m(is_total_trades=10_000), universe_size=1, weights=W, risk_dd_cap=0.3)
    assert r == pytest.approx(-10.0 + 0.25)
    assert r <= -10.0 + 0.25 + 1e-12


def test_fallback_to_legacy_when_per_symbol_target_absent():
    """Fehlt der dedizierte Target, bleibt das Legacy-Verhalten (Rueckwaerts-Kompat)."""
    legacy_weights = {k: v for k, v in W.items() if k != "per_symbol_shaping_trade_target"}
    r = compute_reward(_m(is_total_trades=25), universe_size=1, weights=legacy_weights, risk_dd_cap=0.3)
    assert r == pytest.approx(-10.0 + 0.25 * (25 / 50))


def test_shipped_config_has_per_symbol_target():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg.get("per_symbol_shaping_trade_target") == 400
    assert "per_symbol_shaping_trade_target" in cfg["_schema"]["fields"]
