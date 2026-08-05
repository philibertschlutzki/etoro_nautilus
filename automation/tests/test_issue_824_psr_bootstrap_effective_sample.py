"""Issue #824 (P1, Katalog #822-#827, GitHub-Issue #750) — der PSR-Bootstrap resampelt die volle
Bar-Achse und unterschätzt damit den Standardfehler am einzigen harten Risiko-Gate.

Root-Cause: ``bootstrap_psr_z`` (Issue #757) resampelte ``period_rets.to_numpy()`` — die VOLLE,
ggf. 24/7-aufgefüllte Kalender-Bar-Achse (siehe #823). Der Bootstrap-Standardfehler eines
mittelwertbasierten Verhältnisses skaliert wie ``1/√T``; mit einem Auffüllgrad von z. B. 80 % ist
der SE um den Faktor ``√(T/T_informativ)`` zu klein, ``psr_z`` entsprechend zu gross, ``PSR``
gegen 1 gedrückt.

Fix: ``_calculate_stats`` (backtest_runner.py) übergibt seit diesem Fix dieselbe INFORMATIVE
Teilmenge (#823, ``_informative_period_returns``) an ``bootstrap_psr_z`` UND an
``sample_skew_kurtosis`` — beide Aufrufer nutzen jetzt dieselbe (kleinere, korrekte)
Stichprobengrösse wie der #823-Punktschätzer. ``n_effective_observations`` ist als explizites
Feld im ``_calculate_stats``-Rückgabedict verfügbar (== ``n_periods`` seit #823/#824).
"""
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from automation.backtest_runner import _calculate_stats


def _series_with_flat_padding(informative_rets, n_flat_bars):
    """Baut eine MtM-Serie aus ``informative_rets`` gefolgt von ``n_flat_bars`` Nicht-Handels-Bars
    (Rendite exakt 0) — simuliert ein 24/7-Kalenderraster fuer ein RTH-Instrument (#823-Symptom)."""
    vals = [1000.0]
    for r in informative_rets:
        vals.append(vals[-1] * (1 + r))
    flat_val = vals[-1]
    vals.extend([flat_val] * n_flat_bars)
    idx = pd.date_range("2025-01-01", periods=len(vals), freq="1h", tz="UTC")
    return pd.Series(vals, index=idx)


def test_n_effective_observations_field_matches_informative_count():
    rng = np.random.default_rng(3)
    informative = rng.normal(0.001, 0.01, 40).tolist()
    series = _series_with_flat_padding(informative, n_flat_bars=200)
    with patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=0), \
         patch("automation.backtest_runner._read_sortino_min_periods_absolute", return_value=0):
        stats = _calculate_stats([1.0] * 20 + [-1.0] * 20, [(3600 * 10**9, 1.0)] * 40, 1000.0,
                                 mtm_series=series, min_trades_for_sortino=10)
    assert stats["n_effective_observations"] == 40  # NICHT 240 (informativ + flach)
    assert stats["n_effective_observations"] == stats["n_periods"]


def test_bootstrap_se_does_not_shrink_with_added_flat_padding():
    """Der Bootstrap-SE (psr_se_boot) darf sich NICHT aendern, wenn zusaetzliche FLACHE
    (Nicht-Handels-)Bars angehaengt werden -- vor #824 haette ein groesseres, ueberwiegend flaches
    T den SE kuenstlich verkleinert (SE ~ 1/sqrt(T)), obwohl keine zusaetzliche Information
    hinzukam."""
    rng = np.random.default_rng(11)
    informative = rng.normal(0.0005, 0.012, 50).tolist()

    # Issue #844 — sortino_numeric_guard_min_periods ist jetzt real gesetzt (1600); bei 50
    # Perioden deaktiviert, damit der T-bewusste Guard psr_se_boot hier nicht auf None kippt
    # (nicht Testgegenstand — dieser Test prueft die Bootstrap-SE-Stabilitaet gegen Padding).
    with patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=0), \
         patch("automation.backtest_runner._read_sortino_min_periods_absolute", return_value=0), \
         patch("automation.backtest_runner._read_sortino_numeric_guard_min_periods", return_value=None):
        series_no_padding = _series_with_flat_padding(informative, n_flat_bars=0)
        stats_no_padding = _calculate_stats(
            [1.0] * 25 + [-1.0] * 25, [(3600 * 10**9, 1.0)] * 50, 1000.0,
            mtm_series=series_no_padding, min_trades_for_sortino=10)

        series_padded = _series_with_flat_padding(informative, n_flat_bars=500)
        stats_padded = _calculate_stats(
            [1.0] * 25 + [-1.0] * 25, [(3600 * 10**9, 1.0)] * 50, 1000.0,
            mtm_series=series_padded, min_trades_for_sortino=10)

    se_no_padding = stats_no_padding["psr_se_boot"]
    se_padded = stats_padded["psr_se_boot"]
    assert se_no_padding is not None and se_padded is not None
    # Beide Bootstrap-SEs beruhen jetzt auf DERSELBEN informativen Serie -> nahezu identisch
    # (deterministischer Bootstrap-Seed in bootstrap_psr_z).
    assert se_no_padding == pytest.approx(se_padded, rel=1e-9)


def test_ret_skew_kurtosis_computed_on_informative_subset():
    """sample_skew_kurtosis erhaelt seit #824 dieselbe informative Teilmenge -- eine grosse Zahl
    exakter Nullen (die eine Verteilung kuenstlich stark linksschief um 0 zentrieren wuerden) darf
    Skew/Kurtosis nicht verzerren."""
    rng = np.random.default_rng(5)
    informative = rng.normal(0.001, 0.015, 45).tolist()
    # annualization_periods_per_year fixiert: die Annualisierung (und damit der #614-Numerik-Guard)
    # soll hier NICHT zwischen den beiden Faellen divergieren -- nur Skew/Kurtosis werden geprueft.
    with patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=0), \
         patch("automation.backtest_runner._read_sortino_min_periods_absolute", return_value=0), \
         patch("automation.backtest_runner._read_annualization_periods", return_value=1.0):
        series_no_padding = _series_with_flat_padding(informative, n_flat_bars=0)
        stats_no_padding = _calculate_stats(
            [1.0] * 22 + [-1.0] * 23, [(3600 * 10**9, 1.0)] * 45, 1000.0,
            mtm_series=series_no_padding, min_trades_for_sortino=10)
        series_padded = _series_with_flat_padding(informative, n_flat_bars=300)
        stats_padded = _calculate_stats(
            [1.0] * 22 + [-1.0] * 23, [(3600 * 10**9, 1.0)] * 45, 1000.0,
            mtm_series=series_padded, min_trades_for_sortino=10)
    assert stats_no_padding["ret_skew"] == pytest.approx(stats_padded["ret_skew"], rel=1e-9)
    assert stats_no_padding["ret_kurtosis"] == pytest.approx(stats_padded["ret_kurtosis"], rel=1e-9)
