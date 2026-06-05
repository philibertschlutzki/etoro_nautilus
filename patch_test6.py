import re

with open('automation/tests/test_backtest_runner.py', 'r') as f:
    content = f.read()

content = content.replace('start_ts = collections.namedtuple("TS", ["value"])(value=0)\n    end_ts = collections.namedtuple("TS", ["value"])(value=int(149.8 * 86400 * 1_000_000_000))', 'start_ts = type("TS", (), {"value": 0, "__sub__": lambda self, other: type("TD", (), {"value": self.value - other.value})()})()\n    end_ts = type("TS", (), {"value": int(149.8 * 86400 * 1_000_000_000), "__sub__": lambda self, other: type("TD", (), {"value": self.value - other.value})()})()')

with open('automation/tests/test_backtest_runner.py', 'w') as f:
    f.write(content)
