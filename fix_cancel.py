import re

with open("automation/strategies/hourly_strategy_base.py", "r") as f:
    content = f.read()

# Add _pending_close flag
init_mod = """        self._in_position: bool = False
        self._pending_cancel_for_close: bool = False"""
content = content.replace("        self._in_position: bool = False", init_mod)

reset_mod = """        self._bars_in_position = 0
        self._pending_cancel_for_close = False"""
content = content.replace("        self._bars_in_position = 0", reset_mod)

# Update _close_position_base
close_mod = """    def _close_position_base(self, pos) -> None:
        \"\"\"Submits a market order to close the given position. Cancels pending limits first.\"\"\"
        open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
        if open_orders:
            self._pending_cancel_for_close = True
            for order in open_orders:
                self.cancel_order(order)
            return  # Wait for on_order_canceled

        self._execute_market_close(pos)

    def _execute_market_close(self, pos=None) -> None:
        if pos is None:
            positions = self.cache.positions_open(instrument_id=self.instrument_id)
            if not positions:
                return
            pos = positions[0]

        exit_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=exit_side,
            quantity=pos.quantity,
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    def on_order_canceled(self, event) -> None:
        self._log.info(f"[{self.instrument_id}] OrderCanceled: {event}")
        if self._pending_cancel_for_close:
            self._pending_cancel_for_close = False
            self._execute_market_close()"""

content = re.sub(r"    def _close_position_base\(self, pos\) -> None:.*?self\.submit_order\(order\)", close_mod, content, flags=re.DOTALL)

# Remove reduce_only=True
content = content.replace("                    reduce_only=True,\n", "")

with open("automation/strategies/hourly_strategy_base.py", "w") as f:
    f.write(content)
