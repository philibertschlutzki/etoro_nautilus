"""Issue #614 — Reward-Base = PSR statt annualisierter Sortino.

Der annualisierte Fold-Sortino ist bei T≈200 statistisch bedeutungslos (std 20.97, Range [−43,+227]):
die Annualisierung (·√A) multipliziert Punktschätzer UND Standardfehler gleich ⇒ kein Informations-
gewinn. Die PSR (Probabilistic Sharpe/Sortino Ratio) ist skalenfrei, in [0,1], annualisierungs-invariant
und bezieht T + Schiefe + Kurtosis explizit ein.
"""
import math

import pandas as pd
import pytest

import automation.backtest_runner as br
from automation.optimizer.deflation import probabilistic_sharpe_ratio as _psr
from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics

_W = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
    "penalty_turnover_weight": 0.0, "w_ret": 0.0,
}
_CFG = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}


def _m(*, psr, sortino_annualized, dd=0.0, ret=0.03, psr_z=None):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=1.0,
        oos_sortino=sortino_annualized, oos_max_drawdown=dd, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=ret,
        oos_psr=psr, oos_psr_z=psr_z, oos_sortino_annualized=sortino_annualized)


# ── Referenzrechnung (muss exakt reproduzierbar sein) ────────────────────────────────────────────
def test_psr_reference_calculation():
    """ŜR_annual=4.6109, A=1638 ⇒ ŜR_period=0.11386, T=202, γ₃=0, γ₄=3 ⇒ PSR(0)=0.9463."""
    sr_period = 4.6109 / math.sqrt(1638)
    assert sr_period == pytest.approx(0.11386, abs=1e-4)
    val = _psr(sr_period, 202, skew=0.0, kurtosis=3.0, sr_star=0.0)
    assert val == pytest.approx(0.9463, abs=1e-3)


# ── PSR ∈ [0,1] für alle Eingaben ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sr", [-3.0, -0.5, 0.0, 0.11, 1.0, 5.0])
@pytest.mark.parametrize("T", [10, 50, 202, 1000])
def test_psr_in_unit_interval(sr, T):
    v = _psr(sr, T, skew=0.2, kurtosis=4.0)
    assert v is None or (0.0 <= v <= 1.0)


# ── Reward ist INVARIANT gegenüber der Annualisierungs-Konvention ────────────────────────────────
def test_reward_invariant_to_annualization():
    """Derselbe Return-Pfad (⇒ dieselbe PSR/psr_z) unter A=1638 und A=8766 ⇒ IDENTISCHER Reward. Der
    annualisierte Sortino differiert (4.61 vs. 10.66), fliesst aber nicht mehr in den Reward.
    Issue #630 — die Reward-Base ist seither ``psr_z`` (unbeschränkte Effektstärke), nicht mehr die
    CDF-Wahrscheinlichkeit ``psr``; dieselbe ``psr_z`` ⇒ identischer Reward."""
    m_1638 = _m(psr=0.94, sortino_annualized=4.61, psr_z=1.609)
    m_8766 = _m(psr=0.94, sortino_annualized=4.61 * math.sqrt(8766.0 / 1638.0), psr_z=1.609)
    r1 = compute_reward(m_1638, universe_size=1, weights=_W, risk_dd_cap=0.3, tournament_cfg=_CFG)
    r2 = compute_reward(m_8766, universe_size=1, weights=_W, risk_dd_cap=0.3, tournament_cfg=_CFG)
    assert r1 == pytest.approx(r2, abs=1e-12)


def test_reward_base_is_psr_z_when_present():
    """Issue #630 — bei aktivierter PSR-Z ist die Base die unbeschränkte Effektstärke ``psr_z``,
    nicht die sättigende CDF ``psr`` und nicht die asinh-Sortino-Grösse."""
    _, terms = compute_reward(_m(psr=0.9463, sortino_annualized=4.61, psr_z=1.609), universe_size=1,
                              weights=_W, risk_dd_cap=0.3, tournament_cfg=_CFG, return_terms=True)
    assert terms["base"] == pytest.approx(1.609, abs=1e-9)
    assert terms["divergence"] == 0.0          # #614 — asinh-Overfit-Divergenz von PSR subsumiert


