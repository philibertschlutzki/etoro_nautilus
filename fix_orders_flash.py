import re

with open("automation/strategies/flash_crash_reversal.py", "r") as f:
    content = f.read()

# Update flash crash as well
new_logic = """            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            if not open_orders:
                self._log.info(
                    f"[{self.instrument_id}] RECOVERY COMPLETE. SELL SIGNAL | "
                    f"Close: {close_price:.5f} | RSI: {self.rsi.value:.2f}"
                )
                self.current_signal = "SELL"
                self._on_sell_signal(bar)
                self.current_signal = None
            else:
                for order in open_orders:
                    self.cancel_order(order)"""

content = content.replace("""            # Check pending orders to avoid spamming
            if not self.cache.orders_open(instrument_id=self.instrument_id):
                self._log.info(
                    f"[{self.instrument_id}] RECOVERY COMPLETE. SELL SIGNAL | "
                    f"Close: {close_price:.5f} | RSI: {self.rsi.value:.2f}"
                )
                self.current_signal = "SELL"
                self._on_sell_signal(bar)
                self.current_signal = None""", new_logic)

new_logic2 = """            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            if not open_orders:
                self._close_position(pos)
            else:
                for order in open_orders:
                    self.cancel_order(order)"""

content = content.replace("""            if not self.cache.orders_open(instrument_id=self.instrument_id):
                self._close_position(pos)""", new_logic2)

with open("automation/strategies/flash_crash_reversal.py", "w") as f:
    f.write(content)
