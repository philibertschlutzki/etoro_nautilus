import re

with open("automation/tests/test_backtest_runner.py", "r") as f:
    content = f.read()

content = content.replace("from automation.backtest_runner import load_tournament_config, _is_eligible, _evaluate_oos_eligibility", "from automation.backtest_runner import load_tournament_config, _is_eligible, _evaluate_oos_eligibility")

with open("automation/tests/test_backtest_runner.py", "w") as f:
    f.write(content)