def test_reward_falls_back_to_asinh_without_psr():
    """Fehlt oos_psr (Legacy) ⇒ asinh-Sortino-Base (migrations-sicher, #559)."""
    m = TournamentMetrics(oos_evaluated=True, oos_eligible=True, is_sortino_median=1.0,
                          oos_sortino=1.5, oos_max_drawdown=0.0, oos_total_trades=30, win_count=1,
                          fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.03)
    _, terms = compute_reward(m, universe_size=1, weights=_W, risk_dd_cap=0.3,
                              tournament_cfg=_CFG, return_terms=True)
    assert terms["base"] == pytest.approx(5.0 * math.asinh(1.5 / 5.0), abs=1e-9)


# ── Numerik-Guard: |annualized| > 25 ⇒ Datenfehler (None + Log) ──────────────────────────────────
def test_guard_25_config():
    assert br._read_sortino_numeric_guard() == 25.0


def test_extreme_annualized_sortino_trips_guard(caplog):
    """Ein Fold mit extrem hohem annualisiertem Sortino (Zero-Downside ⇒ dd_dev geklemmt) ⇒ Guard ⇒
    sortino/psr=None + SORTINO_GUARD_TRIPPED-Log."""
    import logging
    idx = pd.date_range("2025-01-01", periods=13, freq="1h", tz="UTC")
    series = pd.Series([1000.0 * (1.003 ** i) for i in range(13)], index=idx)  # streng steigend
    pnls = [5.0] * 12
    holds = [(3600 * 10**9, 1.0)] * 12
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        stats = br._calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=10)
    assert stats["sortino_ratio"] is None
    assert stats["psr"] is None
    assert any("SORTINO_GUARD_TRIPPED" in r.getMessage() for r in caplog.records)


# ── Realer Fold: PSR definiert, annualisierter Sortino als Telemetrie ────────────────────────────
def test_realistic_fold_has_psr_and_annualized_telemetry():
    idx = pd.date_range("2025-01-01", periods=40, freq="1h", tz="UTC")
    import numpy as np
    rng = np.random.default_rng(7)
    steps = 1.0 + rng.normal(0.001, 0.01, size=40)   # kleiner positiver Drift, reale Streuung
    series = pd.Series(1000.0 * np.cumprod(steps), index=idx)
    pnls = [2.0] * 30 + [-1.5] * 8
    holds = [(3600 * 10**9, 1.0)] * 38
    stats = br._calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=10)
    assert stats["psr"] is None or (0.0 <= stats["psr"] <= 1.0)
    # per-Perioden- und annualisierter Sortino sind konsistent (× √A) — Telemetrie.
    if stats["sortino_period"] is not None:
        assert stats["sortino_annualized"] is not None
        assert stats["n_periods"] == 39


# ── Gate nutzt PSR, NICHT den annualisierten Sortino ─────────────────────────────────────────────
def test_gate_uses_psr_not_annualized_sortino():
    """Ein Trial mit hohem annualisiertem Sortino, aber niedriger PSR (kleines T) fällt durch das
    min_psr-Gate — der annualisierte Sortino ist irrelevant."""
    cfg = {
        "oos_min_trades": 1, "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0,
        "oos_min_win_rate": 0.0, "oos_min_psr": 0.75, "max_drawdown": 0.3,
        "eligible_requires_all": ["min_psr"], "eligible_requires_any": [],
    }
    oos_low_psr = {"total_trades": 30, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.03,
                   "expectancy": 0.01, "sortino_ratio": 4.6, "psr": 0.60,
                   "median_position_notional": 1000.0}
    ev = br._evaluate_oos_eligibility(oos_low_psr, cfg)
    assert ev["oos_eligible"] is False
    assert any("oos_min_psr" in r for r in ev["oos_rejection_reasons"])

    oos_high_psr = dict(oos_low_psr, psr=0.95)
    assert br._evaluate_oos_eligibility(oos_high_psr, cfg)["oos_eligible"] is True

    oos_none_psr = dict(oos_low_psr, psr=None)   # undefiniert ⇒ nicht eligible (kein impliziter Pass)
    assert br._evaluate_oos_eligibility(oos_none_psr, cfg)["oos_eligible"] is False
