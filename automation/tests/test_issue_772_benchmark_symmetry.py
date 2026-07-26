"""Issue #772 (P1) — Der Benchmark war per Fold kompoundiert, WEIL `total_return` es war (#632);
die Symmetrie muss mit `#771` mitgezogen werden.

`#771` stellt den Strategie-Zähler (`total_return`) auf die volle konkatenierte OOS-Fold-Union um
(eine offene Position kann einen Fold-Übergang überspannen). Ändert sich NUR der Zähler, kehrt
exakt der `#552`/`#632`-Span-Bug mit umgekehrtem Vorzeichen zurück — `oos_min_excess_return` ist
eines der harten Gates, der Effekt wäre unmittelbar selektionswirksam.

Fix: `oos_buyhold_return` wird über DIESELBE konkatenierte, halb-offen geschnittene,
deduplizierte Fold-Union gebildet wie `oos_mtm` (gemeinsamer Helper
`_fold_segments_half_open`/`_concat_half_open`, keine zweite Inline-Kopie der Schleife mehr).
"""
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from automation.backtest_runner import extract_metrics

DAY_NS = 86400 * 1_000_000_000
HOUR_NS = 3600 * 1_000_000_000


def _fill(iid, price, qty, ts_ns, side):
    return {"instrument_id": iid, "price": price, "quantity": qty,
            "ts_event": ts_ns, "side": side, "commission": 0}


def _two_fold_setup():
    start_ns = 1000 * DAY_NS
    wf = {"is_window_days": 2, "oos_window_days": 2, "splits": 2, "embargo_period_days": 1}
    idx_ns = list(range(start_ns, start_ns + 7 * DAY_NS + 1, HOUR_NS))
    idx = pd.to_datetime(idx_ns, unit="ns")
    f0 = start_ns + 3 * DAY_NS
    f1 = start_ns + 5 * DAY_NS
    return start_ns, wf, idx, f0, f1


def test_strategy_and_benchmark_oos_index_are_identical():
    """Akzeptanzkriterium #772/1: Index von Strategie- und Benchmark-OOS-Serie identisch."""
    from automation.backtest_runner import _calculate_stats, compute_fold_boundaries

    start_ns, wf, idx, f0, f1 = _two_fold_setup()
    equity = pd.Series(np.linspace(10000.0, 10100.0, len(idx)), index=idx)
    close = pd.Series(np.linspace(100.0, 110.0, len(idx)), index=idx)
    fold_boundaries = compute_fold_boundaries(start_ns, wf)

    def _slice_half_open(series, s_ns_, e_ns_):
        start_dt = pd.to_datetime(s_ns_, unit="ns")
        end_excl = pd.to_datetime(e_ns_, unit="ns") - pd.Timedelta(nanoseconds=1)
        return series.loc[start_dt:end_excl]

    strat_frames = [seg for _, s, e in fold_boundaries
                    if not (seg := _slice_half_open(equity, s, e)).empty]
    bench_frames = [seg for _, s, e in fold_boundaries
                    if not (seg := _slice_half_open(close, s, e)).empty]
    strat_concat = pd.concat(strat_frames).sort_index()
    bench_concat = pd.concat(bench_frames).sort_index()
    assert strat_concat.index.equals(bench_concat.index)


def test_constant_benchmark_yields_zero_buyhold_and_excess_equals_total_return():
    """Akzeptanzkriterium #772/2: konstante Benchmark-Serie ⇒ oos_buyhold_return == 0.0 und
    excess_return == total_return — sowohl vor als auch nach #771/#772."""
    start_ns, wf, idx, f0, f1 = _two_fold_setup()
    equity = pd.Series(np.linspace(10000.0, 10100.0, len(idx)), index=idx)
    close = pd.Series(100.0, index=idx)  # konstanter Preis

    fills = pd.DataFrame([
        _fill("X.Y", 100.0, 1.0, f0 + HOUR_NS, "BUY"),
        _fill("X.Y", 101.0, 1.0, f0 + 2 * HOUR_NS, "SELL"),
        _fill("X.Y", 100.0, 1.0, f1 + HOUR_NS, "BUY"),
        _fill("X.Y", 102.0, 1.0, f1 + 2 * HOUR_NS, "SELL"),
    ])
    engine = MagicMock()
    engine.trader.generate_fills_report.return_value = fills

    res = extract_metrics(engine, 10000.0, None, wf, start_ns, mtm_series=equity, benchmark_series=close)
    oos = res["oos_metrics"]

    assert oos["oos_buyhold_return"] == pytest.approx(0.0, abs=1e-12)
    assert oos["oos_excess_return"] == pytest.approx(oos["total_return"], abs=1e-12)


def test_benchmark_and_strategy_span_the_same_fold_union_not_the_full_calendar_span():
    """Der Benchmark-Endpunkt-Quotient nutzt die konkatenierte FOLD-UNION (Gap-Bars ausgeschlossen),
    nicht den vollen Kalenderspann von Fold0-Start bis Fold-(n-1)-Ende — nur die WERTE an den
    Fold-Grenzen unterscheiden sich (siehe test_issue_632 fuer das Sprung-Szenario); hier wird
    verifiziert, dass ein reiner untraded Gap OHNE Kurssprung keinen Unterschied macht."""
    start_ns, wf, idx, f0, f1 = _two_fold_setup()
    equity = pd.Series(np.linspace(10000.0, 10100.0, len(idx)), index=idx)
    close = pd.Series(np.linspace(100.0, 110.0, len(idx)), index=idx)  # stetig, kein Sprung

    fills = pd.DataFrame([
        _fill("X.Y", 100.0, 1.0, f0 + HOUR_NS, "BUY"),
        _fill("X.Y", 101.0, 1.0, f0 + 2 * HOUR_NS, "SELL"),
        _fill("X.Y", 100.0, 1.0, f1 + HOUR_NS, "BUY"),
        _fill("X.Y", 102.0, 1.0, f1 + 2 * HOUR_NS, "SELL"),
    ])
    engine = MagicMock()
    engine.trader.generate_fills_report.return_value = fills

    res = extract_metrics(engine, 10000.0, None, wf, start_ns, mtm_series=equity, benchmark_series=close)
    oos = res["oos_metrics"]
    # Endpunkt-Quotient ueber die Fold-Union: erster Bar von Fold0 vs. letzter Bar von Fold1.
    full_span_quotient = close.iloc[-1] / close.iloc[0] - 1.0
    assert oos["oos_buyhold_return"] != pytest.approx(full_span_quotient, abs=1e-9)
