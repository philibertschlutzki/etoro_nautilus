"""Issue #631 — #614-PSR-Migration hat die Straf-Ko-Kalibrierung zerstört.

Root-Cause: dd_penalty/fold_dispersion_weight/lambda_reg wurden gegen die alte, breiter gestreute
asinh-Sortino-Base kalibriert. Seit #614/#630 ist die Base ``psr_z`` mit einer VIEL engeren
realisierten Eligible-Kohorten-Streuung ⇒ die additiven Strafterme dominieren das Ranking, obwohl
sie nur Korrekturterme sein sollen (dieselbe Skalen-Inkohärenz wie beim Pre-#597-dd_penalty).

Fix: ein globaler ``penalty_scale_vs_base``-Faktor (reward.py ``_penalty_scale_vs_base``) dämpft
dd_penalty/turnover_penalty/fold_dispersion_penalty/param_pen gegen die realisierte Base-Streuung;
``assert_penalty_scale_calibrated`` prüft das fail-loud auf einem deklarativen Kalibrier-Fixture.
"""
import json
import math
import statistics
from pathlib import Path

import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import (
    compute_reward,
    assert_penalty_scale_calibrated,
    _penalty_scale_vs_base,
    _dd_penalty,
)

CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))

W_BASE = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "constraint_distance_penalty_weight": 0.25,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
    "penalty_turnover_weight": 0.0003, "w_ret": 0.0,
    "fold_dispersion_weight": 0.5, "fold_dispersion_scale": 0.03, "missing_fold_penalty_scale": 0.05,
    "dd_reward_scale": 0.03,
}
TCFG = {"oos_min_trades": 10, "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0,
        "oos_min_win_rate": 0.0, "max_drawdown": 0.3}


def _trial(psr_z, dd, n_trades, folds):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=0.0,
        oos_sortino=psr_z, oos_max_drawdown=dd, oos_total_trades=n_trades, win_count=1,
        fully_eligible_pairs=1, is_total_trades=n_trades, oos_total_return=0.01,
        oos_win_rate=0.4, oos_profit_factor=1.3, oos_psr_z=psr_z,
        oos_fold_returns=folds, oos_folds_total=4,
    )


# Realistische Eligible-Kohorte (N=40, seeded/deterministisch): psr_z gleichverteilt über dem
# 0.75-PSR-Gate-Quantil (Φ⁻¹(0.75)≈0.6745) bis deutlich darüber, realisierte Drawdown-/Turnover-/
# Fold-Spannen aus #597/#616. Die Nebenachsen sind UNABHÄNGIG von psr_z gezogen (sonst korreliert ein
# Strafterm durch reine Fixture-Konstruktion mit der Base, unabhängig davon, ob er tatsächlich
# dominiert) — ein grösseres N glättet das Rauschen einer kleinen Handauswahl.
def _synthetic_cohort(n=40, seed=7):
    import random as _random

    rng = _random.Random(seed)
    z_floor = 0.6744897501960817  # Φ⁻¹(0.75)
    out = []
    for _ in range(n):
        z = z_floor + rng.uniform(0.0, 1.0)
        dd = rng.uniform(0.006, 0.024)
        n_tr = rng.randint(20, 150)
        folds = tuple(rng.uniform(-0.02, 0.03) for _ in range(4))
        out.append((z, dd, n_tr, folds))
    return out


_COHORT = _synthetic_cohort()


def _cohort_terms(weights):
    return [
        compute_reward(_trial(z, dd, n, f), universe_size=1, weights=weights,
                       risk_dd_cap=TCFG["max_drawdown"], tournament_cfg=TCFG, return_terms=True)[1]
        for z, dd, n, f in _COHORT
    ]


def _corr(a, b):
    ma, mb = statistics.mean(a), statistics.mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return cov / (da * db) if da > 0 and db > 0 else 0.0


def test_default_weight_is_neutral_bit_identical():
    """Fehlt penalty_scale_vs_base ⇒ Faktor 1.0 (Legacy, bit-identisch, Zero-Hardcoding)."""
    assert _penalty_scale_vs_base({}) == 1.0
    assert _penalty_scale_vs_base({"penalty_scale_vs_base": 0.3}) == 0.3


