"""Issue #589 — Drei Aggregationsebenen in derselben Gate-Klausel entkoppelten den Sortino vom Ergebnis.

Vor dem Fix war der OOS-Sortino der FOLD-MEDIAN (n=4, Schätzfehler ±3–5 Einheiten, maskierte einen
katastrophalen Fold), während total_return compoundiert war ⇒ ``corr(oos_sortino, oos_total_return)``
lag je Study zwischen −0.44 und +0.24 (245/600 Trials mit return>0 ∧ sortino<0). Fix: der Sortino kommt
aus der GEPOOLTEN OOS-Equity-Kurve — derselbe Pfad wie total_return ⇒ Zähler und Nenner sind per
Konstruktion kohärent (gleiches Vorzeichen).
"""
import statistics

import numpy as np
import pandas as pd

from automation.backtest_runner import (
    _calculate_stats, apply_fold_aggregation, _assert_sortino_return_coherence,
)


def _pooled_metrics_from_returns(period_rets: list[float]) -> dict:
    """Ein _calculate_stats über eine aus ``period_rets`` gebaute MtM-Equity-Kurve (gepoolter Pfad)."""
    mtm = [1000.0]
    for r in period_rets:
        mtm.append(mtm[-1] * (1 + r))
    idx = pd.date_range("2025-01-01", periods=len(mtm), freq="1h", tz="UTC")
    series = pd.Series(mtm, index=idx)
    pnls = [v if v != 0 else 0.01 for v in period_rets]  # nur zur Trade-Zählung
    holds = [(3600 * 10**9, 1.0)] * len(pnls)
    return _calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=3)


def test_pooled_sortino_return_correlation_above_0_6():
    """Über 100 synthetische Trials gilt corr(oos_sortino, oos_total_return) > 0.6.

    Issue #614 — der Datenfehler-Guard (25) wird hier hochgesetzt: dieser Test prüft die Sortino↔Return-
    KOHÄRENZ des Rechenpfads über eine breite (bewusst extreme) synthetische Sortino-Verteilung, nicht
    den Guard (der ist in test_issue_614/#588 getestet)."""
    from unittest.mock import patch
    rng = np.random.default_rng(12345)
    sortinos, returns = [], []
    with patch("automation.backtest_runner._read_sortino_numeric_guard", return_value=1e9):
      for _ in range(100):
        # Drift dominiert (breite Spanne), Volatilität variiert nur mild ⇒ gepoolter Sortino und
        # Return werden beide primär vom Drift getrieben (die ökonomisch realistische Kohärenz).
        mu = rng.uniform(-0.006, 0.012)
        sigma = rng.uniform(0.008, 0.012)
        rets = rng.normal(mu, sigma, 60).tolist()
        m = _pooled_metrics_from_returns(rets)
        if m["sortino_ratio"] is None:
            continue
        sortinos.append(m["sortino_ratio"])
        returns.append(m["total_return"])
    assert len(sortinos) >= 50
    corr = float(np.corrcoef(sortinos, returns)[0, 1])
    assert corr > 0.6, f"gepoolter Sortino und Return müssen stark positiv korrelieren, corr={corr:.3f}"


def test_coherence_invariant_flags_incoherent_and_ignores_coherent():
    # Kohärent (return>0 ∧ sortino>0) ⇒ kein Flag.
    ok = {"total_return": 0.05, "sortino_ratio": 1.2}
    _assert_sortino_return_coherence(ok)
    assert "oos_coherence_violation" not in ok

    # Inkohärent (return>0 ∧ sortino<0) ⇒ Flag gesetzt.
    bad = {"total_return": 0.05, "sortino_ratio": -1.2}
    _assert_sortino_return_coherence(bad)
    assert bad.get("oos_coherence_violation") is True


def test_apply_fold_aggregation_keeps_pooled_sortino_coherent():
    """apply_fold_aggregation überschreibt den (kohärenten) gepoolten Sortino NICHT mit dem Fold-Median.
    Ein katastrophaler Fold (−15) verschiebt den Median, aber nicht den gepoolten, return-kohärenten Wert.
    """
    per_fold = [
        {"total_return": 0.04, "sortino_ratio": 2.0},
        {"total_return": 0.03, "sortino_ratio": 1.5},
        {"total_return": 0.02, "sortino_ratio": 1.0},
        {"total_return": -0.30, "sortino_ratio": -15.0},   # katastrophaler Fold
    ]
    # Gepoolt: leicht positiver Return ⇒ leicht positiver Sortino (kohärent).
    oos_metrics = {"total_return": 0.01, "sortino_ratio": 0.4}
    apply_fold_aggregation(oos_metrics, per_fold)
    assert oos_metrics["sortino_ratio"] == 0.4                      # gepoolt, unverändert
    assert oos_metrics["sortino_ratio_fold_median"] == statistics.median([2.0, 1.5, 1.0, -15.0])
    # Kohärenz: sign(sortino) == sign(return).
    assert (oos_metrics["sortino_ratio"] > 0) == (oos_metrics["total_return"] > 0)
    assert oos_metrics["oos_fold_returns"] == [0.04, 0.03, 0.02, -0.30]
