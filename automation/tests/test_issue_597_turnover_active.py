"""Issue #597 — Turnover-Strafe inaktiv, Drawdown-Term tot, Randlösungen unbeobachtet.

(A) ``penalty_turnover_weight`` FEHLTE in optimizer.json ⇒ Default 0.0 ⇒ die Turnover-Strafe (#509) war
    seit Einführung wirkungslos, JEDE Randlösung zeigte Richtung 'Trade-Frequenz maximieren'.
(B) ``dd_penalty`` normierte auf den 30 %-Gate-Cap, während der reale (portfolio-relative) DD 0.6–2.4 %
    war ⇒ vier Größenordnungen zu klein. Fix: eigene ``dd_reward_scale`` (realisierte Risiko-Skala).
(C) ``boundary_hit_fraction``-Telemetrie macht Randlösungen sichtbar (WARNING > 0.3).
"""
import json
import math
from pathlib import Path

from automation.optimizer.reward import compute_reward, _dd_penalty
from automation.optimizer.run_optimization import _boundary_hit_fraction
from automation.optimizer.parsing import TournamentMetrics

CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
BACKTEST = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))


def test_penalty_turnover_weight_set_and_cost_derived():
    """penalty_turnover_weight ist gesetzt und aus den Round-Trip-Kosten abgeleitet (c_rt = commission
    + spread als Fraktion)."""
    w = CFG["penalty_turnover_weight"]
    assert w and w > 0.0, "penalty_turnover_weight muss gesetzt (> 0) sein"
    c_rt = (BACKTEST["commission_bps"] + BACKTEST["spread_bps_by_symbol"]["TSLA.ETORO"]) / 10000.0
    assert math.isclose(w, c_rt, rel_tol=0.34), f"w={w} sollte ~c_rt={c_rt} sein (Kostenherleitung)"


def _m(trades, oos_total_return=0.05, dd=0.02, oos_sortino=1.5):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=1.5,
        oos_sortino=oos_sortino, oos_max_drawdown=dd, oos_total_trades=trades, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=oos_total_return)


def test_turnover_penalty_makes_fewer_trades_win():
    """Zwei Trials, identischer Return; A mit 300 Trades, B mit 60 ⇒ reward_B > reward_A."""
    a = compute_reward(_m(300), universe_size=1)   # weights=None ⇒ optimizer.json
    b = compute_reward(_m(60), universe_size=1)
    assert b > a, "mehr Trades (Cost-Drag) müssen bei gleichem Return schlechter bewertet werden"


def test_dd_penalty_in_same_order_of_magnitude_as_other_terms():
    """dd_penalty liegt bei einem typischen Trial in derselben Größenordnung wie die übrigen
    Strafterme (Faktor < 100), nicht > 1000 wie beim 30 %-Gate-Cap."""
    m = _m(60, dd=0.02)
    dd_pen = _dd_penalty(m, CFG, risk_dd_cap=0.30)
    # base ~ soft-scaled 1.5 (~1.4); ein anderer Strafterm (Turnover) ~ 60·3e-4·penalty_scale_vs_base.
    penalty_scale = CFG.get("penalty_scale_vs_base", 1.0)
    turnover = m.oos_total_trades * CFG["penalty_turnover_weight"] * penalty_scale
    assert dd_pen > 0.0
    assert dd_pen / max(turnover, 1e-9) < 100.0, "dd_penalty darf die übrigen Terme nicht um >100× dominieren"
    # Gegenprobe: auf dem Gate-Cap (0.30) normiert wäre dd_penalty (VOR #631-Rekalibrierung) ~4
    # Größenordnungen kleiner. Issue #631 — die #597-dd_reward_scale-Normierung bleibt strukturell
    # aktiv, wird aber zusätzlich mit penalty_scale_vs_base gegen die psr_z-Base-Streuung gedämpft
    # (die frühere 50×-Baseline schrumpft proportional zum Faktor).
    dd_pen_legacy = CFG["penalty_dd_weight"] * ((0.02 / 0.30) ** 2)
    assert dd_pen > dd_pen_legacy * 50 * penalty_scale


class _Trial:
    def __init__(self, params):
        self.params = params


class _Study:
    def __init__(self, params):
        self.best_trial = _Trial(params)


def test_boundary_hit_fraction_detects_edge_solution():
    """boundary_hit_fraction = Anteil der Gewinner-Parameter innerhalb 2 % einer Suchraumgrenze."""
    # SmaCrossoverStrategy: sma_period ∈ [5, 60], cooldown_bars ∈ [2, 36].
    at_edges = _Study({"sma_period": 5, "cooldown_bars": 36})     # beide am Rand ⇒ 1.0
    interior = _Study({"sma_period": 32, "cooldown_bars": 19})    # beide innen ⇒ 0.0
    assert _boundary_hit_fraction(at_edges, "SmaCrossoverStrategy") == 1.0
    assert _boundary_hit_fraction(interior, "SmaCrossoverStrategy") == 0.0
    half = _Study({"sma_period": 5, "cooldown_bars": 19})         # 1 von 2 am Rand ⇒ 0.5
    assert _boundary_hit_fraction(half, "SmaCrossoverStrategy") == 0.5
    # Unbekannte Strategie / kein Winner ⇒ None (defensiv).
    assert _boundary_hit_fraction(at_edges, "NoSuchStrategy") is None
    assert _boundary_hit_fraction(at_edges, None) is None
