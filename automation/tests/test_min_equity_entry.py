"""Aktien-Mindestgröße im Allocator: kleine Einstiege (Pauschalgebühr ~1 USD je Order) werden übersprungen, nie hochgesetzt."""
from __future__ import annotations

from automation.momentum_ls_allocator import MomentumLSAllocator
from nautilus_trader.model.identifiers import InstrumentId


class _Cache:
    def positions_open(self, instrument_id=None):
        return []


def _alloc(min_usd, total=0.60, symbol=0.02):
    return MomentumLSAllocator(["TSLA.ETORO", "EURUSD.ETORO"], max_total_exposure_fraction=total,
                               max_symbol_exposure_fraction=symbol, min_equity_entry_usd=min_usd)


def test_aktie_unter_mindestgroesse_wird_uebersprungen():
    a = _alloc(1000.0)
    assert a.get_allocation(InstrumentId.from_str("TSLA.ETORO"), _Cache(), 100_000.0) == 2000.0
    assert a.get_allocation(InstrumentId.from_str("TSLA.ETORO"), _Cache(), 30_000.0) == 0.0     # 2 % von 30k = 600 < 1000


def test_mindestgroesse_gilt_nur_fuer_aktien_und_ist_aus_bei_null():
    assert _alloc(1000.0).get_allocation(InstrumentId.from_str("EURUSD.ETORO"), _Cache(), 30_000.0) > 0.0   # keine Aktie: nicht betroffen
    assert _alloc(0.0).get_allocation(InstrumentId.from_str("TSLA.ETORO"), _Cache(), 30_000.0) == 600.0
