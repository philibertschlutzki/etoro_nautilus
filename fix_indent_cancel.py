import re

with open("automation/strategies/hourly_strategy_base.py", "r") as f:
    content = f.read()

# Fix indent issues
content = content.replace("        self._pending_cancel_for_close = False\n            return False", "            self._pending_cancel_for_close = False\n            return False")
content = content.replace("        self._pending_cancel_for_close = False\n            self._trailing_stop_side = \"LONG\" if pos.side == PositionSide.LONG else \"SHORT\"", "            self._pending_cancel_for_close = False\n            self._trailing_stop_side = \"LONG\" if pos.side == PositionSide.LONG else \"SHORT\"")
content = content.replace("        self._pending_cancel_for_close = False\n            return True", "            self._pending_cancel_for_close = False\n            return True")

with open("automation/strategies/hourly_strategy_base.py", "w") as f:
    f.write(content)
