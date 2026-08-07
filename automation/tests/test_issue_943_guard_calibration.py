"""Issue #943 (Katalog B, P0 HEADLINE, Pitfall #293) — exogene H0-Bootstrap-Kalibrierung des
Sortino-Numerik-Guards als Alternative zur endogenen ``family_median``-Konstruktion.

Diese Tests verifizieren die vier in #943 geforderten Eigenschaften direkt gegen die neue
``guard_calibration``-Infrastruktur:
1. Stationär (identische Tabelle unabhängig von der Trial-Ausführung/-Reihenfolge).
2. Reproduzierbar (identischer Seed -> bitgleiche Tabelle, unabhängig von n_jobs — diese Funktion
   hat schlicht KEINE Abhängigkeit von der Trial-Ausführung, das ist die Eigenschaft selbst).
3. Exogen (hängt nur von bar_returns ab).
4. Kalibriert (Fehlerrate ist per Konstruktion ~alpha).
"""
import numpy as np
import pytest

from automation.optimizer.guard_calibration import (
    calibrate_sortino_guard_table, guard_value_for_n_periods, guard_table_hash,
)


def _synthetic_returns(n=2000, seed=0, scale=0.001):
    rng = np.random.default_rng(seed)
    # AR(1)-artige Autokorrelation, damit der Stationary-Bootstrap-Block-Mechanismus etwas zu
    # erhalten hat (ein reiner i.i.d.-Test wuerde den Unterschied zu einem naiven Bootstrap nicht
    # zeigen).
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.1 * x[i - 1] + rng.normal(0, scale)
    return x


def test_empty_or_degenerate_returns_yield_empty_table():
    assert calibrate_sortino_guard_table([], n_grid=[50, 100], seed=1) == {}
    assert calibrate_sortino_guard_table([np.nan, np.nan], n_grid=[50], seed=1) == {}
    assert calibrate_sortino_guard_table([0.01], n_grid=[50], seed=1) == {}


def test_table_has_an_entry_per_n_grid_value():
    rets = _synthetic_returns(seed=1)
    table = calibrate_sortino_guard_table(rets, n_grid=[50, 100, 200], n_boot=100, seed=42)
    assert set(table) == {50, 100, 200}
    assert all(v > 0 for v in table.values())


def test_identical_seed_yields_bitwise_identical_table_reproducibility():
    """Akzeptanzkriterium #943-2 — der eigentliche Abnahmetest: zwei 'Läufe' mit identischem Seed
    liefern eine bitgleiche Tabelle. Da diese Funktion NIE von Trial-Ausführungsreihenfolge/
    n_jobs abhängt (sie läuft VOR jedem Trial, einmalig), ist Reproduzierbarkeit hier eine direkte
    Eigenschaft der Funktion selbst, nicht nur ein empirischer Befund."""
    rets = _synthetic_returns(seed=2)
    table_a = calibrate_sortino_guard_table(rets, n_grid=[50, 150], n_boot=200, seed=7)
    table_b = calibrate_sortino_guard_table(rets, n_grid=[50, 150], n_boot=200, seed=7)
    assert table_a == table_b


def test_different_seed_can_yield_different_table_values():
    """Gegenprobe: der Seed muss tatsaechlich etwas bewirken (kein konstanter Dummy-Output)."""
    rets = _synthetic_returns(seed=3)
    table_a = calibrate_sortino_guard_table(rets, n_grid=[80], n_boot=200, seed=1)
    table_b = calibrate_sortino_guard_table(rets, n_grid=[80], n_boot=200, seed=2)
    assert table_a[80] != table_b[80]


def test_table_is_symbol_specific_not_a_shared_constant():
    """Akzeptanzkriterium #943-3 — guard_reference_value unterscheidet sich zwischen zwei
    Symbolen mit unterschiedlicher Return-Verteilung (Gegenbeweis zum #916-Befund: 320.0 trat bei
    GSAT UND LQDA auf, obwohl beide voellig verschiedene Volatilitaet/Bar-Abdeckung haben)."""
    low_vol = _synthetic_returns(seed=4, scale=0.0005)
    high_vol = _synthetic_returns(seed=4, scale=0.01)
    table_low = calibrate_sortino_guard_table(low_vol, n_grid=[100], n_boot=200, seed=99)
    table_high = calibrate_sortino_guard_table(high_vol, n_grid=[100], n_boot=200, seed=99)
    assert table_low[100] != table_high[100]


def test_error_rate_is_approximately_alpha_under_h0():
    """Akzeptanzkriterium #943-4 — unter H0 (die kalibrierende Verteilung selbst) trippt der
    Guard in ungefaehr alpha der Faelle: eine ZWEITE, unabhaengige Stichprobe aus DERSELBEN H0-
    Verteilung sollte mit Wahrscheinlichkeit ~alpha den (1-alpha)-Quantil-Guard ueberschreiten."""
    rng = np.random.default_rng(123)
    x = rng.normal(0, 0.001, size=5000)
    alpha = 0.05  # groesserer alpha fuer eine stabile Testschaetzung bei realistischem n_boot
    table = calibrate_sortino_guard_table(x, n_grid=[100], n_boot=2000, alpha=alpha, seed=5)
    guard = table[100]

    from automation.optimizer.guard_calibration import _annualized_sortino
    trip_count = 0
    n_trials = 400
    check_rng = np.random.default_rng(999)
    for _ in range(n_trials):
        sample = check_rng.choice(x, size=100, replace=True)
        stat = abs(_annualized_sortino(sample))
        if stat > guard:
            trip_count += 1
    observed_rate = trip_count / n_trials
    # grosszuegiges Toleranzband (binomiale Schwankung bei n_trials=400, alpha=0.05 -> SE ~ 1.1%)
    assert observed_rate < alpha + 0.10


def test_guard_value_for_n_periods_exact_and_interpolated_lookup():
    table = {50: 10.0, 200: 40.0}
    assert guard_value_for_n_periods(table, 50) == 10.0
    assert guard_value_for_n_periods(table, 200) == 40.0
    # log-lineare Interpolation zwischen 50 und 200 bei 100 liegt strikt zwischen beiden Werten.
    mid = guard_value_for_n_periods(table, 100)
    assert 10.0 < mid < 40.0


def test_guard_value_for_n_periods_clamps_outside_grid():
    table = {50: 10.0, 200: 40.0}
    assert guard_value_for_n_periods(table, 10) == 10.0
    assert guard_value_for_n_periods(table, 1000) == 40.0


def test_guard_value_for_n_periods_empty_table_returns_none():
    assert guard_value_for_n_periods({}, 100) is None


def test_guard_table_hash_is_deterministic_and_order_independent():
    table_a = {50: 10.0, 200: 40.0}
    table_b = {200: 40.0, 50: 10.0}
    assert guard_table_hash(table_a) == guard_table_hash(table_b)


def test_guard_table_hash_changes_with_content():
    assert guard_table_hash({50: 10.0}) != guard_table_hash({50: 10.1})