def test_dd_penalty_scaled_by_factor():
    m = _trial(0.8, 0.02, 60, (0.01, 0.01, 0.01, 0.01))
    w_unscaled = dict(W_BASE)
    w_scaled = dict(W_BASE, penalty_scale_vs_base=0.2)
    unscaled = _dd_penalty(m, w_unscaled, risk_dd_cap=0.3)
    scaled = _dd_penalty(m, w_scaled, risk_dd_cap=0.3)
    assert scaled == pytest.approx(unscaled * 0.2, rel=1e-9)


def test_calibration_fixture_fails_loud_without_rescaling():
    """Ohne penalty_scale_vs_base (Faktor 1.0) verletzt das Kalibrier-Fixture die Invariante — das ist
    exakt der #631-Root-Cause-Zustand (Strafterme dominieren die Base-Streuung)."""
    with pytest.raises(ValueError, match="PENALTY_SCALE_MISCALIBRATED"):
        assert_penalty_scale_calibrated(W_BASE)  # kein penalty_scale_vs_base ⇒ Faktor 1.0


def test_calibration_fixture_passes_with_rescaling():
    w = dict(W_BASE, penalty_scale_vs_base=0.2)
    assert_penalty_scale_calibrated(w)  # darf NICHT werfen


def test_calibration_assertion_is_noop_on_incomplete_weights():
    """Ein unvollständiges weights-dict (z. B. fehlende optimizer.json in Tests) darf die Assertion
    nicht zum Absturz bringen — compute_reward selbst wirft dafür bereits an der echten Aufrufstelle."""
    assert_penalty_scale_calibrated({})  # darf NICHT werfen (No-Op)


def test_production_config_is_calibrated():
    """Die ausgelieferte optimizer.json besteht die #631-Kalibrier-Assertion."""
    assert_penalty_scale_calibrated(CFG)  # darf NICHT werfen
    assert CFG.get("penalty_scale_vs_base", 1.0) < 1.0


def test_eligible_cohort_correlation_acceptance_criteria():
    """Akzeptanzkriterium (#631): auf der eligiblen Kohorte corr(reward, base) > 0.7 UND
    |corr(reward, jeder_einzelne_Strafterm)| < 0.5 — mit der produktiven Rekalibrierung."""
    w = dict(W_BASE, penalty_scale_vs_base=CFG.get("penalty_scale_vs_base", 1.0))
    terms_list = _cohort_terms(w)
    rewards = [
        t["base"] - t["divergence"] - t["dd_penalty"] - t.get("param_pen", 0.0)
        - t["turnover"] - t["fold_dispersion"] + t["tie_breaker"]
        for t in terms_list
    ]
    base_vals = [t["base"] for t in terms_list]
    assert _corr(rewards, base_vals) > 0.7

    for k in ("dd_penalty", "turnover", "fold_dispersion"):
        vals = [t.get(k, 0.0) for t in terms_list]
        assert abs(_corr(rewards, vals)) < 0.5, f"|corr(reward, {k})| muss < 0.5 sein"


def test_higher_psr_lower_drawdown_ranks_above_lower_psr_higher_drawdown():
    """Akzeptanzkriterium (#631): ein Trial mit besserem PSR/kleinerem DD rankt über einem mit
    schlechterem PSR/größerem DD (Qualität schlägt DD-Mikro-Optimierung)."""
    w = dict(W_BASE, penalty_scale_vs_base=CFG.get("penalty_scale_vs_base", 1.0))
    good = _trial(1.6, 0.02, 60, (0.01, 0.01, 0.01, 0.01))    # hoeheres psr_z, DD 2%
    poor = _trial(0.85, 0.005, 60, (0.01, 0.01, 0.01, 0.01))  # niedrigeres psr_z, DD 0.5%
    r_good = compute_reward(good, universe_size=1, weights=w, risk_dd_cap=0.3, tournament_cfg=TCFG)
    r_poor = compute_reward(poor, universe_size=1, weights=w, risk_dd_cap=0.3, tournament_cfg=TCFG)
    assert r_good > r_poor
