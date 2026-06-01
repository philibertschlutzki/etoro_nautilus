import re

with open("automation/strategies/flash_crash_reversal.py", "r") as f:
    content = f.read()

# Update flash crash as well
close_mod = """    def _close_position(self, pos) -> None:
        open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
        if open_orders:
            self._pending_cancel_for_close = True
            for order in open_orders:
                self.cancel_order(order)
            return

        self._execute_market_close(pos)"""

content = re.sub(r"    def _close_position\(self, pos\) -> None:.*?self\.submit_order\(order\)", close_mod, content, flags=re.DOTALL)

with open("automation/strategies/flash_crash_reversal.py", "w") as f:
    f.write(content)
