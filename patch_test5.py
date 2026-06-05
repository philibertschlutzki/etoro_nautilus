import re

with open('automation/tests/test_backtest_runner.py', 'r') as f:
    content = f.read()

content = content.replace('strat = {"_walk_forward_days": 150, "strategy_class": "MockStrategy", "strategy_module": "automation.strategies.mock"}', 'strat = {"_walk_forward_days": 150, "strategy_class": "MockStrategy", "strategy_module": "automation.strategies.mock", "config_class": "MockConfig"}')

with open('automation/tests/test_backtest_runner.py', 'w') as f:
    f.write(content)
