"""Issue #801/#802 — die #756/#771-Log-Return-Identität ist gegen NaN/±inf abgesichert; Equity-
Kurven mit Nulldurchgang gelten als ökonomisch ruiniert statt einen stillschweigend teilmengenweise
berechneten (und damit ggf. vorzeichenverkehrten) Sortino zu liefern.

Root-Cause (Issue-Katalog #801): ``np.log1p(mtm_series.pct_change().dropna())`` liess die von
``log1p`` SELBST erzeugten NaN/-inf (Argument < -1 bzw. == -1, d. h. ein Equity-Vorzeichenwechsel)
unbeaufsichtigt in ``period_rets``; jede nachfolgende Aggregation (``mean()``, ``sum()``) überspringt
sie STILLSCHWEIGEND (``skipna=True``-Default) — der Sortino-Zähler wurde über eine ECHTE TEILMENGE
der Bars gebildet, die ``total_return`` abdeckt (Monte-Carlo-Beleg im Katalog: 44,3 % Vorzeichen-
Flips). Fix: (1) ``assert_positive_equity``-Gate VOR jeder Log-Rendite-Berechnung — eine Kurve mit
Nulldurchgang gilt als ``equity_ruined``, ``sortino``/``psr`` werden NICHT berechnet, ``total_return``
bleibt erhalten. (2) ``period_rets`` algebraisch (``np.diff(np.log(mtm))``) statt über
``pct_change()``/``log1p`` (#802 — versionsstabil). (3) ``skipna=False`` erzwungen + Endlichkeits-
Check (``PERIOD_RETURNS_NOT_FINITE``). (4) ``assert_return_series_identity`` behandelt
``total_return <= -1`` (``math.log1p``-``ValueError``) als Verletzung, nicht als "keine Verletzung".
"""
import logging
import math

import numpy as np
import pandas as pd
import pytest

from automation.backtest_runner import (
    _calculate_stats, assert_positive_equity, assert_return_series_identity,
)
from automation.optimizer.parsing import parse_tournament


# ── assert_positive_equity: reine Funktion ────────────────────────────────────────────────────────
def test_assert_positive_equity_all_positive_returns_true_minus_one():
    s = pd.Series([100.0, 105.0, 98.0, 110.0])
    assert assert_positive_equity(s) == (True, -1)


def test_assert_positive_equity_zero_crossing_returns_false_and_index():
    s = pd.Series([100.0, 50.0, -10.0, 20.0])
    ok, idx = assert_positive_equity(s)
    assert ok is False
    assert idx == 2


def test_assert_positive_equity_exact_zero_counts_as_non_positive():
    s = pd.Series([100.0, 0.0, 50.0])
    ok, idx = assert_positive_equity(s)
    assert ok is False
    assert idx == 1


def test_assert_positive_equity_empty_or_none_is_vacuously_true():
    assert assert_positive_equity(None) == (True, -1)
    assert assert_positive_equity(pd.Series([], dtype=float)) == (True, -1)


# ── Property-Test: Σ period_rets == log(1+total_return) auf 10.000 zufaelligen POSITIVEN Kurven ───
def test_log_return_identity_holds_on_ten_thousand_random_positive_curves():
    rng = np.random.default_rng(801)
    n_curves = 10_000
    n_checked = 0
    for _ in range(n_curves):
        n_bars = int(rng.integers(5, 60))
        rets = rng.normal(0.0002, 0.01, n_bars)
        mtm = 1000.0 * np.cumprod(1.0 + rets)
        if (mtm <= 0).any():
            continue  # der ruinierte Fall wird unten isoliert getestet
        series = pd.Series(mtm, index=pd.date_range("2025-01-01", periods=len(mtm), freq="h"))
        stats = _calculate_stats(
            pnl_list=[1.0], hold_list=[], starting_capital=1000.0, mtm_series=series,
        )
        total_return = stats["total_return"]
        log_target = math.log1p(total_return)
        # Reproduziert die Produktionsformel unabhaengig, um die Teleskopsumme zu pruefen.
        log_sum = float(np.diff(np.log(mtm)).sum())
        tol = 1e-9 * max(1.0, abs(log_target))
        assert abs(log_sum - log_target) < tol
        assert stats["return_series_identity_violation"] is False
        assert stats["equity_ruined"] is False
        n_checked += 1
    assert n_checked > 9000, "Testkonstruktion sollte fast ausschliesslich positive Kurven erzeugen"


