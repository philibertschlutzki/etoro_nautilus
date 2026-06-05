import pytest
from nautilus_trader.model.identifiers import InstrumentId
from automation.momentum_ls_allocator import MomentumLSAllocator

def test_live_allocator_smoke():
    """
    Smoke-Test für den Live-Allocator-Pfad.
    Bestätigt, dass die Allokator-Logik aufgerufen werden kann und
    ein gültiges Guthaben akzeptiert.
    """
    universe = ["AAPL.ETORO", "MSFT.ETORO", "TSLA.ETORO"]
    allocator = MomentumLSAllocator(universe=universe)

    class DummyCache:
        def positions_open(self, instrument_id):
            return []

    cache = DummyCache()
    instrument_id = InstrumentId.from_str("AAPL.ETORO")
    account_balance = 10000.0

    allocation = allocator.get_allocation(instrument_id, cache, account_balance)

    # MomentumLSAllocator verteilt das Kapital meistens gleichmäßig über das Universum,
    # oder verwendet spezifische Regeln. Wir testen hier nur, dass es nicht crashed
    # und eine sinnvolle Allokation > 0 zurückgibt.
    assert isinstance(allocation, float)
    assert allocation > 0.0
