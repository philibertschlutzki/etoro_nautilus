# The user wants to set a limit order for the profit target.
# "Können wir das Profit-Target ... anstatt es programmatisch via close >= _take_profit_price in der on_bar Methode zu evaluieren ... als native Limit-Order beim Einstieg mitsenden"
# But we must send it AFTER the entry order is filled, because we need to know the execution price to calculate the take-profit price (which is avg_px * (1 + pct)). Or we calculate it based on the theoretical bar close.
# Nautilus allows contingent orders. OCO (One Cancels Other), Bracket.
# But wait, we can just submit a LimitOrder for take profit when we receive the `on_order_filled` for the opening position! Or we can use OCO.
