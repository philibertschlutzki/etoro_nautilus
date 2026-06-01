# The user wants us to use native limit orders (Bracket OCO) instead of programmatic checks for the profit target.
# Wait, the prompt says "Können wir das Profit-Target ... eleganter als native Limit-Order beim Einstieg mitsenden"
# But currently all strategies inherit from HourlyStrategyBase, which just manages time-exits and ATR trailing.
# If we change `_on_buy_signal` / `_on_sell_signal` to use brackets, we need to know the price.
# A limit/stop order requires a specific limit price, but market orders don't have a guaranteed price until filled.
# Nautilus provides `self.order_factory.bracket(..., take_profit_price=..., stop_loss_price=...)`. But the entry is a Market order, not Limit! Can you do a bracket with a market order?
