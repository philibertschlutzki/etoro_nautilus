"""Issue #771 (P0) — `#756` ist unvollständig: `total_return` und `period_rets` stammen aus ZWEI
verschiedenen Bar-Mengen; die Kohärenz-Invariante war verletzt, nicht behoben.

Root-Cause: `total_return` wurde PER-FOLD-SEGMENT kompoundiert (`mtm_frames`), `period_rets` aus der
KONKATENIERTEN Serie (`mtm_series`) — dieselben Segmente, aber `period_rets` enthält zusätzlich die
Nahtstellen-Returns an den Fold-Übergängen, und `total_return` unterschlägt jedes Segment mit
`len(seg) <= 1` oder `seg.iloc[0] == 0.0` vollständig.

Fix: `total_return` wird jetzt PRIMÄR aus der konkatenierten `mtm_series` gebildet (dieselbe Serie,
aus der `period_rets` abgeleitet wird) — `mtm_frames` bleibt nur ein Fallback für eine fehlende/
nicht nutzbare `mtm_series`. `assert_return_series_identity` prüft die `#756`-Identität maschinell.
"""
import random

import numpy as np
import pandas as pd
import pytest

from automation.backtest_runner import _calculate_stats, assert_return_series_identity


def _synthetic_equity_curve(rng: random.Random, n_folds: int, bars_per_fold: int) -> list[pd.Series]:
    """n_folds unabhängige, ZUFÄLLIGE Equity-Segmente (geometrischer Random Walk), jeweils als
    eigene pd.Series mit einem eigenen Zeit-Index (kontiguierlich innerhalb, disjunkt zwischen den
    Segmenten — wie echte OOS-Fold-Slices)."""
    frames = []
    t0 = 0
    level = 10_000.0
    for _ in range(n_folds):
        vals = [level]
        for _ in range(bars_per_fold - 1):
            level *= (1.0 + rng.uniform(-0.01, 0.01))
            vals.append(level)
        idx = pd.date_range("2024-01-01", periods=bars_per_fold, freq="h") + pd.Timedelta(hours=t0)
        frames.append(pd.Series(vals, index=idx))
        t0 += bars_per_fold + 5  # Luecke zwischen den Segmenten (IS/Embargo-Analogon)
    return frames


def _concat(frames: list[pd.Series]) -> pd.Series:
    return pd.concat(frames)


# ── Akzeptanzkriterium #771/1: Identität über 200 zufällige Kurven, 2-8 Folds ────────────────────
@pytest.mark.parametrize("seed", range(20))
def test_identity_holds_over_random_multi_fold_curves(seed):
    rng = random.Random(seed)
    for n_folds in range(2, 9):
        frames = _synthetic_equity_curve(rng, n_folds, bars_per_fold=20)
        mtm_series = _concat(frames)
        metrics = _calculate_stats(
            pnl_list=[1.0], hold_list=[], starting_capital=10_000.0,
            mtm_series=mtm_series, mtm_frames=frames,
        )
        period_rets = np.log1p(mtm_series.pct_change().dropna())
        log_sum = float(period_rets.sum())
        target = np.log1p(metrics["total_return"])
        assert abs(log_sum - target) < 1e-9, f"seed={seed} n_folds={n_folds}: {log_sum} vs {target}"
        assert metrics["return_series_identity_violation"] is False


# ── Akzeptanzkriterium #771/2: Nahtstellen-Returns sind in period_rets UND total_return enthalten ─
def test_seam_return_is_reflected_in_both_total_return_and_period_rets():
    """Regressionsfixture mit einem grossen Übergangsbar zwischen zwei Folds: das Fold-Segment-
    Kompoundieren (Pre-#771) haette diesen Bar-Return VERLOREN (jedes Segment beginnt/endet an
    seinen eigenen Werten, der Sprung ZWISCHEN den Segmenten existiert dort nicht); die konkatenierte
    Serie (Fix) sieht ihn in BEIDEN Groessen."""
    fold0 = pd.Series([100.0, 105.0], index=pd.date_range("2024-01-01", periods=2, freq="h"))
    # Fold1 beginnt bei 150 (ein grosser Sprung gegenueber Fold0s Endwert 105) — z. B. eine ueber die
    # Luecke hinweg offen gebliebene Position, die substanziell weiterlief (#771-Root-Cause-Begruendung).
    fold1 = pd.Series([150.0, 151.5], index=pd.date_range("2024-01-05", periods=2, freq="h"))
    frames = [fold0, fold1]
    mtm_series = _concat(frames)

    metrics = _calculate_stats(
        pnl_list=[1.0], hold_list=[], starting_capital=100.0,
        mtm_series=mtm_series, mtm_frames=frames,
    )
    # Fix (#771): total_return kommt aus der VOLLEN Spanne (100 -> 151.5), NICHT aus der
    # per-Segment-Kompoundierung (1.05 * 1.01 - 1 = 0.0605), die den Sprung 105->150 unterschlaegt.
    per_segment_compounded = (105.0 / 100.0) * (151.5 / 150.0) - 1.0
    full_span = 151.5 / 100.0 - 1.0
    assert metrics["total_return"] == pytest.approx(full_span, abs=1e-9)
    assert metrics["total_return"] != pytest.approx(per_segment_compounded, abs=1e-6)

    period_rets = np.log1p(mtm_series.pct_change().dropna())
    # Der Naht-Return (105 -> 150) ist eine der vier Perioden-Returns.
    assert len(period_rets) == 3
    seam_ret = np.log(150.0 / 105.0)
    assert any(abs(r - seam_ret) < 1e-9 for r in period_rets)


