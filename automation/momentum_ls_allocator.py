import logging
from typing import List

from nautilus_trader.model.identifiers import InstrumentId

logger = logging.getLogger(__name__)

class MomentumLSAllocator:
    """
    Thread-safe capital allocator for Momentum-LS live trading.
    Called by strategies at signal time to get their USD allocation.
    """

    def __init__(self, universe: List[str]):
        """
        Initialize the allocator with the full list of universe symbols.
        """
        self._universe = [InstrumentId.from_str(sym) for sym in universe]

    def get_allocation(
        self,
        instrument_id: InstrumentId,
        cache: object,
        account_balance: float,
    ) -> float:
        """
        Returns the USD amount this strategy instance should allocate for a new position.
        Returns 0.0 if allocation is not possible (no capital, or position already open).
        """
        if instrument_id not in self._universe:
            logger.warning(f"Instrument {instrument_id} is not in the Momentum-LS universe.")
            return 0.0

        # No-interference rule: if there is an open position for this instrument, do not allocate
        existing_positions = cache.positions_open(instrument_id=instrument_id)
        if existing_positions:
            return 0.0

        # Count pending signals (universe instruments that currently have NO open position)
        pending_signals = 0
        for uni_id in self._universe:
            if not cache.positions_open(instrument_id=uni_id):
                pending_signals += 1

        if pending_signals == 0:
            return 0.0

        # Allocate equally among pending signals
        allocation_per_signal = account_balance / pending_signals

        # Apply floor: eToro minimum order is $10, so $11 buffer is used.
        if allocation_per_signal < 11.0:
            logger.warning(
                f"Computed allocation ${allocation_per_signal:.2f} for {instrument_id} "
                "is below the $11.00 minimum threshold. Returning 0.0."
            )
            return 0.0

        return allocation_per_signal
