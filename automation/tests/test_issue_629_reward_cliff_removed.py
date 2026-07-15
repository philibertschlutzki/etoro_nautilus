"""Issue #629 — Reward-Klippe redundant zum #612-Constraint; flaches Ziel im Feasible-Bereich.

Root-Cause: Feasibility lief NACH #612 doppelt — einmal nativ über den Sampler-Constraint
(``oos_constraint_violations``), einmal als künstliche Reward-Band-Ordnung
(``unevaluable_ceiling < failure_ceiling < evaluable_reward_floor``) in reward.py. Die Klippe
(~12–20 Reward-Einheiten an der Eligibility-Grenze) verseuchte den TPE-Surrogat: der l(x)/g(x)-Split
modellierte die Gate-Passrate statt die eigentliche Qualität.

Fix (siehe reward.py compute_reward): ``_constraint_failure_reward``, ``_evaluable_floor`` und die
drei Band-Konstanten sind ENTFALLEN. Eligible UND evaluated-aber-ineligible Trials teilen sich
denselben Qualitäts-Kern; ineligible Trials erhalten zusätzlich die bestehende, kontinuierliche
Near-Miss-Distanzstrafe (``_constraint_distance_penalty``) additiv obendrauf. Feasibility ist
AUSSCHLIESSLICH ein #612-Sampler-Constraint.

Dieses Modul verifiziert die Akzeptanzkriterien aus Issue #629 direkt.
"""
import math
import statistics

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

W = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "constraint_distance_penalty_weight": 0.25,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
    "penalty_turnover_weight": 0.0, "w_ret": 0.0,
}
TCFG = {
    "oos_min_trades": 20, "oos_min_total_return": 0.005, "oos_min_expectancy": 0.0,
    "oos_min_win_rate": 0.0, "max_drawdown": 0.3, "eligible_requires_any": [],
}


def _spearman(xs, ys) -> float:
    """Rangkorrelation ohne scipy-Abhängigkeit (Pearson auf Rängen == Spearman)."""
    def _rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        for r, i in enumerate(order):
            ranks[i] = float(r)
        return ranks

    rx, ry = _rank(xs), _rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denom = math.sqrt(sum((a - mx) ** 2 for a in rx)) * math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / denom if denom > 0.0 else 0.0


def _trial(psr_z: float, *, eligible: bool) -> TournamentMetrics:
    # oos_total_return moves with psr_z too, so the eligibility gate (oos_min_total_return) and the
    # near-miss distance penalty for ineligible trials remain economically coherent with quality.
    ret = 0.01 + 0.002 * psr_z
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=eligible, is_sortino_median=0.0,
        oos_sortino=psr_z, oos_max_drawdown=0.05, oos_total_trades=50, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=ret,
        oos_win_rate=0.4, oos_profit_factor=1.3, oos_psr_z=psr_z,
    )


def test_reward_cliff_machinery_removed_from_module():
    import automation.optimizer.reward as reward_mod

    assert not hasattr(reward_mod, "_constraint_failure_reward")
    assert not hasattr(reward_mod, "_evaluable_floor")


def test_no_reward_band_config_keys_survive():
    import json
    from pathlib import Path

    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    for dead_key in ("evaluable_reward_floor", "evaluable_floor_epsilon",
                     "failure_reward_mode", "failure_return_softplus_scale",
                     "failure_return_penalty_weight"):
        assert dead_key not in cfg


def test_spearman_rank_correlation_on_synthetic_monotone_landscape():
    """Akzeptanzkriterium (#629): ein Trial-Set mit bekanntem PSR-Ranking wird korrekt geordnet
    (Spearman(trial_rank, reward) > 0.8 auf einer synthetischen monotonen Landschaft über
    feasible+infeasible; vor #629 lag dieser Wert bei 0.04–0.38, weil die Klippe die Gate-Passrate
    statt die Qualität dominierte)."""
    # 20 Trials, PSR-z linear von +2.0 (bester) bis −2.0 (schlechtester); die oberen 12 (psr_z > 0.4)
    # sind eligible, der Rest evaluated-aber-ineligible — ein realistischer gemischter Study-Zustand.
    n = 20
    psr_zs = [2.0 - i * (4.0 / (n - 1)) for i in range(n)]
    trials = [_trial(z, eligible=(z > 0.4)) for z in psr_zs]

    rewards = [compute_reward(t, universe_size=1, weights=W, risk_dd_cap=0.3, tournament_cfg=TCFG)
               for t in trials]

    # true_rank: Index 0 ist der beste Trial (hoechster psr_z) ⇒ absteigend sortiert erwartet.
    corr = _spearman(list(range(n)), [-r for r in rewards])
    assert corr > 0.8, f"Spearman(trial_rank, reward) = {corr:.3f} muss > 0.8 sein (Klippe entfernt)."


def test_no_discontinuous_jump_at_the_eligibility_boundary():
    """Die zwei benachbarten Trials direkt an der Eligibility-Grenze (knapp eligible / knapp
    ineligible, sonst identische Qualität) duerfen sich nur um die kleine Near-Miss-Distanzstrafe
    unterscheiden — NICHT um eine ~12-20-Einheiten-Klippe, wie vor #629."""
    just_eligible = _trial(0.41, eligible=True)
    just_ineligible = _trial(0.39, eligible=False)

    r_elig = compute_reward(just_eligible, universe_size=1, weights=W, risk_dd_cap=0.3, tournament_cfg=TCFG)
    r_inelig = compute_reward(just_ineligible, universe_size=1, weights=W, risk_dd_cap=0.3, tournament_cfg=TCFG)

    gap = r_elig - r_inelig
    assert 0.0 <= gap < 1.0, f"Reward-Gap an der Eligibility-Grenze ({gap:.4f}) sollte klein sein, keine Klippe."
