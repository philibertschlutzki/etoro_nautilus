"""
tests/test_allocator.py
========================
Tests für MomentumLSAllocator — abgedeckte Architektur-Regeln aus AGENTS.md §5.7, seit Issue #999
(P0, HEADLINE) auf die Risikobudget-Formel umgestellt:
  - No-Interference-Regel: existiert eine offene Position, Allokation = 0.0
  - $11-Floor: Allokation unter $11.00 → 0.0
  - Budget-Formel (Issue #999): w_neu = min(max_symbol_exposure_fraction, max_total_exposure_fraction
    − Σ_offen w_i) — ersetzt die VOR #999 monoton STEIGENDE ``account_balance / pending_signals``
    (siehe automation/tests/test_issue_999_live_risk_boundary.py für die volle Budget-/Circuit-
    Breaker-Abdeckung; diese Datei bleibt auf die vier ursprünglichen §5.7-Architekturregeln fokussiert).
  - Nicht-Universe-Instrument → 0.0
"""
import pytest
from unittest.mock import MagicMock

from automation.momentum_ls_allocator import MomentumLSAllocator


def _make_cache(open_positions_by_id: dict) -> MagicMock:
    """Returns a mock cache where positions_open() returns the configured list."""
    cache = MagicMock()

    def _positions_open(instrument_id=None):
        return open_positions_by_id.get(str(instrument_id), [])

    cache.positions_open.side_effect = _positions_open
    return cache


UNIVERSE = ["AAPL.ETORO", "TSLA.ETORO", "GOOG.ETORO"]


def test_no_interference_rule():
    """Open position for instrument must return 0.0 (AGENTS.md §5.7)."""
    cache = _make_cache({"AAPL.ETORO": [object()]})  # one open position
    allocator = MomentumLSAllocator(UNIVERSE)
    from nautilus_trader.model.identifiers import InstrumentId
    instrument_id = InstrumentId.from_str("AAPL.ETORO")
    result = allocator.get_allocation(instrument_id, cache, account_balance=1000.0)
    assert result == 0.0


def test_floor_11_dollars():
    """Allocation below $11.00 must return 0.0 (AGENTS.md §5.7)."""
    # All 3 universe instruments have no open positions → pending=3
    # balance=30.0 → 30/3=10.0 < 11.0 → must return 0.0
    cache = _make_cache({})
    allocator = MomentumLSAllocator(UNIVERSE)
    from nautilus_trader.model.identifiers import InstrumentId
    instrument_id = InstrumentId.from_str("AAPL.ETORO")
    result = allocator.get_allocation(instrument_id, cache, account_balance=30.0)
    assert result == 0.0


def test_dynamic_slicing():
    """Issue #999 — Allocation = min(max_symbol_exposure_fraction, max_total_exposure_fraction −
    Σ_offen w_i) * balance, NICHT mehr balance / pending_signals. TSLA hat eine offene Position mit
    unbekanntem Notional (Test-Double ohne .quantity/.avg_px_open) → konservative Slot-Näherung:
    1 Position beansprucht ihr volles Symbol-Cap (10 % der 60 %-Budgetgrenze), die verbleibenden
    50 % lassen das volle 10 %-Symbol-Cap für AAPL zu."""
    cache = _make_cache({"TSLA.ETORO": [object()]})
    allocator = MomentumLSAllocator(UNIVERSE)
    from nautilus_trader.model.identifiers import InstrumentId
    instrument_id = InstrumentId.from_str("AAPL.ETORO")
    result = allocator.get_allocation(instrument_id, cache, account_balance=1000.0)
    assert result == pytest.approx(100.0)  # 10% of 1000, capped by max_symbol_exposure_fraction


def test_non_universe_instrument_returns_zero():
    """Instrument not in universe must return 0.0."""
    cache = _make_cache({})
    allocator = MomentumLSAllocator(UNIVERSE)
    from nautilus_trader.model.identifiers import InstrumentId
    unknown = InstrumentId.from_str("MSFT.ETORO")
    result = allocator.get_allocation(unknown, cache, account_balance=1000.0)
    assert result == 0.0


def test_all_positions_open_returns_zero():
    """If all universe instruments have open positions, pending=0 → 0.0."""
    open_pos = {sym: [object()] for sym in UNIVERSE}
    cache = _make_cache(open_pos)
    allocator = MomentumLSAllocator(UNIVERSE)
    from nautilus_trader.model.identifiers import InstrumentId
    instrument_id = InstrumentId.from_str("AAPL.ETORO")
    result = allocator.get_allocation(instrument_id, cache, account_balance=10000.0)
    assert result == 0.0


def test_symbol_cap_with_no_open_positions():
    """Issue #999 — mit KEINEN offenen Positionen ist die erste Allokation auf
    max_symbol_exposure_fraction (Default 10 %) des Kontostands gedeckelt — NICHT mehr
    balance / len(universe) (das war die Vor-#999-Formel; siehe test_dynamic_slicing für den
    Ruin-Pfad, den sie bei vielen offenen Positionen erzeugte)."""
    cache = _make_cache({})
    allocator = MomentumLSAllocator(UNIVERSE)
    from nautilus_trader.model.identifiers import InstrumentId
    instrument_id = InstrumentId.from_str("GOOG.ETORO")
    result = allocator.get_allocation(instrument_id, cache, account_balance=330.0)
    assert result == pytest.approx(33.0)  # 10% of 330
