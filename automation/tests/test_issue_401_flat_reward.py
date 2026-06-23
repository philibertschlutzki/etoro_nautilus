"""Issue #401 — Flat Reward Landscape (-9.75) zerstoert TPE-Optimierung.

Zwei Wurzeln, beide regressionsgesichert:

  Part 1 (backtest_runner._calculate_stats): das hartcodierte 'n < 5'-Sortino-Limit ist jetzt
          deklarativ (tournament.json['sortino_min_trades'], Zero-Hardcoding). Zero-Loss bleibt
          weiterhin None (Issue #209 unangetastet).

  Part 2 (optimizer.reward.compute_reward): ein OOS-Sample, das evaluated UND eligible ist,
          dessen Sortino aber mathematisch undefiniert ist, kollabiert nicht mehr auf den
          flachen Unevaluable-Floor (-9.75), sondern erhaelt — config-gegated ueber
          optimizer.json['oos_sortino_fallback'] — den geclippten oos_total_return als
          evaluable Base. Reine Arithmetik via DI-Weights (kein File-IO, kein Backtest).
"""
import json
from pathlib import Path

import pytest

from automation.backtest_runner import _calculate_stats
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward


# DI-Weights (analog test_reward_shaping); 'oos_sortino_fallback' bewusst NICHT gesetzt,
# damit der Default-Pfad (Legacy-Penalty) explizit getestet werden kann.
W = {
    "penalty_unevaluable_oos": -2.0,
    "sortino_clip_abs": 3.0,
    "penalty_overfit_weight": 1.0,
    "penalty_dd_weight": 1.0,
    "bonus_coverage_weight": 0.5,
    "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 1e-3,
    "lambda_reg": 0.25,
    "oos_min_trades": 20,
}


def _m(**kw):
    base = dict(oos_evaluated=False, oos_eligible=False, is_sortino_median=0.0,
                oos_sortino=None, oos_max_drawdown=0.0, oos_total_trades=0,
                win_count=0, fully_eligible_pairs=0, is_total_trades=0, is_max_trades=0,
                oos_total_return=0.0)
    base.update(kw)
    return TournamentMetrics(**base)


# ---------------------------------------------------------------------------
# Part 1 — _calculate_stats: konfigurierbares Sortino-Mindest-Sample
# ---------------------------------------------------------------------------

def test_sortino_threshold_is_configurable_low_sample():
    """n=3, 1 Loss: mit sortino_min_trades=2 wird ein Sortino berechnet (vorher hartes None)."""
    pnl_list = [10.0, 10.0, -2.0]  # n=3, losses_count=1, net positive
    stats = _calculate_stats(pnl_list, hold_list=[], starting_capital=10000.0,
                             min_trades_for_sortino=2)
    assert stats["sortino_ratio"] is not None
    assert stats["sortino_ratio"] <= 50.0


def test_sortino_threshold_legacy_value_preserved():
    """Mit dem Legacy-Wert 5 bleibt n=3 strikt None — kein Verhaltensbruch fuer Altpfade."""
    pnl_list = [10.0, 10.0, -2.0]  # n=3 < 5
    stats = _calculate_stats(pnl_list, hold_list=[], starting_capital=10000.0,
                             min_trades_for_sortino=5)
    assert stats["sortino_ratio"] is None


def test_zero_loss_still_none_issue_209_intact():
    """Zero-Loss ⇒ keine Downside-Deviation ⇒ Sortino bleibt None, unabhaengig vom Threshold
    (Issue #209 unangetastet; der Fallback lebt ausschliesslich im Reward, nicht in den Stats)."""
    pnl_list = [10.0, 10.0, 10.0]  # n=3, 0 Losses
    stats = _calculate_stats(pnl_list, hold_list=[], starting_capital=10000.0,
                             min_trades_for_sortino=2)
    assert stats["sortino_ratio"] is None


# ---------------------------------------------------------------------------
# Part 2 — compute_reward: Zero-Loss/Sub-Threshold-Fallback statt -9.75-Floor
# ---------------------------------------------------------------------------

