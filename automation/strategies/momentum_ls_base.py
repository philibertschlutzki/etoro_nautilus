
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import Bar
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from automation.strategies.hourly_strategy_base import HourlyStrategyBase

from automation.momentum_ls_allocator import MomentumLSAllocator

class MomentumLSBaseStrategy(HourlyStrategyBase):
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


