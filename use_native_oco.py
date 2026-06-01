import re

# The user is making two very valid points:
# 1. Asynchrony. Calling `cancel_order` then immediately `submit_order(market)` creates a race condition if Nautilus doesn't wait for cancel. We should wait for `on_order_canceled` before sending the close market order, or we use OCO logic natively.
# 2. reduce_only=True might not be supported by eToro adapter. We checked and there's no trace of `reduce_only` handling in etoro_execution.py.

# The user's prompt:
# "Hast du bedacht, wie wir mit der asynchronen Natur des Cancels umgehen (z. B. durch das Abwarten des Callbacks on_order_canceled, bevor die Market-Order abgesetzt wird)? Und hast du kurz im Code (etoro_execution.py) verifiziert, ob unser eToro-Adapter das Flag reduce_only=True bei Limit-Orders überhaupt nativ unterstützt und fehlerfrei verarbeitet?"

# So we have to either:
# 1. Implement `on_order_canceled` state tracking.
# 2. Or remove `reduce_only=True` and just do the tracking.

# Actually, the user asks "Und hast du kurz im Code ... verifiziert, ob unser eToro-Adapter das Flag reduce_only=True ... nativ unterstützt?"
# Since it does NOT, we should remove `reduce_only=True`.
# To wait for cancel, we can set a flag `self._pending_close_order` or similar. When we want to close, we cancel the limit order and set a flag. In `on_order_canceled`, we then submit the market close order.
# Wait, what if the order fills while we are canceling? We get `on_order_filled` and the position is closed. That's fine.
