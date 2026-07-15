import pytest
from automation.optimizer.reward import compute_reward
from dataclasses import dataclass

@dataclass
class DummyMetrics:
    oos_evaluated: bool = True
    oos_eligible: bool = False
    oos_total_trades: int = 0
    oos_total_return: float = 0.0
    oos_sortino: float = 0.0
    oos_max_drawdown: float = 0.0
    oos_win_rate: float = 0.0
    oos_profit_factor: float | None = 0.0
    oos_expectancy: float = 0.0

    is_total_trades: int = 0
    is_best_total_return: float = 0.0
    is_best_win_rate: float = 0.0
    is_sortino_median: float = 0.0
    win_count: int = 0

def get_weights():
    return {
        "penalty_unevaluable_oos": -10.0,
        "unevaluable_shaping_span": 0.25,
        "constraint_distance_penalty_weight": 0.25,
        "shaping_trade_target": 50,
        "sortino_clip_abs": 5.0,
        "penalty_overfit_weight": 0.5,
        "penalty_dd_weight": 8.0,
        "bonus_coverage_weight": 1.0,
    }

def get_tournament_cfg():
    # Issue #467/#468 — strikte OOS-Isolation: die Constraint-Distanz-Penalty liest ausschliesslich
    # OOS-gekeyte Schwellen (oos_min_*), niemals den IS-Fallback (min_*). Die Fixture muss daher die
    # OOS-Keys setzen, exakt wie die ausgelieferte tournament.json.
    return {
        "oos_min_trades": 3,
        "oos_min_total_return": 0.05,
        "oos_min_expectancy": 0.0,
        "oos_min_win_rate": 0.0,
        "max_drawdown": 0.2,
    }

