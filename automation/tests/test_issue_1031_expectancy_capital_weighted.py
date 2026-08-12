"""Issue #1031 (Katalog #866) — ``expectancy`` (``mean(pnl_i/notional_i)``) ist ein Mittel von
Quotienten ohne Nennerboden und ohne Winsorisierung: ein einzelner Round-Trip mit degeneriertem
Nenner verschiebt den Mittelwert ueber Hunderte Trades beliebig weit (beobachtet: TSLA-Kandidaten
mit expectancy=0,52 bei implizitem Sizing-Anteil f=0,93 % gegen konfigurierte 15 % — Faktor 16).

``expectancy`` selbst bleibt bewusst UNVERAENDERT (Zero-Regression auf jede kalibrierte
Schwelle/jeden Reward-Gradienten); die neuen, additiven Felder sind
``expectancy_capital_weighted``/``expectancy_winsorized``/``expectancy_outlier_count``/
``expectancy_notional_degenerate_count``.
"""
from automation.backtest_runner import _calculate_stats


def test_single_degenerate_notional_trade_distorts_expectancy_but_not_capital_weighted():
    """19 homogene Trades (ratio 0.01) + 1 Trade mit degeneriertem Notional (1 statt Median 1000)
    und riesigem PnL (ratio 500) — die klassische #1031-Signatur."""
    pnl_list = [10.0] * 19 + [500.0]
    notional_list = [1000.0] * 19 + [1.0]

    m = _calculate_stats(pnl_list, [], 10000.0, notional_list=notional_list)

    # Der unveraenderte, alte Pfad bleibt genauso verzerrt wie vorher (Zero-Regression) —
    # (19*0.01 + 500) / 20 = 25.0095.
    assert abs(m["expectancy"] - 25.0095) < 1e-9

    # Der Nennerboden (5 % des Median-Notionals = 50) schliesst den degenerierten Trade komplett
    # aus expectancy_capital_weighted aus: (19*10)/(19*1000) = 0.01 exakt.
    assert abs(m["expectancy_capital_weighted"] - 0.01) < 1e-9
    assert m["expectancy_notional_degenerate_count"] == 1

    # Die verbleibende, homogene Kohorte hat keine Ausreisser und keine Wirkung der Winsorisierung.
    assert abs(m["expectancy_winsorized"] - 0.01) < 1e-9
    assert m["expectancy_outlier_count"] == 0

    # Kernaussage des Katalogs: die neue Kennzahl liegt um Groessenordnungen naeher an der
    # tatsaechlichen Ökonomie als die alte.
    assert m["expectancy"] / m["expectancy_capital_weighted"] > 1000


def test_capital_weighted_matches_expectancy_on_homogeneous_notionals():
    """Ohne Nennerausreisser (alle Notionale gleich) sind expectancy und
    expectancy_capital_weighted identisch — die neue Kennzahl ist keine andere Definition, nur
    robuster gegen Nennerausreisser."""
    pnl_list = [10.0, 5.0, -2.0, 15.0]
    notional_list = [1000.0] * 4
    m = _calculate_stats(pnl_list, [], 10000.0, notional_list=notional_list)
    assert abs(m["expectancy"] - m["expectancy_capital_weighted"]) < 1e-12
    assert m["expectancy_notional_degenerate_count"] == 0


def test_legacy_path_without_notional_list_leaves_new_fields_none():
    """Direkt-Unit-Calls ohne notional_list (Legacy-Pfad) liefern weiterhin ``expectancy``, aber
    keine der neuen, notional-abhaengigen Kennzahlen (kein stiller Fake-Wert)."""
    m = _calculate_stats([10.0, 5.0, -2.0], [], 1000.0)
    assert m["expectancy_capital_weighted"] is None
    assert m["expectancy_winsorized"] is None
    assert m["expectancy_outlier_count"] == 0
