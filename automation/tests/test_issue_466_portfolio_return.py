"""Issue #465 (Audit #466) — `total_return` darf nicht über sequentielles Aufzinsen der Per-Trade-
Returns (`Π(1 + pnl_i/C0)`) gebildet werden, weil das implizit 100 % Kapitaleinsatz je Trade
nacheinander unterstellt und so das OOS-Gate (`oos_min_total_return`) sowie — über #461 — die
dominante Reward-Penalty verzerrt. Stattdessen: echter Portfolio-Return aus der zeitbasierten
MtM-Equity-Kurve `equity_end / equity_start − 1`.

Akzeptanzkriterien (aus Issue #465):
  * Bei nicht-überlappenden Full-Capital-Trades stimmt der neue total_return mit der alten
    Aufzinsung überein (Abwärtskompatibilität im Sonderfall).
  * Bei überlappenden/fraktionalen Trades weicht er nachweisbar und korrekt ab.
  * Ohne Equity-Kurve bleibt der sequentielle Fallback erhalten (rückwärtskompatibel).
"""
import math

import pandas as pd

from automation.backtest_runner import _calculate_stats


def _seq_compound(pnls, c0):
    cum = 1.0
    for v in pnls:
        cum *= (1.0 + v / c0)
    return cum - 1.0


def test_total_return_from_mtm_equity_curve():
    """Liegt eine MtM-Equity-Kurve vor, ist total_return == equity_end/equity_start − 1."""
    c0 = 100.0
    pnls = [50.0, 50.0]
    ts = pd.date_range("2026-01-01", periods=3, freq="D")
    # Lineare PnL-Akkumulation in der Equity-Kurve: 100 -> 150 -> 200.
    mtm = pd.Series([100.0, 150.0, 200.0], index=ts)

    stats = _calculate_stats(pnls, [(3600 * 1e9, 1.0)] * 2, c0, mtm_series=mtm)

    # Echter Portfolio-Return: 200/100 − 1 = 1.0
    assert math.isclose(stats["total_return"], 1.0, abs_tol=1e-9)
    # Und NICHT die sequentielle Aufzinsung (1.5 * 1.5 − 1 = 1.25), die 100 % Reinvestition unterstellt.
    assert not math.isclose(stats["total_return"], _seq_compound(pnls, c0), abs_tol=1e-6)
    assert math.isclose(_seq_compound(pnls, c0), 1.25, abs_tol=1e-9)


def test_backward_compatible_for_full_capital_single_trade():
    """Sonderfall (nicht-überlappend, ein Trade): MtM-Return == sequentielle Aufzinsung."""
    c0 = 100.0
    pnls = [10.0]
    ts = pd.date_range("2026-01-01", periods=2, freq="D")
    mtm = pd.Series([100.0, 110.0], index=ts)

    stats = _calculate_stats(pnls, [(3600 * 1e9, 1.0)], c0, mtm_series=mtm)
    assert math.isclose(stats["total_return"], 0.1, abs_tol=1e-9)
    assert math.isclose(stats["total_return"], _seq_compound(pnls, c0), abs_tol=1e-9)


def test_fallback_sequential_when_no_mtm():
    """Ohne Equity-Kurve bleibt der sequentielle Fallback erhalten (rückwärtskompatibel)."""
    c0 = 100.0
    pnls = [50.0, 50.0]
    stats = _calculate_stats(pnls, [(3600 * 1e9, 1.0)] * 2, c0, mtm_series=None)
    # Fallback == sequentielle Aufzinsung (1.25).
    assert math.isclose(stats["total_return"], _seq_compound(pnls, c0), abs_tol=1e-9)
    assert math.isclose(stats["total_return"], 1.25, abs_tol=1e-9)


def test_overlapping_trades_use_true_equity_not_naive_sum():
    """Überlappende Trades: der MtM-Return spiegelt die tatsächliche (auch fraktionale) Equity wider,
    nicht die (vorzeichen-kippbare) sequentielle Aufzinsung der Einzel-Trade-Returns."""
    c0 = 1000.0
    # Gemischte Sequenz, bei der sequentielles Aufzinsen die wahre Equity-Entwicklung verfehlt.
    pnls = [300.0, -200.0, 150.0]
    ts = pd.date_range("2026-01-01", periods=4, freq="D")
    # Reale (überlappende) Equity endet bei 1100 ⇒ wahrer Return = 0.10.
    mtm = pd.Series([1000.0, 1300.0, 1100.0, 1100.0], index=ts)

    stats = _calculate_stats(pnls, [(3600 * 1e9, 1.0)] * 3, c0, mtm_series=mtm)
    assert math.isclose(stats["total_return"], 0.10, abs_tol=1e-9)


def test_zero_start_equity_falls_back_safely():
    """Degenerierte Equity-Kurve (Start 0) ⇒ kein Division-by-Zero, sondern sequentieller Fallback."""
    c0 = 100.0
    pnls = [10.0]
    ts = pd.date_range("2026-01-01", periods=2, freq="D")
    mtm = pd.Series([0.0, 110.0], index=ts)
    stats = _calculate_stats(pnls, [(3600 * 1e9, 1.0)], c0, mtm_series=mtm)
    # Fällt sicher auf sequentielle Aufzinsung zurück (0.1), kein ZeroDivisionError.
    assert math.isclose(stats["total_return"], 0.1, abs_tol=1e-9)
