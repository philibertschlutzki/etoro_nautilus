import re

with open('automation/tests/test_backtest_runner.py', 'r') as f:
    content = f.read()

content = content.replace('except Exception:\n            pass', 'except Exception as e:\n            print(e)\n            pass')

with open('automation/tests/test_backtest_runner.py', 'w') as f:
    f.write(content)
