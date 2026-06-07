"""
tests/test_allocator.py
========================
Tests für MomentumLSAllocator — abgedeckte Architektur-Regeln aus AGENTS.md §5.7:
  - No-Interference-Regel: existiert eine offene Position, Allokation = 0.0
  - $11-Floor: Allokation unter $11.00 → 0.0
  - Dynamisches Slicing: account_balance / pending_signals
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
    """Allocation = balance / pending_signals (AGENTS.md §5.7)."""
    # TSLA has open position → pending = AAPL + GOOG = 2
    cache = _make_cache({"TSLA.ETORO": [object()]})
    allocator = MomentumLSAllocator(UNIVERSE)
    from nautilus_trader.model.identifiers import InstrumentId
    instrument_id = InstrumentId.from_str("AAPL.ETORO")
    result = allocator.get_allocation(instrument_id, cache, account_balance=100.0)
    assert result == pytest.approx(50.0)


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


def test_equal_split_no_open_positions():
    """With no open positions, allocation = balance / len(universe)."""
    cache = _make_cache({})
    allocator = MomentumLSAllocator(UNIVERSE)
    from nautilus_trader.model.identifiers import InstrumentId
    instrument_id = InstrumentId.from_str("GOOG.ETORO")
    result = allocator.get_allocation(instrument_id, cache, account_balance=330.0)
    # 330 / 3 = 110.0
    assert result == pytest.approx(110.0)
