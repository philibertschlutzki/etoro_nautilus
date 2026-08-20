"""Issue #597 — Turnover-Strafe inaktiv, Drawdown-Term tot, Randlösungen unbeobachtet.

(A) ``penalty_turnover_weight`` FEHLTE in optimizer.json ⇒ Default 0.0 ⇒ die Turnover-Strafe (#509) war
    seit Einführung wirkungslos, JEDE Randlösung zeigte Richtung 'Trade-Frequenz maximieren'.
(B) ``dd_penalty`` normierte auf den 30 %-Gate-Cap, während der reale (portfolio-relative) DD 0.6–2.4 %
    war ⇒ vier Größenordnungen zu klein. Fix: eigene ``dd_reward_scale`` (realisierte Risiko-Skala).
(C) ``boundary_hit_fraction``-Telemetrie macht Randlösungen sichtbar (WARNING > 0.3).
"""
import json
from pathlib import Path

from automation.optimizer.reward import compute_reward, _dd_penalty
from automation.optimizer.run_optimization import _boundary_hit_fraction
from automation.optimizer.parsing import TournamentMetrics

CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
BACKTEST = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))


def test_penalty_turnover_weight_is_deliberately_zero_since_1218():
    """Issue #1068/#1218 (Katalog #1196-1221, supersedes #597/#774) — turnover_penalty trug in
    14/14 Referenz-Laeufen < 1% der Reward-Streuung (kalibriert gegen die asinh-Sortino-Base vor
    #614/#630, seither obsolet). ``penalty_turnover_weight`` ist seither 0.0 — turnover ist jetzt
    (wie dd_penalty/time_box_penalty/tie_breaker) ein DOKUMENTIERT inerter/retirierter Term
    (reward.RETIRED_REWARD_TERMS), kein aktiver Kostendruck-Term mehr. Die vormalige
    Kostenherleitung (c_rt = commission + spread) bleibt NUR forensisch im reward.py-Docstring."""
    assert CFG["penalty_turnover_weight"] == 0.0


def _m(trades, oos_total_return=0.05, dd=0.02, oos_sortino=1.5):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=1.5,
        oos_sortino=oos_sortino, oos_max_drawdown=dd, oos_total_trades=trades, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=oos_total_return)


def test_turnover_penalty_no_longer_differentiates_trade_count_since_1218():
    """Issue #1068/#1218 — seit der Retirierung ist turnover_penalty IMMER 0.0 (code-seitig
    erzwungen, siehe reward.py), unabhaengig von oos_total_trades — zwei Trials mit identischem
    Return, aber unterschiedlicher Trade-Zahl, erhalten seither DENSELBEN Reward (kein
    Cost-Drag-Term mehr aktiv)."""
    a, terms_a = compute_reward(_m(300), universe_size=1, return_terms=True)  # weights=None ⇒ optimizer.json
    b, terms_b = compute_reward(_m(60), universe_size=1, return_terms=True)
    assert terms_a["turnover"] == 0.0
    assert terms_b["turnover"] == 0.0
    assert a == b


def test_dd_penalty_is_deliberately_inert_since_977():
    """Issue #977 (Katalog C, P0 HEADLINE, supersedes #597/#631) — dd_penalty dominierte die
    Zielfunktion um Faktor 2.7-8.5 gegenüber der Base, aber AUSSCHLIESSLICH im bereits verworfenen
    failure-Zweig (im eligiblen Zweig 1426x kleiner — keine unterscheidende Information dort, wo
    es zählt). Das Risiko ist bereits über das oos_max_drawdown-Gate abgedeckt; eine zusätzliche
    weiche Strafe war Doppelzählung (Pitfall #124). ``penalty_dd_weight`` ist seither 0.0 —
    dd_penalty ist jetzt (wie time_box_penalty/tie_breaker) ein DOKUMENTIERT inerter Term
    (invariants._CONFIGURED_INACTIVE_REWARD_TERMS), kein Kalibrier-Blindgänger mehr."""
    m = _m(60, dd=0.02)
    dd_pen = _dd_penalty(m, CFG, risk_dd_cap=0.30)
    assert CFG["penalty_dd_weight"] == 0.0
    assert dd_pen == 0.0


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
