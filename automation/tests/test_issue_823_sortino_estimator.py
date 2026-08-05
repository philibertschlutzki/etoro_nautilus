"""Issue #823 (P0, Katalog #822-#827, GitHub-Issue #750) — 617x ``SORTINO_GUARD_TRIPPED``: der
Guard zensiert die Spitze der Zielverteilung, und ``sortino_numeric_guard_min_periods`` fehlt in
``tournament.json``.

Root-Cause: ``dd_dev``/``mean_ret``/die Annualisierung wurden über die VOLLE (ggf. 24/7-
aufgefüllte) Kalender-Bar-Achse gebildet — Bars ausserhalb der Handelszeit/ohne offene Position
tragen exakt Log-Return 0, zählen aber voll in den Nenner. Fix: die Sortino-/PSR-INFERENZ läuft
auf der INFORMATIVEN Teilmenge (Bars mit Rendite ≠ 0); eine Mindestzahl an Downside-Beobachtungen
(``sortino_min_downside_observations``) ist Vorbedingung; Guard-Trip-dominierte Studies werden als
``STUDY_GUARD_DOMINATED`` markiert.
"""
from unittest.mock import patch

import pandas as pd
import pytest

from automation.backtest_runner import (
    _calculate_stats, _informative_period_returns, _informative_annualization_factor,
)


# ── _informative_period_returns — reine Filterfunktion ──────────────────────────────────────────
def test_informative_period_returns_drops_zero_return_bars():
    rets = pd.Series([0.01, 0.0, -0.02, 0.0, 0.0, 0.005])
    informative = _informative_period_returns(rets)
    assert list(informative) == [0.01, -0.02, 0.005]


def test_informative_period_returns_empty_when_all_flat():
    rets = pd.Series([0.0, 0.0, 0.0])
    assert _informative_period_returns(rets).empty


# ── _informative_annualization_factor ───────────────────────────────────────────────────────────
def test_informative_annualization_factor_uses_informative_count_not_full_bar_count():
    idx = pd.date_range("2025-01-01", periods=11, freq="1h", tz="UTC")  # 10 Perioden, volle Achse
    series = pd.Series(range(11), index=idx)
    full_factor = _informative_annualization_factor(series, 10)
    half_factor = _informative_annualization_factor(series, 5)
    # dieselbe reale Zeitspanne, halb so viele informative Perioden -> halber Faktor.
    assert half_factor == pytest.approx(full_factor / 2, rel=1e-9)


def test_informative_annualization_factor_respects_explicit_config_override():
    with patch("automation.backtest_runner._read_annualization_periods", return_value=999.0):
        assert _informative_annualization_factor(None, 5) == 999.0


# ── SORTINO_INSUFFICIENT_DOWNSIDE gate ───────────────────────────────────────────────────────────
def test_insufficient_downside_observations_yields_none_with_own_code():
    """< sortino_min_downside_observations Downside-Beobachtungen -> sortino=None mit dem EIGENEN
    Code (nicht SORTINO_GUARD_TRIPPED)."""
    idx = pd.date_range("2025-01-01", periods=41, freq="1h", tz="UTC")
    # 40 Perioden, GENAU 2 davon negativ (Downside).
    rets = [0.001] * 38 + [-0.0005, -0.0005]
    vals = [1000.0]
    for r in rets:
        vals.append(vals[-1] * (1 + r))
    series = pd.Series(vals, index=idx)
    with patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=30):
        stats = _calculate_stats([1.0] * 20 + [-1.0] * 20, [(3600 * 10**9, 1.0)] * 40, 1000.0,
                                 mtm_series=series, min_trades_for_sortino=10)
    assert stats["sortino_ratio"] is None
    assert stats["psr"] is None
    codes = [d["code"] for d in stats["inference_diagnostics"]]
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" in codes
    assert "SORTINO_GUARD_TRIPPED" not in codes


