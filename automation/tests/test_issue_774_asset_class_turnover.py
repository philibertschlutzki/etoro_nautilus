"""Issue #774 (P1) — `penalty_turnover_weight` ist auf TSLA kalibriert; für die 8 Krypto-Symbole
des Universums ist die Strafe 5,3× zu klein.

Root-Cause: `penalty_turnover_weight = 0.0003` (3 bps) ist explizit aus TSLA (commission 1 bps +
spread 2 bps) hergeleitet. Krypto-Symbole haben real 16 bps Round-Trip-Kosten (commission 1 +
spread_bps_by_asset_class.CRYPTO 15) — die Turnover-Strafe unterschätzte die realen Kosten
hochfrequenter Konfigurationen GENAU dort, wo sie am höchsten sind.

Fix: `compute_reward` konsumiert `round_trip_cost_bps` (bereits pro Trial gestempelt, #562/#684) für
die Turnover-Strafe; `penalty_turnover_weight` wird zum Fallback für fehlende Kosten-Telemetrie.
"""
import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward


def _mk(*, round_trip_cost_bps=None, total_trades=50):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
        oos_sortino=1.0, oos_max_drawdown=0.05, oos_total_trades=total_trades, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.05,
        oos_psr_z=0.5, oos_sortino_period=0.05, oos_n_periods=200,
        round_trip_cost_bps=round_trip_cost_bps,
    )


_WEIGHTS = {
    "penalty_unevaluable_oos": -4.0, "sortino_clip_abs": 3.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
    "unevaluable_shaping_span": 0.25, "oos_min_trades": 20,
    "penalty_turnover_weight": 0.0003, "penalty_scale_vs_base": 1.0,
}


def test_turnover_penalty_scales_exactly_with_round_trip_cost_ratio():
    """Akzeptanzkriterium #774/1: identische Trial-Metriken, einmal mit round_trip_cost_bps=3,
    einmal =16 ⇒ Turnover-Strafe skaliert exakt 16/3."""
    m_low = _mk(round_trip_cost_bps=3.0)
    m_high = _mk(round_trip_cost_bps=16.0)
    r_low = compute_reward(m_low, universe_size=1, weights=_WEIGHTS, holdout=True)
    r_high = compute_reward(m_high, universe_size=1, weights=_WEIGHTS, holdout=True)
    # Reward = base - turnover_penalty (u.a.) ⇒ die DIFFERENZ zur baseline (round_trip=0, kein Cost)
    # skaliert linear mit c_rt. Vergleiche die Penalty-Differenz direkt statt des Gesamtrewards.
    m_zero = _mk(round_trip_cost_bps=0.0)
    r_zero = compute_reward(m_zero, universe_size=1, weights=_WEIGHTS, holdout=True)
    penalty_low = r_zero - r_low
    penalty_high = r_zero - r_high
    assert penalty_low == pytest.approx(50 * (3.0 / 10_000.0), rel=1e-9)
    assert penalty_high == pytest.approx(50 * (16.0 / 10_000.0), rel=1e-9)
    assert penalty_high == pytest.approx(penalty_low * (16.0 / 3.0), rel=1e-9)


def test_missing_round_trip_cost_bps_falls_back_to_bit_identical_legacy_value():
    """Akzeptanzkriterium #774/2: fehlendes round_trip_cost_bps ⇒ Wert bit-identisch zu HEAD
    (penalty_turnover_weight-Pfad)."""
    m_missing = _mk(round_trip_cost_bps=None)
    m_zero = _mk(round_trip_cost_bps=0.0)
    r_missing = compute_reward(m_missing, universe_size=1, weights=_WEIGHTS, holdout=True)
    r_zero = compute_reward(m_zero, universe_size=1, weights=_WEIGHTS, holdout=True)
    legacy_penalty = 50 * 0.0003 * 1.0  # oos_total_trades * penalty_turnover_weight * scale
    assert (r_zero - r_missing) == pytest.approx(legacy_penalty, rel=1e-9)