# ── Nulldurchgang: sortino/psr None, equity_ruined True, total_return bleibt erhalten ──────────────
def test_equity_zero_crossing_yields_equity_ruined_and_no_sortino():
    vals = [1000.0, 800.0, -200.0, 300.0, 600.0, 900.0]
    idx = pd.date_range("2025-01-01", periods=len(vals), freq="h")
    series = pd.Series(vals, index=idx)
    stats = _calculate_stats(
        pnl_list=[1.0] * 5, hold_list=[(3600 * 10**9, 1.0)] * 5,
        starting_capital=1000.0, mtm_series=series, min_trades_for_sortino=2,
    )
    assert stats["equity_ruined"] is True
    assert stats["sortino_ratio"] is None
    assert stats["psr"] is None
    assert stats["psr_z"] is None
    assert stats["period_returns"] == []
    assert stats["n_periods"] == 0
    # total_return bleibt erhalten (die Telemetrie "Konto vernichtet").
    assert stats["total_return"] == pytest.approx(900.0 / 1000.0 - 1.0, abs=1e-9)


def test_equity_nonpositive_emits_error_event(caplog):
    vals = [1000.0, 500.0, 0.0, 300.0]
    idx = pd.date_range("2025-01-01", periods=len(vals), freq="h")
    series = pd.Series(vals, index=idx)
    with caplog.at_level(logging.ERROR, logger="optimizer"):
        _calculate_stats(pnl_list=[1.0] * 3, hold_list=[(3600 * 10**9, 1.0)] * 3,
                          starting_capital=1000.0, mtm_series=series)
    assert any("EQUITY_NONPOSITIVE" in r.message for r in caplog.records)


def test_equity_positive_curve_has_equity_ruined_false():
    vals = [1000.0, 1050.0, 980.0, 1100.0]
    idx = pd.date_range("2025-01-01", periods=len(vals), freq="h")
    series = pd.Series(vals, index=idx)
    stats = _calculate_stats(pnl_list=[1.0] * 3, hold_list=[(3600 * 10**9, 1.0)] * 3,
                             starting_capital=1000.0, mtm_series=series)
    assert stats["equity_ruined"] is False


# ── assert_return_series_identity: total_return <= -1 ist die STAERKSTE Verletzung ────────────────
def test_assert_return_series_identity_total_return_at_minus_one_is_a_violation():
    period_rets = pd.Series([0.1, -0.2, 0.05])
    assert assert_return_series_identity(-1.0, period_rets) is True


def test_assert_return_series_identity_total_return_below_minus_one_is_a_violation():
    period_rets = pd.Series([0.1, -0.2, 0.05])
    assert assert_return_series_identity(-1.5, period_rets) is True
    assert assert_return_series_identity(-2.3026, period_rets) is True
    # Verifiziert die Katalog-Behauptung direkt: math.log1p(-1.1026) wirft ValueError.
    with pytest.raises(ValueError):
        math.log1p(-1.1026)
    assert assert_return_series_identity(-1.1026, period_rets) is True


def test_assert_return_series_identity_still_passes_on_a_genuine_match():
    log_px = np.log(np.array([100.0, 110.0, 90.0, 130.0]))
    period_rets = pd.Series(np.diff(log_px))
    total_return = 130.0 / 100.0 - 1.0
    assert assert_return_series_identity(total_return, period_rets) is False


def test_assert_return_series_identity_relative_tolerance_scales_with_large_log_sum():
    # T=4320 Summanden mit winzigen Rundungsfehlern nahe der Gleitkomma-Praezision: eine ABSOLUTE
    # 1e-9-Schranke ist hier zu eng; die RELATIVE Toleranz (1e-12 * |log_sum|) traegt.
    rng = np.random.default_rng(802)
    log_px = np.log(1000.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.005, 4320)))
    period_rets = pd.Series(np.diff(log_px))
    log_sum = float(period_rets.sum())
    total_return = math.expm1(log_sum)
    assert assert_return_series_identity(total_return, period_rets) is False


def test_assert_return_series_identity_empty_period_rets_is_no_violation():
    assert assert_return_series_identity(0.05, pd.Series([], dtype=float)) is False
    assert assert_return_series_identity(0.05, None) is False


