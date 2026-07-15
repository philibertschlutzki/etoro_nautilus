"""Issue #407 (P1) — kontinuierlicher Eligibility-Gradient via normierter Gate-Distanz.

Selbst nach #405/#406 kann ein Per-Symbol-Trial unevaluable bleiben (OOS handelt nie). Damit TPE
auch dann einen Gradienten Richtung Eligibility hat, liefert die IS-Performance ein normiertes
Naehe-Signal: `_gate_proximity(m, weights)` ∈ [0,1] aus `is_best_total_return`/`is_best_win_rate`
gegen `shaping_return_target`/`shaping_winrate_target`. Additiv und gebunden:
`progress = max(progress, _gate_proximity(...))`.

Shaping bleibt durch `unevaluable_shaping_span` gedeckelt (Anti-Gate-Gaming fuer den Unevaluable-Ast
selbst). Issue #629 — die einstige HARTE Invariante 'evaluable schlaegt IMMER unevaluable' wurde
ersatzlos gestrichen: die Feasibility-Rangordnung kommt seither ausschliesslich vom #612-Sampler-
Constraint, compute_reward ist ein ungeklemmtes Qualitaetsziel. Reine Arithmetik via DI-Weights.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.parsing import TournamentMetrics, parse_tournament
from automation.optimizer.reward import compute_reward, _gate_proximity

W = {
    "penalty_unevaluable_oos": -10.0,
    "sortino_clip_abs": 5.0,
    "penalty_overfit_weight": 0.5,
    "penalty_dd_weight": 8.0,
    "bonus_coverage_weight": 1.0,
    "unevaluable_shaping_span": 0.25,
    "shaping_trade_target": 50,
    "per_symbol_shaping_trade_target": 400,
    "shaping_return_target": 0.5,
    "shaping_winrate_target": 0.5,
    "oos_min_trades": 3,
    "lambda_reg": 0.25,
}


def _m(**kw):
    base = dict(oos_evaluated=False, oos_eligible=False, is_sortino_median=0.0,
                oos_sortino=None, oos_max_drawdown=0.0, oos_total_trades=0,
                win_count=0, fully_eligible_pairs=0, is_total_trades=0, hit_trade_cap=False,
                oos_total_return=0.0, is_best_total_return=0.0, is_best_win_rate=0.0)
    base.update(kw)
    return TournamentMetrics(**base)


# ---------------------------------------------------------------------------
# _gate_proximity — normierte Gate-Distanz ∈ [0, 1]
# ---------------------------------------------------------------------------

def test_gate_proximity_unit_range_and_mean():
    assert _gate_proximity(_m(is_best_total_return=0.25, is_best_win_rate=0.5), W) == pytest.approx((0.5 + 1.0) / 2)
    assert _gate_proximity(_m(is_best_total_return=0.0, is_best_win_rate=0.0), W) == 0.0
    # clipped at 1.0 per component (never above the span ceiling)
    assert _gate_proximity(_m(is_best_total_return=99.0, is_best_win_rate=9.0), W) == pytest.approx(1.0)


def test_gate_proximity_clamps_negative_return():
    """Negative IS-Rendite traegt 0 bei (kein negativer Gradient)."""
    assert _gate_proximity(_m(is_best_total_return=-0.4, is_best_win_rate=0.5), W) == pytest.approx((0.0 + 1.0) / 2)


def test_gate_proximity_zero_when_targets_absent():
    legacy = {k: v for k, v in W.items() if k not in ("shaping_return_target", "shaping_winrate_target")}
    assert _gate_proximity(_m(is_best_total_return=0.4, is_best_win_rate=0.4), legacy) == 0.0


# ---------------------------------------------------------------------------
# compute_reward — additiver, gebundener Gradient im Unevaluable-Zweig
# ---------------------------------------------------------------------------

def test_almost_eligible_beats_never_eligible():
    never = compute_reward(_m(), universe_size=1, weights=W, risk_dd_cap=0.3)
    almost = compute_reward(_m(is_best_total_return=0.4, is_best_win_rate=0.4),
                            universe_size=1, weights=W, risk_dd_cap=0.3)
    assert almost > never
    assert never == pytest.approx(-10.0)


def test_gradient_monotonic_in_is_performance():
    low = compute_reward(_m(is_best_total_return=0.1, is_best_win_rate=0.1),
                         universe_size=1, weights=W, risk_dd_cap=0.3)
    high = compute_reward(_m(is_best_total_return=0.4, is_best_win_rate=0.4),
                          universe_size=1, weights=W, risk_dd_cap=0.3)
    assert high > low


def test_exact_reward_value_with_gate_proximity():
    m = _m(is_best_total_return=0.25, is_best_win_rate=0.5)  # proximity = mean(0.5, 1.0) = 0.75
    r = compute_reward(m, universe_size=1, weights=W, risk_dd_cap=0.3)
    assert r == pytest.approx(-10.0 + 0.25 * 0.75)


def test_gate_proximity_bounded_by_span():
    """Floor-Invariante: selbst extreme IS-Performance deckelt Shaping bei unevaluable_shaping_span."""
    r = compute_reward(_m(is_best_total_return=99.0, is_best_win_rate=1.0),
                       universe_size=1, weights=W, risk_dd_cap=0.3)
    assert r == pytest.approx(-10.0 + 0.25)
    assert r <= -10.0 + 0.25 + 1e-12


def test_progress_takes_max_of_activity_and_proximity():
    """progress = max(trade_progress, activity, gate_proximity): das staerkste Signal gewinnt."""
    # geringe IS-Aktivitaet (activity klein), aber starke IS-Performance (proximity hoch)
    m = _m(is_total_trades=40, is_best_total_return=0.5, is_best_win_rate=0.5)
    r = compute_reward(m, universe_size=1, weights=W, risk_dd_cap=0.3)
    # activity = 40/400 = 0.1 ; proximity = 1.0 ; progress = 1.0
    assert r == pytest.approx(-10.0 + 0.25 * 1.0)


def test_worst_evaluable_can_fall_below_best_shaped_unevaluable():
    """Issue #629 — die vormals HARTE Floor-Invariante ('evaluable schlaegt IMMER unevaluable') ist
    ersatzlos gestrichen: compute_reward liefert seit #629 ein ungeklemmtes Qualitaetsziel, ein
    katastrophaler evaluierter Trial (Sortino am Clip, Drawdown 99 %) darf legitim UNTER einem
    maximal geshapeten, nie-evaluierten Trial liegen. Die Feasibility beider Trials wird
    ausschliesslich vom #612-Sampler-Constraint sichergestellt, nicht von diesem Reward-Wert."""
    worst_eval = _m(oos_evaluated=True, oos_eligible=True, oos_sortino=-5.0,
                    is_sortino_median=5.0, oos_max_drawdown=0.99, oos_total_trades=20)
    best_uneval = _m(is_best_total_return=99.0, is_best_win_rate=1.0,
                     is_total_trades=10_000, oos_total_trades=9999)
    r_eval = compute_reward(worst_eval, universe_size=1, weights=W, risk_dd_cap=0.3)
    r_uneval = compute_reward(best_uneval, universe_size=1, weights=W, risk_dd_cap=0.3)
    assert r_eval < r_uneval
    import math
    assert math.isfinite(r_eval)


