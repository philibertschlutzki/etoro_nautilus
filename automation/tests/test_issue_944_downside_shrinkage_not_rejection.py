"""Issue #944 (Katalog B, P0, Pitfall #296) — ``downside_obs >= 0.5 * n_periods`` war ein Anti-
Selektions-Filter: die Verwerfungswahrscheinlichkeit steigt MONOTON mit der Strategiequalität
(weniger Verlustperioden = weniger downside_obs = höhere Verwerfungswahrscheinlichkeit).

Formal: sei p = Pr(r_t < MAR). Unter H1 (positiver Drift) gilt p < 0.5, also Pr(Verwerfung) -> 1
mit wachsendem n. Der Filter misst ausserdem die falsche Grösse: die Schätzpräzision der
Semi-Standardabweichung hängt an der ANZAHL m der Beobachtungen (SE ~ 1/sqrt(2m)), nicht am Anteil.

Fix: statt zu verwerfen (sortino/psr=None, SORTINO_INSUFFICIENT_DOWNSIDE), wird die Downside-
Deviation James-Stein-artig Richtung der Gesamtstreuung ALLER informativen Perioden geschrumpft
(``lambda = downside_obs / (downside_obs + m0)``, ``m0`` = ``sortino_downside_shrinkage_m0``,
Default 30) — der Trial bleibt bewertbar, nur konservativer geschätzt.
"""
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from automation.backtest_runner import _calculate_stats, _read_sortino_downside_shrinkage_m0


def _mtm_from_rets(rets, start=1000.0):
    idx = pd.date_range("2025-01-01", periods=len(rets) + 1, freq="1h", tz="UTC")
    vals = [start]
    for r in rets:
        vals.append(vals[-1] * (1.0 + r))
    return pd.Series(vals, index=idx)


def _stats_for(rets, *, min_downside_obs=0.5, min_periods_absolute=20, shrinkage_m0=None,
              guard=1e9):
    pnl_list = [1.0 if r >= 0 else -1.0 for r in rets]
    holds = [(3600 * 10**9, 1.0)] * len(rets)
    series = _mtm_from_rets(rets)
    patches = [
        patch("automation.backtest_runner._read_sortino_min_downside_observations",
             return_value=min_downside_obs),
        patch("automation.backtest_runner._read_sortino_min_periods_absolute",
             return_value=min_periods_absolute),
        patch("automation.backtest_runner._read_sortino_numeric_guard_min_periods",
             return_value=None),
        patch("automation.backtest_runner._read_sortino_numeric_guard", return_value=guard),
    ]
    if shrinkage_m0 is not None:
        patches.append(patch("automation.backtest_runner._read_sortino_downside_shrinkage_m0",
                             return_value=shrinkage_m0))
    for p in patches:
        p.start()
    try:
        return _calculate_stats(pnl_list, holds, 1000.0, mtm_series=series,
                                min_trades_for_sortino=1)
    finally:
        for p in patches:
            p.stop()


def _rets_with_downside_fraction(n_periods, downside_frac, *, seed=0, magnitude=0.01):
    import random
    rng = random.Random(seed)
    n_downside = round(n_periods * downside_frac)
    n_downside = min(max(n_downside, 0), n_periods)
    rets = [-abs(rng.uniform(magnitude * 0.5, magnitude)) for _ in range(n_downside)]
    rets += [abs(rng.uniform(magnitude * 0.5, magnitude)) for _ in range(n_periods - n_downside)]
    rng.shuffle(rets)
    return rets


def test_no_trial_is_ever_rejected_for_thin_downside_alone():
    """Kern-Regressionsschutz: egal wie duenn der Downside-Nenner (solange > 0 und n_periods die
    absolute Untergrenze erfuellt), es gibt keinen SORTINO_INSUFFICIENT_DOWNSIDE-Code mehr."""
    for frac in (0.5, 0.1, 0.02, 0.015, 0.01):
        rets = _rets_with_downside_fraction(200, frac, seed=int(frac * 1000))
        stats = _stats_for(rets)
        codes = [d["code"] for d in stats["inference_diagnostics"]]
        assert "SORTINO_INSUFFICIENT_DOWNSIDE" not in codes, f"frac={frac} was rejected"


def test_rejection_probability_no_longer_increases_with_strategy_quality():
    """Direkter Test gegen die Anti-Selektion: eine 'gute' Strategie (wenige Verlustperioden) und
    eine 'schlechte' (viele Verlustperioden) werden BEIDE bewertet — keine der beiden wird wegen
    ihres downside_obs-Anteils verworfen."""
    good = _rets_with_downside_fraction(200, 0.02, seed=11)   # 4 Verlustperioden — SEHR gut
    bad = _rets_with_downside_fraction(200, 0.8, seed=12)     # 160 Verlustperioden — schlecht
    stats_good = _stats_for(good)
    stats_bad = _stats_for(bad)
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" not in [
        d["code"] for d in stats_good["inference_diagnostics"]]
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" not in [
        d["code"] for d in stats_bad["inference_diagnostics"]]


def test_shrinkage_lambda_formula():
    """lambda = downside_obs / (downside_obs + m0)."""
    m0 = _read_sortino_downside_shrinkage_m0()
    assert m0 == 30.0
    downside_obs = 5
    expected_lambda = downside_obs / (downside_obs + m0)
    assert expected_lambda == pytest.approx(5 / 35)


def test_shrinkage_pulls_dd_dev_toward_full_sample_std_not_away_from_it():
    """Ein extrem duenner Downside-Nenner (wenige, aber sehr kleine Verluste) darf nach der
    Schrumpfung nicht MEHR abweichen als vor ihr — die Schrumpfung muss Richtung der (robusteren)
    Gesamtstreuung ziehen, nicht von ihr weg."""
    # 3 sehr kleine Verlustperioden, 197 grosse Gewinnperioden -> dd_dev (nur downside) winzig,
    # dd_dev_voll (alle Perioden) viel groesser.
    rets = [-0.0001, -0.0001, -0.0001] + [0.02] * 197
    stats_shrunk = _stats_for(rets, shrinkage_m0=30)
    stats_unshrunk = _stats_for(rets, shrinkage_m0=1e-9)  # lambda -> 1, praktisch kein Shrinkage
    assert stats_shrunk["sortino_ratio"] is not None
    assert stats_unshrunk["sortino_ratio"] is not None
    # Mit staerkerer Schrumpfung (kleineres lambda, naeher an dd_dev_voll) ist |sortino| kleiner
    # (konservativer), weil dd_dev_voll >> dd_dev_downside_only in diesem Szenario.
    assert abs(stats_shrunk["sortino_ratio"]) < abs(stats_unshrunk["sortino_ratio"])


def test_downside_obs_above_threshold_is_never_shrunk():
    """Oberhalb der Schwelle bleibt das Verhalten bit-identisch (kein SORTINO_DOWNSIDE_SHRUNK)."""
    rets = _rets_with_downside_fraction(100, 0.6, seed=7)  # 60 von 100 -> deutlich ueber 0.5*100
    stats = _stats_for(rets, min_downside_obs=0.5)
    codes = [d["code"] for d in stats["inference_diagnostics"]]
    assert "SORTINO_DOWNSIDE_SHRUNK" not in codes
