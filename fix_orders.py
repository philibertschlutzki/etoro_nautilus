with open("automation/strategies/hourly_strategy_base.py", "r") as f:
    content = f.read()

# We need to cancel existing open orders just in case!
new_logic = """        # Do not check exit conditions or try to close if the position is already pending close
        # Avoids Order-Spamming and Event-Loop Blockade
        open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
        if open_orders:
            for order in open_orders:
                self.cancel_order(order)
            return True # Wait for cancellation to complete before generating new signals"""

content = content.replace("        # Do not check exit conditions or try to close if the position is already pending close\n        # Avoids Order-Spamming and Event-Loop Blockade\n        if self.cache.orders_open(instrument_id=self.instrument_id):\n            return True", new_logic)

with open("automation/strategies/hourly_strategy_base.py", "w") as f:
    f.write(content)
