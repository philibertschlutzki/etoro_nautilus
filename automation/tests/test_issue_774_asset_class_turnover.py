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


def test_turnover_penalty_is_retired_regardless_of_round_trip_cost_ratio():
    """Issue #1068/#1218 (Katalog #1196-1221, supersedes #774) — turnover_penalty ist seit der
    Retirierung IMMER 0.0 (code-seitig erzwungen, reward.py), UNABHAENGIG von round_trip_cost_bps:
    der vormalige #774-Kostenskalierungspfad (der genau dieser Test hier pruefte) ist jetzt
    unerreichbarer Code — drei identische Trial-Metriken mit unterschiedlichem round_trip_cost_bps
    liefern seither DENSELBEN Reward."""
    m_low = _mk(round_trip_cost_bps=3.0)
    m_high = _mk(round_trip_cost_bps=16.0)
    m_zero = _mk(round_trip_cost_bps=0.0)
    r_low, t_low = compute_reward(m_low, universe_size=1, weights=_WEIGHTS, holdout=True, return_terms=True)
    r_high, t_high = compute_reward(m_high, universe_size=1, weights=_WEIGHTS, holdout=True, return_terms=True)
    r_zero, t_zero = compute_reward(m_zero, universe_size=1, weights=_WEIGHTS, holdout=True, return_terms=True)
    assert t_low["turnover"] == t_high["turnover"] == t_zero["turnover"] == 0.0
    assert r_low == r_high == r_zero


def test_missing_round_trip_cost_bps_is_also_unaffected_since_1218():
    """Issue #1068/#1218 — der vormalige penalty_turnover_weight-Fallback-Pfad (Akzeptanzkriterium
    #774/2) ist ebenfalls unerreichbar: fehlendes round_trip_cost_bps liefert denselben (Null-)
    Reward-Beitrag wie jeder andere Wert."""
    m_missing = _mk(round_trip_cost_bps=None)
    m_zero = _mk(round_trip_cost_bps=0.0)
    r_missing, t_missing = compute_reward(
        m_missing, universe_size=1, weights=_WEIGHTS, holdout=True, return_terms=True)
    r_zero, t_zero = compute_reward(
        m_zero, universe_size=1, weights=_WEIGHTS, holdout=True, return_terms=True)
    assert t_missing["turnover"] == 0.0
    assert r_missing == r_zero
