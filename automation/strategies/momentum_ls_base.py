
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import Bar
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from automation.momentum_ls_allocator import MomentumLSAllocator

class MomentumLSBaseStrategy(Strategy):
    """
    Base class for Momentum-LS strategies.
    Injects the MomentumLSAllocator and uses it to override quantity computation.
    """

    def __init__(self, config, allocator: MomentumLSAllocator):
        super().__init__(config)
        self.allocator = allocator

        # We need to track the current balance.
        # Since Nautilus doesn't directly inject the account_balance to the strategy natively,
        # we pull it from the cache's account state.
        self._account_id = None

    def on_start(self):
        # We assume the user has a single Margin account configured in the cache.
        accounts = self.cache.accounts()
        if accounts:
            self._account_id = accounts[0].id
        else:
            self._log.warning("No accounts found in cache on start. Allocation might fail.", LogColor.YELLOW)

    def _get_current_balance(self) -> float:
        if not self._account_id:
            accounts = self.cache.accounts()
            if accounts:
                self._account_id = accounts[0].id

        if self._account_id:
            acc = self.cache.account(self._account_id)
            if acc and acc.balances:
                # Assuming the first balance is the one we want (USD)
                return float(acc.balances[0].free)

        # Fallback if no account state is available
        self._log.warning("Could not resolve free balance from cache. Returning 0.0.")
        return 0.0

    def _compute_quantity(self, bar: Bar) -> Quantity | None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self._log.error(f"[{self.instrument_id}] Instrument nicht im Cache")
            return None

        balance = self._get_current_balance()
        usd_amount = self.allocator.get_allocation(self.instrument_id, self.cache, balance)

        if usd_amount <= 0:
            return None

        units = usd_amount / float(bar.close)
        # Pre-check: Equity-Instrumente (size_precision=0) erfordern mindestens 1 ganze Einheit.
        # Nautilus wirft einen harten ValueError bei make_qty() wenn das gerundete Ergebnis 0 ergibt —
        # auch mit round_down=True. Pre-check verhindert den Aufruf; try/except ist zusätzliche Absicherung.
        if units < float(instrument.size_increment):
            self._log.warning(
                f"[{self.instrument_id}] Zu wenig Kapital für 1 Einheit "
                f"(units={units:.6f}, size_increment={instrument.size_increment}) "
                f"— Signal übersprungen"
            )
            return None
        try:
            qty = instrument.make_qty(units, round_down=True)
        except ValueError as e:
            self._log.warning(
                f"[{self.instrument_id}] make_qty ValueError: {e} — Signal übersprungen"
            )
            return None
        if qty == 0:
            self._log.warning(
                f"[{self.instrument_id}] Quantity=0 nach Rundung "
                f"(units={units:.6f}) — Signal übersprungen"
            )
            return None
        return qty
