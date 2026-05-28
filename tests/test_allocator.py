import pytest
from unittest.mock import Mock
from nautilus_trader.model.identifiers import InstrumentId
from automation.momentum_ls_allocator import MomentumLSAllocator

class TestAllocator:
    def test_allocator_no_interference(self):
        allocator = MomentumLSAllocator(["AAPL.ETORO", "TSLA.ETORO"])

        cache = Mock()
        # AAPL has open position
        def mock_positions_open(instrument_id):
            if instrument_id.symbol.value == "AAPL":
                return [Mock()]
            return []

        cache.positions_open.side_effect = mock_positions_open

        # Test AAPL should get 0.0
        allocation_aapl = allocator.get_allocation(InstrumentId.from_str("AAPL.ETORO"), cache, 1000.0)
        assert allocation_aapl == 0.0

        # Test TSLA should get all capital since it is the only one pending (AAPL is already open)
        # Pending signals = TSLA
        allocation_tsla = allocator.get_allocation(InstrumentId.from_str("TSLA.ETORO"), cache, 1000.0)
        assert allocation_tsla == 1000.0

    def test_allocator_floor_limit(self):
        allocator = MomentumLSAllocator(["AAPL.ETORO", "TSLA.ETORO"])

        cache = Mock()
        cache.positions_open.return_value = []

        # Total balance 20.0, 2 pending signals -> 10.0 each.
        # 10.0 is below floor limit of 11.0, so should return 0.0
        allocation = allocator.get_allocation(InstrumentId.from_str("AAPL.ETORO"), cache, 20.0)
        assert allocation == 0.0

        # Total balance 22.0, 2 pending signals -> 11.0 each.
        # Should return 11.0
        allocation2 = allocator.get_allocation(InstrumentId.from_str("AAPL.ETORO"), cache, 22.0)
        assert allocation2 == 11.0
