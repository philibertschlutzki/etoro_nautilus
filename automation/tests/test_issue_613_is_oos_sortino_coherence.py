"""Issue #613 — IS↔OOS-Sortino-Aggregations-Asymmetrie schliessen.

Die Divergenz-Strafe verglich den FOLD-/SYMBOL-MEDIAN-IS-Sortino (``is_sortino_median``) mit dem
GEPOOLTEN OOS-Sortino (``oos_sortino``) — zwei Grössen verschiedener Aggregationsebenen, Fenster und
Skalen (corr=0.185, 96 % im ``oos_luck``-Ast, 72 % konstant am Cap ⇒ gradientenfreier Term).

Fix (#613):
1. ``is_sortino_pooled`` aus der IS-Equity-Kurve (derselbe ``_calculate_stats``-mtm-Pfad wie ``oos_sortino``).
2. ``reward.py`` nutzt AUSSCHLIESSLICH ``is_sortino_pooled`` für die Divergenz; ``is_sortino_median`` bleibt forensisch.
3. Fail-loud Kohärenz-Assertion: IS-/OOS-Sortino MÜSSEN aus derselben Aggregationsebene stammen.
4. ``penalty_relative_cap`` rekalibriert (kommensurable Skalen).
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics, parse_tournament
from automation.backtest_runner import _assert_is_oos_sortino_coherence

_W = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001, "evaluable_reward_floor": -12.0,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
    "w_ret": 0.0, "overfit_divergence_mode": "symmetric", "overfit_oos_luck_weight": 0.25,
    "penalty_relative_cap": 1.0,
}
_CFG = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}


def _m(*, is_pooled, is_median, oos_sortino, dd=0.0, ret=0.03):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True,
        is_sortino_median=is_median, is_sortino_pooled=is_pooled,
        oos_sortino=oos_sortino, oos_max_drawdown=dd, oos_total_trades=30,
        win_count=1, fully_eligible_pairs=1, is_total_trades=100, oos_total_return=ret)


def _terms(m):
    _, t = compute_reward(m, universe_size=1, weights=_W, risk_dd_cap=0.3,
                          tournament_cfg=_CFG, return_terms=True)
    return t


# ── Akzeptanzkriterium: identische IS/OOS-Equity-Kurve ⇒ divergence_penalty == 0.0 ───────────────
@pytest.mark.parametrize("s", [-3.0, -0.5, 0.0, 0.5, 1.5, 4.0])
def test_identical_is_oos_sortino_zero_divergence(s):
    """``is_sortino_pooled == oos_sortino`` ⇒ diff = 0 ⇒ divergence_penalty == 0.0 (beide Vorzeichen)."""
    t = _terms(_m(is_pooled=s, is_median=-13.2, oos_sortino=s, dd=0.0))
    assert t["divergence"] == pytest.approx(0.0, abs=1e-12), f"s={s}: {t}"
    assert t["divergence_at_cap"] is False


# ── Die Divergenz nutzt is_sortino_pooled, NICHT den (forensischen) is_sortino_median ────────────
def test_divergence_uses_pooled_not_median():
    """Bei ``is_pooled == oos_sortino`` ist die Divergenz 0 — UNABHÄNGIG vom (forensischen)
    ``is_sortino_median`` (hier −30.0). Mit der alten Median-Logik wäre die Strafe massiv (am Cap)."""
    t = _terms(_m(is_pooled=1.5, is_median=-30.0, oos_sortino=1.5))
    assert t["divergence"] == pytest.approx(0.0, abs=1e-12), t
    # Kontrast: die Divergenz aus dem Median (−30) wäre riesig und würde den Cap treffen.
    t_median_scale = _terms(_m(is_pooled=-30.0, is_median=1.5, oos_sortino=1.5))
    assert t_median_scale["divergence"] > 1.0


def test_fallback_to_median_when_pooled_absent():
    """Fehlt ``is_sortino_pooled`` (Legacy-JSON/Fixture) ⇒ Fallback auf ``is_sortino_median``
    (bit-identisch zum Alt-Verhalten, migrations-sicher)."""
    r_fb, _ = compute_reward(_m(is_pooled=None, is_median=1.5, oos_sortino=1.5),
                             universe_size=1, weights=_W, risk_dd_cap=0.3,
                             tournament_cfg=_CFG, return_terms=True)
    r_ex, _ = compute_reward(_m(is_pooled=1.5, is_median=999.0, oos_sortino=1.5),
                             universe_size=1, weights=_W, risk_dd_cap=0.3,
                             tournament_cfg=_CFG, return_terms=True)
    assert r_fb == pytest.approx(r_ex, abs=1e-12)


def test_none_pooled_and_median_raises_without_holdout():
    """Beide None ohne holdout ⇒ ValueError (kein Platzhalter im Reward-Ausdruck, #594/#613)."""
    with pytest.raises(ValueError, match="is_sortino_median is None"):
        compute_reward(_m(is_pooled=None, is_median=None, oos_sortino=1.5),
                       universe_size=1, weights=_W, risk_dd_cap=0.3, tournament_cfg=_CFG)


# ── Fail-loud Kohärenz-Assertion (analog _assert_sortino_return_coherence) ───────────────────────
def test_coherence_assertion_flags_basis_mismatch(caplog):
    with caplog.at_level(logging.ERROR, logger="optimizer"):
        violated = _assert_is_oos_sortino_coherence(
            "trade_sequential", -13.2, "pooled_equity_curve", 1.2)
    assert violated is True
    assert any("IS_OOS_SORTINO_AGGREGATION_INCOHERENCE" in r.getMessage() for r in caplog.records)


def test_coherence_assertion_ok_same_basis():
    assert _assert_is_oos_sortino_coherence(
        "pooled_equity_curve", -1.0, "pooled_equity_curve", 1.0) is False


def test_coherence_assertion_none_safe():
    # Ein None-Sortino (undefiniert) kann nicht inkohärent sein ⇒ keine Verletzung.
    assert _assert_is_oos_sortino_coherence(
        "pooled_equity_curve", None, "trade_sequential", 1.0) is False
    assert _assert_is_oos_sortino_coherence(
        None, -1.0, None, 1.0) is False


# ── parse_tournament liest median_is_sortino_pooled + Aggregations-Basen ─────────────────────────
def test_parse_reads_pooled_sortino_and_basis(tmp_path):
    data = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": -13.2, "median_is_sortino_pooled": 1.1,
        "is_sortino_aggregation_basis": "pooled_equity_curve",
        "oos_metrics": {"sortino_ratio": 1.2, "total_return": 0.03, "max_drawdown": 0.02,
                        "total_trades": 30, "sortino_aggregation_basis": "pooled_equity_curve"}}}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data), "utf-8")
    m = parse_tournament(p)
    assert m.is_sortino_median == -13.2          # forensisch erhalten
    assert m.is_sortino_pooled == 1.1            # kohärente Divergenz-Grösse
    assert m.is_sortino_aggregation_basis == "pooled_equity_curve"
    assert m.oos_sortino_aggregation_basis == "pooled_equity_curve"


def test_parse_pooled_absent_is_none(tmp_path):
    """Legacy-JSON ohne median_is_sortino_pooled ⇒ is_sortino_pooled=None (reward fällt auf Median)."""
    data = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": 2.0,
        "oos_metrics": {"sortino_ratio": 1.2, "total_return": 0.03, "max_drawdown": 0.02,
                        "total_trades": 30}}}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data), "utf-8")
    m = parse_tournament(p)
    assert m.is_sortino_pooled is None


# ── Cap-Rekalibrierung (#613) ────────────────────────────────────────────────────────────────────
def test_penalty_relative_cap_recalibrated_and_documented():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["penalty_relative_cap"] == 1.0, "penalty_relative_cap auf 1.0 rekalibriert (#613)"
    doc = cfg["_schema"]["fields"]["penalty_relative_cap"].lower()
    assert "#613" in doc and "kommensurabel" in doc, \
        "Schema muss die #613-Rekalibrierung dokumentieren"
