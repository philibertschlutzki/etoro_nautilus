import re

with open("automation/backtest_runner.py", "r") as f:
    content = f.read()

# Replace the condition: if r.get("oos_metrics") is not None else True
# with: if r.get("oos_metrics") else True

content = content.replace('if r.get("oos_metrics") is not None else True', 'if r.get("oos_metrics") else True')

with open("automation/backtest_runner.py", "w") as f:
    f.write(content)
