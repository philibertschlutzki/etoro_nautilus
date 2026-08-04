"""Issue #563 — Sortino-Annualisierungs-Konsistenz: Gate/Clip mit √8766 abgestimmt.

Der annualisierte Sortino (per-Bar · √8766 ≈ 93.6 für 1h) macht die absoluten Konstanten
oos_min_sortino=0.3 (per-Bar ~0.0032, 'irgendein positives Vorzeichen') und den Hard-Clip ±5
(saturiert bei per-Bar 0.053) fehlskaliert. Fix: (1) Clip strukturell durch #559 (weiche
Sättigung) entschärft; (2) oos_min_sortino als ANNUALISIERTE Schwelle dokumentiert und auf ein
ökonomisch sinnvolles Niveau (1.0) gehoben.

Akzeptanzkriterien (Issue #563):
- Doku in tournament.json._schema stellt klar: oos_min_sortino ist eine ANNUALISIERTE Schwelle.
- Sensitivitäts-Sweep oos_min_sortino ∈ {0.3, 1.0, 2.0}: Winner-Rate monoton fallend.
- Nach #559: keine harte Clip-Sättigung mehr (pstdev(base | eligible) > 0).
"""
import json
import math
from pathlib import Path

import pandas as pd

from automation.backtest_runner import _get_annualization_factor, _evaluate_oos_eligibility
from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


# ── Annualisierungs-Konsistenz (√8766 für 1h) ────────────────────────────────────────────────
def test_hourly_annualization_factor_is_8766():
    idx = pd.date_range("2025-01-01", periods=48, freq="1h", tz="UTC")
    series = pd.Series(range(48), index=idx, dtype=float)
    factor = _get_annualization_factor(series)
    assert math.isclose(factor, 8766.0, rel_tol=0.05)
    assert round(math.sqrt(factor), 1) == 93.6  # der Multiplikator für die Annualisierung (≈93.6)


# ── oos_min_sortino ist annualisiert dokumentiert + auf die √1638-Skala rekalibriert ─────────
# Issue #587/#593 — nach der EMPIRISCHEN Span-Annualisierung (#587) fiel der Faktor für RTH-Equity-1h
# von √8766 (Kalenderjahr-Fehlannahme) auf √1638 (reale Handelsstunden). Der alte 1.0-Wert (√8766-Ära)
# wird analytisch auf 1.0·√(1638/8766) ≈ 0.43 übersetzt (empirisch aus einem Kalibrierlauf zu verfeinern).
def test_oos_min_sortino_calibrated_and_documented():
    assert 0.4 <= TCFG["oos_min_sortino"] <= 0.5, (
        "oos_min_sortino auf die √1638-Skala rekalibrieren (≈0.43, Issue #587/#593).")
    schema = TCFG["_schema"]["fields"]
    doc = (schema.get("min_sortino", "") + schema.get("oos_min_sortino_note", "")).lower()
    assert "annualis" in doc, "Schema muss klarstellen, dass die Schwelle ANNUALISIERT ist."
    assert "1638" in doc, "Schema muss die √1638-Rekalibrierung (#587) dokumentieren."


# ── Sensitivitäts-Sweep: Winner-Rate monoton fallend in oos_min_sortino ──────────────────────
def _oos(sortino):
    return {
        "total_trades": 30, "max_drawdown": 0.05, "win_rate": 0.20,
        "total_return": 0.03, "expectancy": 0.005,
        "sortino_ratio": sortino, "profit_factor": 1.0,  # PF/WR unter Gate ⇒ nur Sortino trägt ANY
        "median_position_notional": 1000.0,
    }


def _cfg(min_sortino):
    return {
        "min_trades": 1, "oos_min_trades": 1, "oos_min_total_return": 0.0,
        "oos_min_expectancy": 0.0, "oos_min_win_rate": 0.25, "oos_min_profit_factor": 1.1,
        "oos_min_sortino": min_sortino, "max_drawdown": 0.3,
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": ["min_sortino", "min_profit_factor", "min_win_rate"],
    }


def test_winner_rate_monotone_in_sortino_gate():
    # Population annualisierter OOS-Sortinos.
    population = [0.4, 0.8, 1.2, 1.6, 2.4, 3.0]
    rates = []
    for gate in (0.3, 1.0, 2.0):
        cfg = _cfg(gate)
        n = sum(1 for s in population
                if _evaluate_oos_eligibility(_oos(s), cfg)["oos_eligible"])
        rates.append(n)
    assert rates[0] >= rates[1] >= rates[2], f"Winner-Rate nicht monoton fallend: {rates}"
    assert rates[0] > rates[2], "Höhere Schwelle muss echt weniger Winner zulassen."


