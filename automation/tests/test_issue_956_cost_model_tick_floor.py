"""Issue #956 (Katalog D, P0, Pitfall #301) — ein Spread-Wert je Asset-Klasse über ein Universum
von Micro-Caps bis Mega-Caps unterschätzt den Round-Trip-Spread genau dort am stärksten, wo der
Backtest die höchsten Roh-Renditen meldet.

Physikalische Untergrenze für einen Round-Trip-Spread bei Mindestpreisschritt ``tau`` und Preis
``P``: ``c_spread_min = 1e4 * tau / P`` [bps]. Bei $2 und $0.01-Tick sind das 50bps Round-Trip —
Faktor ~17 über einer konfigurierten EQUITY-Konstante von 3.0bps.

``resolve_spread_bps`` nimmt seit diesem Fix ``max(config_wert, tick_floor_bps)`` — die
konfigurierte Konstante darf den Spread nie UNTER die physikalische Untergrenze drücken.
"""
import pytest

from automation.backtest_runner import resolve_spread_bps, tick_floor_spread_bps
from automation.optimizer.invariants import check_cost_model_floor


def test_tick_floor_formula_matches_physical_lower_bound():
    # $2 Preis, $0.01 Tick -> 1e4 * 0.01 / 2 = 50.0 bps Round-Trip-Untergrenze.
    assert tick_floor_spread_bps(2.00, 0.01) == pytest.approx(50.0)
    # $100 Preis, $0.01 Tick -> 1.0 bps.
    assert tick_floor_spread_bps(100.00, 0.01) == pytest.approx(1.0)


def test_tick_floor_is_zero_when_inputs_missing_or_invalid():
    assert tick_floor_spread_bps(None, 0.01) == 0.0
    assert tick_floor_spread_bps(2.0, None) == 0.0
    assert tick_floor_spread_bps(0.0, 0.01) == 0.0
    assert tick_floor_spread_bps(2.0, 0.0) == 0.0
    assert tick_floor_spread_bps(-1.0, 0.01) == 0.0


def test_resolve_spread_bps_tick_floor_overrides_asset_class_constant():
    """Ein $2-Micro-Cap mit konfigurierter EQUITY=3.0bps muss trotzdem >= 50.0bps simulieren."""
    ac = {"EQUITY": 3.0}
    floor = tick_floor_spread_bps(2.00, 0.01)  # 50.0
    resolved = resolve_spread_bps("MICROCAP.ETORO", ac, None, "EQUITY", tick_floor_bps=floor)
    assert resolved == pytest.approx(50.0)


def test_resolve_spread_bps_tick_floor_overrides_symbol_override_too():
    """Auch ein manueller Symbol-Override darf nicht UNTER die physikalische Untergrenze fallen."""
    sym = {"MICROCAP.ETORO": 3.0}
    floor = tick_floor_spread_bps(2.00, 0.01)  # 50.0
    resolved = resolve_spread_bps("MICROCAP.ETORO", {}, sym, "EQUITY", tick_floor_bps=floor)
    assert resolved == pytest.approx(50.0)


def test_resolve_spread_bps_tick_floor_default_zero_is_bit_identical_to_pre_956():
    """Kein tick_floor_bps übergeben (Default 0.0) -> unverändertes Pre-#956-Verhalten."""
    ac = {"EQUITY": 3.0}
    assert resolve_spread_bps("AAPL.ETORO", ac, None, "EQUITY") == 3.0


def test_resolve_spread_bps_config_constant_wins_when_already_above_floor():
    """Ein liquider Blue-Chip ($500, $0.01 Tick -> 0.2bps Floor) bleibt bei der (höheren,
    konservativeren) konfigurierten Konstante."""
    ac = {"EQUITY": 3.0}
    floor = tick_floor_spread_bps(500.00, 0.01)  # 0.2
    resolved = resolve_spread_bps("BLUECHIP.ETORO", ac, None, "EQUITY", tick_floor_bps=floor)
    assert resolved == pytest.approx(3.0)


# ── check_cost_model_floor Invariante ──────────────────────────────────────────────────────────
def test_check_cost_model_floor_passes_when_all_symbols_respect_the_floor():
    events = [
        {"symbol": "AAPL.ETORO", "spread_bps": 3.0, "tick_floor_bps": 0.2},
        {"symbol": "MICROCAP.ETORO", "spread_bps": 50.0, "tick_floor_bps": 50.0},
    ]
    result = check_cost_model_floor(events)
    assert result.passed is True


def test_check_cost_model_floor_fails_when_a_symbol_is_simulated_below_the_floor():
    events = [
        {"symbol": "AAPL.ETORO", "spread_bps": 3.0, "tick_floor_bps": 0.2},
        # Regression: resolve_spread_bps's max()-Absicherung haette das nie zugelassen.
        {"symbol": "MICROCAP.ETORO", "spread_bps": 3.0, "tick_floor_bps": 50.0},
    ]
    result = check_cost_model_floor(events)
    assert result.passed is False
    assert result.actual == 1


def test_check_cost_model_floor_not_applicable_without_tick_floor_field():
    """Alt-Telemetrie (vor #956) ohne tick_floor_bps-Feld darf nicht rückwirkend FAILen."""
    events = [{"symbol": "AAPL.ETORO", "spread_bps": 3.0}]
    result = check_cost_model_floor(events)
    assert result.passed is True
    assert result.actual is None
