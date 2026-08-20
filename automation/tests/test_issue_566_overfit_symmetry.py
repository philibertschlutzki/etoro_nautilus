"""Issue #565 — Symmetrische Divergenz-Strafe + Fold-Dispersions-Strafe gegen OOS-Glück.

Der einseitige overfit_gap = max(0, IS − OOS) bestrafte NUR IS≫OOS. Der Fall OOS≫IS (Overfit auf
ein günstiges OOS-Sub-Fenster, das am Holdout revertiert) bekam gap=0 und vollen Kredit. Zusätzlich
floss nur der Median-OOS-Sortino in den Reward, nicht die Streuung über die Folds — ein einzelner
starker Fold konnte den Wert tragen. Fix: symmetrische |IS − OOS|-Strafe (OOS≫IS milder) plus eine
Fold-Dispersions-Strafe (pstdev der Per-Fold-Sortinos).

Akzeptanzkriterien (Issue #565):
- Property: OOS=5, IS=1 ⇒ reward messbar niedriger als OOS=IS=5 (OOS-Glück wird bestraft).
- Fold-Dispersion: gleicher Median-OOS-Sortino, unterschiedliche Fold-Streuung ⇒ unterschiedlicher
  Reward (niedrigere Streuung → höher).
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

WEIGHTS = {
    "penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001, "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0, "bonus_coverage_weight": 1.0,
    "overfit_divergence_mode": "symmetric", "overfit_oos_luck_weight": 0.25,
    "fold_dispersion_weight": 0.5, "w_ret": 2.0,
}
CFG = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}


def _m(oos_sortino, is_sortino_median, fold_sortinos=None, oos_total_return=0.01):
    # Issue #589 — die Fold-Dispersion läuft nun über die per-Fold-RETURNS (+ oos_folds_total); die
    # synthetische ``fold_sortinos``-Streuung wird hier zusätzlich als Return-Serie gespiegelt.
    folds = tuple(fold_sortinos) if fold_sortinos else ()
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=is_sortino_median,
        oos_sortino=oos_sortino, oos_max_drawdown=0.02, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=oos_total_return,
        oos_expectancy=0.001, oos_win_rate=0.5, oos_profit_factor=1.5,
        oos_fold_sortinos=folds,
        oos_fold_returns=folds,
        oos_folds_total=len(folds),
    )


def _reward(m, weights=WEIGHTS):
    return compute_reward(m, universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=CFG)


# ── OOS-Glück (OOS ≫ IS) wird bestraft ───────────────────────────────────────────────────────
def test_oos_luck_penalized_vs_aligned():
    """OOS=5, IS=1 (Glücks-Sub-Fenster) ⇒ Reward niedriger als OOS=5, IS=5 (konsistent)."""
    lucky = _reward(_m(oos_sortino=5.0, is_sortino_median=1.0))
    aligned = _reward(_m(oos_sortino=5.0, is_sortino_median=5.0))
    assert lucky < aligned, "OOS≫IS (Overfit auf günstiges Fenster) muss bestraft werden."


def test_symmetric_divergence_penalizes_both_directions():
    """|IS − base| bestraft IS≫OOS UND OOS≫IS; die perfekt konsistente Ausrichtung ist optimal."""
    # base(5) ≈ 4.407. Ausrichtung genau an base ⇒ Divergenz 0 (kein Strafterm).
    import math
    base5 = 5.0 * math.asinh(5.0 / 5.0)
    aligned = _reward(_m(oos_sortino=5.0, is_sortino_median=base5))
    is_high = _reward(_m(oos_sortino=5.0, is_sortino_median=base5 + 3.0))  # IS≫OOS
    is_low = _reward(_m(oos_sortino=5.0, is_sortino_median=base5 - 3.0))   # OOS≫IS
    assert aligned > is_high
    assert aligned > is_low


# ── Fold-Dispersion: konsistente Folds > glückliche Folds ────────────────────────────────────
def test_fold_dispersion_penalizes_lucky_spread():
    """Vormals (Akzeptanzkriterium #565): gleicher Median-OOS-Sortino (5), aber unterschiedliche
    Fold-Streuung ⇒ konsistent gewinnt.

    Issue #1068/#1218 (Katalog #1196-1221, supersedes #565s Fold-Dispersions-Hälfte) —
    ``fold_dispersion`` trug auf der seit #614/#630 geltenden psr_z-Base < 1% der Reward-Streuung
    in 14/14 Referenz-Läufen und ist seither retiriert (code-seitig auf 0.0 gezwungen,
    ``reward.RETIRED_REWARD_TERMS``) — konsistente und glückliche Fold-Muster sind seither
    ununterscheidbar. Die symmetrische Divergenz-Strafe (#565s ANDERE Hälfte, siehe
    ``test_oos_luck_penalized_vs_aligned``/``test_symmetric_divergence_penalizes_both_directions``
    oben) bleibt davon unberührt aktiv."""
    consistent = _reward(_m(5.0, 5.0, fold_sortinos=[5.0, 5.0, 5.0, 5.0]))   # pstdev 0
    lucky = _reward(_m(5.0, 5.0, fold_sortinos=[1.0, 3.0, 7.0, 9.0]))         # Median 5, hohe Streuung
    assert consistent == pytest.approx(lucky), (
        "Seit #1068/#1218 ist fold_dispersion retiriert -- die Fold-Streuung darf den Reward "
        "nicht mehr unterscheiden.")


def test_fold_dispersion_requires_two_folds():
    """< 2 Fold-Sortinos ⇒ keine Dispersions-Strafe (bit-identisch)."""
    one_fold = _reward(_m(5.0, 5.0, fold_sortinos=[5.0]))
    no_folds = _reward(_m(5.0, 5.0, fold_sortinos=None))
    assert one_fold == pytest.approx(no_folds)


# ── Der Defekt selbst: Legacy-einseitig belohnt OOS-Glück SOGAR höher ─────────────────────────
def test_legacy_one_sided_rewards_oos_luck_higher_the_defect():
    """Ohne die neuen Keys reproduziert der Reward den Ausgangsdefekt: der Glücks-Trial (OOS≫IS)
    erhält KEINEN overfit_gap-Malus (max(0, IS−base)=0) und schlägt damit sogar den konsistenten
    Trial, der einen IS≫OOS-Malus trägt — genau die Inversion, die #565 behebt."""
    legacy = {k: v for k, v in WEIGHTS.items()
              if k not in ("overfit_divergence_mode", "overfit_oos_luck_weight",
                           "fold_dispersion_weight")}
    lucky = _reward(_m(5.0, 1.0, fold_sortinos=[1.0, 3.0, 7.0, 9.0]), weights=legacy)
    aligned = _reward(_m(5.0, 5.0, fold_sortinos=[5.0, 5.0, 5.0, 5.0]), weights=legacy)
    assert lucky > aligned  # Defekt: OOS-Glück wird belohnt statt bestraft
    # Der symmetrische Modus (Produktion) kehrt das um (siehe test_oos_luck_penalized_vs_aligned).


def test_production_config_enables_symmetry_and_dispersion():
    """Issue #1068/#1218 — fold_dispersion_weight ist seither bewusst 0.0 (Term retiriert); die
    symmetrische Divergenz-Strafe (die andere #565-Hälfte) bleibt unveraendert aktiv."""
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg.get("overfit_divergence_mode") == "symmetric"
    assert cfg.get("fold_dispersion_weight") == 0.0
