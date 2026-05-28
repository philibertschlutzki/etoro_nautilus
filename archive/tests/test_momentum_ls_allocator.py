import pytest
from unittest.mock import MagicMock
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.data import Bar
from nautilus_trader.model.objects import Price

from archive.adapters.momentum_ls_allocator import MomentumLSAllocator
from archive.strategies.momentum_ls_sma import MomentumLSSmaStrategy, MomentumLSSmaConfig


def test_allocator_logic():
    allocator = MomentumLSAllocator(["BTC.ETORO", "ETH.ETORO", "ADA.ETORO"])
    mock_cache = MagicMock()

    # Simulate no open positions
    mock_cache.positions_open.return_value = []

    balance = 3000.0
    inst_id = InstrumentId.from_str("BTC.ETORO")

    alloc = allocator.get_allocation(inst_id, mock_cache, balance)
    assert alloc == 1000.0  # 3000 / 3 pending signals

    # Simulate one open position (BTC already open)
    def mock_pos(instrument_id=None):
        if instrument_id == InstrumentId.from_str("BTC.ETORO"):
            return [MagicMock()]
        return []
    mock_cache.positions_open.side_effect = mock_pos

    # BTC should get 0.0 because it's open (no-interference)
    alloc_btc = allocator.get_allocation(inst_id, mock_cache, balance)
    assert alloc_btc == 0.0

    # ETH should split the balance with ADA (2 pending signals left)
    inst_eth = InstrumentId.from_str("ETH.ETORO")
    alloc_eth = allocator.get_allocation(inst_eth, mock_cache, balance)
    assert alloc_eth == 1500.0  # 3000 / 2

def test_allocator_floor():
    allocator = MomentumLSAllocator(["BTC.ETORO", "ETH.ETORO"])
    mock_cache = MagicMock()
    mock_cache.positions_open.return_value = []

    # Balance is 20.0, divided by 2 pending signals = 10.0
    # $10.0 is < $11.0 floor
    balance = 20.0
    inst_id = InstrumentId.from_str("BTC.ETORO")
    alloc = allocator.get_allocation(inst_id, mock_cache, balance)
    assert alloc == 0.0

def test_strategy_cache_balance_fetching():
    # Verify that the strategy extracts the balance from the Nautilus cache
    allocator = MomentumLSAllocator(["ADA.ETORO"])

    config = MomentumLSSmaConfig(
        instrument_id="ADA.ETORO",
        bar_type="ADA.ETORO-1-MINUTE-MID-INTERNAL",
        sma_period=5,
        max_open_positions=1
    )

    # Since nautilus properties are read only we bypass it
    class DummyStrat(MomentumLSSmaStrategy):
        @property
        def _log(self): return MagicMock()
        @property
        def cache(self): return getattr(self, "_mock_cache")

    strategy = DummyStrat.__new__(DummyStrat)
    strategy.allocator = allocator
    strategy._account_id = None
    strategy.instrument_id = InstrumentId.from_str("ADA.ETORO")
    strategy.bar_type = "ADA.ETORO-1-MINUTE-MID-INTERNAL"

    mock_cache = MagicMock()
    mock_account = MagicMock()
    mock_balance = MagicMock()
    mock_balance.free = 150.0  # Free balance in the cache
    mock_account.balances = [mock_balance]
    mock_account.id = "TEST-ACC"

    mock_cache.accounts.return_value = [mock_account]
    mock_cache.account.return_value = mock_account

    object.__setattr__(strategy, "_mock_cache", mock_cache)

    # Just call super().on_start() which does the cache logic without Nautilus internals
    from archive.strategies.momentum_ls_base import MomentumLSBaseStrategy
    MomentumLSBaseStrategy.on_start(strategy)

    assert strategy._account_id == "TEST-ACC"

    # Check that _get_current_balance extracts the 150.0
    balance = strategy._get_current_balance()
    assert balance == 150.0