# ── PERIOD_RETURNS_NOT_FINITE: ein nicht-finiter Log-Return TROTZ positiver Equity ─────────────────
def test_non_finite_period_return_despite_positive_equity_is_treated_as_ruined(caplog):
    # Ueberlauf in float64 (> 1.8e308) erzeugt +inf; log(inf) - log(finit) ist +inf (nicht finit),
    # OBWOHL die Kurve selbst durchgehend > 0 bleibt (assert_positive_equity liefert True).
    vals = [1.0, 1e300, 1e300 * 1e300]
    assert np.isinf(vals[-1])
    idx = pd.date_range("2025-01-01", periods=len(vals), freq="h")
    series = pd.Series(vals, index=idx)
    with caplog.at_level(logging.ERROR, logger="optimizer"):
        stats = _calculate_stats(pnl_list=[1.0] * 2, hold_list=[(3600 * 10**9, 1.0)] * 2,
                                 starting_capital=1.0, mtm_series=series)
    assert stats["equity_ruined"] is True
    assert stats["sortino_ratio"] is None
    assert any("PERIOD_RETURNS_NOT_FINITE" in r.message for r in caplog.records)


# ── Monte-Carlo-Reproduktion des Root-Cause-Abschnitts: 0 Vorzeichen-Flips nach dem Fix ────────────
def test_monte_carlo_leveraged_crypto_zero_crossings_yield_zero_sign_flips():
    """Reproduziert die 44,3%-Vorzeichen-Flip-Rate aus dem Root-Cause-Abschnitt: synthetische 1h-
    Kurven mit gehebelter Krypto-Volatilitaet, gefiltert auf Kurven MIT mindestens einem
    Nulldurchgang. Vor dem Fix lieferten 44,3 % davon einen ENDLICHEN, aber VORZEICHENVERKEHRTEN
    Sortino (sign(sortino) != sign(total_return)). Nach dem Fix ist JEDE dieser Kurven
    ``equity_ruined`` (``sortino=None``) — 0 Vorzeichen-Ergebnisse ueberhaupt, geschweige denn
    verkehrte."""
    rng = np.random.default_rng(2026801)
    n_runs = 500
    n_bars = 4320
    n_with_crossing = 0
    n_sign_flips = 0
    for _ in range(n_runs):
        rets = rng.normal(0.0, 0.012, n_bars)
        # gehebelte MARGIN-Notional (Hebel 3x, additive PnL-Aufzinsung ohne Floor — kann wie ein
        # echtes MARGIN-Konto durch Null gehen).
        mtm = 1.0 + np.cumsum(rets * 3.0)
        if not (mtm <= 0).any():
            continue
        n_with_crossing += 1
        idx = pd.date_range("2025-01-01", periods=len(mtm), freq="h")
        series = pd.Series(mtm, index=idx)
        stats = _calculate_stats(
            pnl_list=[1.0] * 10, hold_list=[(3600 * 10**9, 1.0)] * 10,
            starting_capital=1.0, mtm_series=series, min_trades_for_sortino=2,
        )
        assert stats["equity_ruined"] is True
        sr, tr = stats.get("sortino_ratio"), stats.get("total_return")
        if sr is not None and tr is not None and sr != 0.0 and (tr > 0.0) != (sr > 0.0):
            n_sign_flips += 1
    assert n_with_crossing > 50, "Testkonstruktion sollte genug Nulldurchgaenge erzeugen"
    assert n_sign_flips == 0, f"{n_sign_flips} Vorzeichen-Flips trotz #801-Fix (sollten 0 sein)"


# ── parsing.py: equity_ruined-Telemetrie wird nach TournamentMetrics gehoben (None-safe) ──────────
def test_parse_tournament_lifts_equity_ruined_flag(tmp_path):
    payload = {
        "fully_eligible_pairs": 0,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": False, "win_count": 0,
            "oos_metrics": {
                "sortino_ratio": None, "total_return": -0.97, "max_drawdown": 1.0,
                "equity_ruined": True,
            },
        },
    }
    p = tmp_path / "tournament_result.json"
    p.write_text(__import__("json").dumps(payload), "utf-8")

    metrics = parse_tournament(p)
    assert metrics.oos_equity_ruined is True


def test_parse_tournament_equity_ruined_defaults_false_for_legacy_json(tmp_path):
    """Ein Pre-#801-tournament_result.json ohne ``equity_ruined``-Key bleibt rueckwaertskompatibel
    gruen (None-safe ⇒ False, kein KeyError)."""
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "oos_metrics": {"sortino_ratio": 1.0, "total_return": 0.1, "max_drawdown": 0.05},
        },
    }
    p = tmp_path / "tournament_result.json"
    p.write_text(__import__("json").dumps(payload), "utf-8")

    metrics = parse_tournament(p)
    assert metrics.oos_equity_ruined is False