def test_legacy_weights_without_targets_unchanged():
    """Ohne die neuen Targets bleibt der Unevaluable-Zweig exakt das Legacy-Verhalten."""
    legacy = {k: v for k, v in W.items() if k not in ("shaping_return_target", "shaping_winrate_target")}
    m = _m(is_best_total_return=0.4, is_best_win_rate=0.4)  # waere mit Targets > Floor
    r = compute_reward(m, universe_size=1, weights=legacy, risk_dd_cap=0.3)
    assert r == pytest.approx(-10.0)  # keine Aktivitaet, keine Proximity ⇒ blanker Penalty


# ---------------------------------------------------------------------------
# parsing.parse_tournament — is_best_total_return / is_best_win_rate
# ---------------------------------------------------------------------------

def test_parsing_extracts_is_best_metrics(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"aggregate_winner": None, "full_results": [
        {"metrics": {"total_trades": 10, "total_return": 0.2, "win_rate": 0.4}},
        {"metrics": {"total_trades": 5, "total_return": 0.5, "win_rate": 0.3}},
    ]}), "utf-8")
    m = parse_tournament(p)
    assert m.is_best_total_return == pytest.approx(0.5)
    assert m.is_best_win_rate == pytest.approx(0.4)


def test_parsing_is_best_metrics_none_safe(tmp_path):
    """Fehlende/None-Felder ⇒ 0.0 (kein Crash, kein negativer Default)."""
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"aggregate_winner": None, "full_results": [
        {"metrics": {"total_trades": 10}}, {"metrics": {"total_return": None, "win_rate": None}}, {},
    ]}), "utf-8")
    m = parse_tournament(p)
    assert m.is_best_total_return == 0.0
    assert m.is_best_win_rate == 0.0


def test_shipped_config_has_shaping_targets():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg.get("shaping_return_target") is not None
    assert cfg.get("shaping_winrate_target") is not None
    assert "shaping_return_target" in cfg["_schema"]["fields"]
    assert "shaping_winrate_target" in cfg["_schema"]["fields"]