def test_reward_no_inversion_property():
    """Issue #629 — die Anti-Gate-Gaming-Invariante ('eligible schlaegt jeden Failure') und die
    'evaluated bleibt >= unevaluable_ceiling'-Bandgrenze wurden ERSATZLOS gestrichen: sie waren die
    Reward-seitige Kodierung von Feasibility, die jetzt AUSSCHLIESSLICH der #612-Sampler-Constraint
    (oos_constraint_violations) traegt. compute_reward liefert seither EIN stetiges, nicht-gesaettigtes
    Qualitaetsziel — ein katastrophaler evaluierter Trial (riesiger Drawdown/negativer Return) darf
    legitim UNTER dem Unevaluable-Reward landen (siehe test_catastrophic_trial_can_fall_below_unevaluable
    unten). Was bleibt: der Near-Miss/Far-Miss-Gradient (naeher am Gate ⇒ besser bewertet) UND — in
    dieser Fixture, weil der eligible Trial auch auf jeder zugrundeliegenden Kennzahl mindestens
    gleichauf liegt — eligible > near/far-miss als KONSEQUENZ der Kennzahlen, nicht als erzwungene
    Bandgrenze."""
    weights = get_weights()
    cfg = get_tournament_cfg()

    # 2. Many Trades, Near Miss (Evaluated, not eligible)
    m_near_miss = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=False,
        oos_total_trades=242,
        oos_total_return=0.04,  # misses 0.05
        oos_sortino=1.0,
        oos_max_drawdown=0.1,
        oos_win_rate=0.5,
        is_total_trades=500
    )
    r_near_miss = compute_reward(m_near_miss, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    # 3. Many Trades, Far Miss (Evaluated, not eligible)
    m_far_miss = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=False,
        oos_total_trades=242,
        oos_total_return=-0.50,  # huge miss
        oos_sortino=-1.0,
        oos_max_drawdown=0.5,
        oos_win_rate=0.2,
        is_total_trades=500
    )
    r_far_miss = compute_reward(m_far_miss, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    # 4. Eligible (Evaluated, eligible) — mindestens gleichauf mit near_miss auf jeder Kennzahl,
    # strikt besser als far_miss.
    m_eligible = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=True,
        oos_total_trades=242,
        oos_total_return=0.1,
        oos_sortino=1.0,
        oos_max_drawdown=0.1,
        oos_win_rate=0.5,
        is_total_trades=500
    )
    r_eligible = compute_reward(m_eligible, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    # Konsequenz (nicht erzwungene Invariante): bei gleichwertigen/besseren Kennzahlen UND dem Wegfall
    # der Gate-Distanzstrafe rankt eligible hier ueber near/far-miss.
    assert r_eligible > r_near_miss, f"r_eligible={r_eligible} <= r_near_miss={r_near_miss}"
    assert r_eligible > r_far_miss, f"r_eligible={r_eligible} <= r_far_miss={r_far_miss}"

    # Gradient preservation: near-miss bleibt naeher an eligible als ein katastrophaler far-miss.
    assert r_near_miss > r_far_miss, f"r_near_miss={r_near_miss} <= r_far_miss={r_far_miss}"


def test_catastrophic_trial_can_fall_below_unevaluable_ceiling():
    """Issue #629 — Regressionstest fuer den Kern der Aenderung: ohne Floor/Band darf ein
    katastrophaler evaluierter-aber-ineligibler Trial (riesiger Drawdown, stark negativer Return)
    LEGITIM unter das, was ein niemals-evaluierter Trial (fixer Shaping-Wert) erhaelt, fallen. Der
    alte Code erzwang das Gegenteil (r_far_miss >= unevaluable_ceiling) — genau die Reward-Klippe,
    die #629 entfernt. Die Feasibility beider Trials (unevaluable UND far_miss) wird ausschliesslich
    ueber den #612-Sampler-Constraint sichergestellt, nicht ueber diesen Reward-Wert."""
    weights = get_weights()
    cfg = get_tournament_cfg()

    # Kein Trade ⇒ progress=0 ⇒ shaping=0 ⇒ r_0_trades == penalty_unevaluable_oos (Boden des Bandes).
    m_0_trades = DummyMetrics(oos_evaluated=False, oos_eligible=False, oos_total_trades=0, is_total_trades=0)
    r_0_trades = compute_reward(m_0_trades, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)
    assert r_0_trades == pytest.approx(weights["penalty_unevaluable_oos"], abs=1e-9)

    m_far_miss = DummyMetrics(
        oos_evaluated=True, oos_eligible=False, oos_total_trades=242,
        oos_total_return=-0.50, oos_sortino=-1.0, oos_max_drawdown=0.5, oos_win_rate=0.2,
        is_total_trades=500,
    )
    r_far_miss = compute_reward(m_far_miss, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)
    assert r_far_miss < r_0_trades


def test_reward_gradient_does_not_saturate():
    weights = get_weights()
    cfg = get_tournament_cfg()

    # We want to test that different gaps still yield a difference (linear penalty).
    # oos_min_total_return = 0.05
    # distance = (gap / target) (linear)
    m_penalty_small = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=False,
        oos_total_trades=242,
        oos_total_return=-0.40,  # gap = 0.45
        oos_sortino=1.0,
        oos_max_drawdown=0.1,
        oos_win_rate=0.5,
        is_total_trades=500
    )
    r_penalty_small = compute_reward(m_penalty_small, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    m_penalty_large = DummyMetrics(
        oos_evaluated=True,
        oos_eligible=False,
        oos_total_trades=242,
        oos_total_return=-0.95, # gap = 1.0
        oos_sortino=1.0,
        oos_max_drawdown=0.1,
        oos_win_rate=0.5,
        is_total_trades=500
    )
    r_penalty_large = compute_reward(m_penalty_large, universe_size=1, weights=weights, risk_dd_cap=cfg["max_drawdown"], tournament_cfg=cfg)

    assert r_penalty_small > r_penalty_large, f"Gradient saturated! r_penalty_small={r_penalty_small}, r_penalty_large={r_penalty_large}"
