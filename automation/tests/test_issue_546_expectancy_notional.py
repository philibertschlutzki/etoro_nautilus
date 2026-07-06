"""Issue #546 — Expectancy als notional-relativer Per-Trade-Return (sizing-invariant).

Vor #546 wurde Expectancy auf das fixe ``starting_capital`` normiert und skalierte damit
mit der Positionsgröße; das erzwang die mikroskopische Gate-Schwelle 5e-05. Seit #546 ist
Expectancy der Netto-Return je Trade auf das je Trade eingesetzte Notional. Direkt-Unit-Calls
ohne ``notional_list`` bleiben bit-identisch (Legacy-Pfad).
"""
import statistics

from automation.backtest_runner import _calculate_stats


def test_expectancy_is_sizing_invariant():
    """Zwei identische Per-Trade-Edges bei 10 % vs. 100 % Einsatz ⇒ IDENTISCHE Expectancy."""
    # Kleiner Einsatz: 10 % vom Kapital je Trade (Notional 1000 auf 10000 Kapital)
    small_pnls = [10.0, 5.0, -2.0, 15.0]
    small_notionals = [1000.0, 1000.0, 1000.0, 1000.0]
    # Großer Einsatz: 100 % (Notional 10000) mit dem 10-fachen PnL (identischer Per-Trade-Return)
    large_pnls = [100.0, 50.0, -20.0, 150.0]
    large_notionals = [10000.0, 10000.0, 10000.0, 10000.0]

    m_small = _calculate_stats(small_pnls, [], 10000.0, notional_list=small_notionals)
    m_large = _calculate_stats(large_pnls, [], 10000.0, notional_list=large_notionals)

    # Erwartete Per-Trade-Returns: [0.01, 0.005, -0.002, 0.015] ⇒ Mittel 0.007
    assert abs(m_small["expectancy"] - 0.007) < 1e-12
    assert abs(m_large["expectancy"] - 0.007) < 1e-12
    # Kernaussage: sizing-invariant (vorher Faktor 10 auseinander).
    assert abs(m_small["expectancy"] - m_large["expectancy"]) < 1e-12


def test_legacy_path_bit_identical_without_notionals():
    """Ohne ``notional_list`` bleibt die Berechnung exakt der Legacy-Pfad (mean(pnl/starting_capital))."""
    pnl_list = [10.0, 5.0, -2.0, 15.0]
    starting_capital = 1000.0

    legacy = _calculate_stats(pnl_list, [], starting_capital)
    expected = statistics.mean([v / starting_capital for v in pnl_list])
    assert abs(legacy["expectancy"] - expected) < 1e-12
    # Regressions-Anker aus Issue #506 (unverändert).
    assert abs(legacy["expectancy"] - 0.007) < 1e-9


def test_mismatched_notional_length_falls_back_to_legacy():
    """Längen-inkongruente notional_list ⇒ defensiver Fallback auf den Legacy-Pfad (kein Crash)."""
    pnl_list = [10.0, 5.0]
    m = _calculate_stats(pnl_list, [], 1000.0, notional_list=[1000.0])  # falsche Länge
    expected = statistics.mean([v / 1000.0 for v in pnl_list])
    assert abs(m["expectancy"] - expected) < 1e-12


def test_nonpositive_notionals_are_skipped():
    """Trades mit Notional <= 0 werden defensiv übersprungen (keine Division durch 0)."""
    pnl_list = [10.0, 5.0]
    m = _calculate_stats(pnl_list, [], 1000.0, notional_list=[0.0, 1000.0])
    # Nur der zweite Trade zählt: 5.0 / 1000.0 = 0.005
    assert abs(m["expectancy"] - 0.005) < 1e-12