def test_sufficient_downside_observations_computes_sortino():
    """>= sortino_min_downside_observations Downside-Beobachtungen -> normale Berechnung, kein
    SORTINO_INSUFFICIENT_DOWNSIDE."""
    idx = pd.date_range("2025-01-01", periods=61, freq="1h", tz="UTC")
    rets = ([0.0015, -0.0011] * 30)
    vals = [1000.0]
    for r in rets:
        vals.append(vals[-1] * (1 + r))
    series = pd.Series(vals, index=idx)
    # Issue #844 — sortino_numeric_guard_min_periods ist jetzt real gesetzt (1600); bei 60
    # Perioden deaktiviert, da dieser Test die downside_observations-Schwelle prueft, nicht den
    # T-bewussten Numerik-Guard.
    with patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=25), \
         patch("automation.backtest_runner._read_sortino_numeric_guard_min_periods", return_value=None):
        stats = _calculate_stats([1.0] * 30 + [-1.0] * 30, [(3600 * 10**9, 1.0)] * 60, 1000.0,
                                 mtm_series=series, min_trades_for_sortino=10)
    codes = [d["code"] for d in stats["inference_diagnostics"]]
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" not in codes
    assert stats["sortino_ratio"] is not None


def test_default_min_downside_observations_is_now_relative(tmp_path, monkeypatch):
    """Issue #863 — der absolute Default 30 war fuer hochselektive Strategien strukturell
    unerreichbar; der neue Default ist ein relativer Anteil (0.5) von n_periods."""
    import automation.backtest_runner as br
    monkeypatch.setattr(br, "_sortino_min_downside_observations_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    assert br._read_sortino_min_downside_observations() == 0.5


def test_default_min_periods_absolute_is_20(tmp_path, monkeypatch):
    import automation.backtest_runner as br
    monkeypatch.setattr(br, "_sortino_min_periods_absolute_cache", None)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    assert br._read_sortino_min_periods_absolute() == 20


# ── Informative-Teilmenge ändert n_periods (Telemetrie) ─────────────────────────────────────────
def test_n_periods_reflects_informative_count_not_full_calendar_grid():
    """Ein 24/7-artiges Raster mit vielen flachen (Nicht-Handels-)Bars: n_periods zaehlt nur die
    Bars MIT tatsaechlicher Rendite."""
    idx = pd.date_range("2025-01-01", periods=101, freq="1h", tz="UTC")
    # 40 informative (alternierende) Bewegungen, Rest komplett flach (kein Nicht-Handelsraster-
    # Beitrag zur Sortino-Inferenz).
    rets = ([0.001, -0.0008] * 20) + ([0.0] * 60)
    vals = [1000.0]
    for r in rets:
        vals.append(vals[-1] * (1 + r))
    series = pd.Series(vals, index=idx)
    with patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=1):
        stats = _calculate_stats([1.0] * 20 + [-1.0] * 20, [(3600 * 10**9, 1.0)] * 40, 1000.0,
                                 mtm_series=series, min_trades_for_sortino=10)
    assert stats["n_periods"] == 40  # NICHT 100 (die volle Kalenderachse)


# ── total_return bleibt über die VOLLE Kurve unveraendert (#801-Identitaet) ─────────────────────
def test_total_return_unaffected_by_informative_filtering():
    idx = pd.date_range("2025-01-01", periods=101, freq="1h", tz="UTC")
    rets = ([0.001, -0.0008] * 20) + ([0.0] * 60)
    vals = [1000.0]
    for r in rets:
        vals.append(vals[-1] * (1 + r))
    series = pd.Series(vals, index=idx)
    with patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=1):
        stats = _calculate_stats([1.0] * 20 + [-1.0] * 20, [(3600 * 10**9, 1.0)] * 40, 1000.0,
                                 mtm_series=series, min_trades_for_sortino=10)
    expected_total_return = vals[-1] / vals[0] - 1.0
    assert stats["total_return"] == pytest.approx(expected_total_return, rel=1e-9)