def test_fallback_rescues_eligible_zero_loss_trial():
    """oos_evaluated ∧ oos_eligible ∧ oos_sortino is None, fallback aktiv ⇒ evaluable Base
    aus geclipptem oos_total_return (Per-Symbol-Pfad: kein Coverage-, kein param_pen-Term)."""
    weights = {**W, "oos_sortino_fallback": "total_return"}
    m = _m(oos_evaluated=True, oos_eligible=True, oos_sortino=None,
           oos_total_return=0.02, oos_total_trades=3, win_count=1)
    r = compute_reward(m, universe_size=1, weights=weights, risk_dd_cap=0.30)
    assert r == pytest.approx(0.02)


def test_without_fallback_key_legacy_penalty_path():
    """Fehlt der Schluessel, bleibt der exakte Legacy-Penalty-Pfad erhalten (Rueckwaerts-Kompat)."""
    m = _m(oos_evaluated=True, oos_eligible=True, oos_sortino=None,
           oos_total_return=0.02, oos_total_trades=2, win_count=1)
    r = compute_reward(m, universe_size=1, weights=W, risk_dd_cap=0.30)
    # trade_progress = min(1, 2/20) = 0.1 ; shaping = 0.25 * 0.1 = 0.025
    assert r == pytest.approx(-2.0 + 0.25 * (2 / 20))


def test_fallback_strictly_beats_legacy_penalty():
    """Kernaussage des Fixes: identische Metriken werden durch den Fallback aus dem Floor gerettet."""
    m = _m(oos_evaluated=True, oos_eligible=True, oos_sortino=None,
           oos_total_return=0.02, oos_total_trades=3, win_count=1)
    r_fb = compute_reward(m, universe_size=1, weights={**W, "oos_sortino_fallback": "total_return"},
                          risk_dd_cap=0.30)
    r_legacy = compute_reward(m, universe_size=1, weights=W, risk_dd_cap=0.30)
    assert r_fb > r_legacy


def test_real_sortino_winner_dominates_fallback():
    """Ein echter risiko-adjustierter Sieger (Sortino 3.0) schlaegt den Zero-Loss-Fallback —
    die Reward-Ordnung bleibt erhalten, keine Zero-Loss-Fluke ueberholt echten Edge."""
    weights = {**W, "oos_sortino_fallback": "total_return"}
    fallback = _m(oos_evaluated=True, oos_eligible=True, oos_sortino=None,
                  oos_total_return=0.02, oos_total_trades=3, win_count=1)
    winner = _m(oos_evaluated=True, oos_eligible=True, oos_sortino=3.0,
                oos_total_return=0.5, oos_total_trades=40, win_count=1)
    r_fb = compute_reward(fallback, universe_size=1, weights=weights, risk_dd_cap=0.30)
    r_win = compute_reward(winner, universe_size=1, weights=weights, risk_dd_cap=0.30)
    assert r_win > r_fb


def test_ineligible_zero_loss_still_penalized():
    """Nur eligible Samples werden gerettet — ein evaluiertes, aber NICHT eligibles Zero-Loss-
    Sample (z.B. Micro-Sizing, Pitfall #58) bleibt auf dem Penalty-Pfad (kein Gate-Bypass)."""
    weights = {**W, "oos_sortino_fallback": "total_return"}
    m = _m(oos_evaluated=True, oos_eligible=False, oos_sortino=None,
           oos_total_return=0.02, oos_total_trades=3, win_count=0)
    r = compute_reward(m, universe_size=1, weights=weights, risk_dd_cap=0.30)
    assert r < 0.0  # Penalty-Pfad, nicht der Fallback


# ---------------------------------------------------------------------------
# Integrations-Sanity: die deklarativen Schluessel existieren in den realen Configs
# ---------------------------------------------------------------------------

def test_declarative_keys_present_in_shipped_config():
    cfg_dir = Path(__file__).resolve().parent.parent / "config"
    tournament = json.loads((cfg_dir / "tournament.json").read_text("utf-8"))
    optimizer = json.loads((cfg_dir / "optimizer.json").read_text("utf-8"))
    assert tournament.get("sortino_min_trades") is not None
    assert optimizer.get("oos_sortino_fallback") == "total_return"
