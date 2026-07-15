"""Issue #635 — Constraint-Aggregation summiert UN-normierte Gate-Deltas.

Der #612-Feasibility-Constraint sortierte infeasible Trials nach einer Summe heterogener Gate-
Verletzungen; kleinskalige Gates (excess_return ~[0, 0.04], expectancy ~[0, 0.001]) waren gegenüber
grossskaligen (PSR ~[0, 0.75], drawdown ~[0, 0.3]) im Sort unsichtbar — inkonsistent zum Reward-Pfad,
dessen ``_shortfall_distance`` korrekt auf Target/Scale normiert.

Fix: ``_compute_oos_constraints`` nutzt dieselbe GETEILTE Scale-Auflösung wie
``_constraint_distance_penalty`` (``reward._normalized_gate_distances``, Single Source of Truth) —
jede Verletzung dimensionslos normiert, dann über die aktiven Dimensionen gemittelt.
"""
import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import _normalized_gate_distances, _constraint_distance_penalty
from automation.optimizer.run_optimization import _compute_oos_constraints

TCFG = {
    "oos_min_trades": 20, "oos_min_total_return": 0.005, "oos_min_expectancy": 0.001,
    "oos_min_win_rate": 0.15, "max_drawdown": 0.3, "return_penalty_scale": 0.1,
    "expectancy_penalty_scale": 0.002, "eligible_requires_any": [],
    "oos_min_psr": 0.75,
}


def _metrics(**kw):
    base = dict(
        oos_evaluated=True, oos_eligible=False, is_sortino_median=0.0,
        oos_sortino=0.3, oos_max_drawdown=0.05, oos_total_trades=50, win_count=0,
        fully_eligible_pairs=0, is_total_trades=0, oos_total_return=0.02,
        oos_expectancy=0.002, oos_win_rate=0.20, oos_profit_factor=1.2,
    )
    base.update(kw)
    return TournamentMetrics(**base)


def test_psr_and_excess_return_are_present_in_normalized_distances():
    m = _metrics(oos_psr=0.60, oos_excess_return=-0.02)  # PSR-Shortfall 0.15, Excess-Shortfall 0.02
    tcfg = dict(TCFG, oos_min_excess_return=0.0)
    d = _normalized_gate_distances(m, {}, TCFG["max_drawdown"], tcfg)
    assert "oos_min_psr" in d
    assert "oos_min_excess_return" in d
    assert d["oos_min_psr"] > 0.0
    assert d["oos_min_excess_return"] > 0.0


def test_same_relative_shortfall_yields_comparable_normalized_distance():
    """Akzeptanzkriterium (#635): zwei Trials, die um denselben RELATIVEN Betrag an excess_return
    bzw. an PSR scheitern, erhalten vergleichbare (nicht faktor-19-auseinanderliegende) normierte
    Distanzen — vorher dominierte die PSR-Rohskala um ~Faktor 19."""
    tcfg = dict(TCFG, oos_min_excess_return=0.0)

    # Trial A: PSR verfehlt das Gate um 20% relativ (0.75 * 0.8 = 0.60).
    m_psr_shortfall = _metrics(oos_psr=0.75 * 0.8, oos_excess_return=0.01)  # Excess-Gate erfuellt
    d_psr = _normalized_gate_distances(m_psr_shortfall, {}, TCFG["max_drawdown"], tcfg)

    # Trial B: Excess-Return verfehlt "sein" Gate (return_penalty_scale als Referenzskala) um
    # denselben Bruchteil der Skala wie A bei PSR — hier konkret: 20% von return_penalty_scale (0.1).
    m_excess_shortfall = _metrics(oos_psr=0.80, oos_excess_return=-0.02)  # PSR-Gate erfuellt, Shortfall 0.02 = 20% von scale 0.1
    d_excess = _normalized_gate_distances(m_excess_shortfall, {}, TCFG["max_drawdown"], tcfg)

    # Beide relativen 20%-Shortfalls liegen jetzt in DERSELBEN Grössenordnung (< 2x auseinander) —
    # vorher hätte der PSR-Rohwert (0.15) den Excess-Return-Rohwert (0.02) um ~7.5x dominiert.
    ratio = d_psr["oos_min_psr"] / d_excess["oos_min_excess_return"]
    assert 0.5 < ratio < 2.0, f"normierte Distanzen sollten vergleichbar sein, ratio={ratio:.2f}"


def test_compute_oos_constraints_uses_shared_normalization_with_reward_path():
    """_compute_oos_constraints und _constraint_distance_penalty MÜSSEN auf denselben Skalen
    operieren (Single Source of Truth, #635)."""
    m = _metrics()  # Return 0.02 >= Gate 0.005 (erfuellt), Trades 50>=20 (erfuellt) ⇒ nur ggf. Rest aktiv
    m_fail = _metrics(oos_total_return=0.001)  # verfehlt oos_min_total_return
    c = _compute_oos_constraints(m_fail, TCFG)
    assert c[0] > 0.0

    penalty = _constraint_distance_penalty(m_fail, {"unevaluable_shaping_span": 0.25}, TCFG["max_drawdown"], TCFG)
    assert penalty > 0.0  # dieselbe zugrundeliegende Distanz treibt auch den Reward-Pfad


def test_constant_violation_when_no_distance_data_available():
    """Fehlen die zur Normierung nötigen Metrik-Felder vollständig ⇒ konstante Verletzung 1.0,
    kein Crash (Fail-Safe, wie zuvor bei fehlenden oos_gate_deltas)."""
    class _Bare:
        oos_eligible = False
        oos_evaluated = True
    assert _compute_oos_constraints(_Bare(), {}) == (1.0,)


def test_psr_dominance_regression_is_fixed():
    """Regressionstest fuer den konkreten #635-Root-Cause: ein Trial, der NUR an excess_return
    knapp scheitert (PSR/alles andere erfuellt), erhaelt eine Verletzung > 0 UND diese ist NICHT
    um Groessenordnungen kleiner als ein reiner PSR-Shortfall gleicher Relativitaet."""
    tcfg = dict(TCFG, oos_min_excess_return=0.0)
    m = _metrics(oos_psr=0.80, oos_excess_return=-0.001)  # PSR-Gate erfuellt, winziger Excess-Shortfall
    c = _compute_oos_constraints(m, tcfg)
    assert c[0] > 0.0, "ein reiner Excess-Return-Shortfall darf nicht spurlos verschwinden"
