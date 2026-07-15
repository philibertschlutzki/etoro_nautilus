"""Issue #632 — Benchmark-Excess-Return über andere Zeitspanne als der Strategie-Return (#552-Bug).

Zähler (Strategie-``total_return``) und Nenner (``oos_buyhold_return``) des Excess-Returns wurden
über UNTERSCHIEDLICHE Fensterungen aggregiert: die Strategie per-Fold-kompoundiert über die
disjunkten OOS-Segmente (IS-Fenster + Embargo AUSGESCHLOSSEN), der Benchmark als
``letzter_OOS_Bar / erster_OOS_Bar − 1`` über die VOLLE Spanne Fold-0-Start → Fold-(n-1)-Ende — die
IS-Fenster + Embargos DAZWISCHEN eingeschlossen. Da Kursbewegung pfadabhängig ist, "sieht" der
Endpunkt-Quotient jede Kursbewegung, die während der ausgeschlossenen Lücke passiert, obwohl kein
einziger Lücken-Bar im Array steht — der Benchmark wird für Marktbewegung gutgeschrieben, die
ausserhalb der tatsächlichen OOS-Beobachtungsfenster liegt.

Fix: der Benchmark wird PER-FOLD kompoundiert, exakt wie ``total_return`` (``mtm_frames`` in
``_calculate_stats``) — Zähler und Nenner decken jetzt bit-identisch dieselbe Bar-Menge ab.
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


def test_benchmark_ignores_price_drift_during_the_is_embargo_gap():
    """Kernszenario: der Benchmark ist FLACH innerhalb JEDES OOS-Folds, steigt aber stark WÄHREND der
    Lücke (IS-Fenster + Embargo) zwischen den Folds. Der alte Endpunkt-Quotient über die volle Spanne
    kreditiert den Benchmark fälschlich mit dem Lücken-Anstieg; die per-Fold-Kompoundierung (Fix)
    liefert exakt 0 % — der Benchmark bewegte sich während der TATSÄCHLICHEN OOS-Fenster nicht."""
    start_ns = 1000 * DAY_NS
    # is_window=2d, oos_window=2d, splits=2, embargo=1d ⇒
    #   IS:      [start, start+2d)
    #   Embargo: [start+2d, start+3d)
    #   Fold0 OOS: [start+3d, start+5d)
    #   Fold1 OOS: [start+5d, start+7d)
    wf = {"is_window_days": 2, "oos_window_days": 2, "splits": 2, "embargo_period_days": 1}

    idx_ns = list(range(start_ns, start_ns + 7 * DAY_NS + 1, HOUR_NS))
    idx = pd.to_datetime(idx_ns, unit="ns")
    idx_arr = np.array(idx_ns)

    equity = pd.Series(np.linspace(10000.0, 10050.0, len(idx)), index=idx)

    # Benchmark-Close: konstant 100 bis zum Fold0-Ende (start+5d), dann ein SPRUNG auf 200 exakt in
    # der Lücke vor Fold1 (start+5d..start+5d, am Fold1-Start bereits bei 200), danach wieder FLACH
    # bei 200 durch Fold1. Da Fold0 UND Fold1 jeweils flach sind (kein Return innerhalb der Folds),
    # aber der Sprung exakt zwischen ihnen liegt (im Embargo/IS-Bereich vor Fold1, ausserhalb JEDES
    # OOS-Fensters), ist die "wahre" OOS-Benchmark-Bewegung 0 % — der Sprung fand ausserhalb statt.
    close = pd.Series(100.0, index=idx)
    fold1_start_ns = start_ns + 5 * DAY_NS
    close.loc[idx_arr >= fold1_start_ns] = 200.0

    # Ein OOS-Round-Trip pro Fold, damit oos_metrics evaluiert wird.
    f0 = start_ns + 3 * DAY_NS
    f1 = start_ns + 5 * DAY_NS
    fills = pd.DataFrame([
        _fill("X.Y", 100.0, 1.0, f0 + HOUR_NS, "BUY"),
        _fill("X.Y", 100.5, 1.0, f0 + 2 * HOUR_NS, "SELL"),
        _fill("X.Y", 200.0, 1.0, f1 + HOUR_NS, "BUY"),
        _fill("X.Y", 200.5, 1.0, f1 + 2 * HOUR_NS, "SELL"),
    ])
    engine = MagicMock()
    engine.trader.generate_fills_report.return_value = fills

    res = extract_metrics(engine, 10000.0, None, wf, start_ns, mtm_series=equity, benchmark_series=close)
    oos = res["oos_metrics"]

    assert "oos_buyhold_return" in oos
    # Fix (#632): der Benchmark ist innerhalb JEDES OOS-Folds flach ⇒ per-Fold-kompoundiert 0 %.
    assert oos["oos_buyhold_return"] == pytest.approx(0.0, abs=1e-9)

    # Gegenprobe: der ALTE (fehlerhafte) Endpunkt-Quotient über die volle Spanne hätte den
    # Lücken-Sprung eingepreist ⇒ +100 %. Das ist explizit NICHT mehr das Ergebnis.
    old_buggy = close.loc[pd.to_datetime(f0 + HOUR_NS, unit="ns"):].iloc[-1] / \
        close.loc[pd.to_datetime(f0, unit="ns"):].iloc[0] - 1.0
    assert oos["oos_buyhold_return"] != pytest.approx(old_buggy, abs=1e-6)
    assert old_buggy > 0.5  # Sanity: der alte Endpunkt-Quotient zeigt tatsächlich den Lücken-Sprung.


def test_strategy_and_benchmark_cover_bit_identical_bar_set():
    """Akzeptanzkriterium (#632): Strategie- und Benchmark-Return decken bit-identisch dieselbe
    Bar-Menge ab — beide werden aus GENAU denselben ``fold_boundaries``-Segmenten kompoundiert."""
    start_ns = 1000 * DAY_NS
    wf = {"is_window_days": 2, "oos_window_days": 2, "splits": 2, "embargo_period_days": 1}
    idx_ns = list(range(start_ns, start_ns + 7 * DAY_NS + 1, HOUR_NS))
    idx = pd.to_datetime(idx_ns, unit="ns")

    equity = pd.Series(np.linspace(10000.0, 10100.0, len(idx)), index=idx)
    close = pd.Series(np.linspace(100.0, 110.0, len(idx)), index=idx)

    f0 = start_ns + 3 * DAY_NS
    f1 = start_ns + 5 * DAY_NS
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

    # Beide Grössen müssen definiert sein und aus derselben #632-Fold-für-Fold-Kompoundierung stammen.
    assert oos.get("total_return") is not None
    assert oos.get("oos_buyhold_return") is not None
    assert "oos_excess_return" in oos
    assert oos["oos_excess_return"] == pytest.approx(
        (oos.get("total_return") or 0.0) - oos["oos_buyhold_return"], abs=1e-12)