# ── Nach #559: keine harte Clip-Sättigung mehr (pstdev(base | eligible) > 0) ──────────────────
def test_no_hard_clip_saturation_after_soft_saturation():
    weights = {
        "penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25,
        "evaluable_floor_epsilon": 0.001, "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
        "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0, "bonus_coverage_weight": 1.0,
    }
    cfg = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}
    # Annualisierte Sortinos WEIT über der alten Clip-Grenze 5 — vorher alle base==5.0 (Sättigung).
    bases = []
    for s in (6.0, 9.0, 12.0, 15.0):
        m = TournamentMetrics(
            oos_evaluated=True, oos_eligible=True, is_sortino_median=0.0,
            oos_sortino=s, oos_max_drawdown=0.02, oos_total_trades=30, win_count=1,
            fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.01,
        )
        # base direkt aus der Reward-Formel rekonstruiert (soft-sat).
        bases.append(5.0 * math.asinh(s / 5.0))
    import statistics
    assert statistics.pstdev(bases) > 0.0, "Soft-Sättigung muss über der alten Clip-Grenze streuen."
    # Und der Reward selbst streut ebenfalls (kein +5.0-Deckel mehr).
    rewards = [compute_reward(
        TournamentMetrics(oos_evaluated=True, oos_eligible=True, is_sortino_median=0.0,
                          oos_sortino=s, oos_max_drawdown=0.02, oos_total_trades=30, win_count=1,
                          fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.01),
        universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=cfg)
        for s in (6.0, 9.0, 12.0, 15.0)]
    assert statistics.pstdev(rewards) > 1e-3

# ── Fixes for Issue #573 (Downside Floor) + #588 (no hard clip) ─────────────────
def test_synthetic_zero_loss_sortino():
    # Issue #614 SUPERSEDET #588/#590 hier: eine praktisch verlustFREIE Equity (Downside auf den
    # sortino_downside_floor 1e-6 geklemmt) treibt den ANNUALISIERTEN Sortino ins Extreme (>> 25) ⇒
    # der auf 25.0 gesenkte Numerik-Guard greift ⇒ Datenfehler ⇒ sortino/psr=None (ohne messbare
    # Downside ist die Risiko-Adjustierung ehrlich undefiniert). Ein Fold mit realer Downside bleibt
    # finit (test_issue_590::test_small_downside_fold_yields_finite_positive_sortino).
    idx = pd.date_range("2025-01-01", periods=10, freq="1h", tz="UTC")
    series = pd.Series([1000.0, 1001.0, 1002.0, 1003.0, 1004.0, 1005.0, 1006.0, 1007.0, 1006.999999, 1008.0], index=idx)
    pnls = [1.0] * 11 + [-1e-8]
    holds = [(3600*10**9, 1.0)] * 12
    import automation.backtest_runner as br
    stats = br._calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=10)
    assert br._read_sortino_numeric_guard() == 25.0     # #614 — Guard von 1e6 auf 25.0
    assert stats.get("sortino_ratio") is None            # #614 — |annualized| > 25 ⇒ Guard ⇒ None
    assert stats.get("psr") is None

def test_monotonic_edge_scaling():
    idx = pd.date_range("2025-01-01", periods=10, freq="1h", tz="UTC")
    pnls = [1.0] * 11 + [-1.0]
    holds = [(3600*10**9, 1.0)] * 12
    sortinos = []
    import automation.backtest_runner as br
    from unittest.mock import patch
    for m in [1, 2, 4]:
        # returns: +m, -20, +m, +m...
        series = pd.Series([100.0, 100.0+m, 100.0+m-20.0, 100.0+m*2-20.0, 100.0+m*3-20.0, 100.0+m*4-20.0, 100.0+m*5-20.0, 100.0+m*6-20.0, 100.0+m*7-20.0, 100.0+m*8-20.0], index=idx)
        # Issue #823 — synthetisches 10-Perioden-Fixture, < Default 30 sortino_min_downside_
        # observations; dieser Test prueft Monotonie, nicht die Mindest-Stichprobe. Issue #844 —
        # sortino_numeric_guard_min_periods ist jetzt real gesetzt (1600); bei nur 9 Perioden
        # deaktiviert, um den T-bewussten Guard nicht zu treffen (nicht Testgegenstand hier).
        with patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=1), \
             patch("automation.backtest_runner._read_sortino_numeric_guard_min_periods", return_value=None):
            stats = br._calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=10)
        sortinos.append(stats.get("sortino_ratio"))
    assert sortinos[0] < sortinos[1] < sortinos[2], f"Sortino should scale monotonically: {sortinos}"

def test_gating_immunity():
    # Issue #589 — apply_fold_aggregation aggregiert den Sortino NICHT mehr zum (winsorisierten)
    # Fold-Median; der gepoolte sortino_ratio (hier 0.5) bleibt unangetastet, der frühere Median
    # ist nur noch forensisch (sortino_ratio_fold_median).
    oos_metrics = {"sortino_ratio": 0.5, "total_return": 0.02}
    per_fold = [{"sortino_ratio": 1.0, "total_return": 0.01},
                {"sortino_ratio": 2.0, "total_return": 0.02},
                {"sortino_ratio": 50.0, "total_return": 0.03}]
    import automation.backtest_runner as br
    import statistics
    br.apply_fold_aggregation(oos_metrics, per_fold)
    assert oos_metrics["sortino_ratio"] == 0.5            # gepoolt, unverändert
    assert oos_metrics["sortino_ratio_fold_median"] == statistics.median([1.0, 2.0, 50.0])
    assert oos_metrics["oos_fold_returns"] == [0.01, 0.02, 0.03]