# ── Akzeptanzkriterium #771/3: assert_return_series_identity schlaegt bei kuenstlicher Divergenz an
def test_assert_return_series_identity_fires_on_artificial_per_segment_total_return():
    """Kuenstliche Divergenz: total_return wird ABSICHTLICH per-Segment kompoundiert uebergeben
    (der Pre-#771-Wert), waehrend period_rets aus der VOLLEN konkatenierten Serie stammt — genau
    die Situation, die #771 behebt. Die Assertion muss das als Verletzung erkennen."""
    fold0 = pd.Series([100.0, 105.0], index=pd.date_range("2024-01-01", periods=2, freq="h"))
    fold1 = pd.Series([150.0, 151.5], index=pd.date_range("2024-01-05", periods=2, freq="h"))
    mtm_series = _concat([fold0, fold1])
    period_rets = np.log1p(mtm_series.pct_change().dropna())

    per_segment_compounded_total_return = (105.0 / 100.0) * (151.5 / 150.0) - 1.0
    assert assert_return_series_identity(per_segment_compounded_total_return, period_rets) is True

    full_span_total_return = 151.5 / 100.0 - 1.0
    assert assert_return_series_identity(full_span_total_return, period_rets) is False


def test_assert_return_series_identity_handles_empty_period_rets():
    assert assert_return_series_identity(0.05, pd.Series([], dtype=float)) is False
    assert assert_return_series_identity(0.05, None) is False


# ── Akzeptanzkriterium #771/4: check_log_return_coherence meldet 0 Verletzungen nach dem Fix ─────
def test_no_coherence_violations_over_the_synthetic_multi_fold_cohort():
    """Fuehrt die ECHTE Pipeline (``_calculate_stats`` -> ``_assert_sortino_return_coherence`` ->
    ``invariants.check_log_return_coherence``) auf 50 zufaelligen Multi-Fold-Kurven mit ECHTEN
    Trade-PnLs aus (nicht nur die Identitaets-Assertion isoliert, siehe Tests oben) — der
    Sign-Kohaerenz-Check darf nach #771 auf keiner dieser Kohorten mehr anschlagen."""
    from automation.backtest_runner import _assert_sortino_return_coherence
    from automation.optimizer.invariants import check_log_return_coherence

    rng = random.Random(99)
    trial_attrs = []
    for _ in range(50):
        n_folds = rng.randint(2, 6)
        frames = _synthetic_equity_curve(rng, n_folds=n_folds, bars_per_fold=15)
        mtm_series = _concat(frames)
        # Trades, die grob dem Kurvenverlauf folgen (genug fuer einen definierten Sortino).
        pnl_list = [float(mtm_series.iloc[i + 1] - mtm_series.iloc[i])
                    for i in range(0, len(mtm_series) - 1, 3)]
        hold_list = [(3600 * 1_000_000_000, 1.0) for _ in pnl_list]
        metrics = _calculate_stats(
            pnl_list=pnl_list, hold_list=hold_list, starting_capital=10_000.0,
            mtm_series=mtm_series, mtm_frames=frames, min_trades_for_sortino=2,
        )
        _assert_sortino_return_coherence(metrics)
        trial_attrs.append({"oos_coherence_violation": metrics.get("oos_coherence_violation", False)})
    result = check_log_return_coherence(trial_attrs)
    assert result.passed is True
    assert result.actual == 0
